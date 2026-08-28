from __future__ import annotations

import logging
import signal
import threading
import time
from types import FrameType

from taxhance_pii.container import get_container
from taxhance_pii.domain import JobRecord, JobStatus, TaskType
from taxhance_pii.logging_config import configure_logging
from taxhance_pii.repository import StateConflict
from taxhance_pii.task_queue import ReceivedMessage
from taxhance_pii.worker.detector import TextAnchoredDetector
from taxhance_pii.worker.pipeline import WorkerPipeline
from taxhance_pii.worker.vllm_server import VllmServer

logger = logging.getLogger(__name__)
stopping = False


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


def _claim_aws(message: ReceivedMessage) -> JobRecord | None:
    container = get_container()
    job = container.repository.get(message.task.job_id)
    if job is None:
        return None
    target = JobStatus.DETECTING if message.task.task == TaskType.DETECT else JobStatus.REDACTING
    source = (
        {JobStatus.QUEUED_DETECTION, JobStatus.DETECTING}
        if message.task.task == TaskType.DETECT
        else {JobStatus.QUEUED_REDACTION, JobStatus.REDACTING}
    )
    try:
        return container.repository.update(job.job_id, source, status=target, error_code=None)
    except StateConflict:
        return None


def run_once(pipeline: WorkerPipeline) -> bool:
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
        claimed = _claim_aws(received)
        if claimed is None:
            container.queue.acknowledge(received)
            return True
        job = claimed
        task = received.task.task
    try:
        if received is None:
            pipeline.process(job, task)
        else:
            heartbeat_interval = max(
                10.0,
                container.settings.worker_visibility_timeout_seconds / 3,
            )
            with VisibilityHeartbeat(received, heartbeat_interval):
                pipeline.process(job, task)
    except Exception as exc:
        pipeline.fail(job.job_id, exc)
    finally:
        if received is not None:
            container.queue.acknowledge(received)
    return True


def _run_loop(pipeline: WorkerPipeline) -> None:
    global stopping
    container = get_container()
    try:
        while not stopping:
            worked = run_once(pipeline)
            if not worked and container.settings.runtime == "local":
                time.sleep(container.settings.worker_poll_seconds)
    except BaseException:
        # One broken loop stops the whole worker so the process restarts
        # cleanly instead of running half-crewed.
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


def run() -> None:
    container = get_container()
    configure_logging(container.settings.log_level)
    if container.settings.vllm_launch:
        VllmServer(container.settings).start()
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
    logger.info("worker_started", extra={"runtime": container.settings.runtime})
    _run_loops(pipeline, container.settings.document_concurrency)


if __name__ == "__main__":
    run()
