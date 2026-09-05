# Self-hosting for firms

Run Taxhance PII Redaction on your firm's own GPU servers or in a cloud account you
control. The pipeline is open source; the [project license](../LICENSE) and
[noncommercial private-use exception](../LICENSE-EXCEPTION.md) apply. Model weights
have separate terms. Start with the [Docker Compose instructions](../README.md#local-self-hosting).

For larger firms that want help, Pujan can install the system on their own servers
at **$100/hour (USD)**. Email [pujan@taxhance.com](mailto:pujan@taxhance.com?subject=PII%20Redaction%20self-hosting%20installation)
with your server or cloud setup and expected document volume. Installation is billed
for time; infrastructure costs are separate.

## Our reference configuration

Live AWS configuration checked September 5, 2026:

- Model: `google/gemma-4-E2B-it`, revision `3e22461f65e89153144f8adb70e3b8c2cc9845a7`.
- Primary EC2 worker: `g6.xlarge`, one NVIDIA L4 with 24 GB GPU memory, 4 vCPUs and
  16 GiB system memory, in US East (N. Virginia).
- Capacity fallback: `g5.xlarge`, one NVIDIA A10G with 24 GB GPU memory. Its pricing differs.
- Inference runs in our AWS account on EC2 GPU instances, without a third-party AI API.

An existing suitable NVIDIA GPU server can run the Docker Compose deployment with
NVIDIA Container Toolkit. Your hardware, electricity, storage, and administration
remain your costs; there is no Taxhance software subscription.

## AWS compute cost examples

The AWS Price List API returned **$0.8048 per running hour** for Linux On-Demand
`g6.xlarge`, shared tenancy, in US East (N. Virginia), checked September 5, 2026.

| One GPU worker | Hours/month | Compute only, USD |
| --- | ---: | ---: |
| 8 hours per day, 22 workdays | 176 | $141.64 (about $142) |
| Running continuously | 730 | $587.50 (about $588) |

These are usage examples from our worker's unit price, **not an actual full monthly
bill**. They exclude storage, API services, networking, taxes, discounts, and
administration. Stopping the GPU avoids its running compute charges; other resources
can still cost money. Actual bills depend on how long resources run and what you deploy.

Sources: [AWS On-Demand pricing](https://aws.amazon.com/ec2/pricing/on-demand/),
[G6 specifications](https://aws.amazon.com/ec2/instance-types/g6/), and
[G5 specifications](https://aws.amazon.com/ec2/instance-types/g5/).

## Measured processing speed

Saved Gemma evaluation summaries from August 29, 2026 report:

| Run | Documents | Pages | Wall time | Calculated pages/hour |
| --- | ---: | ---: | ---: | ---: |
| Tuning | 30 | 180 | 239.613 seconds | 2,704 |
| Holdout | 10 | 63 | 80.201 seconds | 2,828 |

The public reference is **roughly 2,700 pages/hour**, calculated as
`pages * 3600 / wall_seconds`. Summed per-document latencies are not wall-clock
throughput when documents process concurrently.

The [evaluation runner](../src/taxhance_pii/evaluation/runner.py) includes document
rendering, detection, redacted PDF output, and automated evaluation checks. Initial
uploads, model startup, result downloads, and human review are outside this timer.
The summaries do not record the exact GPU, so these are pipeline reference results,
not a certified g6.xlarge benchmark or a guaranteed deployment rate.

Page complexity, scan quality, concurrency, and hardware affect speed. Use a sample
batch on the firm's server to establish expected throughput. Only aggregate metrics
are public; source documents and per-document evaluation artifacts remain private.
