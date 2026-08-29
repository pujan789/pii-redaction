from __future__ import annotations

from typing import Any

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
