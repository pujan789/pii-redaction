# Security policy

Do not open a public issue containing a document, filename, access token, AWS identifier,
or other sensitive material. Report vulnerabilities privately to the security contact
published by the project owner before the public launch.

Supported releases and the final reporting address will be listed here before the first
public release. Until then, do not expose this build as a public production service.
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
