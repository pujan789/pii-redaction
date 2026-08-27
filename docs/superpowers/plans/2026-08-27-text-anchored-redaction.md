# Text-Anchored PII Redaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the VLM-draws-boxes detection stack with one universal text-anchored path: layout grid → gemma-4-E2B-it on vLLM → string anchoring → geometry-perfect boxes.

**Architecture:** Words with pixel boxes come from the PDF text layer (pdfplumber) or Tesseract OCR behind a text-quality gate; a layout-preserving text grid goes to a small LLM served by localhost vLLM with one universal prompt; returned strings are anchored back to word geometry, propagated across pages, and backstopped by a deterministic SSN-shape net. All form-specific prompts, regex rule-packs, tiling, GBNF, audit passes, and both old model backends are deleted.

**Tech Stack:** Python 3.13, pydantic v2, pdfplumber, pypdfium2, pytesseract (V1 OCR; PaddleOCR sidecar is a recorded future upgrade), vLLM ≥0.28 serving `google/gemma-4-E2B-it`, rapidfuzz, pytest. Terraform (OpenTofu) for AWS (ECS-on-EC2 ASG, SQS, DynamoDB, S3).

**Spec:** `docs/superpowers/specs/2026-08-27-text-anchored-redaction-design.md`

## Global Constraints

- Production model: `google/gemma-4-E2B-it`, revision pinned (resolve full sha of revision `3e22461f65` via `huggingface_hub` at execution; store in `Settings.model_revision` default).
- Alternate model (config switch only, no code branch): `Qwen/Qwen3.5-4B`.
- Throughput floor 1,000 pages/hour on one g6.xlarge; measured spike rate 11,013.
- Redaction policy: client-side only — client person names, client SSN/ITIN/TIN (incl. masked), first street line (unit line counts), private client IDs, personal email/phone, DOB/DOD. Keep payer/employer/issuer info, account numbers, city/state/ZIP, amounts, form/OMB/control numbers.
- Never commit anything from `C:\Users\pujan\Downloads\all-clients`, `private-evaluation/`, or `private-results/`.
- `PROMPT_VERSION = "tax-pii-v8"`, `DETECTOR_VERSION = "anchored-v1"`.
- Every commit message ends with the Claude Co-Authored-By trailer used in this repo.
- Run tests with `./.venv/Scripts/python.exe -m pytest` (Windows venv in repo root).
- Lint with `./.venv/Scripts/python.exe -m ruff check src tests` before each commit.

## File Structure

```
src/taxhance_pii/
  redaction/
    layout.py      NEW   words+boxes → layout-preserving text grid
    textgate.py    NEW   text-layer quality gate (mojibake detection)
    anchor.py      NEW   string→boxes anchoring, fuzzy fallback, SSN net, merge
    prompt.py      REPLACED  one universal prompt + category map + JSON schema
    document.py    MODIFIED  quality gate wired in; Tesseract kept for V1
                             (PaddleOCR quality is better but its GPU build
                             clashes with vLLM's torch/NCCL — future sidecar)
    detectors.py   DELETED   (1,079 lines of form regexes)
    policy.py      DELETED   (782 lines)
    tiling.py      DELETED
    renderer.py    KEPT (one call-site change in pipeline)
  worker/
    vllm_server.py NEW   vllm serve subprocess launcher + health wait
    detector.py    NEW   TextAnchoredDetector (HTTP → parse → anchor → propagate → net)
    model.py       DELETED   (both old backends)
    pipeline.py    MODIFIED  _detect rewritten; audit passes removed
    main.py        MODIFIED  starts vLLM launcher + TextAnchoredDetector
  config.py        MODIFIED  new vLLM/OCR settings; llama_*/dtype/backend removed
tests/
  test_layout.py test_textgate.py test_anchor.py test_prompt.py
  test_vllm_server.py test_detector.py  NEW
  test_detectors.py test_policy.py test_tiling.py test_model_output.py  DELETED
  test_api_pipeline.py test_worker_resilience.py conftest.py  MODIFIED
docker/worker.Dockerfile REWRITTEN; docker/worker-entrypoint.sh NEW
infra/compute.tf infra/variables.tf MODIFIED (schedules, spot, off-hours alarm)
```

---

### Task 1: Layout grid (`layout.py`)

**Files:**
- Create: `src/taxhance_pii/redaction/layout.py`
- Test: `tests/test_layout.py`

**Interfaces:**
- Consumes: `WordBox` from `taxhance_pii.redaction.document` (fields: `text: str`, `box: BoundingBox` with 0–1000 int coords, `source: str`).
- Produces: `build_grid(words: list[WordBox], width: int = 120) -> str` — monospace text where each word starts at column `round(box.x1/1000*width)`, rows clustered by y-center, blank line inserted at large vertical gaps.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_layout.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_layout.py -v`
Expected: FAIL with `ModuleNotFoundError: taxhance_pii.redaction.layout`

- [ ] **Step 3: Write the implementation**

```python
# src/taxhance_pii/redaction/layout.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_layout.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add src/taxhance_pii/redaction/layout.py tests/test_layout.py
git commit -m "feat: layout-preserving text grid for text-anchored detection"
```

---

### Task 2: Text-layer quality gate (`textgate.py`)

**Files:**
- Create: `src/taxhance_pii/redaction/textgate.py`
- Test: `tests/test_textgate.py`

**Interfaces:**
- Consumes: `WordBox` from `taxhance_pii.redaction.document`.
- Produces: `text_layer_is_garbage(words: list[WordBox]) -> bool` — True when the embedded text layer must be discarded and the rendered image OCR'd instead.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_textgate.py
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
    sample = ["\ufffd\ufffd\ufffd", "\u00c3\u00a9\u00c2\u0081\u00c2\u009a",
              "\ufffd\ufffd", "\u00e8\u00b1\u00a1\u00e5\u00bd\u00a2"] * 5
    assert text_layer_is_garbage(words_from(sample)) is True


def test_too_little_text_fails() -> None:
    assert text_layer_is_garbage(words_from(["W-2"])) is True


def test_empty_fails() -> None:
    assert text_layer_is_garbage([]) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_textgate.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# src/taxhance_pii/redaction/textgate.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_textgate.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/taxhance_pii/redaction/textgate.py tests/test_textgate.py
git commit -m "feat: text-layer quality gate for automatic re-OCR"
```

---

### Task 3: Wire the quality gate into `document.py`; expose `ocr_words`

**Files:**
- Modify: `src/taxhance_pii/redaction/document.py` (gate replaces `_needs_ocr`; `_ocr_words` becomes public `ocr_words`)
- Test: extend `tests/test_textgate.py`; update `tests/test_document_limits.py` if it references `_needs_ocr`/`_ocr_words`

**Interfaces:**
- Consumes: `text_layer_is_garbage` (Task 2); existing pytesseract `_ocr_words`.
- Produces: `ocr_words(image: Image.Image) -> list[WordBox]` (public rename of `_ocr_words`, unchanged behavior, source="ocr"); `load_document(...)` keeps its exact existing signature but decides OCR via the gate.

