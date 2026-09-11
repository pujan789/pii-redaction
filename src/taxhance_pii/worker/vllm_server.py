"""Launch and supervise the localhost vLLM OpenAI server for the worker."""

from __future__ import annotations

import logging
import subprocess
import time
import urllib.error
import urllib.request

from taxhance_pii.config import Settings

logger = logging.getLogger(__name__)


def build_command(settings: Settings) -> list[str]:
    return [
        "vllm",
        "serve",
        settings.model_id,
        "--revision",
        settings.model_revision,
        "--host",
        "127.0.0.1",
        "--port",
        str(settings.vllm_port),
        "--max-model-len",
        str(settings.vllm_max_model_len),
        "--gpu-memory-utilization",
        str(settings.vllm_gpu_memory_utilization),
        # Prompts contain document text; request logging must stay off even if
        # a future vLLM changes the default (privacy contract: no document
        # text in logs).
        "--no-enable-log-requests",
    ]


class VllmServer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        # Command is built from validated settings; no shell is involved.
        self._process = subprocess.Popen(build_command(self.settings))  # noqa: S603
        health_url = f"http://127.0.0.1:{self.settings.vllm_port}/health"
        deadline = time.monotonic() + self.settings.vllm_startup_timeout_seconds
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(f"vllm_exited:{self._process.returncode}")
            try:
                with urllib.request.urlopen(health_url, timeout=2) as response:  # noqa: S310
                    if response.status == 200:
                        logger.info("vllm_ready", extra={"model_id": self.settings.model_id})
                        return
            except (urllib.error.URLError, TimeoutError):
                pass
            time.sleep(2)
        self.stop()
        raise RuntimeError("vllm_startup_timeout")

    def poll(self) -> int | None:
        """Exit code of the vLLM process, or None while it is still running."""
        return None if self._process is None else self._process.poll()

    def stop(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
        self._process = None
