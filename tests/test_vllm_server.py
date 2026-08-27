import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from taxhance_pii.config import Settings
from taxhance_pii.worker.vllm_server import VllmServer, build_command


def settings(**overrides) -> Settings:
    return Settings(token_pepper="x" * 40, **overrides)


def test_command_pins_model_and_port() -> None:
    command = build_command(settings())
    assert command[:2] == ["vllm", "serve"]
    assert "google/gemma-4-E2B-it" in command
    assert "--revision" in command and "--port" in command
    assert "--gpu-memory-utilization" in command


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):  # noqa: ANN002
        pass


def test_wait_healthy_returns_when_health_endpoint_up(monkeypatch) -> None:
    server = HTTPServer(("127.0.0.1", 0), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cfg = settings(vllm_port=server.server_address[1], vllm_startup_timeout_seconds=60)
        launcher = VllmServer(cfg)

        class FakeProcess:
            def poll(self):
                return None

            def terminate(self):
                pass

        monkeypatch.setattr(
            "taxhance_pii.worker.vllm_server.subprocess.Popen",
            lambda *a, **k: FakeProcess(),
        )
        launcher.start()  # returns without raising
    finally:
        server.shutdown()
