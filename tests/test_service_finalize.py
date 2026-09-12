from __future__ import annotations

from pathlib import Path

import pytest
from pytest import MonkeyPatch

from taxhance_pii.config import Settings
from taxhance_pii.container import build_container
from taxhance_pii.domain import (
    BoundingBox,
    CreateJobRequest,
    Detection,
    JobStatus,
    ManifestUpdate,
    PiiCategory,
    RedactionManifest,
    utc_now,
)
from taxhance_pii.service import JobService, ServiceError


def _service(tmp_path: Path) -> JobService:
    settings = Settings(
        runtime="local",
        data_dir=tmp_path,
        public_base_url="http://localhost",
        token_pepper="test-pepper-that-is-at-least-thirty-two-characters",  # noqa: S106
    )
    container = build_container(settings)
    return JobService(settings, container.repository, container.blobs, container.queue)


def _detection(identifier: str, source: str = "model") -> Detection:
    return Detection(
        id=identifier,
        page_index=0,
        category=PiiCategory.PERSON_NAME if source == "model" else PiiCategory.USER_ADDED,
        box=BoundingBox(x1=100, y1=100, x2=300, y2=140),
        confidence=1,
        source=source,  # type: ignore[arg-type]
    )


def _completed_batch_job(service: JobService) -> tuple[str, str]:
    created = service.create_job(
        CreateJobRequest(
            filename="synthetic.pdf",
            size_bytes=10,
            content_type="application/pdf",
            auto_finalize=True,
        ),
        "192.0.2.1",
    )
    job = service.repository.update(
        created.job_id, None, status=JobStatus.COMPLETE, page_count=1, finding_count=1
    )
    manifest = RedactionManifest(
        job_id=job.job_id,
        page_count=1,
        detections=[_detection("suggested")],
        detector_version="test",
        prompt_version="test",
        model_id="test",
        created_at=utc_now(),
    )
    encoded = manifest.model_dump_json().encode("utf-8")
    service.blobs.put_bytes(job.draft_manifest_key, encoded, "application/json")
    service.blobs.put_bytes(job.approved_manifest_key, encoded, "application/json")
    return created.job_id, created.access_token


def test_finalize_from_complete_requeues_redaction_as_a_reviewed_job(tmp_path: Path) -> None:
    service = _service(tmp_path)
    job_id, token = _completed_batch_job(service)
    update = ManifestUpdate(
        detections=[_detection("suggested"), _detection("manual", "user")], rotation=90
    )

    response = service.finalize(job_id, token, update)

    assert response.status == JobStatus.QUEUED_REDACTION
    assert response.finding_count == 2
    stored = service.repository.get(job_id)
    assert stored is not None
    assert stored.auto_finalize is False
    assert stored.error_code is None
    approved = RedactionManifest.model_validate_json(
        service.blobs.get_bytes(stored.approved_manifest_key)
    )
    assert [item.id for item in approved.detections] == ["suggested", "manual"]
    assert approved.rotation == 90
    claimed = service.repository.claim_local_task()
    assert claimed is not None and claimed.status == JobStatus.REDACTING


def test_finalize_while_redaction_is_queued_is_idempotent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    job_id, token = _completed_batch_job(service)
    service.repository.update(job_id, {JobStatus.COMPLETE}, status=JobStatus.QUEUED_REDACTION)

    response = service.finalize(job_id, token, ManifestUpdate(detections=[]))

    assert response.status == JobStatus.QUEUED_REDACTION
    stored = service.repository.get(job_id)
    assert stored is not None
    approved = RedactionManifest.model_validate_json(
        service.blobs.get_bytes(stored.approved_manifest_key)
    )
    assert len(approved.detections) == 1


def test_finalize_from_complete_rolls_back_to_complete_when_enqueue_fails(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    service = _service(tmp_path)
    job_id, token = _completed_batch_job(service)

    def broken(message: object) -> None:
        raise RuntimeError("queue down")

    monkeypatch.setattr(service.queue, "enqueue", broken)

    with pytest.raises(ServiceError, match="queue_unavailable") as error:
        service.finalize(job_id, token, ManifestUpdate(detections=[]))

    assert error.value.status_code == 503
    stored = service.repository.get(job_id)
    assert stored is not None and stored.status == JobStatus.COMPLETE


def test_finalize_rejects_a_failed_job(tmp_path: Path) -> None:
    service = _service(tmp_path)
    job_id, token = _completed_batch_job(service)
    service.repository.update(job_id, {JobStatus.COMPLETE}, status=JobStatus.FAILED)

    with pytest.raises(ServiceError, match="job_not_reviewable") as error:
        service.finalize(job_id, token, ManifestUpdate(detections=[]))

    assert error.value.status_code == 409
