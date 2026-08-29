from datetime import UTC, datetime

import pytest

from taxhance_pii.evaluation.corpus import (
    CorpusError,
    SampleEntry,
    SampleManifest,
    shard_manifest,
)


def _entry(identifier: str, pages: int, split: str = "tuning") -> SampleEntry:
    return SampleEntry(
        evaluation_id=identifier,
        source_path=f"/{identifier}.pdf",
        content_sha256="a" * 64,
        size_bytes=100,
        page_count=pages,
        text_kind="digital",
        split=split,  # type: ignore[arg-type]
    )


def _manifest() -> SampleManifest:
    return SampleManifest(
        created_at=datetime.now(UTC),
        seed="test",
        corpus_file_count=5,
        entries=[
            _entry("a", 8),
            _entry("b", 5),
            _entry("c", 4),
            _entry("d", 3),
            _entry("holdout", 20, "holdout"),
        ],
    )


def test_shard_manifest_balances_pages_and_excludes_other_split() -> None:
    shards = shard_manifest(_manifest(), "tuning", 2)
    assert [[entry.evaluation_id for entry in shard.entries] for shard in shards] == [
        ["a", "d"],
        ["b", "c"],
    ]
    assert [sum(entry.page_count for entry in shard.entries) for shard in shards] == [11, 9]
    assert all(
        "holdout" not in {entry.evaluation_id for entry in shard.entries} for shard in shards
    )


def test_shard_manifest_rejects_too_many_shards() -> None:
    with pytest.raises(CorpusError, match="invalid_shard_count"):
        shard_manifest(_manifest(), "tuning", 5)
