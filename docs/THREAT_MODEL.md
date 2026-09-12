# Threat model

## Assets

- Original tax documents and raster previews
- Detected locations and redacted outputs
- Per-job access tokens and the token-hashing pepper
- Model and container supply chain
- AWS spending capacity

## Primary threats and controls

| Threat | Control |
|---|---|
| Guessing or enumerating another job | UUID job IDs plus independent 256-bit bearer tokens; indistinguishable 404 responses |
| Recovering covered PDF text | New raster-only PDF; no overlays, text layer, forms, annotations, or attachments |
| Logs becoming a second document store | No filenames/text/prompts/model outputs; disabled HTTP access logs; opaque IDs only |
| Upload parser exploit | Type/size/magic checks; isolated non-root worker; read-only container filesystem; no inbound GPU-host ports |
| Stale documents | 55-minute job deadline, five-minute cleanup, delete-after-download (single files and batch downloads), delete on batch clear, explicit delete, S3 lifecycle, DynamoDB TTL |
| Worker race after deletion | Tombstone first; status checks during processing; delete prefix if final conditional update loses |
| Model misses PII | OCR/regex signal, tiled model grounding, adversarial residual pass, manual review mode and guidance to check automatic batch results before sharing, fail-closed validation |
| Model hallucinates | Model never edits documents; strict schema; bounded boxes; retry then fail closed; user can remove boxes |
| Cost/denial-of-wallet attack | WAF limit of 2,000 requests per IP per five minutes, per-IP issuance and active-job limits, byte/page caps, bounded queue, API Gateway/account throttles, AWS Budget |
| Cross-project access | Dedicated KMS key, buckets, table, queue, roles, VPC, state, and tag boundary; no Autokey IAM actions |
| Supply-chain drift | Locked Python/npm dependencies, pinned model commit, pinned base images, CI scans, ECR image scanning |

The WAF request allowance includes CORS preflights, status polling, result retrieval,
and deletion for batches. The API separately limits job creation to 100 per IP per
hour and five active jobs, so a higher HTTP allowance does not increase the job budget.

## Explicit non-goals

- Claiming automated detection is perfect
- Preserving selectable text in the redacted result
- Accepting arbitrary Office archives or encrypted/password-protected PDFs in v0.1
- Using customer documents for training, analytics, support, or debugging
- Operating from the AWS root principal after the deployment role is bootstrapped
