from pathlib import Path

from PIL import Image, ImageDraw

from taxhance_pii.config import Settings
from taxhance_pii.evaluation.corpus import SampleEntry
from taxhance_pii.evaluation.runner import EvaluationPaths, evaluate_entry
from taxhance_pii.worker.detector import NoopDetector


def _make_pdf(path: Path, pages: int = 2) -> None:
    images = []
    for index in range(pages):
        image = Image.new("RGB", (800, 1000), "white")
        draw = ImageDraw.Draw(image)
        draw.text((60, 60), f"Sample statement page {index + 1}", fill="black")
        draw.text((60, 120), "Total interest paid 1,234.56", fill="black")
        images.append(image)
    images[0].save(path, format="PDF", save_all=True, append_images=images[1:], resolution=150.0)


def _entry(pdf: Path) -> SampleEntry:
    return SampleEntry(
        evaluation_id="eval-test-0001",
        source_path=str(pdf),
        content_sha256="a" * 64,
        size_bytes=pdf.stat().st_size,
        page_count=2,
        text_kind="digital",
        split="tuning",
    )


def test_evaluate_entry_produces_outputs_and_passes(tmp_path: Path) -> None:
    pdf = tmp_path / "sample.pdf"
    _make_pdf(pdf)
    settings = Settings(token_pepper="x" * 40, data_dir=tmp_path, ocr_enabled=False)
    result = evaluate_entry(
        _entry(pdf), settings, NoopDetector(), EvaluationPaths(tmp_path / "out")
    )
    document_dir = tmp_path / "out" / "eval-test-0001"
    assert (document_dir / "redacted.pdf").is_file()
    assert (document_dir / "manifest.json").is_file()
    assert (document_dir / "result.json").is_file()
    assert (document_dir / "review-page-0001.jpg").is_file()
    assert result.page_count == 2
    assert result.finding_count == 0
    assert result.residual_deterministic == 0
    assert result.passed_automatic_checks is True
    assert result.error_code is None


def test_evaluate_entry_records_failure_for_unreadable_source(tmp_path: Path) -> None:
    bogus = tmp_path / "broken.pdf"
    bogus.write_bytes(b"not a pdf at all")
    settings = Settings(token_pepper="x" * 40, data_dir=tmp_path, ocr_enabled=False)
    entry = SampleEntry(
        evaluation_id="eval-test-0002",
        source_path=str(bogus),
        content_sha256="b" * 64,
        size_bytes=bogus.stat().st_size,
        page_count=1,
        text_kind="unknown",
        split="tuning",
    )
    result = evaluate_entry(entry, settings, NoopDetector(), EvaluationPaths(tmp_path / "out"))
    assert result.passed_automatic_checks is False
    assert result.error_code is not None
