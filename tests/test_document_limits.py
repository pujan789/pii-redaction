from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from taxhance_pii.domain import BoundingBox
from taxhance_pii.redaction import document
from taxhance_pii.redaction.document import (
    MAX_DOCUMENT_RENDER_PIXELS,
    MAX_PAGE_RENDER_PIXELS,
    DocumentError,
    WordBox,
    _add_pixel_budget,
    _limit_tesseract_threads,
)


def test_pixel_budget_accepts_normal_300_page_document() -> None:
    total = 0
    for _ in range(300):
        total = _add_pixel_budget(3_740_000, total)
    assert total < MAX_DOCUMENT_RENDER_PIXELS


def test_pixel_budget_rejects_oversized_page_and_aggregate() -> None:
    with pytest.raises(DocumentError, match="page_dimensions_too_large"):
        _add_pixel_budget(MAX_PAGE_RENDER_PIXELS + 1, 0)
    with pytest.raises(DocumentError, match="document_dimensions_too_large"):
        _add_pixel_budget(1, MAX_DOCUMENT_RENDER_PIXELS)


def test_load_document_ocrs_textless_pages_in_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    images = [Image.new("RGB", (size, size), "white") for size in (100, 120, 140)]
    pdf = tmp_path / "scanned.pdf"
    images[0].save(pdf, format="PDF", save_all=True, append_images=images[1:], resolution=72.0)

    active = 0
    peak = 0
    lock = threading.Lock()

    def fake_ocr(image: Image.Image) -> list[WordBox]:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.2)
        with lock:
            active -= 1
        return [
            WordBox(
                text=f"w{image.width}",
                box=BoundingBox(x1=1, y1=1, x2=10, y2=10),
                source="ocr",
            )
        ]

    monkeypatch.setattr(document, "ocr_words", fake_ocr)
    pages = document.load_document(pdf, ".pdf", 96, 10, True)

    assert [page.words[0].text for page in pages] == [f"w{page.image.width}" for page in pages]
    assert peak >= 2


def test_tesseract_thread_limit_defaults_to_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMP_THREAD_LIMIT", raising=False)
    _limit_tesseract_threads()
    assert os.environ["OMP_THREAD_LIMIT"] == "1"


def test_tesseract_thread_limit_respects_operator_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMP_THREAD_LIMIT", "3")
    _limit_tesseract_threads()
    assert os.environ["OMP_THREAD_LIMIT"] == "3"
