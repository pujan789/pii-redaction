from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pdfplumber
import pypdfium2 as pdfium
from pydantic import BaseModel, ConfigDict

from taxhance_pii.redaction.document import PDFIUM_LOCK


class SampleEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_id: str
    source_path: str
    content_sha256: str
    size_bytes: int
    page_count: int
    text_kind: Literal["digital", "scanned", "mixed", "unknown"]
    split: Literal["tuning", "holdout"]


class SampleManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    created_at: datetime
    seed: str
    corpus_file_count: int
    entries: list[SampleEntry]


class CorpusError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _page_count(path: Path) -> int:
    try:
        with PDFIUM_LOCK:
            document = pdfium.PdfDocument(path)
            count = len(document)
            document.close()
        return count
    except Exception as exc:
        raise CorpusError("unreadable_pdf") from exc


def _text_kind(path: Path, page_count: int) -> Literal["digital", "scanned", "mixed", "unknown"]:
    try:
        with pdfplumber.open(path) as document:
            indices = sorted({0, min(1, page_count - 1), page_count - 1})
            presence = [
                bool((document.pages[index].extract_text() or "").strip()) for index in indices
            ]
    except Exception:
        return "unknown"
    if all(presence):
        return "digital"
    if not any(presence):
        return "scanned"
    return "mixed"


def _page_band(page_count: int) -> str:
    if page_count == 1:
        return "single"
    if page_count <= 5:
        return "short"
    if page_count <= 20:
        return "medium"
    return "long"


def _allocate(groups: dict[str, list[SampleEntry]], total: int) -> dict[str, int]:
    population = sum(len(entries) for entries in groups.values())
    if total > population:
        raise CorpusError("sample_larger_than_corpus")
    raw = {key: total * len(entries) / population for key, entries in groups.items()}
    allocated = {key: min(len(groups[key]), math.floor(value)) for key, value in raw.items()}
    remaining = total - sum(allocated.values())
    order = sorted(
        groups,
        key=lambda key: (raw[key] - math.floor(raw[key]), len(groups[key])),
        reverse=True,
    )
    while remaining:
        progressed = False
        for key in order:
            if allocated[key] < len(groups[key]):
                allocated[key] += 1
                remaining -= 1
                progressed = True
                if remaining == 0:
                    break
        if not progressed:
            raise CorpusError("sample_allocation_failed")
    return allocated


def build_sample(corpus: Path, count: int, tuning_count: int, seed: str) -> SampleManifest:
    if tuning_count < 1 or tuning_count >= count:
        raise CorpusError("invalid_split")
    candidates = sorted(
        (path for path in corpus.rglob("*") if path.is_file() and path.suffix.casefold() == ".pdf"),
        key=lambda path: str(path).casefold(),
    )
    groups: dict[str, list[SampleEntry]] = defaultdict(list)
    seen_content: set[str] = set()
    for path in candidates:
        try:
            page_count = _page_count(path)
            if page_count < 1:
                continue
            digest = _sha256(path)
            if digest in seen_content:
                continue
            seen_content.add(digest)
            kind = _text_kind(path, page_count)
        except CorpusError:
            continue
        rank = hashlib.sha256(f"{seed}:{digest}".encode()).hexdigest()
        entry = SampleEntry(
            evaluation_id=rank[:16],
            source_path=str(path.resolve()),
            content_sha256=digest,
            size_bytes=path.stat().st_size,
            page_count=page_count,
            text_kind=kind,
            split="holdout",
        )
        groups[f"{_page_band(page_count)}:{kind}"].append(entry)
    if len(candidates) < count or sum(len(group) for group in groups.values()) < count:
        raise CorpusError("not_enough_readable_pdfs")
    for entries in groups.values():
        entries.sort(key=lambda entry: entry.evaluation_id)

    allocation = _allocate(groups, count)
    selected = [entry for key, entries in groups.items() for entry in entries[: allocation[key]]]
    selected.sort(key=lambda entry: entry.evaluation_id)

    # Assign each stratum proportionally to both splits, then rebalance to the exact total.
    tuning_allocation = _allocate(
        {
            key: [
                entry
                for entry in selected
                if f"{_page_band(entry.page_count)}:{entry.text_kind}" == key
            ]
            for key in groups
            if any(f"{_page_band(entry.page_count)}:{entry.text_kind}" == key for entry in selected)
        },
        tuning_count,
    )
    tuning_ids: set[str] = set()
    for key, amount in tuning_allocation.items():
        stratum = [
            entry
            for entry in selected
            if f"{_page_band(entry.page_count)}:{entry.text_kind}" == key
        ]
        tuning_ids.update(entry.evaluation_id for entry in stratum[:amount])
    final_entries = [
        entry.model_copy(
            update={"split": "tuning" if entry.evaluation_id in tuning_ids else "holdout"}
        )
        for entry in selected
    ]
    return SampleManifest(
        created_at=datetime.now(UTC),
        seed=seed,
        corpus_file_count=len(candidates),
        entries=final_entries,
    )


def save_manifest(manifest: SampleManifest, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")


def load_manifest(path: Path) -> SampleManifest:
    return SampleManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))


def shard_manifest(
    manifest: SampleManifest,
    split: Literal["tuning", "holdout"],
    shard_count: int,
) -> list[SampleManifest]:
    """Balance one private split by page count without inspecting document content."""
    selected = [entry for entry in manifest.entries if entry.split == split]
    if shard_count < 1 or shard_count > len(selected):
        raise CorpusError("invalid_shard_count")
    shards: list[list[SampleEntry]] = [[] for _ in range(shard_count)]
    page_totals = [0] * shard_count
    for entry in sorted(selected, key=lambda item: (-item.page_count, item.evaluation_id)):
        target = min(range(shard_count), key=lambda index: (page_totals[index], index))
        shards[target].append(entry)
        page_totals[target] += entry.page_count
    return [
        manifest.model_copy(
            update={
                "seed": f"{manifest.seed}:{split}:shard-{index + 1}-of-{shard_count}",
                "entries": sorted(entries, key=lambda item: item.evaluation_id),
            }
        )
        for index, entries in enumerate(shards)
    ]
