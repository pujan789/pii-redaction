from __future__ import annotations

import io
from pathlib import Path
from typing import cast

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageDraw

from taxhance_pii.domain import Detection
from taxhance_pii.redaction.document import PDFIUM_LOCK, PageArtifact


class RedactionValidationError(RuntimeError):
    pass


def _pixel_box(
    detection: Detection, image: Image.Image, padding_pixels: int = 3
) -> tuple[int, int, int, int]:
    width, height = image.size
    return (
        max(0, round(detection.box.x1 * width / 1000) - padding_pixels),
        max(0, round(detection.box.y1 * height / 1000) - padding_pixels),
        min(width, round(detection.box.x2 * width / 1000) + padding_pixels),
        min(height, round(detection.box.y2 * height / 1000) + padding_pixels),
    )


def render_redacted_pdf(
    pages: list[PageArtifact],
    detections: list[Detection],
    destination: Path,
    dpi: int,
) -> None:
    by_page: dict[int, list[Detection]] = {}
    for detection in detections:
        by_page.setdefault(detection.page_index, []).append(detection)

    redacted_pages: list[Image.Image] = []
    for page in pages:
        redacted_pages.append(redact_image(page.image, by_page.get(page.page_index, [])))

    if not redacted_pages:
        raise RedactionValidationError("document_empty")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        redacted_pages[0].save(
            destination,
            format="PDF",
            save_all=True,
            append_images=redacted_pages[1:],
            resolution=float(dpi),
            quality=95,
            optimize=True,
        )
        validate_flattened_pdf(destination, len(redacted_pages), detections)
    finally:
        for image in redacted_pages:
            image.close()


# Nothing legible is shorter than ~1.6pt (2 thousandths of a letter page); a
# thinner box means broken source glyph geometry, and painting it draws a
# strikethrough that leaves the value readable.
MIN_BOX_THOUSANDTHS = 2


def validate_flattened_pdf(
    path: Path,
    expected_pages: int,
    detections: list[Detection],
) -> None:
    for detection in detections:
        if (
            detection.box.y2 - detection.box.y1 < MIN_BOX_THOUSANDTHS
            or detection.box.x2 - detection.box.x1 < MIN_BOX_THOUSANDTHS
        ):
            raise RedactionValidationError("degenerate_redaction_box")
    raw = path.read_bytes()
    if not raw.startswith(b"%PDF-"):
        raise RedactionValidationError("output_not_pdf")
    forbidden_markers = (b"/EmbeddedFile", b"/JavaScript", b"/JS ", b"/AcroForm")
    if any(marker in raw for marker in forbidden_markers):
        raise RedactionValidationError("output_contains_active_content")
    try:
        with PDFIUM_LOCK:
            document = pdfium.PdfDocument(path)
            page_total = len(document)
            document.close()
        if page_total != expected_pages:
            raise RedactionValidationError("output_page_count_mismatch")
        with pdfplumber.open(path) as output:
            if any((page.extract_text() or "").strip() for page in output.pages):
                raise RedactionValidationError("output_contains_text_layer")
    except RedactionValidationError:
        raise
    except Exception as exc:
        raise RedactionValidationError("output_validation_failed") from exc

    # Pixel-level verification ensures every requested region was actually painted black.
    with PDFIUM_LOCK:
        check_document = pdfium.PdfDocument(path)
    try:
        for page_index in range(expected_pages):
            page_detections = [item for item in detections if item.page_index == page_index]
            if not page_detections:
                continue
            # Scale 2 (144 dpi) keeps a minimum-size 1-thousandth box at >=1
            # rendered pixel; at scale 1 both edges can round to the same pixel
            # and a correctly painted box would fail as empty.
            with PDFIUM_LOCK:
                image = check_document[page_index].render(scale=2).to_pil().convert("RGB")
            for detection in page_detections:
                crop = image.crop(_pixel_box(detection, image, padding_pixels=0))
                if crop.width == 0 or crop.height == 0:
                    raise RedactionValidationError("redaction_box_empty")
                samples = list(crop.resize((8, 8)).get_flattened_data())
                dark_ratio = sum(max(pixel) < 24 for pixel in samples) / len(samples)
                if dark_ratio < 0.96:
                    raise RedactionValidationError("redaction_pixels_not_opaque")
    finally:
        with PDFIUM_LOCK:
            check_document.close()


def redacted_preview(image: Image.Image, detections: list[Detection]) -> bytes:
    preview = redact_image(image, detections)
    output = io.BytesIO()
    preview.save(output, format="JPEG", quality=88, optimize=True, exif=b"")
    return output.getvalue()


def flattened_audit_image(image: Image.Image, dpi: int) -> Image.Image:
    """Round-trip a page through the same pixel-only PDF form used for downloads."""

    encoded = io.BytesIO()
    image.save(
        encoded,
        format="PDF",
        resolution=float(dpi),
        quality=95,
        optimize=True,
    )
    with PDFIUM_LOCK:
        document = pdfium.PdfDocument(encoded.getvalue())
        try:
            return cast(Image.Image, document[0].render(scale=dpi / 72).to_pil().convert("RGB"))
        finally:
            document.close()


def redact_image(image: Image.Image, detections: list[Detection]) -> Image.Image:
    redacted = image.copy().convert("RGB")
    draw = ImageDraw.Draw(redacted)
    for detection in detections:
        draw.rectangle(_pixel_box(detection, redacted), fill=(0, 0, 0))
    clean = Image.new("RGB", redacted.size, "white")
    clean.paste(redacted)
    return clean
