from __future__ import annotations

from typing import Any

import pytest

from taxhance_pii.storage import S3BlobStore


def test_s3_store_forces_signature_v4(monkeypatch: Any) -> None:
    captured: dict[str, object] = {}
    client = object()

    def fake_client(service: str, **options: object) -> object:
        captured.update(service=service, **options)
        return client

    monkeypatch.setattr("taxhance_pii.storage.boto3.client", fake_client)

    store = S3BlobStore("private-bucket", "us-east-1")

    assert store.client is client
    assert captured["service"] == "s3"
    assert captured["region_name"] == "us-east-1"
    assert captured["config"].signature_version == "s3v4"  # type: ignore[union-attr]


class _DeletePaginator:
    def paginate(self, **request: object) -> list[dict[str, object]]:
        del request
        return [{"Contents": [{"Key": "jobs/job-1/source.pdf"}]}]


class _DeleteClient:
    def get_paginator(self, operation: str) -> _DeletePaginator:
        assert operation == "list_objects_v2"
        return _DeletePaginator()

    def delete_objects(self, **request: object) -> dict[str, object]:
        del request
        return {"Errors": [{"Code": "InternalError"}]}


def test_s3_delete_prefix_surfaces_per_object_failures() -> None:
    store = object.__new__(S3BlobStore)
    store.bucket = "private-bucket"
    store.client = _DeleteClient()

    with pytest.raises(RuntimeError, match="s3_delete_failed"):
        store.delete_prefix("jobs/job-1")
