# [Taxhance PII Redaction](https://taxhance.com/pii-redaction)

Remove personal information from tax documents before sharing them. Taxhance PII
Redaction detects client identifiers, lets you review and edit the redactions, and
exports a new PDF with the covered information removed.

Built for CPAs and tax firms working with returns, W-2s, 1099s, K-1s, and scanned
client documents. Use the hosted tool or run the same pipeline on your own servers.

**[Use PII Redaction](https://taxhance.com/pii-redaction)** ·
[Self-hosting](#local-self-hosting) ·
[Documentation](#documentation) ·
[taxhance.com](https://taxhance.com)

## What you can do

- **Redact PDFs and scans.** Upload PDFs, PNGs, JPEGs, or TIFFs.
- **Process a batch.** Select multiple files or a folder, then download individual
  results or a ZIP.
- **Review every box.** Add or remove redactions, rotate the whole document, and
  reopen completed batch documents for another review.
- **Keep useful tax data.** The default policy targets client information while
  preserving amounts and issuer details.
- **Run it yourself.** Self-host with Docker Compose on an NVIDIA GPU server or
  deploy to your own AWS account.

## Use the hosted tool

1. Open [Taxhance PII Redaction](https://taxhance.com/pii-redaction).
2. Upload a document, multiple files, or a folder.
3. Review the proposed redactions, or use automatic redaction for a batch.
4. Download the redacted PDFs individually or as a ZIP.

Single-document uploads open manual review. Batches default to automatic redaction;
choose **Review each document** before uploading if you want to approve every file.
You can also select **Review manually** on a finished batch document to change its
boxes and rebuild the PDF without running detection again.

Automatic detection can miss information. Check the results before sharing, especially
if you need to hide fields that the default policy keeps visible.

### Working with batches

The browser processes two documents at a time and shows progress, previews, and retry
options in one table. Failed files stay visible for retry and are excluded from downloads.
Capacity limits pause the waiting queue.

Keep the tab open until your downloads finish. Completed PDFs and files waiting to
upload are held in that tab; refreshing loses those local copies. In-flight server
jobs can be recovered from tab-scoped access tokens. Filenames and document contents
are not saved in browser storage.

ZIP downloads use neutral numbered filenames matching the document table and must
total less than 4 GB. Individual downloads remain available. Default limits are
100 documents per hour and five active jobs per network; self-hosted installations
can adjust these in [`.env.example`](.env.example).

## Default redaction policy

The policy distinguishes the client from the organization issuing the document.
For example, a taxpayer's name and SSN are targets for redaction, while the employer's
name and EIN on a W-2 stay visible.

| Redacted automatically | Kept visible by default |
| --- | --- |
| Names of taxpayers, spouses, dependents, employees, and beneficiaries | Payer, employer, issuer, lender, and financial-institution details |
| Client SSNs, ITINs, and TINs, including masked values | Issuer and employer EINs |
| Client street addresses, unit lines, and PO boxes; mortgage property street addresses | City, state, ZIP, and postal codes |
| Private client identifiers, such as member, payroll, and policy IDs | Account numbers |
| Personal email addresses, phone numbers, and dates of birth or death | Amounts, tax years, form numbers, OMB numbers, and control numbers |

Any value shaped like an SSN (`123-45-6789`), including masked forms, is targeted
regardless of its label. You can add or remove boxes in the review screen to suit
the document you need to share.

See the [complete redaction policy](docs/REDACTION_POLICY.md) for the full rules,
role distinctions, and validation checks.

## How redaction works

1. **Read the page.** Extract text and word positions from the PDF, using OCR for scans.
2. **Detect client information.** A local language model identifies exact text strings
   in a layout-preserving representation of each page.
3. **Place the boxes.** Match those strings to PDF or OCR word positions. The model
   identifies text; the pipeline calculates the coordinates.
4. **Check identifiers.** An independent rule catches SSN-shaped values, including
   partially masked numbers.
5. **Build a new PDF.** Apply the boxes to rendered page images and export a PDF
   containing only those images. The original text layers, annotations, and source
   metadata are not carried into the output.

The pipeline checks the rendered output again for SSN-shaped values. Invalid model
responses, unreadable pages, or failed identifier checks prevent a job from being
marked complete.

The default model is `google/gemma-4-E2B-it`, served by vLLM on the deployment's own
GPU. Document inference does not use a third-party AI API. See the
[architecture guide](docs/ARCHITECTURE.md) for implementation details.

## Privacy and file retention

- **Processing stays within the deployment.** A local installation stores and processes
  documents on your server. The AWS deployment uses a dedicated document bucket and
  GPU worker in the account running the service.
- **Each job has its own access token.** Redacting documents requires no account,
  and there is no shared job index.
- **Logs exclude document contents.** Application logs use opaque job IDs and counters,
  without filenames, document text, prompts containing that text, access tokens, or
  detected personal information.
- **Downloads trigger deletion.** For a single document, the browser receives the full
  PDF, starts the local save, and requests deletion of the server copy. It cannot
  verify that the operating system finished saving the file.
- **Batch results remain available for review until downloaded.** Server copies are
  deleted when you download a document individually or in a ZIP, or clear the batch.
- **Retention is limited to one hour from job creation.** A separate cleanup task
  removes remaining job data. S3 lifecycle rules and DynamoDB TTL provide additional
  cleanup safeguards in AWS. The delete action removes inputs, outputs, and previews.
- **Usage analytics contain aggregate counts.** These cover visits, page views,
  document activity, batch sizes, and processing time. They contain no raw IPs,
  filenames, document contents, or traffic sources. Analytics access is restricted
  to a separate owner account. See the [analytics data policy](docs/ANALYTICS.md).

This service has separate code, storage, database, encryption keys, access roles,
network boundaries, and deployment state from Taxhance AutoKey.

Read the [threat model](docs/THREAT_MODEL.md) and
[security reporting guide](SECURITY.md) for more detail.

## Local self-hosting

Run the full pipeline on your own servers or in a cloud account you control.
You need Docker with Compose, an NVIDIA GPU, and NVIDIA Container Toolkit.

```bash
git clone https://github.com/pujan789/pii-redaction.git
cd pii-redaction
cp .env.example .env
```

Edit `.env` and replace `PII_TOKEN_PEPPER` with a secret containing at least
32 random characters. Then start the services:

```bash
docker compose up --build
```

Open [localhost:8080](http://localhost:8080) when the services are ready. The first
worker start downloads the model weights, which are not bundled with this repository.

If staff will access the app from other computers, set `PII_PUBLIC_BASE_URL` and
`PII_ALLOWED_ORIGINS` in `.env` to the address they will open, such as
`http://redaction.office.local:8080`. Every `PII_*` setting in `.env` is passed to
the containers; see [the configuration reference](.env.example) for limits, OCR,
and vLLM settings.

The default Gemma model revision is pinned to
`3e22461f65e89153144f8adb70e3b8c2cc9845a7` for reproducibility. To use the benchmark
alternative, set the following values in `.env` before starting Compose:

```dotenv
PII_MODEL_ID=Qwen/Qwen3.5-4B
PII_MODEL_REVISION=<pin the exact commit sha>
```

See [self-hosting costs and performance](docs/SELF_HOSTING.md) for the AWS reference
configuration, measured throughput, and infrastructure cost examples.

### Installation help

I'm Pujan, the developer behind this project. I offer installation help on your
firm's servers or cloud account at **$100/hour (USD)**. Email me at
[pujan@taxhance.com](mailto:pujan@taxhance.com?subject=PII%20Redaction%20self-hosting%20installation)
with your setup and expected document volume.

The software is free under the [project license](LICENSE); hardware and hosting
costs are separate. You can find more of my work at [taxhance.com](https://taxhance.com).

## Development

For CPU-only API development:

```bash
uv sync --extra worker --extra dev
uv run uvicorn taxhance_pii.api.main:app --reload
```

This starts the API. Running the full detection pipeline also requires the GPU worker.
See [CONTRIBUTING.md](CONTRIBUTING.md) for checks and contribution guidelines. Use
synthetic documents in tests and bug reports; never include real taxpayer information.

## AWS deployment

The [OpenTofu configuration](infra/) provisions CloudFront/WAF, a static S3 web
origin, API Gateway and Lambda, DynamoDB, SQS, an encrypted document bucket, and a
GPU EC2 worker.

Start with the [operations guide](docs/OPERATIONS.md) for deployment roles, GPU
quotas, capacity settings, cleanup, and evaluation procedures. The reference worker
uses `g6.xlarge` with an NVIDIA L4, with `g5.xlarge` and an NVIDIA A10G as a capacity
fallback. Both have 24 GB of GPU memory.

## Documentation

| Guide | What it covers |
| --- | --- |
| [Redaction policy](docs/REDACTION_POLICY.md) | What automatic detection removes and preserves |
| [Self-hosting](docs/SELF_HOSTING.md) | Hardware, reference costs, and measured performance |
| [Architecture](docs/ARCHITECTURE.md) | API, storage, detection, and rendering design |
| [Operations](docs/OPERATIONS.md) | AWS deployment and maintenance |
| [Threat model](docs/THREAT_MODEL.md) | Privacy boundaries, risks, and safeguards |
| [Evaluation](docs/EVALUATION.md) | How redaction quality is measured |
| [Security](SECURITY.md) | How to report a vulnerability |
| [Contributing](CONTRIBUTING.md) | Development checks and test data requirements |

### Repository map

| Directory | Contents |
| --- | --- |
| [`src/taxhance_pii/`](src/taxhance_pii/) | API, storage adapters, redaction pipeline, and GPU worker |
| [`frontend/`](frontend/) | Landing page and React redaction app |
| [`infra/`](infra/) | OpenTofu modules for AWS deployment |
| [`tests/`](tests/) | Unit and integration tests with synthetic documents |
| [`scripts/`](scripts/) | Deployment and private evaluation helpers |
| [`docs/`](docs/) | Design, operations, and evaluation guides |

## License

Licensed under the [GNU Affero General Public License v3.0](LICENSE)
(`AGPL-3.0-only`), with the
[Taxhance Noncommercial Private-Use Exception](LICENSE-EXCEPTION.md).

The exception allows private noncommercial modifications to remain private.
Commercial users do not receive this exception; commercial network deployments
of modified versions must offer users the complete corresponding source as required
by the AGPL. See the linked license files for the full terms. Model weights are
distributed separately under their own licenses.
