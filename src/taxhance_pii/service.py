from __future__ import annotations

import json
import uuid
from datetime import timedelta

from taxhance_pii.config import Settings
from taxhance_pii.domain import (
    CreateJobRequest,
    CreateJobResponse,
    JobRecord,
    JobResponse,
    JobStatus,
    ManifestUpdate,
    QueueMessage,
    RedactionManifest,
    TaskType,
    utc_now,
)
from taxhance_pii.repository import JobNotFound, JobRepository, StateConflict
from taxhance_pii.security import (
    client_fingerprint,
    new_access_token,
    token_matches,
    validate_magic,
    validate_upload_metadata,
)
from taxhance_pii.storage import BlobStore
from taxhance_pii.task_queue import TaskQueue


class ServiceError(RuntimeError):
    def __init__(self, code: str, status_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class JobService:
    def __init__(
        self,
        settings: Settings,
        repository: JobRepository,
        blobs: BlobStore,
        queue: TaskQueue,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.blobs = blobs
        self.queue = queue

    def create_job(self, request: CreateJobRequest, source_ip: str) -> CreateJobResponse:
        if request.size_bytes > self.settings.max_upload_bytes:
            raise ServiceError("upload_too_large", 413)
        try:
            extension, content_type = validate_upload_metadata(
                request.filename, request.content_type
            )
        except ValueError as exc:
            raise ServiceError(str(exc), 415) from exc

        client_hash = client_fingerprint(source_ip, self.settings.pepper_bytes)
        one_hour_ago = utc_now() - timedelta(hours=1)
        if (
            self.repository.count_recent(client_hash, one_hour_ago)
            >= self.settings.max_jobs_per_ip_per_hour
        ):
            raise ServiceError("hourly_abuse_limit", 429)
        if self.repository.count_active(client_hash) >= self.settings.max_active_jobs_per_ip:
            raise ServiceError("active_job_abuse_limit", 429)
        if self.repository.count_queued() >= self.settings.max_queue_depth:
            raise ServiceError("service_busy", 503)

        now = utc_now()
        retention = timedelta(hours=self.settings.retention_hours) - timedelta(
            minutes=self.settings.retention_cleanup_margin_minutes
        )
        job_id = str(uuid.uuid4())
        token = new_access_token()
        prefix = f"jobs/{job_id}"
        job = JobRecord(
            job_id=job_id,
            status=JobStatus.AWAITING_UPLOAD,
            created_at=now,
            expires_at=now + retention,
            updated_at=now,
            token_hash=self._hash_token(token),
            client_hash=client_hash,
            input_key=f"{prefix}/source{extension}",
            output_key=f"{prefix}/redacted.pdf",
            draft_manifest_key=f"{prefix}/draft-manifest.json",
            approved_manifest_key=f"{prefix}/approved-manifest.json",
            file_extension=extension,
            content_type=content_type,
            expected_bytes=request.size_bytes,
            auto_finalize=request.auto_finalize,
        )
        self.repository.create(job)
        try:
            upload = self.blobs.upload_plan(
                job,
                token,
                self.settings.upload_url_ttl_seconds,
            )
        except Exception as exc:
            self.repository.update(
                job_id,
                {JobStatus.AWAITING_UPLOAD},
                status=JobStatus.FAILED,
                error_code="upload_plan_failed",
            )
            raise ServiceError("upload_plan_failed", 503) from exc
        return CreateJobResponse(
            job_id=job_id,
            access_token=token,
            expires_at=job.expires_at,
            upload=upload,
        )

    def require_job(self, job_id: str, token: str) -> JobRecord:
        job = self.repository.get(job_id)
        if job is None or not token_matches(token, job.token_hash, self.settings.pepper_bytes):
            # Deliberately do not reveal whether a job ID exists.
            raise ServiceError("job_not_found", 404)
        if job.status in {JobStatus.DELETED, JobStatus.EXPIRED}:
            raise ServiceError("job_not_found", 404)
        if job.expires_at <= utc_now():
            self._expire(job)
            raise ServiceError("job_not_found", 404)
        return job

    def submit(self, job_id: str, token: str) -> JobResponse:
        job = self.require_job(job_id, token)
        if job.status != JobStatus.AWAITING_UPLOAD:
            if job.status in {
                JobStatus.QUEUED_DETECTION,
                JobStatus.DETECTING,
                JobStatus.REVIEW_REQUIRED,
                JobStatus.QUEUED_REDACTION,
                JobStatus.REDACTING,
                JobStatus.COMPLETE,
            }:
                return self.to_response(job)
            raise ServiceError("job_not_submittable", 409)

        blob = self.blobs.head(job.input_key)
        if blob is None:
            raise ServiceError("upload_missing", 409)
        if blob.size != job.expected_bytes or blob.size > self.settings.max_upload_bytes:
            self.blobs.delete_prefix(f"jobs/{job.job_id}")
            self.repository.update(
                job.job_id,
                {JobStatus.AWAITING_UPLOAD},
                status=JobStatus.FAILED,
                error_code="upload_size_mismatch",
            )
            raise ServiceError("upload_size_mismatch", 400)
        try:
            validate_magic(self.blobs.read_prefix(job.input_key, 16), job.file_extension)
        except ValueError as exc:
            self.blobs.delete_prefix(f"jobs/{job.job_id}")
            self.repository.update(
                job.job_id,
                {JobStatus.AWAITING_UPLOAD},
                status=JobStatus.FAILED,
                error_code=str(exc),
            )
            raise ServiceError(str(exc), 400) from exc

        queued = self.repository.update(
            job.job_id,
            {JobStatus.AWAITING_UPLOAD},
            status=JobStatus.QUEUED_DETECTION,
            actual_bytes=blob.size,
            error_code=None,
        )
        try:
            self.queue.enqueue(QueueMessage(job_id=job.job_id, task=TaskType.DETECT))
        except Exception as exc:
            self.repository.update(
                job.job_id,
                {JobStatus.QUEUED_DETECTION},
                status=JobStatus.AWAITING_UPLOAD,
                error_code="queue_unavailable",
            )
            raise ServiceError("queue_unavailable", 503) from exc
        return self.to_response(queued)

    def status(self, job_id: str, token: str) -> JobResponse:
        return self.to_response(self.require_job(job_id, token))

    def manifest(self, job_id: str, token: str) -> RedactionManifest:
        job = self.require_job(job_id, token)
        if job.status not in {
            JobStatus.REVIEW_REQUIRED,
            JobStatus.QUEUED_REDACTION,
            JobStatus.REDACTING,
            JobStatus.COMPLETE,
        }:
            raise ServiceError("manifest_not_ready", 409)
        key = (
            job.approved_manifest_key
            if self.blobs.head(job.approved_manifest_key)
            else job.draft_manifest_key
        )
        try:
            return RedactionManifest.model_validate_json(self.blobs.get_bytes(key))
        except (ValueError, json.JSONDecodeError) as exc:
            raise ServiceError("manifest_invalid", 500) from exc

    def finalize(self, job_id: str, token: str, update: ManifestUpdate) -> JobResponse:
        job = self.require_job(job_id, token)
        if job.status != JobStatus.REVIEW_REQUIRED:
            if job.status in {
                JobStatus.QUEUED_REDACTION,
                JobStatus.REDACTING,
                JobStatus.COMPLETE,
            }:
                return self.to_response(job)
            raise ServiceError("job_not_reviewable", 409)
        if job.page_count is None:
            raise ServiceError("manifest_invalid", 500)
        ids: set[str] = set()
        for detection in update.detections:
            if detection.id in ids or detection.page_index >= job.page_count:
                raise ServiceError("manifest_invalid", 400)
            ids.add(detection.id)
        draft = self.manifest(job_id, token)
        approved = draft.model_copy(
            update={"detections": update.detections, "created_at": utc_now()}
        )
        self.blobs.put_bytes(
            job.approved_manifest_key,
            approved.model_dump_json().encode("utf-8"),
            "application/json",
        )
        queued = self.repository.update(
            job.job_id,
            {JobStatus.REVIEW_REQUIRED},
            status=JobStatus.QUEUED_REDACTION,
            finding_count=len(update.detections),
        )
        try:
            self.queue.enqueue(QueueMessage(job_id=job.job_id, task=TaskType.REDACT))
        except Exception as exc:
            self.repository.update(
                job.job_id,
                {JobStatus.QUEUED_REDACTION},
                status=JobStatus.REVIEW_REQUIRED,
                error_code="queue_unavailable",
            )
            raise ServiceError("queue_unavailable", 503) from exc
        return self.to_response(queued)

    def delete(self, job_id: str, token: str) -> None:
        job = self.require_job(job_id, token)
        try:
            self.repository.update(
                job.job_id,
                None,
                status=JobStatus.DELETED,
                error_code=None,
                expires_at=utc_now(),
            )
        except (JobNotFound, StateConflict) as exc:
            raise ServiceError("job_not_found", 404) from exc
        self.blobs.delete_prefix(f"jobs/{job.job_id}")

    def cleanup_expired(self, limit: int = 100) -> int:
        expired = self.repository.list_expired(utc_now(), limit)
        for job in expired:
            self._expire(job)
        return len(expired)

    def _expire(self, job: JobRecord) -> None:
        try:
            self.repository.update(job.job_id, None, status=JobStatus.EXPIRED, error_code=None)
        except (JobNotFound, StateConflict):
            return
        self.blobs.delete_prefix(f"jobs/{job.job_id}")

    def _hash_token(self, token: str) -> str:
        from taxhance_pii.security import keyed_hash

        return keyed_hash(token, self.settings.pepper_bytes)

    @staticmethod
    def to_response(job: JobRecord) -> JobResponse:
        return JobResponse(
            job_id=job.job_id,
            status=job.status,
            expires_at=job.expires_at,
            page_count=job.page_count,
            pages_completed=job.pages_completed,
            finding_count=job.finding_count,
            error_code=job.error_code,
        )
