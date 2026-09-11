# TaxHance PII Redaction

An auditable, self-hostable PII redaction service designed for CPA and tax-document
workflows. Detection is text-anchored: each page is serialized into a
layout-preserving text grid, a pinned local text LLM (`google/gemma-4-E2B-it`
served by vLLM) names the client-side PII strings, and redaction boxes come from
PDF/OCR word geometry — the model never draws coordinates. A deterministic
SSN-shape safety net backstops the model, and the output is a new raster-only
PDF so covered text, hidden layers, annotations, and source metadata are not
recoverable.

This repository is intentionally isolated from TaxHance Autokey. It has its own code,
storage, database, encryption key, IAM roles, network boundary, and deployment state.

## Privacy contract

- Documents stay on the selected host. The production design sends them only to the
  dedicated S3 bucket and GPU worker in your AWS account.
- Application logs contain opaque job IDs and counters, never filenames, document
  text, model prompts containing document text, access tokens, or detected PII.
- Each job has an unguessable bearer token. There are no user accounts or shared job
  indexes.
- The delete action removes input, output, and previews immediately. An independent
  cleanup task removes remaining job data no later than one hour after job creation;
  S3 lifecycle and DynamoDB TTL are defense-in-depth fallbacks.
- After the complete result reaches the browser, the client starts the local save and
  immediately calls the authenticated delete endpoint. It cannot prove the operating
  system saved the file, but it deletes only after the full PDF was received successfully.
- Redaction is fail-closed: malformed model output, unreadable pages, or residual
  deterministic identifiers prevent a document from being marked complete.

No automated system can promise perfect PII recognition. The UI asks clients to check
automatic results before sharing. Manual review exposes each proposed redaction for editing.

## Batch workflow

Selecting multiple files or a folder defaults to automatic redaction. The browser runs
two jobs at a time, receives each completed PDF, and deletes its server copy before
advancing. Clients see one document table with progress, optional previews, failure
retries, and a single ZIP download. Files that fail are excluded from downloads and
remain visible for retry; capacity limits pause the waiting queue. The default hourly
allowance is 100 documents per network, with the existing five-active-job limit intact.

Completed PDFs and waiting source files stay in this tab only. Keep it open until the
download finishes: refreshing loses those local files. In-flight server jobs can be
recovered from their tab-scoped tokens; filenames and document contents are not persisted
in browser storage. ZIP exports use neutral numbered filenames matching the document
table and must total less than 4 GB; individual downloads remain available.

Choose **Review each document** before uploading to retain the manual editing workflow.
Single-file uploads continue to use manual review.

## Default redaction policy

The automatic policy is recipient/client-side by design. It redacts private recipient,
taxpayer, spouse, dependent, employee, and beneficiary information: person names,
SSN/ITIN/TIN values (including masked forms), the street line of the client's
address, private client identifiers, personal email/phone, and dates of
birth/death. It intentionally keeps payer/payor, employer, issuer, and
financial-institution information visible — names, addresses, phones, and EINs —
along with account numbers, amounts, public form/OMB/control numbers, and the
city/state/ZIP portion of any address. Any SSN-shaped value (3-2-4) is always
redacted regardless of label, which is the fail-closed direction on forms whose
labels are ambiguous about whose number a field holds.

These are defaults, not restrictions: the review screen can remove any proposed box or
add a manual box over information the operator chooses to hide. The complete, testable
policy is documented in [`docs/REDACTION_POLICY.md`](docs/REDACTION_POLICY.md).

## Repository map

```text
src/taxhance_pii/       API, storage adapters, redaction pipeline, and GPU worker
frontend/               Marketing landing page (site root) and React redaction app (/app)
infra/                  OpenTofu modules for an isolated AWS deployment
tests/                  Unit and integration tests using synthetic documents only
scripts/                Deployment and private-corpus evaluation helpers
docs/                   Threat model, operations, and evaluation protocol
```

## Local self-hosting

Larger firms can run the full pipeline on their own servers or in their own cloud
account. For installation assistance at **$100/hour (USD)**, contact Pujan at
[pujan@taxhance.com](mailto:pujan@taxhance.com?subject=PII%20Redaction%20self-hosting%20installation).
The software is free under the project license; hardware and hosting are separate.
See [self-hosting costs and performance](docs/SELF_HOSTING.md) for our AWS reference
costs and measured page throughput.

Requirements: Docker with Compose and an NVIDIA GPU with the Container Toolkit.

```bash
cp .env.example .env
# Set PII_TOKEN_PEPPER to a random value.
docker compose up --build
```

Open `http://localhost:8080`. If staff will use the app from other computers, set
`PII_PUBLIC_BASE_URL` and `PII_ALLOWED_ORIGINS` in `.env` to the address they will open
(for example `http://redaction.office.local:8080`); uploads are sent to that address.
Every `PII_*` value in `.env` reaches the containers, so the limits and vLLM settings
documented in `.env.example` can be tuned there. The first worker start downloads the pinned
`google/gemma-4-E2B-it` revision from Hugging Face (Apache-2.0). Model weights
are not bundled with this repository; the default revision is pinned to commit
`3e22461f65e89153144f8adb70e3b8c2cc9845a7` for reproducibility.

To run the benchmark alternate (`Qwen/Qwen3.5-4B`) instead, set these values in
`.env` before starting Compose — no code change, same pipeline:

```dotenv
PII_MODEL_ID=Qwen/Qwen3.5-4B
PII_MODEL_REVISION=<pin the exact commit sha>
```

For CPU-only API development:

```bash
uv sync --extra worker --extra dev
uv run uvicorn taxhance_pii.api.main:app --reload
```

## AWS deployment

The AWS design uses CloudFront/WAF, a static S3 web origin, API Gateway + Lambda,
DynamoDB, SQS, a dedicated encrypted document bucket, and one GPU EC2 worker. See
`docs/OPERATIONS.md` before applying `infra/`.

Infrastructure is reproducible with OpenTofu. Do not run the application as the AWS
root principal; root is used only to bootstrap a scoped deployment role.

The deployed worker prefers `g6.xlarge` with one 24 GB NVIDIA L4 and can use
`g5.xlarge` with one 24 GB NVIDIA A10G when regional capacity requires it. The API,
queue, database, storage, and static frontend are deliberately serverless and
usage-based. Pricing and taxes can change, so verify the AWS Price List before each
deployment. AWS must approve at least four vCPUs for the "Running On-Demand G and VT
instances" quota before the worker can launch.

The deployment helper accepts independent `-GpuCapacityDesiredCount` and
`-WorkerDesiredCount` switches. Set both to `0` to keep the public application deployed
without launching a GPU. For private evaluation, use GPU capacity `1` and production
workers `0`, giving the standalone evaluation task exclusive use of the GPU.
An explicitly authorized model comparison may temporarily set GPU capacity to `2` while
production workers remain `0`; the Auto Scaling group has a hard maximum of two.

## License

This project is licensed under the GNU Affero General Public License, version 3
(`AGPL-3.0-only`), with the
[Taxhance Noncommercial Private-Use Exception](LICENSE-EXCEPTION.md). You may inspect,
modify, and self-host it. Private noncommercial modifications can remain private.
Commercial network deployments do not receive that exception and must prominently
offer users the complete Corresponding Source of the deployed version under the AGPL.

See [`LICENSE`](LICENSE) and [`LICENSE-EXCEPTION.md`](LICENSE-EXCEPTION.md) for the
controlling terms. This is a practical project description, not legal advice; have
counsel review the exception before relying on it commercially.
