from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from taxhance_pii.api import main as api_main
from taxhance_pii.config import Settings
from taxhance_pii.container import build_container
from taxhance_pii.domain import BoundingBox, Detection, JobStatus, PiiCategory, TaskType
from taxhance_pii.worker.pipeline import WorkerPipeline


class FixedDetector:
    def preflight(self) -> None:
        return None

    def detect_document(self, pages: object) -> list[Detection]:
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
