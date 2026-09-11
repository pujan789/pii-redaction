from __future__ import annotations

import io
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
import pytesseract
from PIL import Image, ImageSequence, UnidentifiedImageError
from pytesseract import Output

from taxhance_pii.domain import BoundingBox


def _limit_tesseract_threads() -> None:
    # Tesseract's OpenMP build spin-waits across its worker threads, so
    # concurrent page OCR collapses on small hosts (measured 112x slower for
    # three parallel pages on 4 vCPUs) while output is identical. One OMP
    # thread per tesseract process keeps parallel OCR linear; an operator can
    # still override the limit through the environment.
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")


_limit_tesseract_threads()


class DocumentError(RuntimeError):
    pass


MAX_PAGE_RENDER_PIXELS = 24_000_000
MAX_DOCUMENT_RENDER_PIXELS = 1_300_000_000

# Annotation subtypes that never add text of their own to the rendered page.
# Every other subtype (FreeText, Widget form fields, Stamp, ...) can paint
# text that is absent from the content stream, so such pages are OCR'd from
# the rendered image instead of trusting the text layer.
_TEXTLESS_ANNOTATIONS = {"Link", "Popup", "Highlight", "Underline", "StrikeOut", "Squiggly"}

# PDFium is not thread-safe and pypdfium2 adds no synchronization; concurrent
# renders from separate documents segfault inside libpdfium. Every pdfium call
# in the codebase must hold this lock.
PDFIUM_LOCK = threading.Lock()


def _add_pixel_budget(page_pixels: int, accumulated_pixels: int) -> int:
    if page_pixels < 1 or page_pixels > MAX_PAGE_RENDER_PIXELS:
        raise DocumentError("page_dimensions_too_large")
    total = accumulated_pixels + page_pixels
    if total > MAX_DOCUMENT_RENDER_PIXELS:
        raise DocumentError("document_dimensions_too_large")
    return total


@dataclass(frozen=True)
class WordBox:
    text: str
    box: BoundingBox
    source: str


@dataclass
class PageArtifact:
    page_index: int
    image: Image.Image
    words: list[WordBox]
    # True when the text layer cannot be trusted to contain everything that is
    # painted on the page (annotations or form fields carry their own text).
    needs_ocr: bool = False


def _normalized_box(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    width: float,
    height: float,
) -> BoundingBox:
    left = max(0, min(999, round(1000 * x1 / width)))
    top = max(0, min(999, round(1000 * y1 / height)))
    right = max(left + 1, min(1000, round(1000 * x2 / width)))
    bottom = max(top + 1, min(1000, round(1000 * y2 / height)))
    return BoundingBox(x1=left, y1=top, x2=right, y2=bottom)


def _page_paints_annotation_text(page: pdfplumber.page.Page) -> bool:
    try:
        annotations = page.annots
    except Exception:
        # Unparseable annotations: assume the worst and OCR the render.
        return True
    for annotation in annotations:
        data = annotation.get("data", {}) if isinstance(annotation, dict) else {}
        subtype = data.get("Subtype") if isinstance(data, dict) else None
        name = getattr(subtype, "name", str(subtype) if subtype is not None else "")
        if name not in _TEXTLESS_ANNOTATIONS:
            return True
    return False


def _pdf_words(path: Path, page_count: int) -> list[list[WordBox] | None]:
    """Per-page text-layer words; None marks a page whose text layer is unusable."""
    result: list[list[WordBox] | None] = [[] for _ in range(page_count)]
    try:
        with pdfplumber.open(path) as document:
            if len(document.pages) != page_count:
                return result
            for index, page in enumerate(document.pages):
                if _page_paints_annotation_text(page):
                    result[index] = None
                    continue
                words = page.extract_words(
                    x_tolerance=2,
                    y_tolerance=2,
                    keep_blank_chars=False,
                    use_text_flow=True,
                )
                page_words: list[WordBox] = []
                for word in words:
                    text = str(word.get("text", "")).strip()
                    if not text:
                        continue
                    page_words.append(
                        WordBox(
                            text=text,
                            box=_normalized_box(
                                float(word["x0"]),
                                float(word["top"]),
                                float(word["x1"]),
                                float(word["bottom"]),
                                float(page.width),
                                float(page.height),
                            ),
                            source="pdf",
                        )
                    )
                result[index] = page_words
    except Exception:
        # Text extraction is an optional signal. Rendering remains authoritative.
        return [[] for _ in range(page_count)]
    return result


