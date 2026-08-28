# Operations

## Deployment order

1. Bootstrap a dedicated OpenTofu state bucket and deployment role while authenticated
   to AWS with the account owner session.
2. Switch to an IAM Identity Center or IAM administrator identity and assume the
   deployment role. AWS account-root sessions cannot assume IAM roles; do not create a
   long-lived root access key to work around that restriction.
3. Apply the foundation resources and ECR repositories.
4. Request at least four vCPUs for EC2 quota `L-DB2E81BA`, then wait for approval.
5. Run `scripts/build-aws-images.ps1`. The isolated CodeBuild project builds and pushes
   both containers when a local Docker daemon is unavailable. Record its opaque image tag.
6. Build, test, scan, and deploy the Lambda and worker images by immutable digest.
7. Before quota approval, apply with both desired counts at zero. For private evaluation,
   use `GpuCapacityDesiredCount 1` and `WorkerDesiredCount 0`, so the standalone task owns
   the single GPU. Upload the frontend only to the dedicated web bucket.
8. Run the 30-document tuning split, inspect every private contact sheet, and repeat after
   any detector/prompt change. Freeze the configuration, then run the 20-document holdout.
9. Re-apply with `WorkerDesiredCount 1` for the production queue worker.
10. Verify S3 public-access blocks, KMS encryption, WAF association, cleanup schedule,
    DynamoDB TTL, SQS dead-letter queue, IAM simulator results, and deletion canary.

An explicitly authorized parallel model comparison may temporarily use
`GpuCapacityDesiredCount 2` with `WorkerDesiredCount 0`. The Auto Scaling group is capped
at two instances. Keep both running during the active comparison to avoid needless
instance churn, then restore the release-time capacity after the evaluation session.

## GPU capacity schedule

The worker ASG is spot-first (`price-capacity-optimized` across g6.xlarge and
g5.xlarge, capacity rebalance on). Two scheduled actions own desired capacity:
scale to 1 at 11:00 UTC (07:00 ET) and to 0 at 01:00 UTC (21:00 ET) — a 14-hour
warm window for US business hours. Overnight, an SQS queue-depth alarm
(`offhours-queue-depth`) scales 0→1 when a job arrives; expect a cold start of a
few minutes for the instance plus vLLM model load from the cache volume. Spot
interruptions are absorbed by the SQS visibility timeout: the job returns to
the queue and the replacement instance reprocesses it. Terraform ignores
`desired_capacity` drift so applies never fight the schedule.

The generated `*.cloudfront.net` hostname is a staging endpoint: AWS fixes its default
certificate's minimum protocol at legacy TLSv1. Before public launch, issue or import an
ACM certificate in `us-east-1`, set `cloudfront_alias` and
`cloudfront_certificate_arn` together, include `https://<alias>` in `allowed_origins`,
and point that hostname at the distribution. The stack then enforces `TLSv1.2_2021`.

The deployment helper always produces an OpenTofu plan first. It applies only when
`-Apply` is supplied and refuses to synchronize an unexpected S3 bucket name:

```powershell
.\scripts\deploy-aws-application.ps1 `
  -LambdaTag build-YYYYMMDDHHMMSS `
  -WorkerTag build-YYYYMMDDHHMMSS `
  -WorkerDesiredCount 0 `
  -GpuCapacityDesiredCount 1 `
  -Apply
```

The helper resolves immutable digests and waits for both ECR scans. It refuses to deploy
an image with any critical scan findings.

The production model is `google/gemma-4-E2B-it` served by the worker's
loopback-only vLLM process. To evaluate the config-switchable alternate
(`Qwen/Qwen3.5-4B`), pass the model selection explicitly and restore the Gemma
values before the next production deploy:

```powershell
.\scripts\deploy-aws-application.ps1 `
  -LambdaTag build-YYYYMMDDHHMMSS `
  -WorkerTag build-YYYYMMDDHHMMSS `
  -WorkerDesiredCount 0 `
  -GpuCapacityDesiredCount 1 `
  -ModelId Qwen/Qwen3.5-4B `
  -ModelRevision <exact commit sha> `
  -Apply
```

If Docker Hub throttles a rebuild, `build-aws-images.ps1 -Target worker
-BaseWorkerTag <last-clean-tag>` creates a thin release overlay on the previously scanned
immutable worker digest. Only source plus explicitly reviewed, hash-pinned additive wheels
from `requirements/worker-overlay.txt` may enter that layer. Never use it for upgrades,
removals, or base-image changes; run and deploy a clean full build before public release.

If EC2 GPU quota is temporarily zero, private tuning may use the exact same pinned model
on the reviewed `m7i.4xlarge` CPU fallback (currently $0.8064/hour in `us-east-1`). Keep
the production worker at zero, monitor the task, and return capacity to zero immediately
after it stops:

```powershell
.\scripts\deploy-aws-application.ps1 `
  -LambdaTag build-YYYYMMDDHHMMSS `
  -WorkerTag build-YYYYMMDDHHMMSS `
  -WorkerDesiredCount 0 `
  -GpuCapacityDesiredCount 1 `
  -EvaluationCpuFallback `
  -Apply
```

The evaluation uploads an encrypted checkpoint after every document, so a task restart
resumes the same run rather than reprocessing completed documents. The CPU fallback is
for private evaluation only; public queue workers remain GPU-only.

While a task is running, completed opaque results can be copied into the ignored
`private-results/` directory without deleting or exposing the remote checkpoint:

```powershell
.\scripts\sync-private-evaluation-progress.ps1 `
  -Split tuning `
  -Prefix private-evaluation/<opaque-run-id> `
  -Output .\private-results\tuning-live
```

Private evaluation uses opaque object keys and a separate least-privilege ECS task role.
The tuning and holdout splits are staged separately; do not stage the holdout early:

```powershell
.\scripts\start-private-evaluation.ps1 `
  -Split tuning `
  -Manifest .\private-evaluation\sample-50.json

# After the returned ECS task is STOPPED with exit code 0:
.\scripts\fetch-private-evaluation.ps1 `
  -Split tuning `
  -Prefix private-evaluation/<opaque-run-id> `
  -TaskArn <task-arn> `
  -Output .\private-results\tuning
```

Fetching downloads the encrypted results and then explicitly deletes the exact validated
AWS evaluation prefix. The bucket lifecycle remains a one-day fallback if a task fails.

## Routine checks

- GPU ECS service desired/running task counts are both one.
- SQS oldest-message age stays below the worker visibility timeout.
- Cleanup Lambda has no errors and expired-object canaries disappear no later than one hour.
- WAF blocked-request rate and API throttles are not suppressing ordinary use.
- AWS Budget forecast remains under the project ceiling.
- ECR images and the pinned model revision match the release manifest.
- No objects remain under a completed `private-evaluation/<run-id>/` prefix.

## Incident deletion

Use the normal DELETE API when the job token is available. For an operational incident,
delete the exact `jobs/<uuid>/` S3 prefix and mark only that DynamoDB job deleted. Never
issue a recursive command against a bucket, workspace root, home directory, or unresolved
variable.

## Backups

Document data is deliberately not backed up, replicated, or versioned. Source code and
infrastructure state are backed up; customer documents are not.