**OCR engine decision (validated on the spike box 2026-08-27):** Tesseract stays for V1. PaddleOCR's quality is better, but its CPU path takes 41–487 s/page (unusable) and `paddlepaddle-gpu` corrupts vLLM's torch/NCCL when co-installed (`undefined symbol: ncclCommResume`). A Paddle sidecar container is the recorded future upgrade; do not attempt it in this plan.

- [ ] **Step 1: Write the failing test** (append to `tests/test_textgate.py`)

```python
# append to tests/test_textgate.py
from pathlib import Path

from taxhance_pii.redaction import document as document_module
from taxhance_pii.redaction.document import load_document


def test_load_document_ocrs_pages_failing_the_gate(monkeypatch, tmp_path: Path) -> None:
    from PIL import Image

    image_path = tmp_path / "scan.png"
    Image.new("RGB", (200, 200), "white").save(image_path)
    sentinel = words_from(["OCR", "RESULT", "WORDS", "FOR", "GATE", "TEST", "PAGE", "ONE"])
    monkeypatch.setattr(document_module, "ocr_words", lambda image: sentinel)
    pages = load_document(image_path, ".png", 200, 10, True)
    assert pages[0].words == sentinel  # image pages have no text layer → gate fails → OCR


def test_load_document_skips_ocr_when_disabled(monkeypatch, tmp_path: Path) -> None:
    from PIL import Image

    image_path = tmp_path / "scan.png"
    Image.new("RGB", (200, 200), "white").save(image_path)
    monkeypatch.setattr(
        document_module, "ocr_words", lambda image: (_ for _ in ()).throw(AssertionError)
    )
    pages = load_document(image_path, ".png", 200, 10, False)
    assert pages[0].words == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_textgate.py -v`
Expected: the two new tests FAIL (`ocr_words` not found / gate not wired)

- [ ] **Step 3: Apply the changes to `document.py`**

1. Rename `_ocr_words` → `ocr_words` (module-public; body unchanged, Tesseract via pytesseract stays).
2. Delete `_needs_ocr` (lines 132-134).
3. Replace the OCR branch in `load_document` (lines 188-198):

```python
def load_document(
    path: Path, extension: str, dpi: int, max_pages: int, ocr: bool
) -> list[PageArtifact]:
    from taxhance_pii.redaction.textgate import text_layer_is_garbage

    pages = (
        _render_pdf(path, dpi, max_pages) if extension == ".pdf" else _render_image(path, max_pages)
    )
    if ocr:
        for page in pages:
            if text_layer_is_garbage(page.words):
                page.words = ocr_words(page.image)
    return pages
```

(`ocr_words` already raises `DocumentError("ocr_unavailable")` when Tesseract is missing — keep that.)

- [ ] **Step 4: Run tests**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_textgate.py tests/test_document_limits.py -v`
Expected: PASS (update any `_needs_ocr`/`_ocr_words` references in `test_document_limits.py` to the new names).

- [ ] **Step 5: Commit**

```bash
git add src/taxhance_pii/redaction/document.py tests
git commit -m "feat: route OCR through text-quality gate; expose ocr_words"
```

---

### Task 4: Universal prompt (`prompt.py` replacement)

**Files:**
- Rewrite: `src/taxhance_pii/redaction/prompt.py` (delete everything currently in it)
- Test: `tests/test_prompt.py`

**Interfaces:**
- Produces:
  - `PROMPT_VERSION = "tax-pii-v8"`
  - `CATEGORY_MAP: dict[str, PiiCategory]` mapping model categories → domain enum
  - `build_messages(page_grid: str) -> list[dict[str, str]]`
  - `RESPONSE_JSON_SCHEMA: dict` for vLLM structured output (`response_format: {"type": "json_schema", ...}`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prompt.py
from taxhance_pii.domain import PiiCategory
from taxhance_pii.redaction.prompt import (
    CATEGORY_MAP,
    PROMPT_VERSION,
    RESPONSE_JSON_SCHEMA,
    build_messages,
)


def test_category_map_targets_domain_enum() -> None:
    assert CATEGORY_MAP == {
        "client_name": PiiCategory.PERSON_NAME,
        "client_tin": PiiCategory.SSN,
        "address_line": PiiCategory.STREET_ADDRESS,
        "private_id": PiiCategory.OTHER_PRIVATE_ID,
        "email": PiiCategory.EMAIL,
        "phone": PiiCategory.PHONE,
        "dob": PiiCategory.DATE_OF_BIRTH,
    }


def test_schema_enum_matches_map_keys() -> None:
    enum = RESPONSE_JSON_SCHEMA["json_schema"]["schema"]["properties"]["items"][
        "items"
    ]["properties"]["category"]["enum"]
    assert sorted(enum) == sorted(CATEGORY_MAP)


def test_messages_carry_policy_and_grid() -> None:
    messages = build_messages("THE GRID")
    assert messages[0]["role"] == "system"
    user = messages[1]["content"]
    assert "THE GRID" in user
    assert "payer" in user.lower() and "never" in user.lower()
    assert "city/state/zip" in user.lower().replace(" ", "").replace("-", "/") or "ZIP" in user


def test_version() -> None:
    assert PROMPT_VERSION == "tax-pii-v8"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_prompt.py -v`
Expected: FAIL (old module contents)

- [ ] **Step 3: Replace the module**

