from __future__ import annotations

from pathlib import Path

import pdfplumber
import pytest
from PIL import Image, ImageDraw

from taxhance_pii.domain import BoundingBox, Detection, PiiCategory
from taxhance_pii.redaction.document import PageArtifact
from taxhance_pii.redaction.renderer import (
    RedactionValidationError,
    flattened_audit_image,
    render_redacted_pdf,
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
