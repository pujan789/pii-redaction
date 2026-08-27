from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field

from taxhance_pii.config import Settings
from taxhance_pii.domain import BoundingBox, Detection, PiiCategory, RedactionManifest, utc_now
from taxhance_pii.evaluation.corpus import SampleEntry, SampleManifest
from taxhance_pii.redaction.anchor import ssn_safety_net
from taxhance_pii.redaction.document import PageArtifact, load_document
from taxhance_pii.redaction.prompt import PROMPT_VERSION
from taxhance_pii.redaction.renderer import redact_image, render_redacted_pdf
from taxhance_pii.worker.detector import DETECTOR_VERSION, DocumentDetector

logger = logging.getLogger(__name__)


class EvaluationFinding(BaseModel):
    """Value-free geometry retained only for private evaluation diagnostics."""

    model_config = ConfigDict(extra="forbid")

    page_index: int
    category: PiiCategory
    box: BoundingBox
    confidence: float
    source: Literal["model", "regex", "ocr", "user"]

    @classmethod
    def from_detection(cls, detection: Detection) -> EvaluationFinding:
        return cls(
            page_index=detection.page_index,
            category=detection.category,
            box=detection.box,
            confidence=detection.confidence,
            source=detection.source,
        )


class EvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_id: str
    split: Literal["tuning", "holdout"]
    page_count: int
    elapsed_seconds: float
    finding_count: int
    deterministic_findings: int
    model_findings: int
    residual_deterministic: int
    residual_model: int
    residual_deterministic_findings: list[EvaluationFinding] = Field(default_factory=list)
    residual_model_findings: list[EvaluationFinding] = Field(default_factory=list)
    flattened: bool
    passed_automatic_checks: bool
    error_code: str | None = None


@dataclass(frozen=True)
class EvaluationPaths:
    root: Path

    def document(self, evaluation_id: str) -> Path:
        path = self.root / evaluation_id
        path.mkdir(parents=True, exist_ok=True)
        return path


ResultCallback = Callable[[EvaluationResult, Path], None]


def _contact_sheet(original: Image.Image, redacted: Image.Image, destination: Path) -> None:
    max_side = 1000
    left = original.copy()
    right = redacted.copy()
    try:
        left.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        right.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        label_height = 36
        sheet = Image.new(
            "RGB",
            (left.width + right.width, max(left.height, right.height) + label_height),
            "#e5e7eb",
        )
        try:
            sheet.paste(left, (0, label_height))
            sheet.paste(right, (left.width, label_height))
            draw = ImageDraw.Draw(sheet)
            draw.text((12, 10), "PRIVATE ORIGINAL - DO NOT SHARE", fill="#991b1b")
            draw.text((left.width + 12, 10), "REDACTED CANDIDATE", fill="#111827")
            sheet.save(destination, format="JPEG", quality=88, optimize=True, exif=b"")
        finally:
            sheet.close()
    finally:
        left.close()
        right.close()


