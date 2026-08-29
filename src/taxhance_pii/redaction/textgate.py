"""Pre-model gate: a PDF can render perfectly while its embedded text layer is
mojibake (broken font encodings / missing ToUnicode maps) or carries broken
glyph geometry (near-zero word heights). Detect both before the model ever
sees the text; the caller then OCRs the rendered image instead."""
from __future__ import annotations

from statistics import median

from taxhance_pii.redaction.document import WordBox

MIN_TEXT_CHARS = 40
MIN_CLEAN_RATIO = 0.85
# Legible text is >=6pt, roughly 8 thousandths of a letter page; a median word
# height below this means the glyph boxes are broken and redaction boxes built
# from them would paint strikethrough slivers instead of covering the text.
MIN_MEDIAN_WORD_HEIGHT = 3


def text_layer_is_garbage(words: list[WordBox]) -> bool:
    joined = "".join(word.text for word in words)
    if len(joined) < MIN_TEXT_CHARS:
        return True
    clean = sum(1 for ch in joined if ch.isascii() and ch.isprintable())
    if clean / len(joined) < MIN_CLEAN_RATIO:
        return True
    heights = [word.box.y2 - word.box.y1 for word in words]
    return median(heights) < MIN_MEDIAN_WORD_HEIGHT
