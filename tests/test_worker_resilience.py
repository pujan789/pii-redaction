from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from pytest import MonkeyPatch

from taxhance_pii.config import Settings
from taxhance_pii.domain import JobRecord, JobStatus, QueueMessage, TaskType, utc_now
from taxhance_pii.repository import SQLiteJobRepository
from taxhance_pii.storage import LocalBlobStore
from taxhance_pii.task_queue import LocalTaskQueue, ReceivedMessage, SqsTaskQueue
from taxhance_pii.worker import main as worker_main
from taxhance_pii.worker.pipeline import WorkerPipeline


class _NoopDetector:
    def preflight(self) -> None:
        return None

    def detect_document(self, pages: object) -> list[object]:
        del pages
        return []


class _RecordingSqsClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def change_message_visibility(self, **request: object) -> None:
        self.calls.append(request)


def _deleted_job(job_id: str) -> JobRecord:
    now = utc_now()
    return JobRecord(
        job_id=job_id,
        status=JobStatus.DELETED,
        created_at=now,
        expires_at=now + timedelta(hours=1),
        updated_at=now,
        token_hash="a" * 64,
        client_hash="b" * 32,
        input_key=f"jobs/{job_id}/source.pdf",
        output_key=f"jobs/{job_id}/redacted.pdf",
        draft_manifest_key=f"jobs/{job_id}/draft.json",
        approved_manifest_key=f"jobs/{job_id}/approved.json",
        file_extension=".pdf",
        content_type="application/pdf",
        expected_bytes=10,
    )


def test_worker_failure_after_delete_removes_raced_blobs(tmp_path: Path) -> None:
    repository = SQLiteJobRepository(tmp_path / "jobs.sqlite3")
    job = _deleted_job("deleted-job")
    repository.create(job)
    blobs = LocalBlobStore(tmp_path / "blobs", "http://localhost")
    blobs.put_bytes(f"jobs/{job.job_id}/late-output.pdf", b"late", "application/pdf")
    pipeline = WorkerPipeline(
        Settings(data_dir=tmp_path),
        repository,
        blobs,
        LocalTaskQueue(),
        _NoopDetector(),  # type: ignore[arg-type]
    )

    pipeline.fail(job.job_id, RuntimeError("late failure"))

    assert not (tmp_path / "blobs" / "jobs" / job.job_id).exists()


def test_sqs_renew_uses_full_visibility_timeout() -> None:
    queue = object.__new__(SqsTaskQueue)
    queue.queue_url = "https://sqs.example.test/queue"
    queue.visibility_timeout = 1_800
    client = _RecordingSqsClient()
    queue.client = client
    message = ReceivedMessage(
        task=QueueMessage(job_id="job", task=TaskType.DETECT),
        receipt_handle="receipt",
    )

    queue.renew(message)

    assert client.calls == [
        {
            "QueueUrl": queue.queue_url,
            "ReceiptHandle": "receipt",
            "VisibilityTimeout": 1_800,
        }
    ]


def test_redelivered_message_reclaims_interrupted_job(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    repository = SQLiteJobRepository(tmp_path / "jobs.sqlite3")
    interrupted = _deleted_job("interrupted").model_copy(update={"status": JobStatus.DETECTING})
    repository.create(interrupted)
    monkeypatch.setattr(
        worker_main,
        "get_container",
        lambda: SimpleNamespace(repository=repository),
    )
    message = ReceivedMessage(
        task=QueueMessage(job_id=interrupted.job_id, task=TaskType.DETECT),
        receipt_handle="redelivered",
    )

    reclaimed = worker_main._claim_aws(message)

    assert reclaimed is not None
    assert reclaimed.status == JobStatus.DETECTING
    assert reclaimed.version == interrupted.version + 1
