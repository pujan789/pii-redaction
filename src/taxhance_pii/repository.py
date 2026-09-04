from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Protocol

import boto3
from boto3.dynamodb.conditions import Attr, Key

from taxhance_pii.domain import ACTIVE_STATUSES, JobRecord, JobStatus, utc_now


class JobNotFound(LookupError):
    pass


class StateConflict(RuntimeError):
    pass


class JobRepository(Protocol):
    def create(self, job: JobRecord) -> None: ...

    def get(self, job_id: str) -> JobRecord | None: ...

    def update(
        self,
        job_id: str,
        allowed_statuses: set[JobStatus] | None,
        **changes: object,
    ) -> JobRecord: ...

    def count_recent(self, client_hash: str, since: datetime) -> int: ...

    def count_active(self, client_hash: str) -> int: ...

    def count_queued(self) -> int: ...

    def list_expired(self, before: datetime, limit: int = 100) -> list[JobRecord]: ...

    def claim_local_task(self) -> JobRecord | None: ...


def _epoch(value: datetime) -> int:
    return int(value.timestamp())


def _serialize(job: JobRecord) -> str:
    return job.model_dump_json()


def _deserialize(payload: str) -> JobRecord:
    return JobRecord.model_validate_json(payload)


class SQLiteJobRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at_epoch INTEGER NOT NULL,
                    expires_at_epoch INTEGER NOT NULL,
                    client_hash TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_client_created
                    ON jobs(client_hash, created_at_epoch);
                CREATE INDEX IF NOT EXISTS idx_jobs_status_created
                    ON jobs(status, created_at_epoch);
                CREATE INDEX IF NOT EXISTS idx_jobs_expiry
                    ON jobs(expires_at_epoch);
                """
            )

    def create(self, job: JobRecord) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO jobs (
                        job_id, status, created_at_epoch, expires_at_epoch,
                        client_hash, version, payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job.job_id,
                        job.status.value,
                        _epoch(job.created_at),
                        _epoch(job.expires_at),
                        job.client_hash,
                        job.version,
                        _serialize(job),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise StateConflict("job_already_exists") from exc

    def get(self, job_id: str) -> JobRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return _deserialize(row["payload"]) if row else None

    def update(
        self,
        job_id: str,
        allowed_statuses: set[JobStatus] | None,
        **changes: object,
    ) -> JobRecord:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload, version FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise JobNotFound(job_id)
            current = _deserialize(row["payload"])
            if allowed_statuses is not None and current.status not in allowed_statuses:
                raise StateConflict(f"invalid_state:{current.status.value}")
            data = current.model_dump()
            data.update(changes)
            data["updated_at"] = utc_now()
            data["version"] = current.version + 1
            updated = JobRecord.model_validate(data)
            result = connection.execute(
                """
                UPDATE jobs SET status = ?, expires_at_epoch = ?, client_hash = ?,
                    version = ?, payload = ?
                WHERE job_id = ? AND version = ?
                """,
                (
                    updated.status.value,
                    _epoch(updated.expires_at),
                    updated.client_hash,
                    updated.version,
                    _serialize(updated),
                    job_id,
                    current.version,
                ),
            )
            if result.rowcount != 1:
                raise StateConflict("concurrent_update")
        return updated

    def count_recent(self, client_hash: str, since: datetime) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count FROM jobs
                WHERE client_hash = ? AND created_at_epoch >= ?
                """,
                (client_hash, _epoch(since)),
            ).fetchone()
        return int(row["count"])

    def count_active(self, client_hash: str) -> int:
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        parameters: list[object] = [client_hash, *(status.value for status in ACTIVE_STATUSES)]
        with self._connect() as connection:
            row = connection.execute(
                f"""SELECT COUNT(*) AS count FROM jobs
                WHERE client_hash = ? AND status IN ({placeholders})""",  # noqa: S608
                parameters,
            ).fetchone()
        return int(row["count"])

    def count_queued(self) -> int:
        queued = (JobStatus.QUEUED_DETECTION.value, JobStatus.QUEUED_REDACTION.value)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE status IN (?, ?)",
                queued,
            ).fetchone()
        return int(row["count"])

    def list_expired(self, before: datetime, limit: int = 100) -> list[JobRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload FROM jobs
                WHERE expires_at_epoch <= ? AND status != ?
                ORDER BY expires_at_epoch LIMIT ?
                """,
                (_epoch(before), JobStatus.EXPIRED.value, limit),
            ).fetchall()
        return [_deserialize(row["payload"]) for row in rows]

    def claim_local_task(self) -> JobRecord | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT payload FROM jobs
                WHERE status IN (?, ?)
                ORDER BY created_at_epoch LIMIT 1
                """,
                (JobStatus.QUEUED_DETECTION.value, JobStatus.QUEUED_REDACTION.value),
            ).fetchone()
            if row is None:
                return None
            current = _deserialize(row["payload"])
            next_status = (
                JobStatus.DETECTING
                if current.status == JobStatus.QUEUED_DETECTION
                else JobStatus.REDACTING
            )
            data = current.model_dump()
            data.update(status=next_status, updated_at=utc_now(), version=current.version + 1)
            updated = JobRecord.model_validate(data)
            result = connection.execute(
                """UPDATE jobs SET status = ?, version = ?, payload = ?
                WHERE job_id = ? AND version = ?""",
                (
                    updated.status.value,
                    updated.version,
                    _serialize(updated),
                    updated.job_id,
                    current.version,
                ),
            )
            if result.rowcount != 1:
                raise StateConflict("concurrent_claim")
        return updated


