# Text-Anchored PII Redaction — Design

Date: 2026-08-27
Status: approved in discussion; pending final spec review

## Problem

The current detection stack asks a vision model to draw bounding boxes on page
images. Small VLMs localize poorly, so the system accumulated compensating
machinery: five form-specific prompt profiles (W-2 / 1099 / 1098 / K-1) with
skip-model shortcuts, ~1,100 lines of form-specific regex rules, ~800 lines of
policy code, image tiling, GBNF grammar generation, adversarial audit passes,
and two model backends (transformers Qwen-27B 4-bit and llama.cpp GGUF). It is
hard to reason about, hard to tune, and the model at its core is not low-end.

## Goals

- One universal detection path: every page of every document goes through the
  same model with the same prompt. No form-type routing, no skip-model paths.
- Good accuracy on digitally-produced PDFs and clean scans. Mobile photos and
  poor scans are explicitly out of scope.
- Throughput ≥ 1,000 pages/hour sustained on one GPU (target 3–6k).
- Low-end open-weight model servable on a single 24 GB L4 (g6.xlarge).
- GPU cost: spot-first, 14 h/day warm window, scale-to-zero overnight.

## Non-goals

- Handwriting and signature detection (accepted miss; source docs rarely
  carry them).
- Mobile-photo / skewed-scan robustness.
- Multi-GPU serving, fine-tuning, or training.

## Redaction policy (unchanged in substance, simplified in expression)

Redact client/recipient-side values only:

- client person names (taxpayer, spouse, dependent, beneficiary, partner,
  shareholder, employee-as-recipient), including repeats
- client SSN / ITIN / recipient TIN, including masked forms (`***-**-1234`)
- first street-address line of the client block only (never city/state/ZIP)
- private client identifiers (policy, member, payroll, brokerage IDs)
- client personal email / phone, date of birth / death

Keep visible: payer / employer / issuer / lender names, addresses, phones,
EINs and payer TINs, account numbers, amounts, dates, form/OMB numbers,
city/state/ZIP everywhere.

## Architecture

```
PDF/image upload
  └─ document.py (existing, kept): render pages (pdfium) + extract words with
     boxes (pdfplumber text layer; Tesseract OCR when no text layer)
       └─ textgate.py (NEW): per-page text-layer quality gate
            garbage text layer → discard, OCR the rendered image instead
       └─ layout.py (NEW): words+boxes → layout-preserving text grid
            rows clustered by y-center, column offsets scaled from x
            (the page "looks like" the form in monospace text)
       └─ llm.py (NEW): one universal prompt + page grid → vLLM
            (OpenAI-compatible, localhost) → guided-JSON output:
            {"items": [{"text": "<exact string>", "category": "..."}]}
       └─ anchor.py (NEW): match each returned string to consecutive word
            sequences (normalized exact match, then fuzzy ≥ 90 for OCR noise)
            → box = union of matched word boxes (+ small pad)
            → every occurrence on the page is redacted
            → doc-level values (names, TINs) re-anchored across all pages
       └─ safety net (slimmed detectors.py, ~80 lines, form-agnostic):
            SSN-format values (3-2-4, incl. masked) always redacted.
            EIN format (2-7) never matches, so payer EINs are unaffected.
  └─ renderer.py (existing, kept): paint boxes, produce flattened PDF
```

The model never produces coordinates. Boxes always come from PDF/OCR
geometry, so they are pixel-accurate by construction.

### Text-quality gates (automatic re-OCR)

1. **Pre-model (deterministic).** A PDF can render perfectly while its
   embedded text layer is mojibake (broken font encodings). Per page, before
   trusting the text layer, check: ratio of plausible word tokens, letter vs
   symbol balance, U+FFFD density. Fail → drop the text layer, OCR the
   already-rendered page image. No extra render pass needed.
2. **Post-model.** If < 50% of the strings the model returned can be anchored
   to the page's words, the text the model saw does not match the page.
   Re-run that page once with OCR-derived text.

### Prompt

One system + one user prompt (~50 lines total) encoding the policy above.
Output contract: JSON `items` array of `{text, category}` where `text` is the
exact string as printed on the page. Enforced by vLLM guided JSON (structured
output), so parsing cannot fail; no GBNF, no repair-retry loops. Categories:
`client_name`, `client_tin`, `address_line`, `private_id`, `email`, `phone`,
`dob`. These map onto the existing `PiiCategory` enum for the manifest/UI.

### Model serving

