from __future__ import annotations

from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw

from taxhance_pii.domain import BoundingBox, Detection, PiiCategory
from taxhance_pii.redaction.document import PageArtifact
from taxhance_pii.redaction.renderer import (
    RedactionValidationError,
    flattened_audit_image,
    render_redacted_pdf,
    rotate_box,
)


def test_renderer_builds_textless_pdf_with_opaque_box(tmp_path: Path) -> None:
    image = Image.new("RGB", (800, 1000), "white")
    ImageDraw.Draw(image).text((80, 100), "123-45-6789", fill="black")
    detection = Detection(
        id="one",
        page_index=0,
        category=PiiCategory.SSN,
        box=BoundingBox(x1=90, y1=80, x2=300, y2=145),
        confidence=1,
        source="regex",
    )
    output = tmp_path / "redacted.pdf"
    render_redacted_pdf([PageArtifact(0, image, [])], [detection], output, dpi=200)
    assert output.read_bytes().startswith(b"%PDF-")
    with pdfplumber.open(output) as document:
        assert document.pages[0].extract_text() in {None, ""}


def test_sliver_box_from_broken_glyph_metrics_fails_closed(tmp_path: Path) -> None:
    # A 1-thousandth-tall box comes from a text layer with broken glyph
    # heights; painting it draws a strikethrough that leaves the value
    # readable, so the document must fail instead of shipping.
    image = Image.new("RGB", (2550, 3300), "white")
    detection = Detection(
        id="sliver",
        page_index=0,
        category=PiiCategory.SSN,
        box=BoundingBox(x1=100, y1=507, x2=600, y2=508),
        confidence=1,
        source="regex",
    )
    output = tmp_path / "redacted.pdf"
    with pytest.raises(RedactionValidationError, match="degenerate_redaction_box"):
        render_redacted_pdf([PageArtifact(0, image, [])], [detection], output, dpi=300)


def test_small_but_sane_box_survives_pixel_verification(tmp_path: Path) -> None:
    # Small legitimate text must still verify; only degenerate slivers fail.
    image = Image.new("RGB", (2550, 3300), "white")
    detection = Detection(
        id="small",
        page_index=0,
        category=PiiCategory.SSN,
        box=BoundingBox(x1=100, y1=507, x2=600, y2=510),
        confidence=1,
        source="regex",
    )
    output = tmp_path / "redacted.pdf"
    render_redacted_pdf([PageArtifact(0, image, [])], [detection], output, dpi=300)
    assert output.read_bytes().startswith(b"%PDF-")


def test_flattened_audit_image_round_trips_through_pixel_only_pdf() -> None:
    image = Image.new("RGB", (800, 1000), "white")
    ImageDraw.Draw(image).rectangle((80, 100, 240, 160), fill="black")

    audit = flattened_audit_image(image, dpi=200)
    try:
        assert abs(audit.width - image.width) <= 1
        assert abs(audit.height - image.height) <= 1
        assert max(audit.getpixel((120, 130))) < 24
    finally:
        audit.close()


def test_rotate_box_follows_a_clockwise_page_rotation() -> None:
    box = BoundingBox(x1=100, y1=200, x2=300, y2=400)
    assert rotate_box(box, 0) == box
    assert rotate_box(box, 90) == BoundingBox(x1=600, y1=100, x2=800, y2=300)
    assert rotate_box(box, 180) == BoundingBox(x1=700, y1=600, x2=900, y2=800)
    assert rotate_box(box, 270) == BoundingBox(x1=200, y1=700, x2=400, y2=900)


def test_rotated_output_turns_every_page_and_still_verifies_boxes(tmp_path: Path) -> None:
    pages = []
    for index in range(2):
        image = Image.new("RGB", (800, 1000), "white")
        ImageDraw.Draw(image).text((80, 100), "123-45-6789", fill="black")
        pages.append(PageArtifact(index, image, []))
    detection = Detection(
        id="one",
        page_index=0,
        category=PiiCategory.SSN,
        box=BoundingBox(x1=90, y1=80, x2=300, y2=145),
        confidence=1,
        source="regex",
    )
    output = tmp_path / "redacted.pdf"

    render_redacted_pdf(pages, [detection], output, dpi=200, rotation=90)

    document = pdfium.PdfDocument(output)
    try:
        assert len(document) == 2
        for index in range(2):
            width, height = document[index].get_size()
            assert width > height, "a portrait page must come out landscape after 90 degrees"
        rendered = document[0].render(scale=1).to_pil().convert("RGB")
    finally:
        document.close()
    # The box centre (195, 112) in page space lands at (888, 195) after the turn.
    sample = rendered.getpixel((round(rendered.width * 0.888), round(rendered.height * 0.195)))
    assert max(sample) < 24
    untouched = rendered.getpixel((round(rendered.width * 0.2), round(rendered.height * 0.8)))
    assert min(untouched) > 200
