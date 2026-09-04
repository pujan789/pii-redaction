from taxhance_pii.domain import BoundingBox
from taxhance_pii.redaction.document import WordBox
from taxhance_pii.redaction.textgate import text_layer_is_garbage


def words_from(texts: list[str]) -> list[WordBox]:
    return [WordBox(text=t, box=BoundingBox(x1=1, y1=1, x2=10, y2=10), source="pdf") for t in texts]


def test_normal_english_form_text_passes() -> None:
    sample = [
        "Employee's",
        "social",
        "security",
        "number",
        "Wages,",
        "tips,",
        "other",
        "compensation",
        "123-45-6789",
        "48,563.35",
    ]
    assert text_layer_is_garbage(words_from(sample)) is False


def test_degenerate_word_heights_fail() -> None:
    # Some PDF generators emit glyph boxes with near-zero heights; boxes built
    # from them paint unreadable slivers, so the page must route to OCR.
    sample = [
        "Employee's",
        "social",
        "security",
        "number",
        "Wages,",
        "tips,",
        "other",
        "compensation",
        "123-45-6789",
        "48,563.35",
    ]
    words = [
        WordBox(text=t, box=BoundingBox(x1=1, y1=500, x2=10, y2=501), source="pdf") for t in sample
    ]
    assert text_layer_is_garbage(words) is True


def test_mojibake_text_fails() -> None:
    sample = ["���", "Ã©ÂÂ", "��", "è±¡å½¢"] * 5
    assert text_layer_is_garbage(words_from(sample)) is True


def test_too_little_text_fails() -> None:
    assert text_layer_is_garbage(words_from(["W-2"])) is True


def test_empty_fails() -> None:
    assert text_layer_is_garbage([]) is True


def test_load_document_ocrs_pages_failing_the_gate(monkeypatch, tmp_path) -> None:
    from PIL import Image

    from taxhance_pii.redaction import document as document_module
    from taxhance_pii.redaction.document import load_document

    image_path = tmp_path / "scan.png"
    Image.new("RGB", (200, 200), "white").save(image_path)
    sentinel = words_from(["OCR", "RESULT", "WORDS", "FOR", "GATE", "TEST", "PAGE", "ONE"])
    monkeypatch.setattr(document_module, "ocr_words", lambda image: sentinel)
    pages = load_document(image_path, ".png", 200, 10, True)
    assert pages[0].words == sentinel  # image pages have no text layer → gate fails → OCR


def test_load_document_skips_ocr_when_disabled(monkeypatch, tmp_path) -> None:
    from PIL import Image

    from taxhance_pii.redaction import document as document_module
    from taxhance_pii.redaction.document import load_document

    image_path = tmp_path / "scan.png"
    Image.new("RGB", (200, 200), "white").save(image_path)
    monkeypatch.setattr(
        document_module, "ocr_words", lambda image: (_ for _ in ()).throw(AssertionError)
    )
    pages = load_document(image_path, ".png", 200, 10, False)
    assert pages[0].words == []
