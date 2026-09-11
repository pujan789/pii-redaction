import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PIL import Image

from taxhance_pii.config import Settings
from taxhance_pii.domain import BoundingBox, PiiCategory
from taxhance_pii.redaction.document import PageArtifact, WordBox
from taxhance_pii.worker.detector import (
    DetectorError,
    DetectorUnavailable,
    NoopDetector,
    TextAnchoredDetector,
)


def word(text: str, x1: int, y1: int, source: str = "pdf") -> WordBox:
    return WordBox(text=text, box=BoundingBox(x1=x1, y1=y1, x2=x1 + 40, y2=y1 + 10), source=source)


def page(index: int, words: list[WordBox]) -> PageArtifact:
    return PageArtifact(index, Image.new("RGB", (100, 100), "white"), words)


class ScriptedVllm(BaseHTTPRequestHandler):
    responses: list[dict] = []
    raw_content: str | None = None
    finish_reason: str = "stop"
    calls: int = 0

    def do_GET(self):  # noqa: N802 - /models preflight
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"data": []}')

    def do_POST(self):  # noqa: N802
        length = int(self.headers["Content-Length"])
        self.rfile.read(length)
        if ScriptedVllm.raw_content is not None:
            content = ScriptedVllm.raw_content
        else:
            body = ScriptedVllm.responses[min(ScriptedVllm.calls, len(ScriptedVllm.responses) - 1)]
            content = json.dumps(body)
        ScriptedVllm.calls += 1
        payload = {
            "choices": [
                {"message": {"content": content}, "finish_reason": ScriptedVllm.finish_reason}
            ]
        }
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):  # noqa: ANN002
        pass


@pytest.fixture()
def vllm_stub():
    server = HTTPServer(("127.0.0.1", 0), ScriptedVllm)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ScriptedVllm.calls = 0
    ScriptedVllm.raw_content = None
    ScriptedVllm.finish_reason = "stop"
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()


def make_detector(base_url: str) -> TextAnchoredDetector:
    settings = Settings(
        token_pepper="x" * 40,
        vllm_base_url=base_url,
        vllm_launch=False,
        detector_concurrency=1,
    )
    return TextAnchoredDetector(settings)


def test_preflight_passes_against_stub(vllm_stub) -> None:
    make_detector(vllm_stub).preflight()


def test_detects_anchors_and_propagates_across_pages(vllm_stub) -> None:
    ScriptedVllm.responses = [
        {"items": [{"text": "JULIA ZHOU", "category": "client_name"}]},
        {"items": []},  # page 2 model finds nothing, propagation must cover it
    ]
    pages = [
        page(0, [word("JULIA", 10, 10), word("ZHOU", 60, 10)]),
        page(1, [word("JULIA", 10, 10), word("ZHOU", 60, 10), word("Wages", 10, 40)]),
    ]
    detections = make_detector(vllm_stub).detect_document(pages)
    assert any(d.page_index == 1 and d.category == PiiCategory.PERSON_NAME for d in detections)


def test_safety_net_applies_even_when_model_misses(vllm_stub) -> None:
    ScriptedVllm.responses = [{"items": []}]
    detections = make_detector(vllm_stub).detect_document([page(0, [word("123-45-6789", 10, 10)])])
    assert len(detections) == 1
    assert detections[0].source == "regex" and detections[0].category == PiiCategory.SSN


def test_unparseable_content_fails_closed(vllm_stub) -> None:
    ScriptedVllm.raw_content = '{"items": [{"text": "JOHN'
    with pytest.raises(DetectorError, match="model_output_invalid"):
        make_detector(vllm_stub).detect_document([page(0, [word("hello", 10, 10)])])


def test_missing_items_key_fails_closed(vllm_stub) -> None:
    ScriptedVllm.responses = [{"garbage": True}]
    with pytest.raises(DetectorError, match="model_output_invalid"):
        make_detector(vllm_stub).detect_document([page(0, [word("hello", 10, 10)])])


def test_truncated_output_fails_closed(vllm_stub) -> None:
    ScriptedVllm.responses = [{"items": []}]
    ScriptedVllm.finish_reason = "length"
    with pytest.raises(DetectorError, match="model_output_truncated"):
        make_detector(vllm_stub).detect_document([page(0, [word("hello", 10, 10)])])


def test_unanchored_tin_fails_closed(vllm_stub) -> None:
    ScriptedVllm.responses = [{"items": [{"text": "987-65-4321", "category": "client_tin"}]}]
    words = [word("hello", 10, 10, source="ocr")]
    with pytest.raises(DetectorError, match="detection_unanchored"):
        make_detector(vllm_stub).detect_document([page(0, words)])


def test_unanchored_tin_is_tolerated_when_the_page_still_gets_an_ssn_box(vllm_stub) -> None:
    ScriptedVllm.responses = [{"items": [{"text": "987-65-4321", "category": "client_tin"}]}]
    words = [word("123-45-6789", 10, 10, source="ocr")]
    detections = make_detector(vllm_stub).detect_document([page(0, words)])
    assert [d.category for d in detections] == [PiiCategory.SSN]


def test_unanchored_name_does_not_fail_the_job(vllm_stub) -> None:
    ScriptedVllm.responses = [{"items": [{"text": "NOBODY HERE", "category": "client_name"}]}]
    words = [word("hello", 10, 10, source="ocr")]
    assert make_detector(vllm_stub).detect_document([page(0, words)]) == []


def test_progress_callback_fires_once_per_page(vllm_stub) -> None:
    ScriptedVllm.responses = [{"items": []}]
    calls: list[int] = []
    pages = [page(0, [word("a", 10, 10)]), page(1, [word("b", 10, 10)]), page(2, [])]
    make_detector(vllm_stub).detect_document(pages, progress=lambda: calls.append(1))
    assert len(calls) == 3


def test_connection_refused_is_reported_as_unavailable() -> None:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    detector = make_detector(f"http://127.0.0.1:{port}/v1")
    with pytest.raises(DetectorUnavailable):
        detector.preflight()
    with pytest.raises(DetectorUnavailable):
        detector.detect_document([page(0, [word("hello", 10, 10)])])


def test_noop_detector_contract() -> None:
    detector = NoopDetector()
    detector.preflight()
    assert detector.detect_document([]) == []
