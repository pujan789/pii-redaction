"""Pre-model gate: a PDF can render perfectly while its embedded text layer is
mojibake (broken font encodings / missing ToUnicode maps). Detect that before
the model ever sees the text; the caller then OCRs the rendered image instead."""
from __future__ import annotations

from taxhance_pii.redaction.document import WordBox

MIN_TEXT_CHARS = 40
MIN_CLEAN_RATIO = 0.85


def text_layer_is_garbage(words: list[WordBox]) -> bool:
    joined = "".join(word.text for word in words)
    if len(joined) < MIN_TEXT_CHARS:
        return True
    clean = sum(1 for ch in joined if ch.isascii() and ch.isprintable())
    return clean / len(joined) < MIN_CLEAN_RATIO
