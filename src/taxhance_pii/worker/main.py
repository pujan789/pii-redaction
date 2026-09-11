from __future__ import annotations

import contextlib
import logging
import signal
import threading
import time
from types import FrameType

from taxhance_pii.container import get_container
from taxhance_pii.domain import JobRecord, JobStatus, TaskType, utc_now
from taxhance_pii.logging_config import configure_logging
from taxhance_pii.repository import JobNotFound, StateConflict
from taxhance_pii.service import JobService
from taxhance_pii.task_queue import ReceivedMessage
from taxhance_pii.worker.detector import DetectorUnavailable, TextAnchoredDetector
from taxhance_pii.worker.pipeline import WorkerPipeline
from taxhance_pii.worker.vllm_server import VllmServer

logger = logging.getLogger(__name__)
stopping = False
# Set when the worker must exit non-zero so the supervisor (compose, ECS)
# restarts it: the model server died or became unreachable.
fatal = False
vllm_server: VllmServer | None = None
CLEANUP_INTERVAL_SECONDS = 60.0


class LeaseHeld(RuntimeError):
    """Another worker still owns this in-flight job."""


class VisibilityHeartbeat:
    def __init__(self, message: ReceivedMessage, interval_seconds: float) -> None:
        self.message = message
        self.interval_seconds = interval_seconds
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, name="sqs-visibility", daemon=True)

    def _run(self) -> None:
        container = get_container()
        while not self.stopped.wait(self.interval_seconds):
            try:
                container.queue.renew(self.message)
            except Exception:
                logger.exception("queue_visibility_renewal_failed")

    def __enter__(self) -> VisibilityHeartbeat:
        self.thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.stopped.set()
        self.thread.join(timeout=2)


def _stop(signum: int, frame: FrameType | None) -> None:
    del signum, frame
    global stopping
    stopping = True


def _heartbeat_interval(visibility_timeout_seconds: int) -> float:
    return max(10.0, visibility_timeout_seconds / 3)


def _statuses(task: TaskType) -> tuple[JobStatus, JobStatus]:
    """(queued, in_flight) statuses for a task type."""
    if task == TaskType.DETECT:
        return JobStatus.QUEUED_DETECTION, JobStatus.DETECTING
    return JobStatus.QUEUED_REDACTION, JobStatus.REDACTING


def _claim_aws(message: ReceivedMessage) -> JobRecord | None:
    container = get_container()
    job = container.repository.get(message.task.job_id)
    if job is None:
        return None
    queued, in_flight = _statuses(message.task.task)
    if job.status == in_flight:
        # An in-flight row is reclaimable only once its owner has been silent
        # longer than a heartbeat could be late by; otherwise this is a
        # duplicate delivery for a job another worker is still processing.
        timeout = container.settings.worker_visibility_timeout_seconds
        lease_seconds = timeout - _heartbeat_interval(timeout)
        if (utc_now() - job.updated_at).total_seconds() < lease_seconds:
            raise LeaseHeld(job.job_id)
    try:
        return container.repository.update(
            job.job_id, {queued, in_flight}, status=in_flight, error_code=None
        )
    except StateConflict:
        return None


def _release_job(job: JobRecord, task: TaskType, received: ReceivedMessage | None) -> None:
    """Hand a job back to the queue untouched; the model server, not the job, failed."""
    container = get_container()
    queued, in_flight = _statuses(task)
    with contextlib.suppress(StateConflict, JobNotFound):
        container.repository.update(job.job_id, {in_flight}, status=queued)
    if received is not None:
        container.queue.release(received)


