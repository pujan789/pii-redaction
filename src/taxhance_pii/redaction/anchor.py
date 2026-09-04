"""Anchor model-returned strings to word geometry; deterministic SSN net.

The model never localizes. Boxes come from the same word stream the grid was
built from, so a matched value is pixel-accurate by construction. Failure
direction is closed: a word that merely STARTS with the target is redacted
whole, and every occurrence on the page is boxed.
"""

from __future__ import annotations

import re
import uuid

from rapidfuzz.fuzz import ratio

from taxhance_pii.domain import BoundingBox, Detection, PiiCategory
from taxhance_pii.redaction.document import WordBox

_STRIP = re.compile(r"[^a-z0-9*x•]")
_MAX_WINDOW = 12
_FUZZY_THRESHOLD = 90.0


def _norm(token: str) -> str:
    return _STRIP.sub("", token.lower())


def _boxes(words: list[WordBox]) -> list[BoundingBox]:
    return [w.box for w in words]


def _exact(target: str, words: list[WordBox], norms: list[str]) -> list[BoundingBox]:
    found: list[BoundingBox] = []
    for start, start_norm in enumerate(norms):
        if not start_norm:
            continue
        if start_norm == target or (len(target) >= 4 and start_norm.startswith(target)):
            found.append(words[start].box)
            continue
        if not target.startswith(start_norm):
            continue
        accumulated = start_norm
        for end in range(start + 1, min(len(words), start + _MAX_WINDOW)):
            next_norm = norms[end]
            if not next_norm:
                break
            accumulated += next_norm
            if accumulated == target or (len(target) >= 8 and accumulated.startswith(target)):
                found.extend(_boxes(words[start : end + 1]))
                break
            if not target.startswith(accumulated):
                break
    return found


def _fuzzy(target: str, words: list[WordBox], norms: list[str]) -> list[BoundingBox]:
    approx_window = max(1, min(_MAX_WINDOW, round(len(target) / 5)))
    best: tuple[float, int, int] | None = None
    for start in range(len(words)):
        accumulated = ""
        for end in range(start, min(len(words), start + approx_window + 2)):
            if not norms[end]:
                break
            accumulated += norms[end]
            score = ratio(accumulated, target)
            if score >= _FUZZY_THRESHOLD and (best is None or score > best[0]):
                best = (score, start, end)
    if best is None:
        return []
    return _boxes(words[best[1] : best[2] + 1])


def anchor_value(value: str, words: list[WordBox]) -> list[BoundingBox]:
    target = _norm("".join(value.split()))
    if len(target) < 2:
        return []
    norms = [_norm(w.text) for w in words]
    found = _exact(target, words, norms)
    if found:
        return found
    fragments = [f.strip() for f in re.split(r"\s{2,}|,|\n", value) if len(f.strip()) >= 5]
    if len(fragments) > 1:
        for fragment in fragments:
            found.extend(anchor_value(fragment, words))
        if found:
            return found
    return _fuzzy(target, words, norms)


_SSN_SHAPE = re.compile(r"^[\dxX*•]{3}[-–][\dxX*•]{2}[-–][\dxX*•]{4}$")


def _detection(page_index: int, box: BoundingBox) -> Detection:
    return Detection(
        id=str(uuid.uuid4()),
        page_index=page_index,
        category=PiiCategory.SSN,
        box=box,
        confidence=0.95,
        source="regex",
    )


def ssn_safety_net(page_index: int, words: list[WordBox]) -> list[Detection]:
    detections: list[Detection] = []
    used: set[int] = set()
    for index, current in enumerate(words):
        if index in used:
            continue
        token = current.text.strip(".,;:()")
        if _SSN_SHAPE.match(token) and any(c.isdigit() for c in token):
            detections.append(_detection(page_index, current.box))
            continue
        if index + 1 < len(words):
            neighbour = words[index + 1].text.strip(".,;:()")
            if token.endswith("-") or neighbour.startswith("-"):
                joined = token + neighbour
                if _SSN_SHAPE.match(joined) and any(c.isdigit() for c in joined):
                    detections.append(_detection(page_index, current.box))
                    detections.append(_detection(page_index, words[index + 1].box))
                    used.add(index + 1)
    return detections


def _overlaps(a: BoundingBox, b: BoundingBox) -> bool:
    return not (a.x2 < b.x1 or b.x2 < a.x1 or a.y2 < b.y1 or b.y2 < a.y1)


def merge_page_detections(detections: list[Detection]) -> list[Detection]:
    merged: list[Detection] = []
    ordered = sorted(detections, key=lambda d: (d.page_index, d.category, d.box.y1, d.box.x1))
    for detection in ordered:
        target = next(
            (
                candidate
                for candidate in merged
                if candidate.page_index == detection.page_index
                and candidate.category == detection.category
                and _overlaps(candidate.box, detection.box)
            ),
            None,
        )
        if target is None:
            merged.append(detection)
            continue
        merged[merged.index(target)] = target.model_copy(
            update={
                "box": BoundingBox(
                    x1=min(target.box.x1, detection.box.x1),
                    y1=min(target.box.y1, detection.box.y1),
                    x2=max(target.box.x2, detection.box.x2),
                    y2=max(target.box.y2, detection.box.y2),
                ),
                "confidence": max(target.confidence, detection.confidence),
            }
        )
    return merged
