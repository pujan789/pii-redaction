#!/usr/bin/env bash
# The worker process launches and supervises its own localhost vLLM server
# (Settings.vllm_launch), so this entrypoint is deliberately a single exec.
set -euo pipefail
exec python3 -m taxhance_pii.worker.main
