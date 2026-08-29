# Private evaluation protocol

Real client documents are never committed, attached to issues, uploaded to a third-party
inference provider, or included in CI.

## Fixed sample

`pii-evaluate sample` reads the local corpus and writes an ignored manifest containing
50 unique-content PDFs. Sampling is stratified by page-count band and whether pages are
digital, scanned, or mixed. Thirty documents form the tuning set. Twenty documents are
held out and must not be opened, prompted against, or used for code changes until the
pipeline and prompt are frozen.

## Tuning gate

For all 30 tuning documents:

- process every page with deterministic detection, Qwen grounding, and one residual pass;
- verify the output is raster-only and every requested box is opaque;
- rerun OCR patterns and Qwen's adversarial auditor on the redacted output;
- inspect private original/redacted contact sheets for missed PII and harmful false positives;
- verify payer information, account numbers, payer/borrower TINs, public form references,
  and city/state/postal data remain visible under `REDACTION_POLICY.md`;
- record only counts, categories, timing, prompt version, and pass/fail in aggregate reports.

Prompt or detector changes restart the 30-document tuning run. They do not consume the
holdout.

## Holdout gate

After freezing the prompt, model revision, thresholds, tiling, and detector code, run all
20 holdout documents once. A release candidate fails if any page cannot render, any
deterministic identifier remains, the auditor finds visible PII, a requested box is not
opaque, or private visual review finds an unredacted sensitive value.

This protocol measures observed performance on the corpus; it does not justify a claim
that future documents will be perfectly redacted.

## Private AWS execution

The production worker role cannot read the private evaluation prefix. A separate ECS task
role can access only `private-evaluation/*`, the project KMS key, and the token pepper. The
local staging command replaces filenames with opaque evaluation IDs and verifies every
file's size and SHA-256 before upload. The GPU task repeats the integrity check, runs the
same pinned detector, and writes completion last. Fetching results verifies that marker,
downloads into `private-results/`, and deletes the exact validated remote prefix.

Never stage both splits together. Never add client filenames, extracted text, contact
sheets, manifests, redacted PDFs, or per-document results to Git.
