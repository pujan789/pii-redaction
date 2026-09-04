from taxhance_pii.domain import BoundingBox, Detection, PiiCategory
from taxhance_pii.redaction.anchor import (
    anchor_value,
    merge_page_detections,
    ssn_safety_net,
)
from taxhance_pii.redaction.document import WordBox


def word(text: str, x1: int = 10, y1: int = 10, x2: int = 50, y2: int = 20) -> WordBox:
    return WordBox(text=text, box=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2), source="pdf")


def row(texts: list[str], y: int) -> list[WordBox]:
    return [word(t, x1=10 + i * 60, y1=y, x2=60 + i * 60, y2=y + 10) for i, t in enumerate(texts)]


def test_multiword_exact_match_returns_one_box_per_word() -> None:
    words = row(["JULIA", "ZHOU", "45", "LEWIS", "ST"], 100)
    assert len(anchor_value("Julia Zhou", words)) == 2


def test_all_occurrences_matched() -> None:
    words = row(["JULIA", "ZHOU"], 100) + row(["JULIA", "ZHOU"], 400)
    assert len(anchor_value("JULIA ZHOU", words)) == 4


def test_merged_word_prefix_match() -> None:
    words = row(["39AUBURNPATHDR", "THEWOODLANDSTX77382"], 100)
    assert len(anchor_value("39AUBURNPATHDR", words)) == 1


def test_fragmented_value_matches_via_normalization() -> None:
    # OCR split LEWIS as LEWI S — normalized join still matches exactly
    words = row(["45", "LEWI", "S", "STREET"], 100)
    assert len(anchor_value("45 LEWIS STREET", words)) == 4


def test_merged_lines_fall_back_to_fragments() -> None:
    words = row(["45", "LEWIS", "ST"], 100) + row(["EAST", "BOSTON,", "MA"], 120)
    boxes = anchor_value("45 LEWIS ST  EAST BOSTON, MA", words)
    assert len(boxes) >= 3  # at least the street fragment anchors


def test_no_match_returns_empty() -> None:
    assert anchor_value("NOT PRESENT", row(["JULIA", "ZHOU"], 100)) == []


def test_safety_net_catches_ssn_and_masked_not_ein() -> None:
    words = row(["123-45-6789", "12-3456789", "***-**-1442"], 100)
    detections = ssn_safety_net(0, words)
    assert len(detections) == 2
    assert all(d.category == PiiCategory.SSN and d.source == "regex" for d in detections)


def test_safety_net_joins_dash_split_pairs() -> None:
    words = row(["123-45-", "6789"], 100)
    assert len(ssn_safety_net(0, words)) == 2  # both fragments boxed


def test_merge_unions_overlapping_same_category() -> None:
    a = Detection(
        id="a",
        page_index=0,
        category=PiiCategory.SSN,
        box=BoundingBox(x1=10, y1=10, x2=50, y2=20),
        confidence=0.9,
        source="model",
    )
    b = Detection(
        id="b",
        page_index=0,
        category=PiiCategory.SSN,
        box=BoundingBox(x1=40, y1=10, x2=90, y2=20),
        confidence=0.95,
        source="regex",
    )
    merged = merge_page_detections([a, b])
    assert len(merged) == 1
    assert merged[0].box.x1 == 10 and merged[0].box.x2 == 90
