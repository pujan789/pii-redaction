from __future__ import annotations

import contextlib
import logging
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from taxhance_pii.config import Settings
from taxhance_pii.domain import (
    JobRecord,
    JobStatus,
    RedactionManifest,
    TaskType,
    utc_now,
)
from taxhance_pii.redaction.anchor import ssn_safety_net
from taxhance_pii.redaction.document import (
    DocumentError,
    PageArtifact,
    encode_preview,
    load_document,
)
from taxhance_pii.redaction.prompt import PROMPT_VERSION
from taxhance_pii.redaction.renderer import (
    RedactionValidationError,
    render_redacted_pdf,
)
from taxhance_pii.repository import JobNotFound, JobRepository, StateConflict
from taxhance_pii.storage import BlobStore
from taxhance_pii.task_queue import TaskQueue
from taxhance_pii.worker.detector import DETECTOR_VERSION, DetectorError, DocumentDetector

logger = logging.getLogger(__name__)


class JobCancelled(RuntimeError):
    pass


class WorkerPipeline:
    def __init__(
        self,
        settings: Settings,
        repository: JobRepository,
        blobs: BlobStore,
        queue: TaskQueue,
        detector: DocumentDetector,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.blobs = blobs
        self.queue = queue
        self.detector = detector

    def process(self, job: JobRecord, task: TaskType) -> None:
        if task == TaskType.DETECT:
            self._detect(job)
        else:
            self._redact(job)

    def _load_pages(self, job: JobRecord, directory: Path) -> tuple[Path, list[PageArtifact]]:
        input_path = directory / f"source{job.file_extension}"
        self.blobs.download_file(job.input_key, input_path)
        pages = load_document(
            input_path,
            job.file_extension,
            self.settings.render_dpi,
            self.settings.max_pages,
            self.settings.ocr_enabled,
        )
        return input_path, pages

    def _progress_reporter(self, job_id: str) -> Callable[[], None]:
        """Per-page progress writes; a vanished or re-owned job is ignored."""
        lock = threading.Lock()
        completed = 0

        def report() -> None:
            nonlocal completed
            with lock, contextlib.suppress(StateConflict, JobNotFound):
                completed += 1
                self.repository.update(job_id, {JobStatus.DETECTING}, pages_completed=completed)

        return report

    def _detect(self, job: JobRecord) -> None:
        with tempfile.TemporaryDirectory(prefix=f"pii-{job.job_id}-") as raw_directory:
            directory = Path(raw_directory)
            _, pages = self._load_pages(job, directory)
            self.repository.update(
                job.job_id,
                {JobStatus.DETECTING},
                page_count=len(pages),
                pages_completed=0,
            )
            for page in pages:
                self._assert_active(job.job_id, JobStatus.DETECTING)
                self.blobs.put_bytes(
                    f"jobs/{job.job_id}/previews/{page.page_index:04d}.jpg",
                    encode_preview(page.image),
                    "image/jpeg",
                )
            detections = self.detector.detect_document(
                pages, progress=self._progress_reporter(job.job_id)
            )
            self._assert_active(job.job_id, JobStatus.DETECTING)
            self.repository.update(
                job.job_id,
                {JobStatus.DETECTING},
                pages_completed=len(pages),
                finding_count=len(detections),
            )

            manifest = RedactionManifest(
                job_id=job.job_id,
                page_count=len(pages),
                detections=detections,
                detector_version=DETECTOR_VERSION,
                prompt_version=PROMPT_VERSION,
                model_id=self.settings.model_id,
                created_at=utc_now(),
            )
            encoded = manifest.model_dump_json().encode("utf-8")
            self.blobs.put_bytes(job.draft_manifest_key, encoded, "application/json")
            if job.auto_finalize:
                self.blobs.put_bytes(job.approved_manifest_key, encoded, "application/json")
                redacting = self.repository.update(
                    job.job_id,
                    {JobStatus.DETECTING},
                    status=JobStatus.REDACTING,
                    finding_count=len(manifest.detections),
                )
                self._render_loaded(redacting, pages, manifest, directory)
            else:
                self.repository.update(
                    job.job_id,
                    {JobStatus.DETECTING},
                    status=JobStatus.REVIEW_REQUIRED,
                    finding_count=len(manifest.detections),
                )

    def _redact(self, job: JobRecord) -> None:
        with tempfile.TemporaryDirectory(prefix=f"pii-{job.job_id}-") as raw_directory:
            directory = Path(raw_directory)
            _, pages = self._load_pages(job, directory)
            manifest = RedactionManifest.model_validate_json(
                self.blobs.get_bytes(job.approved_manifest_key)
            )
            if manifest.job_id != job.job_id or manifest.page_count != len(pages):
                raise RedactionValidationError("manifest_document_mismatch")
            self._render_loaded(job, pages, manifest, directory)

    def _render_loaded(
        self,
        job: JobRecord,
        pages: list[PageArtifact],
        manifest: RedactionManifest,
        directory: Path,
    ) -> None:
        output_path = directory / "redacted.pdf"
        render_redacted_pdf(
            pages,
            manifest.detections,
            output_path,
            self.settings.render_dpi,
            manifest.rotation,
        )
        if self.settings.ocr_enabled:
            residual_pages = load_document(
                output_path,
                ".pdf",
                self.settings.render_dpi,
                self.settings.max_pages,
                True,
            )
            try:
                if any(ssn_safety_net(page.page_index, page.words) for page in residual_pages):
                    raise RedactionValidationError("residual_identifier_detected")
            finally:
                for page in residual_pages:
                    page.image.close()
        self._assert_active(job.job_id, JobStatus.REDACTING)
        self.blobs.put_file(job.output_key, output_path, "application/pdf")
        self._mark_complete(job, len(pages), len(manifest.detections))

    def _mark_complete(self, job: JobRecord, pages: int, finding_count: int) -> None:
        try:
            self.repository.update(
                job.job_id,
                {JobStatus.REDACTING},
                status=JobStatus.COMPLETE,
                pages_completed=pages,
                finding_count=finding_count,
                error_code=None,
            )
        except (StateConflict, JobNotFound) as exc:
            current = self.repository.get(job.job_id)
            if current is None or current.status in {JobStatus.DELETED, JobStatus.EXPIRED}:
                # A concurrent delete wins; remove any output uploaded during the race.
                self.blobs.delete_prefix(f"jobs/{job.job_id}")
                raise JobCancelled("job_deleted") from exc
            # Another worker owns the job now; its output must stay untouched.
            raise JobCancelled("job_state_changed") from exc

    def _assert_active(self, job_id: str, expected: JobStatus) -> None:
        current = self.repository.get(job_id)
        if current is None or current.status != expected or current.expires_at <= utc_now():
            raise JobCancelled("job_inactive")

    def fail(self, job_id: str, error: Exception) -> None:
        if isinstance(error, JobCancelled):
            return
        if isinstance(error, (DocumentError, DetectorError, RedactionValidationError)):
            code = str(error)
        else:
            code = "processing_failed"
        current = self.repository.get(job_id)
        if current is None or current.status in {JobStatus.DELETED, JobStatus.EXPIRED}:
            self.blobs.delete_prefix(f"jobs/{job_id}")
            return
        if current.status == JobStatus.COMPLETE:
            return
        if (
            isinstance(error, RedactionValidationError)
            and current.status == JobStatus.REDACTING
            and not current.auto_finalize
        ):
            # The reviewer's approved boxes are still stored; let them fix the
            # document instead of re-uploading and redoing every edit.
            try:
                self.repository.update(
                    job_id,
                    {JobStatus.REDACTING},
                    status=JobStatus.REVIEW_REQUIRED,
                    error_code=code[:80],
                )
            except StateConflict:
                return
            logger.error("job_returned_to_review", extra={"job_id": job_id, "error_code": code})
            return
        try:
            self.repository.update(
                job_id,
                {JobStatus.DETECTING, JobStatus.REDACTING},
                status=JobStatus.FAILED,
                error_code=code[:80],
            )
        except StateConflict:
            return
        logger.error(
            "job_failed",
            extra={"job_id": job_id, "error_code": code, "error_type": type(error).__name__},
            exc_info=code == "processing_failed",
        )