def run_once(pipeline: WorkerPipeline) -> bool:
    global fatal, stopping
    container = get_container()
    received: ReceivedMessage | None = None
    if container.settings.runtime == "local":
        job = container.repository.claim_local_task()
        if job is None:
            return False
        task = TaskType.DETECT if job.status == JobStatus.DETECTING else TaskType.REDACT
    else:
        received = container.queue.receive(wait_seconds=20)
        if received is None:
            return False
        try:
            claimed = _claim_aws(received)
        except LeaseHeld:
            container.queue.release(received)
            return True
        if claimed is None:
            container.queue.acknowledge(received)
            return True
        job = claimed
        task = received.task.task
    acknowledge = received is not None
    try:
        if received is None:
            pipeline.process(job, task)
        else:
            interval = _heartbeat_interval(container.settings.worker_visibility_timeout_seconds)
            with VisibilityHeartbeat(received, interval):
                pipeline.process(job, task)
    except DetectorUnavailable:
        logger.error("detector_unavailable", extra={"job_id": job.job_id})
        _release_job(job, task, received)
        acknowledge = False
        fatal = True
        stopping = True
    except Exception as exc:
        pipeline.fail(job.job_id, exc)
    finally:
        if received is not None and acknowledge:
            container.queue.acknowledge(received)
    return True


def _model_server_alive() -> bool:
    return vllm_server is None or vllm_server.poll() is None


def _run_loop(pipeline: WorkerPipeline) -> None:
    global fatal, stopping
    container = get_container()
    try:
        while not stopping:
            if not _model_server_alive():
                logger.error("vllm_exited")
                fatal = True
                stopping = True
                break
            worked = run_once(pipeline)
            if not worked and container.settings.runtime == "local":
                time.sleep(container.settings.worker_poll_seconds)
    except BaseException:
        # One broken loop stops the whole worker so the process restarts
        # cleanly instead of running half-crewed.
        fatal = True
        stopping = True
        raise


def _run_loops(pipeline: WorkerPipeline, concurrency: int) -> None:
    # CPU stages (render, OCR, residual checks) dominate a document, so
    # several documents in flight keep the GPU fed. run_once stays serial per
    # loop; SQS claiming and the repository conditional update arbitrate jobs.
    threads = [
        threading.Thread(target=_run_loop, args=(pipeline,), name=f"doc-loop-{index}")
        for index in range(concurrency)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def _cleanup_once() -> int:
    container = get_container()
    service = JobService(container.settings, container.repository, container.blobs, container.queue)
    return service.cleanup_expired()


def _cleanup_loop(interval_seconds: float) -> None:
    # The local runtime has no scheduled Lambda; this thread is what keeps the
    # one-hour retention promise for documents nobody polls again.
    while not stopping:
        try:
            deleted = _cleanup_once()
            if deleted:
                logger.info("retention_cleanup", extra={"count": deleted})
        except Exception:
            logger.exception("retention_cleanup_failed")
        deadline = time.monotonic() + interval_seconds
        while not stopping and time.monotonic() < deadline:
            time.sleep(1)


def run() -> None:
    global vllm_server
    container = get_container()
    configure_logging(container.settings.log_level)
    if container.settings.vllm_launch:
        vllm_server = VllmServer(container.settings)
        vllm_server.start()
    try:
        detector = TextAnchoredDetector(container.settings)
        detector.preflight()
        logger.info("worker_model_ready", extra={"model_id": container.settings.model_id})
        pipeline = WorkerPipeline(
            container.settings,
            container.repository,
            container.blobs,
            container.queue,
            detector,
        )
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
        if container.settings.runtime == "local":
            # A single local worker process: anything still marked in-flight
            # belonged to a previous incarnation of this process.
            requeued = container.repository.requeue_in_flight()
            if requeued:
                logger.info("jobs_requeued", extra={"count": requeued})
            threading.Thread(
                target=_cleanup_loop,
                args=(CLEANUP_INTERVAL_SECONDS,),
                name="retention-cleanup",
                daemon=True,
            ).start()
        logger.info("worker_started", extra={"runtime": container.settings.runtime})
        _run_loops(pipeline, container.settings.document_concurrency)
    finally:
        if vllm_server is not None:
            vllm_server.stop()
    if fatal:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