def ocr_words(image: Image.Image) -> list[WordBox]:
    try:
        data = pytesseract.image_to_data(image, output_type=Output.DICT, config="--psm 11")
    except (pytesseract.TesseractError, FileNotFoundError) as exc:
        raise DocumentError("ocr_unavailable") from exc
    words: list[WordBox] = []
    width, height = image.size
    for index, raw_text in enumerate(data["text"]):
        text = str(raw_text).strip()
        if not text:
            continue
        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            confidence = -1
        if confidence < 20:
            continue
        x = int(data["left"][index])
        y = int(data["top"][index])
        box_width = int(data["width"][index])
        box_height = int(data["height"][index])
        if box_width <= 0 or box_height <= 0:
            continue
        words.append(
            WordBox(
                text=text,
                box=_normalized_box(x, y, x + box_width, y + box_height, width, height),
                source="ocr",
            )
        )
    return words


def _open_pdf(path: Path) -> pdfium.PdfDocument:
    try:
        document = pdfium.PdfDocument(path)
    except Exception as exc:
        code = (
            "pdf_password_protected"
            if pdfium.raw.FPDF_GetLastError() == pdfium.raw.FPDF_ERR_PASSWORD
            else "pdf_unreadable"
        )
        raise DocumentError(code) from exc
    # Form fields only render through a form environment; without it a filled
    # W-9 or organizer comes back with every field blank.
    document.init_forms()
    return document


def _render_pdf(path: Path, dpi: int, max_pages: int) -> list[PageArtifact]:
    with PDFIUM_LOCK:
        document = _open_pdf(path)
        page_count = len(document)
    if page_count < 1:
        raise DocumentError("document_empty")
    if page_count > max_pages:
        raise DocumentError("too_many_pages")
    extracted_words = _pdf_words(path, page_count)
    pages: list[PageArtifact] = []
    scale = dpi / 72
    total_pixels = 0
    try:
        for index in range(page_count):
            with PDFIUM_LOCK:
                page = document[index]
                width_points, height_points = page.get_size()
                projected_pixels = int(width_points * scale) * int(height_points * scale)
                total_pixels = _add_pixel_budget(projected_pixels, total_pixels)
                image = page.render(scale=scale, rotation=0).to_pil().convert("RGB")
            words = extracted_words[index]
            pages.append(
                PageArtifact(index, image, words if words is not None else [], words is None)
            )
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError("pdf_render_failed") from exc
    finally:
        with PDFIUM_LOCK:
            document.close()
    return pages


def _render_image(path: Path, max_pages: int) -> list[PageArtifact]:
    try:
        source = Image.open(path)
    except (UnidentifiedImageError, OSError) as exc:
        raise DocumentError("image_unreadable") from exc
    pages: list[PageArtifact] = []
    total_pixels = 0
    try:
        for index, frame in enumerate(ImageSequence.Iterator(source)):
            if index >= max_pages:
                raise DocumentError("too_many_pages")
            image = frame.convert("RGB")
            total_pixels = _add_pixel_budget(image.width * image.height, total_pixels)
            pages.append(PageArtifact(index, image.copy(), []))
    finally:
        source.close()
    if not pages:
        raise DocumentError("document_empty")
    return pages


def load_document(
    path: Path, extension: str, dpi: int, max_pages: int, ocr: bool
) -> list[PageArtifact]:
    from taxhance_pii.redaction.textgate import text_layer_is_garbage

    pages = (
        _render_pdf(path, dpi, max_pages) if extension == ".pdf" else _render_image(path, max_pages)
    )
    if not ocr:
        if any(page.needs_ocr for page in pages):
            # Annotation or form-field text is painted on the page but absent
            # from the text layer; without OCR it would ship unredacted.
            raise DocumentError("ocr_unavailable")
        return pages
    targets = [page for page in pages if page.needs_ocr or text_layer_is_garbage(page.words)]
    if targets:
        # Tesseract runs as a subprocess pinned to one OMP thread, so
        # pages OCR in parallel across cores instead of one at a time.
        workers = min(len(targets), max(1, os.cpu_count() or 1))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            extracted = list(pool.map(lambda page: ocr_words(page.image), targets))
        for page, words in zip(targets, extracted, strict=True):
            page.words = words
    return pages


def encode_preview(image: Image.Image, max_width: int = 1800) -> bytes:
    preview = image.copy()
    if preview.width > max_width:
        height = round(preview.height * max_width / preview.width)
        preview.thumbnail((max_width, height), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    preview.save(output, format="JPEG", quality=88, optimize=True, exif=b"")
    return output.getvalue()
