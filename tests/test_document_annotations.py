from __future__ import annotations

from pathlib import Path

import pytest
from pdf_fixtures import (
    ANNOTATION_RECT,
    acroform_pdf,
    freetext_annotation_pdf,
    link_annotation_pdf,
    password_protected_pdf,
    plain_text_pdf,
)
from PIL import Image

from taxhance_pii.domain import BoundingBox
from taxhance_pii.redaction import document
from taxhance_pii.redaction.document import DocumentError, WordBox, _render_pdf


def _ocr_sentinel(image: Image.Image) -> list[WordBox]:
    del image
    return [WordBox(text="OCR-SENTINEL", box=BoundingBox(x1=1, y1=1, x2=10, y2=10), source="ocr")]


def _has_ink(image: Image.Image, rect: tuple[int, int, int, int], dpi: int) -> bool:
    scale = dpi / 72
    x1, y1, x2, y2 = rect
    page_height_points = 792
    crop = image.convert("L").crop(
        (
            int(x1 * scale),
            int((page_height_points - y2) * scale),
            int(x2 * scale),
            int((page_height_points - y1) * scale),
        )
    )
    return crop.point(lambda p: 255 if p < 128 else 0).getbbox() is not None


def test_freetext_annotation_page_is_routed_to_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "freetext.pdf"
    pdf.write_bytes(freetext_annotation_pdf())
    monkeypatch.setattr(document, "ocr_words", _ocr_sentinel)

    pages = document.load_document(pdf, ".pdf", 72, 10, True)

    assert [word.text for word in pages[0].words] == ["OCR-SENTINEL"]


def test_freetext_annotation_without_ocr_fails_closed(tmp_path: Path) -> None:
    pdf = tmp_path / "freetext.pdf"
    pdf.write_bytes(freetext_annotation_pdf())

    with pytest.raises(DocumentError, match="ocr_unavailable"):
        document.load_document(pdf, ".pdf", 72, 10, False)


def test_link_annotations_keep_the_text_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "link.pdf"
    pdf.write_bytes(link_annotation_pdf())
    monkeypatch.setattr(document, "ocr_words", _ocr_sentinel)

    pages = document.load_document(pdf, ".pdf", 72, 10, True)

    assert "Employee" in [word.text for word in pages[0].words]


def test_plain_pages_keep_the_text_layer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pdf = tmp_path / "plain.pdf"
    pdf.write_bytes(plain_text_pdf())
    monkeypatch.setattr(document, "ocr_words", _ocr_sentinel)

    pages = document.load_document(pdf, ".pdf", 72, 10, True)

    assert "Employee" in [word.text for word in pages[0].words]


def test_form_field_values_are_rendered(tmp_path: Path) -> None:
    pdf = tmp_path / "form.pdf"
    pdf.write_bytes(acroform_pdf())

    pages = _render_pdf(pdf, 72, 10)

    assert _has_ink(pages[0].image, ANNOTATION_RECT, 72)


def test_form_field_page_is_routed_to_ocr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pdf = tmp_path / "form.pdf"
    pdf.write_bytes(acroform_pdf())
    monkeypatch.setattr(document, "ocr_words", _ocr_sentinel)

    pages = document.load_document(pdf, ".pdf", 72, 10, True)

    assert [word.text for word in pages[0].words] == ["OCR-SENTINEL"]


def test_password_protected_pdf_reports_a_specific_code(tmp_path: Path) -> None:
    pdf = tmp_path / "locked.pdf"
    pdf.write_bytes(password_protected_pdf())

    with pytest.raises(DocumentError, match="pdf_password_protected"):
        _render_pdf(pdf, 72, 10)
