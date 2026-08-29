# Contributing

Use synthetic documents in tests and bug reports. Never commit or upload real taxpayer
documents, screenshots, extracted strings, model traces, or filenames.

Before submitting a change, run:

```bash
uv run ruff check .
uv run pytest --cov
npm --prefix frontend test
npm --prefix frontend run build
```

Security-sensitive changes should include a failure-path test. Redaction changes should
include both a positive fixture and a nearby non-PII value that must remain visible.

