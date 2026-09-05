from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from taxhance_pii.config import Settings
from taxhance_pii.container import build_container
from taxhance_pii.domain import CreateJobRequest, JobStatus, utc_now
from taxhance_pii.service import JobService, ServiceError


def _service(tmp_path: Path, **overrides: object) -> JobService:
    settings = Settings(
        runtime="local",
        data_dir=tmp_path,
        public_base_url="http://localhost",
        token_pepper="test-pepper-that-is-at-least-thirty-two-characters",  # noqa: S106
        **overrides,
    )
    container = build_container(settings)
    return JobService(settings, container.repository, container.blobs, container.queue)


def _request(size: int = 1_024) -> CreateJobRequest:
    return CreateJobRequest(
        filename="synthetic.pdf",
        size_bytes=size,
        content_type="application/pdf",
    )


def test_active_job_limit_is_per_client(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        max_active_jobs_per_ip=1,
        max_jobs_per_ip_per_hour=10,
    )
    service.create_job(_request(), "192.0.2.1")

    with pytest.raises(ServiceError, match="active_job_abuse_limit") as error:
        service.create_job(_request(), "192.0.2.1")
    assert error.value.status_code == 429

    # The cap is not a global product limit; a different client may still create a job.
    service.create_job(_request(), "192.0.2.2")


def test_default_allowance_supports_fifty_document_batches(tmp_path: Path) -> None:
    service = _service(tmp_path)
    for _ in range(50):
        created = service.create_job(_request(), "192.0.2.50")
        service.delete(created.job_id, created.access_token)
    assert service.settings.max_jobs_per_ip_per_hour >= 50
    assert service.settings.max_active_jobs_per_ip == 5


def test_hourly_and_queue_limits_fail_closed(tmp_path: Path) -> None:
    hourly = _service(
        tmp_path / "hourly",
        max_active_jobs_per_ip=10,
        max_jobs_per_ip_per_hour=1,
    )
    hourly.create_job(_request(), "192.0.2.3")
    with pytest.raises(ServiceError, match="hourly_abuse_limit") as hourly_error:
        hourly.create_job(_request(), "192.0.2.3")
    assert hourly_error.value.status_code == 429

    queued = _service(tmp_path / "queued", max_queue_depth=1)
    first = queued.create_job(_request(), "192.0.2.4")
    queued.repository.update(
        first.job_id,
        {JobStatus.AWAITING_UPLOAD},
        status=JobStatus.QUEUED_DETECTION,
    )
    with pytest.raises(ServiceError, match="service_busy") as queue_error:
        queued.create_job(_request(), "192.0.2.5")
    assert queue_error.value.status_code == 503


def test_upload_size_limit_rejects_before_job_creation(tmp_path: Path) -> None:
    service = _service(tmp_path, max_upload_bytes=1_024)

    with pytest.raises(ServiceError, match="upload_too_large") as error:
        service.create_job(_request(1_025), "192.0.2.6")
    assert error.value.status_code == 413
    assert service.repository.count_queued() == 0


def test_default_job_deadline_leaves_cleanup_margin_inside_one_hour(tmp_path: Path) -> None:
    service = _service(tmp_path)
    before = utc_now()
    created = service.create_job(_request(), "192.0.2.7")
    after = utc_now()

    assert before + timedelta(minutes=55) <= created.expires_at
    assert created.expires_at <= after + timedelta(minutes=55)


def test_retention_margin_must_be_shorter_than_retention() -> None:
    with pytest.raises(ValueError, match="cleanup margin must be shorter"):
        Settings(retention_hours=1, retention_cleanup_margin_minutes=60)
