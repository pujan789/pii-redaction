from taxhance_pii.domain import BoundingBox
from taxhance_pii.redaction.document import WordBox
from taxhance_pii.redaction.textgate import text_layer_is_garbage


def words_from(texts: list[str]) -> list[WordBox]:
    return [
        WordBox(text=t, box=BoundingBox(x1=1, y1=1, x2=10, y2=10), source="pdf")
        for t in texts
    ]


def test_normal_english_form_text_passes() -> None:
    sample = ["Employee's", "social", "security", "number", "Wages,", "tips,",
              "other", "compensation", "123-45-6789", "48,563.35"]
    assert text_layer_is_garbage(words_from(sample)) is False


def test_mojibake_text_fails() -> None:
    sample = ["���", "Ã©ÂÂ",
              "��", "è±¡å½¢"] * 5
    assert text_layer_is_garbage(words_from(sample)) is True


def test_too_little_text_fails() -> None:
    assert text_layer_is_garbage(words_from(["W-2"])) is True


def test_empty_fails() -> None:
    assert text_layer_is_garbage([]) is True
