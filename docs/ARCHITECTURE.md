# Architecture

## Trust boundary

The public browser receives a random per-job token once. Only its keyed hash is stored.
The browser uploads directly to a dedicated encrypted S3 bucket, while job state lives
in a dedicated DynamoDB table. The token is required before status, preview, manifest,
download, finalize, or delete operations.

```text
Browser
  ├─ static assets ──> CloudFront ──> private web bucket
  ├─ job API ────────> CloudFront/WAF ──> API Gateway ──> Lambda
  └─ signed upload ─────────────────────────────────────> private data bucket
                                                           │
Lambda ──> DynamoDB + SQS                                  │
                     │                                     │
                     └──> isolated GPU ECS/EC2 worker <────┘
                              │
                              └── pinned local text LLM (vLLM) + OCR + deterministic renderer
```

No TaxHance Autokey database, storage account, queue, key, role, network, deployment
state, or application code is imported. Sharing the same AWS payer/account does not
grant either application access to the other.

## Processing stages

1. Validate declared type, byte length, and magic bytes.
2. Render every PDF page to RGB pixels. Active PDF content is never executed.
3. Extract digital words with their geometry when present; a text-quality gate
   discards mojibake text layers and falls back to Tesseract OCR.
4. Serialize each page into a layout-preserving text grid: rows clustered by
   y-position, columns spaced by x-position, so label→value adjacency survives.
5. Send the grid to the pinned local text LLM (`google/gemma-4-E2B-it` on a
   loopback-only vLLM server) with one universal prompt. The model returns the
   exact client-side PII strings — never coordinates.
6. Anchor every returned string back to word geometry (all occurrences, fuzzy
   fallback for OCR noise); propagate values found anywhere in the document
   across all of its pages; add the deterministic SSN-shape safety net; merge
   overlapping boxes.
7. Store raster previews and a text-free box manifest for human review.
8. After manual approval, or automatic finalization selected for a batch, paint opaque
   black rectangles, turn every page by the rotation chosen during review, and encode a
   new image-only PDF. A completed job may be finalized again with an edited manifest
   (boxes and rotation); the worker re-renders from the stored source and overwrites the
   output, and the job is treated as human-reviewed from then on.
9. Reopen the output and fail unless page count, absent text layer/active content, and
   opaque redaction pixels all verify.
10. OCR the flattened output and fail closed if an SSN-shaped identifier
    remains visible.

The automatic inclusion/exclusion contract is defined in `REDACTION_POLICY.md`.
Boxes always come from PDF/OCR geometry, so they are pixel-accurate by
construction; the model's only job is deciding which printed values are
client-side. Evaluation checkpoints include the model id, revision, prompt and
detector versions so artifacts from two configurations cannot be mixed.

The output intentionally sacrifices searchable text and accessibility in favor of
strong removal guarantees. A future OCR layer must never be added without proving that
redacted strings cannot re-enter the PDF.

## Retention

The application deadline is 55 minutes after creation. A scheduled cleanup runs every
five minutes, so deletion occurs no later than the stated one-hour limit even at the
worst schedule boundary. In single-document mode a successful full-PDF fetch triggers
immediate deletion; in batch mode the browser deletes a job when its PDF is downloaded
(alone or in the ZIP) or when the batch is cleared, keeping finished documents available
for manual review until then.
The browser can prove receipt of the bytes but cannot prove that the operating system
completed its save dialog. Explicit deletion tombstones the job before deleting its entire object prefix so a
concurrent worker cannot recreate a downloadable result. S3 lifecycle and DynamoDB TTL
are fallback controls, not the primary deadline mechanism.
