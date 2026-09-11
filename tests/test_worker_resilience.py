from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pytest import MonkeyPatch

from taxhance_pii.config import Settings
from taxhance_pii.domain import JobRecord, JobStatus, QueueMessage, TaskType, utc_now
from taxhance_pii.redaction.renderer import RedactionValidationError
from taxhance_pii.repository import SQLiteJobRepository
from taxhance_pii.storage import LocalBlobStore
from taxhance_pii.task_queue import LocalTaskQueue, ReceivedMessage, SqsTaskQueue
from taxhance_pii.worker import main as worker_main
from taxhance_pii.worker.detector import DetectorUnavailable
from taxhance_pii.worker.pipeline import JobCancelled, WorkerPipeline


class _NoopDetector:
    def preflight(self) -> None:
        return None

    def detect_document(self, pages: object, progress: object = None) -> list[object]:
        del pages, progress
        return []


class _RecordingSqsClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def change_message_visibility(self, **request: object) -> None:
        self.calls.append(request)


class _RecordingQueue:
    def __init__(self, message: ReceivedMessage | None) -> None:
        self.message = message
        self.acknowledged: list[ReceivedMessage] = []
        self.released: list[ReceivedMessage] = []
        self.renewed: list[ReceivedMessage] = []

    def enqueue(self, message: QueueMessage) -> None:
        del message

    def receive(self, wait_seconds: int = 20) -> ReceivedMessage | None:
        del wait_seconds
        message, self.message = self.message, None
        return message

    def acknowledge(self, message: ReceivedMessage) -> None:
        self.acknowledged.append(message)

    def renew(self, message: ReceivedMessage) -> None:
        self.renewed.append(message)

    def release(self, message: ReceivedMessage, delay_seconds: int = 30) -> None:
        del delay_seconds
        self.released.append(message)


def _job(job_id: str, status: JobStatus = JobStatus.DELETED, **extra: object) -> JobRecord:
    now = utc_now()
    fields: dict[str, object] = {
        "job_id": job_id,
        "status": status,
        "created_at": now,
        "expires_at": now + timedelta(hours=1),
        "updated_at": now,
        "token_hash": "a" * 64,
        "client_hash": "b" * 32,
        "input_key": f"jobs/{job_id}/source.pdf",
        "output_key": f"jobs/{job_id}/redacted.pdf",
        "draft_manifest_key": f"jobs/{job_id}/draft.json",
        "approved_manifest_key": f"jobs/{job_id}/approved.json",
        "file_extension": ".pdf",
        "content_type": "application/pdf",
        "expected_bytes": 10,
    }
    fields.update(extra)
    return JobRecord.model_validate(fields)


def _deleted_job(job_id: str) -> JobRecord:
    return _job(job_id)


