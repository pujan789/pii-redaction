# Private usage analytics

The owner dashboard answers whether people visit the site, submit documents, and
download results. It uses aggregate metrics in the existing AWS account. It does not
provide a document browser or access to anyone's redaction job.

## What is collected

| Metric | Definition |
| --- | --- |
| Visits | Estimated browsing sessions per tab; a new visit starts after 30 minutes without a page load |
| Page views | Loads of the landing page, redaction app, and self-hosting page |
| Documents submitted | Valid uploads successfully queued for detection |
| Documents completed | First successful PDF completion for a job; reopening and rebuilding it does not add another completion |
| Pages redacted | Pages in those first successful document completions |
| Documents downloaded | PDFs included when the browser starts a PDF or ZIP save; repeat downloads count again |
| Downloads started | PDF or ZIP save actions reported by the browser |
| Batches selected | Selections containing more than one supported document |
| Average batch size | Supported documents in batch selections divided by the number of selections |
| Failed documents | Worker jobs that enter the failed state |
| Average task time | Active time per successful worker task; manual detection and rendering can be separate tasks |

These numbers are usage indicators. Visits do not identify unique people. Browser
blocking and network failures can reduce client-reported counts; bots can increase
them. A download event means the complete file was available in the browser and a
save was started, not that the operating system confirmed a saved file. Failed
uploads and infrastructure incidents before a worker job starts are not included
in the failed-document metric.

The charts show daily UTC totals for the selected period. Counts at different
stages are not a visitor-level conversion funnel: a visitor can upload a batch,
and a document can be uploaded and downloaded on different days. Collection starts
with deployment; previous traffic is not reconstructed from document records.

## Data boundaries and retention

- Collected payloads have fixed event names and numeric counts. Unexpected fields
  and oversized requests are rejected before anything is recorded.
- No raw IPs, names, emails, filenames, document text, job IDs, referrers, traffic
  sources, full URLs, user agents, or browser fingerprints enter usage metrics.
- A per-tab timestamp in session storage estimates visits. It is never sent to the
  server, and no analytics cookie or persistent visitor identifier is created.
- Existing abuse controls use the request IP separately; their keyed hashes remain
  in short-lived job records under the existing document retention policy.
- Metrics use CloudWatch Embedded Metric Format with a fixed deployment dimension.
  Logs retain the existing seven-day expiry; CloudWatch retains aggregate metrics
  under its standard retention schedule (up to 15 months). Document deletion does
  not remove non-identifying aggregate counts.
- Metric collection is best effort. Logging failures do not interrupt redaction.
  CloudWatch can occasionally duplicate delivered metrics, so counts are not billing
  or audit records. New points can take several minutes to appear.

## Owner access

The owner page has no links from public navigation and carries a `noindex` directive.
The route is visible in source code, so its obscurity is not an access control.

Authentication uses a dedicated Cognito pool, with self-registration disabled and
authenticator-app MFA required. The browser uses an authorization code with PKCE
and a one-time state value. Access tokens remain in memory and expire after 30
minutes. Refreshing the page requires sign-in again; the Cognito session may make
this quick. Sign out also ends the managed-login session.

The analytics route requires a signed access token from the configured pool and app
client, the `pii-analytics/read` scope, and membership in `analytics-owners`.
The API accepts only the verified API Gateway authorizer context, never a claim
sent in a client-controlled header. Local mode has no authentication bypass.

## Deployment and initial account

Deploy the API and worker images, frontend, and infrastructure together. The normal
deployment creates the Cognito pool, client, owner group, JWT authorizer, and a
read-only CloudWatch permission for the API. The private page's exact HTTPS callback
is configured with `analytics_owner_url`; it must match the URL you open, including
its trailing slash.

The completion flag is a separate DynamoDB attribute so the shared job payload
remains readable by older workers and API versions during deployment or rollback.
An older writer can discard that flag, so rebuilding a document across a mixed
deployment can count it again. This affects aggregate accuracy, not processing.

After deployment, use an authorized AWS identity to provision the owner's account:

```powershell
$poolId = tofu -chdir=infra output -raw analytics_user_pool_id
uv run python scripts/setup-analytics-owner.py --user-pool-id $poolId --email pujan@taxhance.com
tofu -chdir=infra output -raw analytics_owner_url
```

The provisioning script sends no invitation and prints no password. For a newly
created account, open the owner page, choose **Sign in securely**, then **Forgot
your password?** to request an email code and choose a password. Cognito then guides
you through authenticator-app enrollment. The script does not reset an existing
account's password.

Removing an owner from the Cognito group affects newly issued tokens; an existing
access token can remain usable until its 30-minute expiry. For immediate shutdown,
disable the analytics API route or set `PII_ANALYTICS_ENABLED=false` on the API.

## Verification

1. An anonymous request to the analytics endpoint must not return metrics.
2. A valid account outside the owner group must also be denied.
3. Sign in with the provisioned owner and check the empty, populated, and expired-session states.
4. Open a public page and process a synthetic document; download it and confirm new
   metrics appear. Rebuilding the same job must not increment completed documents.
5. Inspect the generated metric fields, using synthetic events only, to confirm no
   document or request identifiers are present.

Sources: [CloudWatch metric retention](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/cloudwatch_concepts.html),
[embedded metrics](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format.html),
[API Gateway JWT authorization](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html).
