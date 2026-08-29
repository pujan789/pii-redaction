from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from taxhance_pii.config import Settings, get_settings
from taxhance_pii.repository import DynamoJobRepository, JobRepository, SQLiteJobRepository
from taxhance_pii.storage import BlobStore, LocalBlobStore, S3BlobStore
from taxhance_pii.task_queue import LocalTaskQueue, SqsTaskQueue, TaskQueue


@dataclass(frozen=True)
class Container:
    settings: Settings
    repository: JobRepository
    blobs: BlobStore
    queue: TaskQueue


def build_container(settings: Settings) -> Container:
    if settings.runtime == "aws":
        assert settings.s3_bucket is not None
        assert settings.dynamodb_table is not None
        assert settings.sqs_queue_url is not None
        repository: JobRepository = DynamoJobRepository(
            settings.dynamodb_table,
            settings.aws_region,
        )
        blobs: BlobStore = S3BlobStore(settings.s3_bucket, settings.aws_region)
        queue: TaskQueue = SqsTaskQueue(
            settings.sqs_queue_url,
            settings.aws_region,
            settings.worker_visibility_timeout_seconds,
        )
    else:
        repository = SQLiteJobRepository(settings.data_dir / "jobs.sqlite3")
        blobs = LocalBlobStore(settings.data_dir / "blobs", settings.public_base_url)
        queue = LocalTaskQueue()
    return Container(settings=settings, repository=repository, blobs=blobs, queue=queue)


@lru_cache
def get_container() -> Container:
    return build_container(get_settings())