class DynamoJobRepository:
    CLIENT_INDEX = "client-created-index"

    def __init__(self, table_name: str, region: str) -> None:
        self.table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    @staticmethod
    def _item(job: JobRecord) -> dict[str, object]:
        return {
            "job_id": job.job_id,
            "status": job.status.value,
            "created_at_epoch": _epoch(job.created_at),
            "expires_at_epoch": _epoch(job.expires_at),
            "client_hash": job.client_hash,
            "version": job.version,
            "payload": _serialize(job),
        }

    def create(self, job: JobRecord) -> None:
        try:
            self.table.put_item(
                Item=self._item(job),
                ConditionExpression="attribute_not_exists(job_id)",
            )
        except self.table.meta.client.exceptions.ConditionalCheckFailedException as exc:
            raise StateConflict("job_already_exists") from exc

    def get(self, job_id: str) -> JobRecord | None:
        item = self.table.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item")
        return _deserialize(str(item["payload"])) if item else None

    def update(
        self,
        job_id: str,
        allowed_statuses: set[JobStatus] | None,
        **changes: object,
    ) -> JobRecord:
        current = self.get(job_id)
        if current is None:
            raise JobNotFound(job_id)
        if allowed_statuses is not None and current.status not in allowed_statuses:
            raise StateConflict(f"invalid_state:{current.status.value}")
        data = current.model_dump()
        data.update(changes)
        data["updated_at"] = utc_now()
        data["version"] = current.version + 1
        updated = JobRecord.model_validate(data)
        try:
            self.table.put_item(
                Item=self._item(updated),
                ConditionExpression="#version = :version",
                ExpressionAttributeNames={"#version": "version"},
                ExpressionAttributeValues={":version": current.version},
            )
        except self.table.meta.client.exceptions.ConditionalCheckFailedException as exc:
            raise StateConflict("concurrent_update") from exc
        return updated

    def _client_jobs(self, client_hash: str, since: datetime | None = None) -> list[JobRecord]:
        condition = Key("client_hash").eq(client_hash)
        if since is not None:
            condition &= Key("created_at_epoch").gte(_epoch(since))
        request: dict[str, object] = {
            "IndexName": self.CLIENT_INDEX,
            "KeyConditionExpression": condition,
            "ProjectionExpression": "payload",
        }
        items: list[dict[str, object]] = []
        while True:
            response = self.table.query(**request)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            request["ExclusiveStartKey"] = last_key
        return [_deserialize(str(item["payload"])) for item in items]

    def count_recent(self, client_hash: str, since: datetime) -> int:
        return len(self._client_jobs(client_hash, since))

    def count_active(self, client_hash: str) -> int:
        return sum(job.status in ACTIVE_STATUSES for job in self._client_jobs(client_hash))

    def count_queued(self) -> int:
        request: dict[str, object] = {
            "Select": "COUNT",
            "FilterExpression": Attr("status").is_in(
                [JobStatus.QUEUED_DETECTION.value, JobStatus.QUEUED_REDACTION.value]
            ),
        }
        total = 0
        while True:
            response = self.table.scan(**request)
            total += int(response.get("Count", 0))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return total
            request["ExclusiveStartKey"] = last_key

    def list_expired(self, before: datetime, limit: int = 100) -> list[JobRecord]:
        request: dict[str, object] = {
            "Limit": limit,
            "FilterExpression": Attr("expires_at_epoch").lte(_epoch(before))
            & Attr("status").ne(JobStatus.EXPIRED.value),
            "ProjectionExpression": "payload",
        }
        items: list[dict[str, object]] = []
        while len(items) < limit:
            response = self.table.scan(**request)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            request["ExclusiveStartKey"] = last_key
        return [_deserialize(str(item["payload"])) for item in items[:limit]]

    def claim_local_task(self) -> JobRecord | None:
        raise NotImplementedError("AWS workers claim tasks from SQS")


def statuses(values: Iterable[JobStatus]) -> set[JobStatus]:
    return set(values)