```python
# src/taxhance_pii/redaction/prompt.py
"""The one universal detection prompt. Every page of every form type goes
through this prompt — form-specific routing is deliberately gone."""
from __future__ import annotations

from taxhance_pii.domain import PiiCategory

PROMPT_VERSION = "tax-pii-v8"

CATEGORY_MAP: dict[str, PiiCategory] = {
    "client_name": PiiCategory.PERSON_NAME,
    "client_tin": PiiCategory.SSN,
    "address_line": PiiCategory.STREET_ADDRESS,
    "private_id": PiiCategory.OTHER_PRIVATE_ID,
    "email": PiiCategory.EMAIL,
    "phone": PiiCategory.PHONE,
    "dob": PiiCategory.DATE_OF_BIRTH,
}

SYSTEM_PROMPT = (
    "You are a precise PII detector for US tax and accounting documents. "
    "You read one page rendered as a layout-preserving text grid (whitespace "
    "mirrors the page layout, so values sit near their labels exactly as "
    "printed). You return only JSON."
)

USER_TEMPLATE = """Find every CLIENT-SIDE private value on this tax document page. The client is the \
recipient of the document: the taxpayer, employee, payer/borrower, partner, shareholder, \
spouse, dependent, or beneficiary.

Report these categories:
- client_name: each distinct client person name (taxpayer, spouse, dependent, beneficiary, \
partner, shareholder, employee). Report each distinct spelling once.
- client_tin: the client person's SSN, ITIN, or TIN, including partially masked values \
like ***-**-1234 or XXX-XX-1234 — the value near labels such as "Employee's social \
security number", "RECIPIENT'S TIN", "Partner's SSN", or any TIN field whose value \
identifies the client person rather than an institution.
- address_line: ONLY the street line of the client's address (house number + street, \
or PO Box). A unit/apartment printed on its own line is its own address_line item. \
NEVER include the city/state/ZIP text in an item. Also the street line of a property \
address securing a mortgage.
- private_id: private client identifiers (policy, member, payroll, case, brokerage, \
retirement account holder IDs). Not form numbers, not control numbers, not account numbers.
- email: an email address belonging to the client personally.
- phone: a phone number belonging to the client personally — never a company or agency \
phone number.
- dob: date of birth or death.

DO NOT report (these stay visible):
- payer / employer / issuer / lender / financial-institution names, street addresses, \
phone numbers, and their EINs (e.g. values near "Employer ID number" or "PAYER'S TIN" \
when the payer is an institution)
- city/state/ZIP lines, account numbers, money amounts, dates other than birth/death, \
form numbers, OMB numbers, control numbers, tax years, generic labels
Note: labels vary by form. Decide by WHO the value identifies — a private person who \
receives this document (client side, report it) or an institution/business issuing it \
(keep it). A mortgage form's "payer/borrower" is the client person; a bank issuing the \
form is not, whatever its label says.

Rules:
- Copy each value EXACTLY as it appears in the grid, character for character, \
including run-together words, punctuation, and masking characters.
- Report printed VALUES only. Never report a field label, heading, or instruction text.
- One item per value from a single line of the grid. Never merge several lines into one item.
- If the same value repeats, report it once.
- If nothing qualifies, return {{"items": []}}.

Return ONLY the JSON object. Example of the format (with made-up sample values):
{{"items": [{{"text": "JOHN Q SAMPLE", "category": "client_name"}}, \
{{"text": "***-**-9999", "category": "client_tin"}}, \
{{"text": "123 EXAMPLE AVE", "category": "address_line"}}]}}

PAGE GRID:
{page_grid}"""

RESPONSE_JSON_SCHEMA: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "pii_items",
        "schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "maxItems": 100,
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "maxLength": 200},
                            "category": {
                                "type": "string",
                                "enum": sorted(CATEGORY_MAP),
                            },
                        },
                        "required": ["text", "category"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        },
    },
}


def build_messages(page_grid: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(page_grid=page_grid)},
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_prompt.py -v`
Expected: 4 PASS. (Delete `tests/test_model_output.py` here if it fails to import — it tests the removed GBNF machinery; full deletion is Task 9's job but an import break must not linger.)

- [ ] **Step 5: Commit**

```bash
git add src/taxhance_pii/redaction/prompt.py tests/test_prompt.py
git commit -m "feat: single universal detection prompt with vLLM JSON schema"
```

---

### Task 5: Anchoring + safety net (`anchor.py`)

**Files:**
- Create: `src/taxhance_pii/redaction/anchor.py`
- Modify: `pyproject.toml` / `requirements/` (add `rapidfuzz`)
- Test: `tests/test_anchor.py`

**Interfaces:**
- Consumes: `WordBox`, `BoundingBox`, `Detection`, `PiiCategory`.
- Produces:
  - `anchor_value(value: str, words: list[WordBox]) -> list[BoundingBox]` — every occurrence; exact normalized window match, then fragment split, then fuzzy (rapidfuzz `ratio >= 90`) over windows.
  - `ssn_safety_net(page_index: int, words: list[WordBox]) -> list[Detection]` — SSN-shaped (3-2-4, masked forms included) values, `source="regex"`, `confidence=0.95`, category `PiiCategory.SSN`.
  - `merge_page_detections(detections: list[Detection]) -> list[Detection]` — same page+category overlapping boxes unioned, exact duplicates dropped.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_anchor.py
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
    return [
        word(t, x1=10 + i * 60, y1=y, x2=60 + i * 60, y2=y + 10)
        for i, t in enumerate(texts)
    ]


def test_multiword_exact_match_returns_one_box_per_word() -> None:
    words = row(["JULIA", "ZHOU", "45", "LEWIS", "ST"], 100)
    assert len(anchor_value("Julia Zhou", words)) == 2


def test_all_occurrences_matched() -> None:
    words = row(["JULIA", "ZHOU"], 100) + row(["JULIA", "ZHOU"], 400)
    assert len(anchor_value("JULIA ZHOU", words)) == 4


def test_merged_word_prefix_match() -> None:
    words = row(["39AUBURNPATHDR", "THEWOODLANDSTX77382"], 100)
    assert len(anchor_value("39AUBURNPATHDR", words)) == 1


def test_fragmented_value_matches_via_fuzzy() -> None:
    # OCR split LEWIS as LEWI S — exact join still matches via normalization
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
    a = Detection(id="a", page_index=0, category=PiiCategory.SSN,
                  box=BoundingBox(x1=10, y1=10, x2=50, y2=20), confidence=0.9, source="model")
    b = Detection(id="b", page_index=0, category=PiiCategory.SSN,
                  box=BoundingBox(x1=40, y1=10, x2=90, y2=20), confidence=0.95, source="regex")
    merged = merge_page_detections([a, b])
    assert len(merged) == 1
    assert merged[0].box.x1 == 10 and merged[0].box.x2 == 90
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_anchor.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# src/taxhance_pii/redaction/anchor.py
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
            if accumulated == target or (
                len(target) >= 8 and accumulated.startswith(target)
            ):
                found.extend(_boxes(words[start : end + 1]))
                break
            if not target.startswith(accumulated):
                break
    return found


def _fuzzy(target: str, words: list[WordBox], norms: list[str]) -> list[BoundingBox]:
    target_tokens = max(1, target.count(" ") + 1)
    del target_tokens  # window size derives from normalized length instead
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
    for index, word in enumerate(words):
        if index in used:
            continue
        token = word.text.strip(".,;:()")
        if _SSN_SHAPE.match(token) and any(c.isdigit() for c in token):
            detections.append(_detection(page_index, word.box))
            continue
        if index + 1 < len(words):
            neighbour = words[index + 1].text.strip(".,;:()")
            if token.endswith("-") or neighbour.startswith("-"):
                joined = token + neighbour
                if _SSN_SHAPE.match(joined) and any(c.isdigit() for c in joined):
                    detections.append(_detection(page_index, word.box))
                    detections.append(_detection(page_index, words[index + 1].box))
                    used.add(index + 1)
    return detections


def _overlaps(a: BoundingBox, b: BoundingBox) -> bool:
    return not (a.x2 < b.x1 or b.x2 < a.x1 or a.y2 < b.y1 or b.y2 < a.y1)


def merge_page_detections(detections: list[Detection]) -> list[Detection]:
    merged: list[Detection] = []
    for detection in sorted(detections, key=lambda d: (d.page_index, d.category, d.box.y1, d.box.x1)):
        target = next(
            (
                m
                for m in merged
                if m.page_index == detection.page_index
                and m.category == detection.category
                and _overlaps(m.box, detection.box)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_anchor.py -v`
Expected: 9 PASS

- [ ] **Step 5: Commit**

```bash
git add src/taxhance_pii/redaction/anchor.py tests/test_anchor.py pyproject.toml requirements
git commit -m "feat: string anchoring with fuzzy fallback and SSN safety net"
```

---

### Task 6: Settings rework (`config.py`)

**Files:**
- Modify: `src/taxhance_pii/config.py`
- Test: `tests/test_config.py` (new)

**Interfaces:**
- Produces (new/changed `Settings` fields — exact):
  - `model_id: str = "google/gemma-4-E2B-it"`
  - `model_revision: str = "<full sha resolved at execution from revision prefix 3e22461f65>"`
  - `model_max_new_tokens: int = Field(default=1500, ge=64, le=16_384)` (kept)
  - `vllm_base_url: str = "http://127.0.0.1:8000/v1"`
  - `vllm_launch: bool = True` (worker starts its own `vllm serve`)
  - `vllm_port: int = Field(default=8000, ge=1024, le=65_535)`
  - `vllm_max_model_len: int = Field(default=16_384, ge=4_096, le=131_072)`
  - `vllm_gpu_memory_utilization: float = Field(default=0.90, ge=0.3, le=0.98)`
  - `vllm_startup_timeout_seconds: int = Field(default=600, ge=60, le=1_800)`
  - `detector_concurrency: int = Field(default=8, ge=1, le=64)`
- Removes: `model_backend`, `model_dtype`, `model_max_pixels`, `model_audit_passes`, all seven `llama_*` fields.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from taxhance_pii.config import Settings


def test_new_model_defaults() -> None:
    settings = Settings(token_pepper="x" * 40)
    assert settings.model_id == "google/gemma-4-E2B-it"
    assert settings.vllm_base_url == "http://127.0.0.1:8000/v1"
    assert settings.vllm_launch is True
    assert settings.detector_concurrency == 8


def test_llama_settings_are_gone() -> None:
    settings = Settings(token_pepper="x" * 40)
    for name in ("model_backend", "model_dtype", "llama_server_path", "model_audit_passes"):
        assert not hasattr(settings, name)
```

- [ ] **Step 2: Run test to verify it fails** — `./.venv/Scripts/python.exe -m pytest tests/test_config.py -v` → FAIL

- [ ] **Step 3: Apply the field changes to `Settings`** exactly as listed in Interfaces (add the new block where `model_backend` currently sits at `config.py:41-60`; delete the removed fields). Resolve the full revision sha first:

Run: `./.venv/Scripts/python.exe -c "from huggingface_hub import HfApi; print(HfApi().model_info('google/gemma-4-E2B-it').sha)"`
Put the printed sha in `model_revision`'s default.

- [ ] **Step 4: Run tests** — `./.venv/Scripts/python.exe -m pytest tests/test_config.py -v` → PASS. Run the full suite; fix imports that referenced removed settings only when the file is being deleted in a later task anyway (leave those failing tests to their own tasks if they are old-stack tests slated for deletion — otherwise fix here).

- [ ] **Step 5: Commit**

```bash
git add src/taxhance_pii/config.py tests/test_config.py
git commit -m "feat: vLLM-centric settings; drop llama.cpp and transformers knobs"
```

---

### Task 7: vLLM server launcher (`worker/vllm_server.py`)

**Files:**
- Create: `src/taxhance_pii/worker/vllm_server.py`
- Test: `tests/test_vllm_server.py`

**Interfaces:**
- Consumes: `Settings` (Task 6 fields).
- Produces:
  - `build_command(settings: Settings) -> list[str]`
  - `class VllmServer:` with `start() -> None` (spawn + wait healthy or raise `RuntimeError("vllm_startup_timeout")` / `RuntimeError(f"vllm_exited:{code}")`), `stop() -> None`. Constructor: `VllmServer(settings: Settings)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vllm_server.py
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from taxhance_pii.config import Settings
from taxhance_pii.worker.vllm_server import VllmServer, build_command


def settings(**overrides) -> Settings:
    return Settings(token_pepper="x" * 40, **overrides)


def test_command_pins_model_and_port() -> None:
    command = build_command(settings())
    assert command[:2] == ["vllm", "serve"]
    assert "google/gemma-4-E2B-it" in command
    assert "--revision" in command and "--port" in command
    assert "--gpu-memory-utilization" in command


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):  # noqa: ANN002
        pass


def test_wait_healthy_returns_when_health_endpoint_up(monkeypatch) -> None:
    server = HTTPServer(("127.0.0.1", 0), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cfg = settings(vllm_port=server.server_address[1], vllm_startup_timeout_seconds=60)
        launcher = VllmServer(cfg)

        class FakeProcess:
            def poll(self):
                return None

            def terminate(self):
                pass

        monkeypatch.setattr(
            "taxhance_pii.worker.vllm_server.subprocess.Popen",
            lambda *a, **k: FakeProcess(),
        )
        launcher.start()  # returns without raising
    finally:
        server.shutdown()
```

- [ ] **Step 2: Run tests to verify they fail** — Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# src/taxhance_pii/worker/vllm_server.py
"""Launch and supervise the localhost vLLM OpenAI server for the worker."""
from __future__ import annotations

import logging
import subprocess
import time
import urllib.error
import urllib.request

from taxhance_pii.config import Settings

logger = logging.getLogger(__name__)


def build_command(settings: Settings) -> list[str]:
    return [
        "vllm",
        "serve",
        settings.model_id,
        "--revision",
        settings.model_revision,
        "--host",
        "127.0.0.1",
        "--port",
        str(settings.vllm_port),
        "--max-model-len",
        str(settings.vllm_max_model_len),
        "--gpu-memory-utilization",
        str(settings.vllm_gpu_memory_utilization),
        "--disable-log-requests",
    ]


class VllmServer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        # Command is built from validated settings; no shell involved.
        self._process = subprocess.Popen(build_command(self.settings))  # noqa: S603
        health_url = f"http://127.0.0.1:{self.settings.vllm_port}/health"
        deadline = time.monotonic() + self.settings.vllm_startup_timeout_seconds
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(f"vllm_exited:{self._process.returncode}")
            try:
                with urllib.request.urlopen(health_url, timeout=2) as response:  # noqa: S310
                    if response.status == 200:
                        logger.info("vllm_ready", extra={"model_id": self.settings.model_id})
                        return
            except (urllib.error.URLError, TimeoutError):
                pass
            time.sleep(2)
        self.stop()
        raise RuntimeError("vllm_startup_timeout")

    def stop(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
        self._process = None
```

- [ ] **Step 4: Run tests** — Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add src/taxhance_pii/worker/vllm_server.py tests/test_vllm_server.py
git commit -m "feat: vLLM server launcher with health gate"
```

---

### Task 8: The detector (`worker/detector.py`)

**Files:**
- Create: `src/taxhance_pii/worker/detector.py`
- Test: `tests/test_detector.py`

**Interfaces:**
- Consumes: `Settings`; `PageArtifact` (fields `page_index`, `image`, `words`); `build_grid` (Task 1); `build_messages`, `CATEGORY_MAP`, `RESPONSE_JSON_SCHEMA` (Task 4); `anchor_value`, `ssn_safety_net`, `merge_page_detections` (Task 5); `ocr_words` (Task 3).
- Produces:
  - `class TextAnchoredDetector:` constructor `TextAnchoredDetector(settings: Settings)`; methods `preflight() -> None` (GET `{vllm_base_url}/models`, raise on failure) and `detect_document(pages: list[PageArtifact]) -> list[Detection]`.
  - `class NoopDetector:` same two methods; `detect_document` returns `[]` — used by tests and local no-GPU runs.
  - Module constant `DETECTOR_VERSION = "anchored-v1"`.

Behavior of `detect_document` (this is the heart of the system):
1. For each page with words, build the grid and POST a chat completion (ThreadPoolExecutor, `settings.detector_concurrency` workers) — payload: `model`, `messages`, `temperature: 0`, `max_tokens: settings.model_max_new_tokens`, `response_format: RESPONSE_JSON_SCHEMA`.
2. Parse `choices[0].message.content` as JSON (`json.loads`; on failure treat as empty items).
3. Anchor each item on its page. If fewer than half of a page's items anchor AND every word on the page has `source == "pdf"`, re-extract with `ocr_words(page.image)`, rebuild grid, retry that page once (same request), and keep the retry's results.
4. Doc-level propagation: collect `(text, category)` pairs whose category maps to `PERSON_NAME`, `SSN`, or `STREET_ADDRESS`; re-anchor every collected value on every page; propagated hits become detections with `confidence=0.85`.
5. Per page, append `ssn_safety_net(page_index, words)`.
6. Return `merge_page_detections` over everything. Model-item detections use `confidence=0.9`, `source="model"`; PII strings never leave this function.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_detector.py
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PIL import Image

from taxhance_pii.config import Settings
from taxhance_pii.domain import BoundingBox, PiiCategory
from taxhance_pii.redaction.document import PageArtifact, WordBox
from taxhance_pii.worker.detector import NoopDetector, TextAnchoredDetector


def word(text: str, x1: int, y1: int) -> WordBox:
    return WordBox(
        text=text, box=BoundingBox(x1=x1, y1=y1, x2=x1 + 40, y2=y1 + 10), source="pdf"
    )


def page(index: int, words: list[WordBox]) -> PageArtifact:
    return PageArtifact(index, Image.new("RGB", (100, 100), "white"), words)


class ScriptedVllm(BaseHTTPRequestHandler):
    responses: list[dict] = []
    calls: int = 0

    def do_GET(self):  # noqa: N802 - /models preflight
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"data": []}')

    def do_POST(self):  # noqa: N802
        length = int(self.headers["Content-Length"])
        self.rfile.read(length)
        body = ScriptedVllm.responses[min(ScriptedVllm.calls, len(ScriptedVllm.responses) - 1)]
        ScriptedVllm.calls += 1
        payload = {"choices": [{"message": {"content": json.dumps(body)}}]}
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):  # noqa: ANN002
        pass


@pytest.fixture()
def vllm_stub():
    server = HTTPServer(("127.0.0.1", 0), ScriptedVllm)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ScriptedVllm.calls = 0
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()


def make_detector(base_url: str) -> TextAnchoredDetector:
    settings = Settings(
        token_pepper="x" * 40, vllm_base_url=base_url, vllm_launch=False,
        detector_concurrency=1,
    )
    return TextAnchoredDetector(settings)


def test_detects_anchors_and_propagates_across_pages(vllm_stub) -> None:
    ScriptedVllm.responses = [
        {"items": [{"text": "JULIA ZHOU", "category": "client_name"}]},
        {"items": []},  # page 2 model finds nothing, propagation must cover it
    ]
    pages = [
        page(0, [word("JULIA", 10, 10), word("ZHOU", 60, 10)]),
        page(1, [word("JULIA", 10, 10), word("ZHOU", 60, 10), word("Wages", 10, 40)]),
    ]
    detections = make_detector(vllm_stub).detect_document(pages)
    assert any(d.page_index == 1 and d.category == PiiCategory.PERSON_NAME for d in detections)


def test_safety_net_applies_even_when_model_misses(vllm_stub) -> None:
    ScriptedVllm.responses = [{"items": []}]
    detections = make_detector(vllm_stub).detect_document(
        [page(0, [word("123-45-6789", 10, 10)])]
    )
    assert len(detections) == 1
    assert detections[0].source == "regex" and detections[0].category == PiiCategory.SSN


def test_unparseable_content_yields_only_net(vllm_stub) -> None:
    ScriptedVllm.responses = [{"garbage": True}]
    detections = make_detector(vllm_stub).detect_document(
        [page(0, [word("hello", 10, 10)])]
    )
    assert detections == []


def test_noop_detector_contract() -> None:
    detector = NoopDetector()
    detector.preflight()
    assert detector.detect_document([]) == []
```

- [ ] **Step 2: Run tests to verify they fail** — Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# src/taxhance_pii/worker/detector.py
"""Text-anchored detection: grid → LLM names strings → geometry gives boxes."""
from __future__ import annotations

import json
import logging
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from taxhance_pii.config import Settings
from taxhance_pii.domain import BoundingBox, Detection, PiiCategory
from taxhance_pii.redaction.anchor import (
    anchor_value,
    merge_page_detections,
    ssn_safety_net,
)
from taxhance_pii.redaction.document import PageArtifact, WordBox
from taxhance_pii.redaction.layout import build_grid
from taxhance_pii.redaction.prompt import (
    CATEGORY_MAP,
    RESPONSE_JSON_SCHEMA,
    build_messages,
)

logger = logging.getLogger(__name__)
DETECTOR_VERSION = "anchored-v1"
PROPAGATED_CATEGORIES = {
    PiiCategory.PERSON_NAME,
    PiiCategory.SSN,
    PiiCategory.STREET_ADDRESS,
}


@dataclass
class _PageResult:
    page: PageArtifact
    items: list[tuple[str, PiiCategory]] = field(default_factory=list)
    detections: list[Detection] = field(default_factory=list)


class DetectorError(RuntimeError):
    pass


class TextAnchoredDetector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def preflight(self) -> None:
        request = urllib.request.Request(f"{self.settings.vllm_base_url}/models")  # noqa: S310
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            if response.status != 200:
                raise DetectorError("vllm_unhealthy")

    def _complete(self, grid: str) -> list[tuple[str, PiiCategory]]:
        payload = {
            "model": self.settings.model_id,
            "messages": build_messages(grid),
            "temperature": 0,
            "max_tokens": self.settings.model_max_new_tokens,
            "response_format": RESPONSE_JSON_SCHEMA,
        }
        request = urllib.request.Request(  # noqa: S310
            f"{self.settings.vllm_base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310
            body = json.loads(response.read())
        content = body["choices"][0]["message"]["content"]
        try:
            parsed = json.loads(content)
            raw_items = parsed.get("items", [])
        except (json.JSONDecodeError, AttributeError, TypeError):
            logger.warning("model_output_unparseable")
            return []
        items: list[tuple[str, PiiCategory]] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text", "")).strip()
            category = CATEGORY_MAP.get(str(raw.get("category", "")))
            if text and category is not None:
                items.append((text, category))
        return items

    def _detect_page(self, page: PageArtifact) -> _PageResult:
        result = _PageResult(page=page)
        if not page.words:
            return result
        result.items = self._complete(build_grid(page.words))
        anchored = self._anchor_items(result)
        pdf_only = all(w.source == "pdf" for w in page.words)
        if result.items and anchored * 2 < len(result.items) and pdf_only:
            from taxhance_pii.redaction.document import ocr_words

            logger.info("page_reocr_retry", extra={"page_index": page.page_index})
            page.words = ocr_words(page.image)
            result.items = self._complete(build_grid(page.words))
            result.detections = []
            self._anchor_items(result)
        return result

    def _anchor_items(self, result: _PageResult) -> int:
        anchored_count = 0
        for text, category in result.items:
            boxes = anchor_value(text, result.page.words)
            if boxes:
                anchored_count += 1
            result.detections.extend(
                self._detection(result.page.page_index, category, box, 0.9)
                for box in boxes
            )
        return anchored_count

    @staticmethod
    def _detection(
        page_index: int, category: PiiCategory, box: BoundingBox, confidence: float
    ) -> Detection:
        return Detection(
            id=str(uuid.uuid4()),
            page_index=page_index,
            category=category,
            box=box,
            confidence=confidence,
            source="model",
        )

    def detect_document(self, pages: list[PageArtifact]) -> list[Detection]:
        with ThreadPoolExecutor(max_workers=self.settings.detector_concurrency) as pool:
            results = list(pool.map(self._detect_page, pages))
        doc_values = {
            (text, category)
            for result in results
            for text, category in result.items
            if category in PROPAGATED_CATEGORIES
        }
        detections: list[Detection] = []
        for result in results:
            page = result.page
            page_detections = list(result.detections)
            for text, category in doc_values:
                page_detections.extend(
                    self._detection(page.page_index, category, box, 0.85)
                    for box in anchor_value(text, page.words)
                )
            page_detections.extend(ssn_safety_net(page.page_index, page.words))
            detections.extend(merge_page_detections(page_detections))
        return detections


class NoopDetector:
    """Local/no-GPU runs and pipeline tests."""

    def preflight(self) -> None:
        return None

    def detect_document(self, pages: list[PageArtifact]) -> list[Detection]:
        del pages
        return []
```

- [ ] **Step 4: Run tests** — `./.venv/Scripts/python.exe -m pytest tests/test_detector.py -v` → 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/taxhance_pii/worker/detector.py tests/test_detector.py
git commit -m "feat: text-anchored detector with propagation, re-OCR gate, SSN net"
```

---

### Task 9: Rewire pipeline and worker; delete the old stack

**Files:**
- Modify: `src/taxhance_pii/worker/pipeline.py` (rewrite `_detect`, residual check)
- Modify: `src/taxhance_pii/worker/main.py:14,105-117` (launcher + detector wiring)
- Delete: `src/taxhance_pii/worker/model.py`, `src/taxhance_pii/redaction/tiling.py`, `src/taxhance_pii/redaction/policy.py`, `src/taxhance_pii/redaction/detectors.py`
- Delete: `tests/test_tiling.py`, `tests/test_policy.py`, `tests/test_model_output.py`, `tests/test_detectors.py`
- Modify: `tests/conftest.py`, `tests/test_api_pipeline.py`, `tests/test_worker_resilience.py` (swap `NoopVisionDetector` → `NoopDetector` from `taxhance_pii.worker.detector`; the pipeline constructor signature is unchanged)

**Interfaces:**
- Consumes: `TextAnchoredDetector` / `NoopDetector` (`preflight()`, `detect_document(pages)`), `VllmServer`, `ssn_safety_net`, `DETECTOR_VERSION`, `PROMPT_VERSION`.
- Produces: `WorkerPipeline` with the same constructor except the `detector` parameter is now typed to a `DocumentDetector` protocol (define it in `detector.py`):

```python
class DocumentDetector(Protocol):
    def preflight(self) -> None: ...
    def detect_document(self, pages: list[PageArtifact]) -> list[Detection]: ...
```

- [ ] **Step 1: Rewrite `WorkerPipeline._detect`** (replaces `pipeline.py:76-156`). The per-page loop with `detect_patterns`, `apply_redaction_policy`, and the audit-pass loop goes away:

```python
    def _detect(self, job: JobRecord) -> None:
        with tempfile.TemporaryDirectory(prefix=f"pii-{job.job_id}-") as raw_directory:
            directory = Path(raw_directory)
            _, pages = self._load_pages(job, directory)
            self.repository.update(
                job.job_id,
                {JobStatus.DETECTING},
                page_count=len(pages),
                pages_completed=0,
            )
            for page in pages:
                self._assert_active(job.job_id, JobStatus.DETECTING)
                self.blobs.put_bytes(
                    f"jobs/{job.job_id}/previews/{page.page_index:04d}.jpg",
                    encode_preview(page.image),
                    "image/jpeg",
                )
            detections = self.detector.detect_document(pages)
            self._assert_active(job.job_id, JobStatus.DETECTING)
            self.repository.update(
                job.job_id,
                {JobStatus.DETECTING},
                pages_completed=len(pages),
                finding_count=len(detections),
            )
            manifest = RedactionManifest(
                job_id=job.job_id,
                page_count=len(pages),
                detections=detections,
                detector_version=DETECTOR_VERSION,
                prompt_version=PROMPT_VERSION,
                model_id=self.settings.model_id,
                created_at=utc_now(),
            )
            # ... unchanged from here: encode manifest, auto_finalize branch
```

Imports in `pipeline.py` change to:

```python
from taxhance_pii.redaction.anchor import ssn_safety_net
from taxhance_pii.worker.detector import DETECTOR_VERSION, DocumentDetector
from taxhance_pii.redaction.prompt import PROMPT_VERSION
```

(remove `detect_patterns`, `merge_detections`, `apply_redaction_policy`, `flattened_audit_image` imports; delete the module-level `DETECTOR_VERSION = "hybrid-v27"`).

- [ ] **Step 2: Rewrite the residual check in `_render_loaded`** (replaces `pipeline.py:183-207`): after rendering, re-load the output with OCR and require zero SSN-shaped residuals:

```python
        if self.settings.ocr_enabled:
            residual_pages = load_document(
                output_path, ".pdf", self.settings.render_dpi, self.settings.max_pages, True
            )
            try:
                if any(
                    ssn_safety_net(page.page_index, page.words) for page in residual_pages
                ):
                    raise RedactionValidationError("residual_identifier_detected")
            finally:
                for page in residual_pages:
                    page.image.close()
```

- [ ] **Step 3: Rewire `worker/main.py`** — replace `build_vision_detector` (line 14 import, lines 105-117):

```python
from taxhance_pii.worker.detector import TextAnchoredDetector
from taxhance_pii.worker.vllm_server import VllmServer


def run() -> None:
    container = get_container()
    configure_logging(container.settings.log_level)
    vllm = VllmServer(container.settings) if container.settings.vllm_launch else None
    if vllm is not None:
        vllm.start()
    detector = TextAnchoredDetector(container.settings)
    detector.preflight()
    logger.info("worker_model_ready", extra={"model_id": container.settings.model_id})
    pipeline = WorkerPipeline(
        container.settings,
        container.repository,
        container.blobs,
        container.queue,
        detector,
    )
    # signal handlers and loop unchanged
```

- [ ] **Step 4: Delete the dead modules and their tests**

```bash
git rm src/taxhance_pii/worker/model.py src/taxhance_pii/redaction/tiling.py \
       src/taxhance_pii/redaction/policy.py src/taxhance_pii/redaction/detectors.py \
       tests/test_tiling.py tests/test_policy.py tests/test_model_output.py tests/test_detectors.py
```

Then grep for leftovers: `rg "detect_patterns|apply_redaction_policy|VisionDetector|tiling|model_audit" src tests` — every hit must be fixed or removed. `security.content_fingerprint` loses its only caller: check `rg "content_fingerprint" src tests`; if only `security.py` + its test remain, keep them (fingerprint stays an optional manifest field).

- [ ] **Step 5: Fix the remaining tests** — in `tests/conftest.py` and the API/worker tests, replace `NoopVisionDetector` with `NoopDetector` (import from `taxhance_pii.worker.detector`). Run the FULL suite:

Run: `./.venv/Scripts/python.exe -m pytest -x -q`
Expected: all green.

- [ ] **Step 6: Lint and commit**

```bash
./.venv/Scripts/python.exe -m ruff check src tests
git add -A
git commit -m "feat: wire text-anchored detector into worker; delete VLM/regex stack"
```

---

### Task 10: Worker container image

**Files:**
- Rewrite: `docker/worker.Dockerfile`
- Create: `docker/worker-entrypoint.sh`
- Modify: `infra/compute.tf:186-212` (env vars in the task definition), `infra/variables.tf` (drop `model_backend`, `model_dtype`, `llama_model_file`, `llama_mmproj_file` variables; retarget `model_id` default to `google/gemma-4-E2B-it`)

**Interfaces:**
- Consumes: the worker package (Task 9), `vllm serve` CLI.
- Produces: an image whose entrypoint starts the worker process (`python -m taxhance_pii.worker.main`), which itself launches vLLM (`vllm_launch=True`).

- [ ] **Step 1: Rewrite the Dockerfile**

```dockerfile
# docker/worker.Dockerfile
FROM vllm/vllm-openai:v0.28.0

RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 10001 --create-home worker
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN pip install --no-cache-dir . rapidfuzz
COPY docker/worker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
USER 10001:10001
ENTRYPOINT ["/entrypoint.sh"]
```

```bash
# docker/worker-entrypoint.sh
#!/usr/bin/env bash
set -euo pipefail
exec python -m taxhance_pii.worker.main
```

- [ ] **Step 2: Update the ECS task definition environment** in `infra/compute.tf` — delete the `PII_MODEL_BACKEND`, `PII_MODEL_DTYPE`, `PII_LLAMA_MODEL_FILE`, `PII_LLAMA_MMPROJ_FILE`, `PII_MODEL_AUDIT_PASSES` entries; keep `PII_MODEL_ID` / `PII_MODEL_REVISION` (now defaulting to gemma) and the cache-dir env block unchanged; add:

```
      { name = "PII_VLLM_LAUNCH", value = "true" },
      { name = "PII_VLLM_PORT", value = "8000" },
      { name = "PII_DETECTOR_CONCURRENCY", value = "8" },
```

- [ ] **Step 3: Build locally to prove the image assembles**

Run: `docker build -f docker/worker.Dockerfile -t pii-worker:dev .`
Expected: image builds (model download happens at runtime into the mounted cache volume).

- [ ] **Step 4: Terraform validate**

Run: `cd infra && tofu validate` (or `terraform validate`)
Expected: valid.

- [ ] **Step 5: Commit**

```bash
git add docker infra
git commit -m "feat: vLLM-based worker image and task definition"
```

---

### Task 11: Infra — 14h warm window, spot-first, overnight scale-to-zero

**Files:**
- Modify: `infra/compute.tf` (`aws_autoscaling_group.worker` block at lines 74-131 + new resources)
- Modify: `infra/variables.tf` (new schedule variables)
- Modify: `infra/monitoring.tf` (queue-depth alarm)

**Interfaces:**
- Consumes: existing `aws_autoscaling_group.worker[0]`, `aws_sqs_queue.jobs`.
- Produces: Terraform resources — names below are load-bearing for later maintenance: `aws_autoscaling_schedule.warm_window_start`, `aws_autoscaling_schedule.warm_window_end`, `aws_autoscaling_policy.offhours_scale_up`, `aws_cloudwatch_metric_alarm.offhours_queue_depth`.

- [ ] **Step 1: Switch the ASG to spot-first.** In the `instances_distribution` block (compute.tf:88-92) replace with:

```hcl
    instances_distribution {
      on_demand_base_capacity                  = 0
      on_demand_percentage_above_base_capacity = 0
      spot_allocation_strategy                 = "price-capacity-optimized"
    }
```

and add `capacity_rebalance = true` at the ASG top level. Ensure `local.worker_instance_types` contains at least `["g6.xlarge", "g5.xlarge"]` (check `locals`; extend if shorter).

- [ ] **Step 2: Add schedule variables** to `infra/variables.tf`:

```hcl
variable "warm_window_start_cron" {
  description = "UTC cron for scale-up to 1 (07:00 ET = 11:00/12:00 UTC depending on DST; pick 11:00 UTC)."
  type        = string
  default     = "0 11 * * *"
}

variable "warm_window_end_cron" {
  description = "UTC cron for overnight scale-to-zero (21:00 ET ≈ 01:00 UTC)."
  type        = string
  default     = "0 1 * * *"
}
```

- [ ] **Step 3: Add the scheduled actions + off-hours alarm** (new file section in compute.tf):

```hcl
resource "aws_autoscaling_schedule" "warm_window_start" {
  count                  = var.deploy_application ? 1 : 0
  scheduled_action_name  = "warm-window-start"
  autoscaling_group_name = aws_autoscaling_group.worker[0].name
  recurrence             = var.warm_window_start_cron
  min_size               = 0
  max_size               = 2
  desired_capacity       = 1
}

resource "aws_autoscaling_schedule" "warm_window_end" {
  count                  = var.deploy_application ? 1 : 0
  scheduled_action_name  = "warm-window-end"
  autoscaling_group_name = aws_autoscaling_group.worker[0].name
  recurrence             = var.warm_window_end_cron
  min_size               = 0
  max_size               = 2
  desired_capacity       = 0
}

resource "aws_autoscaling_policy" "offhours_scale_up" {
  count                  = var.deploy_application ? 1 : 0
  name                   = "offhours-queue-scale-up"
  autoscaling_group_name = aws_autoscaling_group.worker[0].name
  policy_type            = "SimpleScaling"
  adjustment_type        = "ExactCapacity"
  scaling_adjustment     = 1
  cooldown               = 600
}

resource "aws_cloudwatch_metric_alarm" "offhours_queue_depth" {
  count               = var.deploy_application ? 1 : 0
  alarm_name          = "${local.prefix}-offhours-queue-depth"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.jobs.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  alarm_actions       = [aws_autoscaling_policy.offhours_scale_up[0].arn]
}
```

Also set the ASG's own `desired_capacity` handling so Terraform applies don't fight the schedules: add `ignore_changes = [desired_capacity]` in a `lifecycle` block on `aws_autoscaling_group.worker`.

- [ ] **Step 4: Validate** — `cd infra && tofu validate` → valid. Then `tofu plan` and eyeball: only the intended resources change.

- [ ] **Step 5: Commit**

```bash
git add infra
git commit -m "feat: spot-first ASG with 14h warm window and off-hours queue scale-up"
```

Note: the spec's spot→on-demand fallback Lambda is deliberately deferred to its own follow-up task after the first week of spot-interruption data — `price-capacity-optimized` across two instance families makes total dry-up rare, and the queue retry loop already absorbs interruptions. Record this as an open item in the spec when closing the plan.

---

### Task 12: Evaluation on the private corpus

**Files:**
- Modify: `src/taxhance_pii/evaluation/runner.py` + `evaluation/cli.py` (drive the new detector; HTML review sheet; zero-SSN output check)
- Test: `tests/test_evaluation_runner.py` (update to new interfaces)

**Interfaces:**
- Consumes: `TextAnchoredDetector.detect_document`, `load_document`, `render_redacted_pdf`, `ocr_words`, `ssn_safety_net`.
- Produces: CLI `python -m taxhance_pii.evaluation.cli run --docs <dir> --out <dir>` that writes per-doc `*.redacted.pdf`, `review.html` (original|redacted page images side by side), `metrics.json` (`pages`, `wall_seconds`, `pages_per_hour`, `residual_ssn_boxes` — must be 0).

The zero-SSN acceptance check (exact code for the runner):

```python
def residual_ssn_boxes(redacted_pdf: Path, dpi: int, max_pages: int) -> int:
    pages = load_document(redacted_pdf, ".pdf", dpi, max_pages, True)
    try:
        return sum(len(ssn_safety_net(p.page_index, p.words)) for p in pages)
    finally:
        for p in pages:
            p.image.close()
```

- [ ] **Step 1:** Update `runner.py` to call `detector.detect_document(pages)` once per document (delete per-page audit logic), collect wall time around detection only, and emit the three outputs above. Keep `aws_runner.py` untouched (it shells the CLI remotely).
- [ ] **Step 2:** Update `tests/test_evaluation_runner.py` to drive the runner with `NoopDetector` and assert `metrics.json` shape and `review.html` existence.
- [ ] **Step 3:** Run: `./.venv/Scripts/python.exe -m pytest tests/test_evaluation_runner.py -v` → PASS.
- [ ] **Step 4:** Hand-pick ~40 documents from `C:\Users\pujan\Downloads\all-clients` (spread: W-2, 1099-DIV/INT/R/G/SA, 1098, K-1 if present, brokerage) into `private-evaluation/corpus/` (gitignored — verify with `git status`).
- [ ] **Step 5:** Run the evaluation on the AWS spike/eval box; require: `residual_ssn_boxes == 0`, `pages_per_hour >= 1000`; then human review of `review.html`.
- [ ] **Step 6: Commit** (code only — never the corpus or results):

```bash
git add src/taxhance_pii/evaluation tests/test_evaluation_runner.py
git commit -m "feat: evaluation runner for text-anchored pipeline with zero-SSN gate"
```

---

### Task 13: Documentation sweep

**Files:**
- Modify: `docs/ARCHITECTURE.md`, `docs/REDACTION_POLICY.md`, `docs/OPERATIONS.md`, `README.md`
- Modify: `docs/superpowers/specs/2026-08-27-text-anchored-redaction-design.md` (status → implemented; record the deferred spot-fallback Lambda as an open item)

- [ ] **Step 1:** Rewrite the detection sections of `ARCHITECTURE.md` to describe: extract → gate → grid → gemma-4-E2B-it on vLLM → anchor → propagate → SSN net → paint. Remove every mention of tiling, GBNF, audit passes, form-type prompts, llama.cpp, Qwen-27B.
- [ ] **Step 2:** Update `OPERATIONS.md` with the ASG schedule (14h warm / overnight scale-to-zero, spot-first), the off-hours cold-start behavior, and the vLLM health gate.
- [ ] **Step 3:** `REDACTION_POLICY.md`: align category list with `CATEGORY_MAP` and the client-side-only policy.
- [ ] **Step 4:** Run the full suite + lint one final time: `./.venv/Scripts/python.exe -m pytest -q && ./.venv/Scripts/python.exe -m ruff check src tests`.
- [ ] **Step 5: Commit**

```bash
git add docs README.md
git commit -m "docs: describe text-anchored pipeline and new infra schedule"
```

---

## Self-Review Notes

- **Spec coverage:** extraction/gate (T2,T3), grid (T1), prompt+schema (T4), anchor/fuzzy/net/merge (T5), settings (T6), serving (T7), detector incl. propagation & re-OCR retry (T8), pipeline/deletions/residual check (T9), image (T10), infra schedule/spot (T11), benchmark & acceptance (T12), docs (T13). Spot-fallback Lambda: deliberately deferred, recorded in T11/T13.
- **Types:** `WordBox(text, box: BoundingBox, source)` and `PageArtifact(page_index, image, words)` used consistently; detector protocol name `DocumentDetector`; the pipeline constructor keeps its five-argument shape so API tests only swap the detector class.
- **Placeholder scan:** the only intentionally deferred item is the spot-fallback Lambda, called out explicitly with rationale — not a TBD.
