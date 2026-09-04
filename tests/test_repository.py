from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from taxhance_pii.domain import JobRecord, JobStatus, utc_now
from taxhance_pii.repository import DynamoJobRepository, SQLiteJobRepository, StateConflict


def _job(job_id: str = "job-1") -> JobRecord:
    now = utc_now()
    return JobRecord(
        job_id=job_id,
        status=JobStatus.AWAITING_UPLOAD,
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
        expected_bytes=100,
    )


def test_sqlite_state_transition_and_claim(tmp_path: Path) -> None:
    repository = SQLiteJobRepository(tmp_path / "jobs.sqlite3")
    repository.create(_job())
    queued = repository.update(
        "job-1",
        {JobStatus.AWAITING_UPLOAD},
        status=JobStatus.QUEUED_DETECTION,
    )
    assert queued.version == 2
    claimed = repository.claim_local_task()
    assert claimed is not None
    assert claimed.status == JobStatus.DETECTING
    assert repository.claim_local_task() is None


def test_sqlite_rejects_invalid_transition(tmp_path: Path) -> None:
    repository = SQLiteJobRepository(tmp_path / "jobs.sqlite3")
    repository.create(_job())
    with pytest.raises(StateConflict, match="invalid_state"):
        repository.update("job-1", {JobStatus.COMPLETE}, status=JobStatus.DELETED)


def test_sqlite_abuse_counts(tmp_path: Path) -> None:
    repository = SQLiteJobRepository(tmp_path / "jobs.sqlite3")
    repository.create(_job("one"))
    repository.create(_job("two"))
    assert repository.count_active("b" * 32) == 2
    assert repository.count_recent("b" * 32, utc_now() - timedelta(minutes=1)) == 2


def test_sqlite_deleted_jobs_remain_expiry_cleanup_candidates(tmp_path: Path) -> None:
    repository = SQLiteJobRepository(tmp_path / "jobs.sqlite3")
    deleted = _job("deleted").model_copy(
        update={
            "status": JobStatus.DELETED,
            "expires_at": utc_now() - timedelta(minutes=1),
        }
    )
    repository.create(deleted)

    assert [job.job_id for job in repository.list_expired(utc_now())] == ["deleted"]


class _PagedTable:
    def __init__(self, pages: list[dict[str, object]]) -> None:
        self.pages = iter(pages)
        self.requests: list[dict[str, object]] = []

    def query(self, **request: object) -> dict[str, object]:
        self.requests.append(request)
        return next(self.pages)

    def scan(self, **request: object) -> dict[str, object]:
        self.requests.append(request)
        return next(self.pages)


def _dynamo_with_table(table: _PagedTable) -> DynamoJobRepository:
    repository = object.__new__(DynamoJobRepository)
    repository.table = table
    return repository


def test_dynamo_client_query_paginates() -> None:
    first = _job("first")
    second = _job("second")
    table = _PagedTable(
        [
            {
                "Items": [{"payload": first.model_dump_json()}],
                "LastEvaluatedKey": {"job_id": "first"},
            },
            {"Items": [{"payload": second.model_dump_json()}]},
        ]
    )
    repository = _dynamo_with_table(table)

    assert [job.job_id for job in repository._client_jobs(first.client_hash)] == ["first", "second"]
    assert table.requests[1]["ExclusiveStartKey"] == {"job_id": "first"}


def test_dynamo_count_and_expiry_scans_paginate() -> None:
    count_table = _PagedTable(
        [
            {"Count": 2, "LastEvaluatedKey": {"job_id": "two"}},
            {"Count": 3},
        ]
    )
    assert _dynamo_with_table(count_table).count_queued() == 5
    assert count_table.requests[1]["ExclusiveStartKey"] == {"job_id": "two"}

    expired = _job("expired")
    expiry_table = _PagedTable(
        [
            {"Items": [], "LastEvaluatedKey": {"job_id": "filtered"}},
            {"Items": [{"payload": expired.model_dump_json()}]},
        ]
    )
    found = _dynamo_with_table(expiry_table).list_expired(utc_now(), limit=1)
    assert [job.job_id for job in found] == ["expired"]
    assert expiry_table.requests[1]["ExclusiveStartKey"] == {"job_id": "filtered"}
