from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from taxhance_pii.api import main as api_main
from taxhance_pii.config import Settings
from taxhance_pii.container import build_container
from taxhance_pii.domain import BoundingBox, Detection, JobStatus, PiiCategory, TaskType
from taxhance_pii.service import JobService
from taxhance_pii.worker.pipeline import WorkerPipeline


class FixedDetector:
    def preflight(self) -> None:
        return None

    def detect_document(self, pages: object, progress: object = None) -> list[Detection]:
        del progress
        return [
            Detection(
                id=f"visual-name-{page.page_index}",
                page_index=page.page_index,
                category=PiiCategory.PERSON_NAME,
                box=BoundingBox(x1=90, y1=80, x2=400, y2=145),
                confidence=0.95,
                source="model",
            )
            for page in pages  # type: ignore[attr-defined]
        ]


def _settings(path: Path) -> Settings:
    return Settings(
        runtime="local",
        data_dir=path,
        public_base_url="http://testserver",
        token_pepper="test-pepper-that-is-at-least-thirty-two-characters",  # noqa: S106
        ocr_enabled=False,
    )


def test_upload_process_download_delete(tmp_path: Path, sample_jpeg: bytes) -> None:
    container = build_container(_settings(tmp_path))
    previous = api_main.container
    api_main.container = container
    try:
        client = TestClient(api_main.app)
        created_response = client.post(
            "/v1/jobs",
            json={
                "filename": "synthetic.jpg",
                "size_bytes": len(sample_jpeg),
                "content_type": "image/jpeg",
                "auto_finalize": True,
            },
        )
        assert created_response.status_code == 201
        created = created_response.json()
        headers = {"X-Job-Token": created["access_token"], "Content-Type": "image/jpeg"}
        assert (
            client.put(created["upload"]["url"], headers=headers, content=sample_jpeg).status_code
            == 204
        )
        submitted = client.post(
            f"/v1/jobs/{created['job_id']}/submit",
            headers=headers,
        )
        assert submitted.json()["status"] == JobStatus.QUEUED_DETECTION

        claimed = container.repository.claim_local_task()
        assert claimed is not None
        pipeline = WorkerPipeline(
            container.settings,
            container.repository,
            container.blobs,
            container.queue,
            FixedDetector(),
        )
        pipeline.process(claimed, TaskType.DETECT)

        status_response = client.get(f"/v1/jobs/{created['job_id']}", headers=headers)
        assert status_response.json()["status"] == JobStatus.COMPLETE
        result = client.get(f"/v1/jobs/{created['job_id']}/result", headers=headers)
        assert result.status_code == 200
        assert result.content.startswith(b"%PDF-")

        deleted = client.delete(f"/v1/jobs/{created['job_id']}", headers=headers)
        assert deleted.status_code == 204
        assert client.get(f"/v1/jobs/{created['job_id']}", headers=headers).status_code == 404
        assert not (tmp_path / "blobs" / "jobs" / created["job_id"]).exists()
    finally:
        api_main.container = previous


def test_access_token_is_required(tmp_path: Path, sample_jpeg: bytes) -> None:
    container = build_container(_settings(tmp_path))
    previous = api_main.container
    api_main.container = container
    try:
        client = TestClient(api_main.app)
        created = client.post(
            "/v1/jobs",
            json={
                "filename": "synthetic.jpg",
                "size_bytes": len(sample_jpeg),
                "content_type": "image/jpeg",
            },
        ).json()
        assert client.get(f"/v1/jobs/{created['job_id']}").status_code == 404
        assert (
            client.get(
                f"/v1/jobs/{created['job_id']}",
                headers={"X-Job-Token": "incorrect"},
            ).status_code
            == 404
        )
    finally:
        api_main.container = previous


def test_failed_blob_deletion_can_be_retried_and_cleaned_up(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    container = build_container(_settings(tmp_path))
    previous = api_main.container
    api_main.container = container
    try:
        client = TestClient(api_main.app)
        created = client.post(
            "/v1/jobs",
            json={
                "filename": "synthetic.pdf",
                "size_bytes": 10,
                "content_type": "application/pdf",
            },
        ).json()
        headers = {"X-Job-Token": created["access_token"]}
        original_delete = container.blobs.delete_prefix
        attempts = 0

        def delete_prefix(prefix: str) -> None:
            nonlocal attempts
            attempts += 1
            if attempts in {1, 3}:
                raise RuntimeError("temporary storage failure")
            original_delete(prefix)

        monkeypatch.setattr(container.blobs, "delete_prefix", delete_prefix)

        first = client.delete(f"/v1/jobs/{created['job_id']}", headers=headers)
        assert first.status_code == 503
        assert first.json() == {"error": "delete_failed"}
        tombstone = container.repository.get(created["job_id"])
        assert tombstone is not None
        assert tombstone.status == JobStatus.DELETED

        repeated = client.delete(f"/v1/jobs/{created['job_id']}", headers=headers)
        assert repeated.status_code == 204

        service = JobService(
            container.settings,
            container.repository,
            container.blobs,
            container.queue,
        )
        assert service.cleanup_expired() == 0
        tombstone = container.repository.get(created["job_id"])
        assert tombstone is not None
        assert tombstone.status == JobStatus.DELETED

        assert service.cleanup_expired() == 1
        expired = container.repository.get(created["job_id"])
        assert expired is not None
        assert expired.status == JobStatus.EXPIRED
    finally:
        api_main.container = previous


def _request(headers: dict[str, str], client: str, event: dict | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/jobs",
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        "client": (client, 12345),
    }
    if event is not None:
        scope["aws.event"] = event
    return Request(scope)


def test_source_ip_uses_the_reverse_proxy_header_in_local_runtime() -> None:
    request = _request({"X-Real-IP": "203.0.113.9"}, client="172.18.0.3")
    assert api_main._source_ip(request) == "203.0.113.9"


def test_source_ip_falls_back_to_the_socket_peer_without_a_proxy() -> None:
    assert api_main._source_ip(_request({}, client="127.0.0.1")) == "127.0.0.1"


def _aws_event(source_ip: str) -> dict:
    return {"requestContext": {"http": {"sourceIp": source_ip}}}


def test_source_ip_behind_cloudfront_uses_the_viewer_address() -> None:
    request = _request(
        {"X-Forwarded-For": "198.51.100.7, 203.0.113.9, 70.132.1.1"},
        client="10.0.0.1",
        event=_aws_event("70.132.1.1"),
    )
    assert api_main._source_ip(request) == "203.0.113.9"


def test_source_ip_behind_cloudfront_prefers_the_cloudfront_viewer_header() -> None:
    request = _request(
        {
            "CloudFront-Viewer-Address": "203.0.113.9:51234",
            "X-Forwarded-For": "1.1.1.1, 70.132.1.1",
        },
        client="10.0.0.1",
        event=_aws_event("70.132.1.1"),
    )
    assert api_main._source_ip(request) == "203.0.113.9"


def test_source_ip_on_aws_without_a_proxy_chain_uses_the_gateway_source() -> None:
    request = _request(
        {"X-Forwarded-For": "203.0.113.9"},
        client="10.0.0.1",
        event=_aws_event("203.0.113.9"),
    )
    assert api_main._source_ip(request) == "203.0.113.9"