def _pipeline(tmp_path: Path, repository: SQLiteJobRepository) -> WorkerPipeline:
    return WorkerPipeline(
        Settings(data_dir=tmp_path),
        repository,
        LocalBlobStore(tmp_path / "blobs", "http://localhost"),
        LocalTaskQueue(),
        _NoopDetector(),  # type: ignore[arg-type]
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


def test_worker_runs_document_loops_concurrently(monkeypatch: MonkeyPatch) -> None:
    import threading

    barrier = threading.Barrier(2)
    met = threading.Event()

    def fake_run_once(pipeline: object) -> bool:
        del pipeline
        barrier.wait(timeout=10)
        met.set()
        worker_main.stopping = True
        return True

    monkeypatch.setattr(worker_main, "run_once", fake_run_once)
    monkeypatch.setattr(
        worker_main,
        "get_container",
        lambda: SimpleNamespace(settings=SimpleNamespace(runtime="aws", worker_poll_seconds=0.1)),
    )
    monkeypatch.setattr(worker_main, "stopping", False)
    monkeypatch.setattr(worker_main, "vllm_server", None)

    worker_main._run_loops(pipeline=None, concurrency=2)  # type: ignore[arg-type]

    assert met.is_set()


def _aws_container(repository: SQLiteJobRepository, queue: object = None) -> SimpleNamespace:
    return SimpleNamespace(
        repository=repository,
        queue=queue,
        settings=SimpleNamespace(runtime="aws", worker_visibility_timeout_seconds=1_800),
    )


def test_redelivered_message_reclaims_job_whose_lease_went_stale(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    repository = SQLiteJobRepository(tmp_path / "jobs.sqlite3")
    stale = _job(
        "interrupted",
        JobStatus.DETECTING,
        updated_at=utc_now() - timedelta(minutes=25),
    )
    repository.create(stale)
    monkeypatch.setattr(worker_main, "get_container", lambda: _aws_container(repository))
    message = ReceivedMessage(
        task=QueueMessage(job_id=stale.job_id, task=TaskType.DETECT),
        receipt_handle="redelivered",
    )

    reclaimed = worker_main._claim_aws(message)

    assert reclaimed is not None
    assert reclaimed.status == JobStatus.DETECTING
    assert reclaimed.version == stale.version + 1


def test_duplicate_delivery_does_not_steal_a_live_job(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    repository = SQLiteJobRepository(tmp_path / "jobs.sqlite3")
    live = _job("live", JobStatus.DETECTING)
    repository.create(live)
    monkeypatch.setattr(worker_main, "get_container", lambda: _aws_container(repository))
    message = ReceivedMessage(
        task=QueueMessage(job_id=live.job_id, task=TaskType.DETECT),
        receipt_handle="duplicate",
    )

    with pytest.raises(worker_main.LeaseHeld):
        worker_main._claim_aws(message)

    unchanged = repository.get(live.job_id)
    assert unchanged is not None and unchanged.version == live.version


def test_run_once_releases_rather_than_acknowledges_a_held_lease(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    repository = SQLiteJobRepository(tmp_path / "jobs.sqlite3")
    repository.create(_job("live", JobStatus.DETECTING))
    message = ReceivedMessage(
        task=QueueMessage(job_id="live", task=TaskType.DETECT),
        receipt_handle="duplicate",
    )
    queue = _RecordingQueue(message)
    monkeypatch.setattr(worker_main, "get_container", lambda: _aws_container(repository, queue))

    assert worker_main.run_once(_pipeline(tmp_path, repository)) is True

    assert queue.released == [message]
    assert queue.acknowledged == []


def test_completion_conflict_deletes_output_only_when_the_job_is_gone(tmp_path: Path) -> None:
    repository = SQLiteJobRepository(tmp_path / "jobs.sqlite3")
    pipeline = _pipeline(tmp_path, repository)
    gone = _job("gone", JobStatus.DELETED)
    reowned = _job("reowned", JobStatus.REVIEW_REQUIRED)
    repository.create(gone)
    repository.create(reowned)
    for job in (gone, reowned):
        pipeline.blobs.put_bytes(job.output_key, b"pdf", "application/pdf")

    with pytest.raises(JobCancelled):
        pipeline._mark_complete(gone, pages=1, finding_count=0)
    with pytest.raises(JobCancelled):
        pipeline._mark_complete(reowned, pages=1, finding_count=0)

    assert pipeline.blobs.head(gone.output_key) is None
    assert pipeline.blobs.head(reowned.output_key) is not None


def test_mark_complete_transitions_a_redacting_job(tmp_path: Path) -> None:
    repository = SQLiteJobRepository(tmp_path / "jobs.sqlite3")
    pipeline = _pipeline(tmp_path, repository)
    repository.create(_job("active", JobStatus.REDACTING))

    pipeline._mark_complete(repository.get("active"), pages=3, finding_count=2)  # type: ignore[arg-type]

    done = repository.get("active")
    assert done is not None
    assert done.status == JobStatus.COMPLETE
    assert (done.pages_completed, done.finding_count) == (3, 2)


def test_validation_failure_after_manual_approval_returns_to_review(tmp_path: Path) -> None:
    repository = SQLiteJobRepository(tmp_path / "jobs.sqlite3")
    pipeline = _pipeline(tmp_path, repository)
    repository.create(_job("manual", JobStatus.REDACTING, auto_finalize=False))
    repository.create(_job("auto", JobStatus.REDACTING, auto_finalize=True))

    pipeline.fail("manual", RedactionValidationError("residual_identifier_detected"))
    pipeline.fail("auto", RedactionValidationError("residual_identifier_detected"))

    manual = repository.get("manual")
    auto = repository.get("auto")
    assert manual is not None and auto is not None
    assert manual.status == JobStatus.REVIEW_REQUIRED
    assert manual.error_code == "residual_identifier_detected"
    assert auto.status == JobStatus.FAILED


def test_detector_unavailable_requeues_local_job_and_stops_the_worker(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    repository = SQLiteJobRepository(tmp_path / "jobs.sqlite3")
    repository.create(_job("queued", JobStatus.QUEUED_DETECTION))
    monkeypatch.setattr(
        worker_main,
        "get_container",
        lambda: SimpleNamespace(
            repository=repository,
            queue=LocalTaskQueue(),
            settings=SimpleNamespace(runtime="local", worker_visibility_timeout_seconds=1_800),
        ),
    )
    monkeypatch.setattr(worker_main, "stopping", False)
    monkeypatch.setattr(worker_main, "fatal", False)

    class _DownPipeline:
        def process(self, job: JobRecord, task: TaskType) -> None:
            del job, task
            raise DetectorUnavailable("vllm_unreachable")

        def fail(self, job_id: str, error: Exception) -> None:
            raise AssertionError("an unreachable model must not fail the job")

    worker_main.run_once(_DownPipeline())  # type: ignore[arg-type]

    requeued = repository.get("queued")
    assert requeued is not None and requeued.status == JobStatus.QUEUED_DETECTION
    assert worker_main.stopping is True
    assert worker_main.fatal is True


def test_detector_unavailable_releases_the_sqs_message(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    repository = SQLiteJobRepository(tmp_path / "jobs.sqlite3")
    repository.create(_job("queued", JobStatus.QUEUED_REDACTION))
    message = ReceivedMessage(
        task=QueueMessage(job_id="queued", task=TaskType.REDACT),
        receipt_handle="r",
    )
    queue = _RecordingQueue(message)
    monkeypatch.setattr(worker_main, "get_container", lambda: _aws_container(repository, queue))
    monkeypatch.setattr(worker_main, "stopping", False)
    monkeypatch.setattr(worker_main, "fatal", False)

    class _DownPipeline:
        def process(self, job: JobRecord, task: TaskType) -> None:
            del job, task
            raise DetectorUnavailable("vllm_unreachable")

        def fail(self, job_id: str, error: Exception) -> None:
            raise AssertionError("an unreachable model must not fail the job")

    worker_main.run_once(_DownPipeline())  # type: ignore[arg-type]

    requeued = repository.get("queued")
    assert requeued is not None and requeued.status == JobStatus.QUEUED_REDACTION
    assert queue.released == [message]
    assert queue.acknowledged == []


def test_vllm_exit_stops_the_loop_fatally(monkeypatch: MonkeyPatch) -> None:
    class _DeadServer:
        def poll(self) -> int:
            return 137

    monkeypatch.setattr(worker_main, "vllm_server", _DeadServer())
    monkeypatch.setattr(worker_main, "stopping", False)
    monkeypatch.setattr(worker_main, "fatal", False)
    monkeypatch.setattr(
        worker_main,
        "run_once",
        lambda pipeline: pytest.fail("run_once must not run once vLLM has exited"),
    )
    monkeypatch.setattr(
        worker_main,
        "get_container",
        lambda: SimpleNamespace(settings=SimpleNamespace(runtime="aws", worker_poll_seconds=0.1)),
    )

    worker_main._run_loop(pipeline=None)  # type: ignore[arg-type]

    assert worker_main.stopping is True
    assert worker_main.fatal is True


def test_progress_reporter_updates_pages_completed(tmp_path: Path) -> None:
    repository = SQLiteJobRepository(tmp_path / "jobs.sqlite3")
    pipeline = _pipeline(tmp_path, repository)
    repository.create(_job("detecting", JobStatus.DETECTING))

    report = pipeline._progress_reporter("detecting")
    report()
    report()

    job = repository.get("detecting")
    assert job is not None and job.pages_completed == 2


def test_progress_reporter_ignores_a_job_that_disappeared(tmp_path: Path) -> None:
    repository = SQLiteJobRepository(tmp_path / "jobs.sqlite3")
    pipeline = _pipeline(tmp_path, repository)
    repository.create(_job("deleted", JobStatus.DELETED))

    pipeline._progress_reporter("deleted")()
    pipeline._progress_reporter("missing")()


def test_local_cleanup_expires_jobs_without_a_client_poll(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    settings = Settings(data_dir=tmp_path, token_pepper="x" * 40)
    repository = SQLiteJobRepository(tmp_path / "jobs.sqlite3")
    blobs = LocalBlobStore(tmp_path / "blobs", "http://localhost")
    abandoned = _job("abandoned", JobStatus.DETECTING, expires_at=utc_now() - timedelta(minutes=1))
    repository.create(abandoned)
    blobs.put_bytes(abandoned.input_key, b"pdf", "application/pdf")
    monkeypatch.setattr(
        worker_main,
        "get_container",
        lambda: SimpleNamespace(
            settings=settings, repository=repository, blobs=blobs, queue=LocalTaskQueue()
        ),
    )

    assert worker_main._cleanup_once() == 1

    expired = repository.get("abandoned")
    assert expired is not None and expired.status == JobStatus.EXPIRED
    assert blobs.head(abandoned.input_key) is None
