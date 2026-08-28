from __future__ import annotations

import io
import os
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


def _pdf_words(path: Path, page_count: int) -> list[list[WordBox]]:
    result: list[list[WordBox]] = [[] for _ in range(page_count)]
    try:
        with pdfplumber.open(path) as document:
            if len(document.pages) != page_count:
                return result
            for index, page in enumerate(document.pages):
                words = page.extract_words(
                    x_tolerance=2,
                    y_tolerance=2,
                    keep_blank_chars=False,
                    use_text_flow=True,
                )
                for word in words:
                    text = str(word.get("text", "")).strip()
                    if not text:
                        continue
                    result[index].append(
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
    except Exception:
        # Text extraction is an optional signal. Rendering remains authoritative.
        return result
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


def _render_pdf(path: Path, dpi: int, max_pages: int) -> list[PageArtifact]:
    try:
        document = pdfium.PdfDocument(path)
    except Exception as exc:
        raise DocumentError("pdf_unreadable") from exc
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
            page = document[index]
            width_points, height_points = page.get_size()
            projected_pixels = int(width_points * scale) * int(height_points * scale)
            total_pixels = _add_pixel_budget(projected_pixels, total_pixels)
            image = page.render(scale=scale, rotation=0).to_pil().convert("RGB")
            pages.append(PageArtifact(index, image, extracted_words[index]))
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError("pdf_render_failed") from exc
    finally:
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
    if ocr:
        for page in pages:
            if text_layer_is_garbage(page.words):
                page.words = ocr_words(page.image)
    return pages


def encode_preview(image: Image.Image, max_width: int = 1800) -> bytes:
    preview = image.copy()
    if preview.width > max_width:
        height = round(preview.height * max_width / preview.width)
        preview.thumbnail((max_width, height), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    preview.save(output, format="JPEG", quality=88, optimize=True, exif=b"")
    return output.getvalue()
