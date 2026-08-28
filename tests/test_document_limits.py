from __future__ import annotations

import os

import pytest

from taxhance_pii.redaction.document import (
    MAX_DOCUMENT_RENDER_PIXELS,
    MAX_PAGE_RENDER_PIXELS,
    DocumentError,
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