def evaluate_entry(
    entry: SampleEntry,
    settings: Settings,
    detector: DocumentDetector,
    paths: EvaluationPaths,
) -> EvaluationResult:
    started = time.monotonic()
    document_dir = paths.document(entry.evaluation_id)
    source = Path(entry.source_path)
    pages: list[PageArtifact] = []
    redacted_pages: list[PageArtifact] = []
    try:
        pages = load_document(
            source,
            ".pdf",
            settings.render_dpi,
            settings.max_pages,
            settings.ocr_enabled,
        )
        detections = detector.detect_document(pages)
        output_path = document_dir / "redacted.pdf"
        render_redacted_pdf(pages, detections, output_path, settings.render_dpi)
        manifest = RedactionManifest(
            job_id=f"evaluation-{entry.evaluation_id}",
            page_count=len(pages),
            detections=detections,
            detector_version=DETECTOR_VERSION,
            prompt_version=PROMPT_VERSION,
            model_id=settings.model_id,
            created_at=utc_now(),
        )
        (document_dir / "manifest.json").write_text(
            manifest.model_dump_json(indent=2),
            encoding="utf-8",
        )

        redacted_pages = load_document(
            output_path,
            ".pdf",
            settings.render_dpi,
            settings.max_pages,
            settings.ocr_enabled,
        )
        residual_exact = [
            item
            for redacted in redacted_pages
            for item in ssn_safety_net(redacted.page_index, redacted.words)
        ]
        residual_visual: list[Detection] = []
        by_page: dict[int, list[Detection]] = {}
        for detection in detections:
            by_page.setdefault(detection.page_index, []).append(detection)
        for original, redacted in zip(pages, redacted_pages, strict=True):
            del redacted
            redacted_image = redact_image(original.image, by_page.get(original.page_index, []))
            try:
                _contact_sheet(
                    original.image,
                    redacted_image,
                    document_dir / f"review-page-{original.page_index + 1:04d}.jpg",
                )
            finally:
                redacted_image.close()
        result = EvaluationResult(
            evaluation_id=entry.evaluation_id,
            split=entry.split,
            page_count=len(pages),
            elapsed_seconds=round(time.monotonic() - started, 3),
            finding_count=len(detections),
            deterministic_findings=sum(item.source != "model" for item in detections),
            model_findings=sum(item.source == "model" for item in detections),
            residual_deterministic=len(residual_exact),
            residual_model=len(residual_visual),
            residual_deterministic_findings=[
                EvaluationFinding.from_detection(item) for item in residual_exact
            ],
            residual_model_findings=[
                EvaluationFinding.from_detection(item) for item in residual_visual
            ],
            flattened=True,
            passed_automatic_checks=not residual_exact and not residual_visual,
        )
    except (ImportError, MemoryError, ModuleNotFoundError):
        raise
    except Exception as exc:
        # Evaluation sources use opaque IDs, so a traceback is safe here and is
        # essential for distinguishing a malformed PDF from a systemic runtime
        # problem. The production request path never logs document text or the
        # user's original filename.
        logger.exception(
            "evaluation_document_failed",
            extra={"evaluation_id": entry.evaluation_id, "error_code": type(exc).__name__},
        )
        result = EvaluationResult(
            evaluation_id=entry.evaluation_id,
            split=entry.split,
            page_count=entry.page_count,
            elapsed_seconds=round(time.monotonic() - started, 3),
            finding_count=0,
            deterministic_findings=0,
            model_findings=0,
            residual_deterministic=0,
            residual_model=0,
            flattened=False,
            passed_automatic_checks=False,
            error_code=type(exc).__name__,
        )
    finally:
        for page in [*pages, *redacted_pages]:
            page.image.close()
    (document_dir / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result


def run_evaluation(
    manifest: SampleManifest,
    split: Literal["tuning", "holdout"],
    settings: Settings,
    detector: DocumentDetector,
    output: Path,
    existing_results: list[EvaluationResult] | None = None,
    on_result: ResultCallback | None = None,
) -> list[EvaluationResult]:
    paths = EvaluationPaths(output)
    entries = [entry for entry in manifest.entries if entry.split == split]
    entry_ids = {entry.evaluation_id for entry in entries}
    results_by_id: dict[str, EvaluationResult] = {}
    for result in existing_results or []:
        if (
            result.split != split
            or result.evaluation_id not in entry_ids
            or result.evaluation_id in results_by_id
        ):
            raise ValueError("evaluation_checkpoint_invalid")
        results_by_id[result.evaluation_id] = result

    for index, entry in enumerate(entries, start=1):
        if entry.evaluation_id in results_by_id:
            logger.info(
                "evaluation_resume_skip",
                extra={
                    "split": split,
                    "completed": index,
                    "total": len(entries),
                },
            )
            continue
        result = evaluate_entry(entry, settings, detector, paths)
        if on_result is not None:
            on_result(result, paths.document(entry.evaluation_id))
        results_by_id[entry.evaluation_id] = result
        logger.info(
            "evaluation_progress",
            extra={
                "split": split,
                "completed": index,
                "total": len(entries),
                "passed": result.passed_automatic_checks,
            },
        )
    results = [results_by_id[entry.evaluation_id] for entry in entries]
    summary = {
        "run_id": str(uuid.uuid4()),
        "split": split,
        "prompt_version": PROMPT_VERSION,
        "detector_version": DETECTOR_VERSION,
        "model_id": settings.model_id,
        "documents": len(results),
        "pages": sum(result.page_count for result in results),
        "automatic_passes": sum(result.passed_automatic_checks for result in results),
        "failures": sum(result.error_code is not None for result in results),
        "residual_deterministic": sum(result.residual_deterministic for result in results),
        "residual_model": sum(result.residual_model for result in results),
        "elapsed_seconds": round(sum(result.elapsed_seconds for result in results), 3),
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / f"summary-{split}.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    return results