- vLLM OpenAI-compatible server inside the worker container, localhost only.
- Candidates to benchmark (current generation as of 2026-08; all fit an L4
  in bf16/FP8; exact HF ids and revisions pinned at implementation time):
  1. Qwen3.5-4B (expected winner on speed)
  2. Qwen3.5-9B (field reports praise instruction following but call it
     lazy on extraction — the benchmark decides)
  3. gemma-4-E4B-it (different family as control; gemma-4-12B-it in FP8 is
     the fallback upgrade if all candidates disappoint on attribution)
  4. gemma-4-E2B-it (~2.3B effective; cheapest/fastest row — wins by
     default if it passes client-vs-payer attribution; verify vLLM
     support for the E-series architecture when pinning ids, else drop)
  5. LFM2.5-2.6B (LiquidAI) — TESTED 2026-08-27 spike, REJECTED: 11 of 17
     pages returned zero findings (names/streets left visible; SSN safety
     net had to carry 8 boxes) and ~5k chars of rambling output per page
     held it to 1,427 pages/hour despite its size. Also non-Apache LFM
     license. Spike results: docs/superpowers/specs plus
     private-results/spike/lfm25-26b (local only).
  6. MiniCPM-V-4.6 (openbmb, ~1.3B incl. vision tower) — TESTED 2026-08-27
     spike: does not load in vLLM 0.28 (minicpmv4_6 weight mapping
     unsupported). Not pursued further; below the viable size floor anyway.
- Model provenance rule: official publisher repos only (Qwen, Google, Meta,
  Mistral orgs), safetensors, pinned revision. No community finetunes,
  distills, "uncensored" variants, or third-party GGUF quants — this
  pipeline handles client SSNs; the supply chain stays first-party. If
  quantization is needed, use the publisher's FP8/AWQ release or quantize
  in-house with llm-compressor.
- All pages of a document submitted concurrently (async client) so vLLM's
  continuous batching keeps the GPU saturated.
- Deterministic decoding (temperature 0), pinned model revisions.

### Code removed

- `redaction/prompt.py` form routing, five form prompt pairs, audit prompts,
  skip-model shortcuts → replaced by one prompt module.
- `redaction/tiling.py`, GBNF grammar builder, `worker/model.py` transformers
  and llama.cpp backends and server lifecycle → replaced by an ~80-line
  OpenAI-client wrapper for localhost vLLM.
- Audit passes and painted-image re-inspection in `worker/pipeline.py`.
- `redaction/detectors.py` 1,079 → ~80 lines (SSN safety net only).
- `redaction/policy.py` 782 → ~100 lines (overlap merge + manifest assembly).

## Infrastructure

- **Warm window (14 h, default 07:00–21:00 ET, configurable):** ASG scheduled
  actions hold desired=1. Purchase: spot-first via mixed-instances policy,
  `price-capacity-optimized`, pools g6.xlarge + g5.xlarge across AZs, capacity
  rebalance on. If no spot pool can fill for ~10 min, a small alarm-driven
  Lambda flips the ASG to on-demand; it reverts to spot at the next scheduled
  scale event.
- **Overnight (10 h):** scheduled action sets desired=0 (kills spot or
  on-demand alike). A queue-depth alarm scales 0→1 when a job arrives
  (~3 min cold start), and back to 0 after sustained idle.
- Scale-in protection while a job is mid-flight (worker sets instance
  protection during processing).
- Worker image: CUDA base + vLLM replaces the llama.cpp build.
- Estimated GPU cost: ~$130–180/mo (spot for most of the 14 h window).

## Benchmark & acceptance (private corpus)

- Corpus: ~40 documents hand-picked from `C:\Users\pujan\Downloads\all-clients`
  spanning W-2, 1099-DIV/INT/R/G/SA, 1098, K-1, brokerage statements.
  Documents live only on the local machine and the private S3 evaluation
  bucket; never in git (`private-evaluation/`, `private-results/` ignored).
- Run all three candidate models via the existing AWS evaluation plumbing
  (`evaluation.tf`, `aws_runner.py`).
- Outputs per model: redacted PDFs in `private-results/`, an HTML side-by-side
  review sheet, measured pages/hour.
- Acceptance gates:
  1. Zero visible SSN-format values in any output (deterministic re-OCR
     check of rendered outputs).
  2. Manual review of the sheet: no missed client names / address lines the
     reviewer flags as unacceptable; payer/employer info intact.
  3. Sustained throughput ≥ 1,000 pages/hour on g6.xlarge; report actual.
- Winner is pinned in `Settings.model_id` + revision.

## Error handling

- vLLM unreachable / model load failure → worker preflight fails, job stays
  queued, instance recycled by ASG health check.
- Guided JSON guarantees parseable output; an empty `items` array is a valid
  result (page with no client PII).
- Anchor failure below threshold → one OCR retry (gate 2), then proceed with
  whatever anchored plus the SSN safety net; page flagged in the manifest so
  the review UI can highlight it.
- Spot interruption mid-job → SQS visibility timeout returns the job to the
  queue; replacement instance reprocesses idempotently.

## Testing

- Unit: layout grid serialization (fixture pages incl. two-column forms),
  anchor matching (exact, fuzzy, repeated values, cross-page), text-quality
  gate (mojibake fixtures), SSN safety net (formats, masked, EIN non-match).
- Integration: pipeline against a stub LLM server (canned JSON) — no GPU in CI.
- The private benchmark above is the accuracy gate; CI never sees client docs.
