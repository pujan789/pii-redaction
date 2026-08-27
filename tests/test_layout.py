from taxhance_pii.domain import BoundingBox
from taxhance_pii.redaction.document import WordBox
from taxhance_pii.redaction.layout import build_grid


def word(text: str, x1: int, y1: int, x2: int, y2: int) -> WordBox:
    return WordBox(text=text, box=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2), source="pdf")


def test_same_row_words_share_a_line_in_x_order() -> None:
    words = [word("Wages", 500, 100, 560, 112), word("SSN", 10, 101, 40, 113)]
    grid = build_grid(words)
    lines = grid.splitlines()
    assert len(lines) == 1
    assert lines[0].index("SSN") < lines[0].index("Wages")


def test_column_position_tracks_x_coordinate() -> None:
    grid = build_grid([word("RIGHT", 500, 100, 560, 112)])
    # x1=500/1000 * default width 120 → column 60
    assert grid.splitlines()[0].index("RIGHT") == 60


def test_label_and_value_stack_vertically() -> None:
    words = [
        word("Employee's", 10, 100, 90, 112),
        word("SSN", 95, 100, 120, 112),
        word("123-45-6789", 12, 120, 100, 132),
    ]
    lines = build_grid(words).splitlines()
    assert "Employee's SSN" in lines[0].replace("  ", " ")
    assert "123-45-6789" in lines[1]


def test_large_vertical_gap_inserts_blank_line() -> None:
    words = [word("TOP", 10, 50, 40, 62), word("BOTTOM", 10, 500, 60, 512)]
    lines = build_grid(words).splitlines()
    assert "" in lines


def test_empty_words_gives_empty_grid() -> None:
    assert build_grid([]) == ""
