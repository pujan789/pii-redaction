# Security policy

Do not open a public issue containing a document, filename, access token, AWS identifier,
or other sensitive material. Report vulnerabilities privately to the project maintainer
at [pujan@taxhance.com](mailto:pujan@taxhance.com?subject=PII%20Redaction%20security%20report).

Include the affected commit or version, reproduction steps using synthetic documents,
and the expected and observed behavior. Do not email real client documents or credentials.
Authorized private-corpus evaluation must follow `docs/EVALUATION.md`; its artifacts may
never enter Git, CI, issues, or third-party inference services.

## Security invariants

- No document contents or original filenames in application logs.
- No public S3 access and no cross-project IAM permissions.
- Opaque per-job bearer tokens are stored only as keyed hashes.
- Source and result objects are removed by explicit deletion and scheduled expiry.
- Completed PDFs are rebuilt from pixels; overlay-only redaction is forbidden.
- Processing failures produce no downloadable partial result.
- Production images run as non-root and pin dependencies and model revisions.
