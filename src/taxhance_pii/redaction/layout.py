"""Layout-preserving text grid from word geometry.

The grid is what the LLM reads: whitespace mirrors the page, so a value sits
under or beside its label exactly as printed. This is the defence against
linear reading order scrambling label→value association on 2-D forms.
"""

from __future__ import annotations

from taxhance_pii.redaction.document import WordBox

GRID_WIDTH = 120


def build_grid(words: list[WordBox], width: int = GRID_WIDTH) -> str:
    if not words:
        return ""
    heights = sorted(w.box.y2 - w.box.y1 for w in words)
    median_height = heights[len(heights) // 2]
    row_tolerance = max(4.0, median_height * 0.6)

    rows: list[list[WordBox]] = []
    row_centers: list[float] = []
    for word in sorted(words, key=lambda w: ((w.box.y1 + w.box.y2) / 2, w.box.x1)):
        center = (word.box.y1 + word.box.y2) / 2
        if rows and abs(center - row_centers[-1]) <= row_tolerance:
            rows[-1].append(word)
            count = len(rows[-1])
            row_centers[-1] = row_centers[-1] * (count - 1) / count + center / count
        else:
            rows.append([word])
            row_centers.append(center)

    lines: list[str] = []
    previous_center: float | None = None
    for center, row in zip(row_centers, rows, strict=True):
        if previous_center is not None and center - previous_center > 3.5 * row_tolerance:
            lines.append("")
        previous_center = center
        line = ""
        for word in sorted(row, key=lambda w: w.box.x1):
            column = round(word.box.x1 / 1000 * width)
            padded_to = max(len(line) + (1 if line else 0), column)
            line = line.ljust(padded_to) + word.text
        lines.append(line)
    return "\n".join(lines)
