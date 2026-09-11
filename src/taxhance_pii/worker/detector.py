"""Text-anchored detection: grid → LLM names strings → geometry gives boxes."""

from __future__ import annotations

import http.client
import json
import logging
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Protocol

from taxhance_pii.config import Settings
from taxhance_pii.domain import BoundingBox, Detection, PiiCategory
from taxhance_pii.redaction.anchor import (
    anchor_value,
    merge_page_detections,
    ssn_safety_net,
)
from taxhance_pii.redaction.document import PageArtifact
from taxhance_pii.redaction.layout import build_grid
from taxhance_pii.redaction.prompt import (
    CATEGORY_MAP,
    RESPONSE_JSON_SCHEMA,
    build_messages,
)

logger = logging.getLogger(__name__)
DETECTOR_VERSION = "anchored-v1"
PROPAGATED_CATEGORIES = {
    PiiCategory.PERSON_NAME,
    PiiCategory.SSN,
    PiiCategory.STREET_ADDRESS,
}
# A named taxpayer identifier that cannot be located on the page must never
# ship silently; every other category degrades to a logged warning.
_TIN_CATEGORIES = {PiiCategory.SSN, PiiCategory.ITIN}

ProgressCallback = Callable[[], None]


class DocumentDetector(Protocol):
    def preflight(self) -> None: ...

    def detect_document(
        self, pages: list[PageArtifact], progress: ProgressCallback | None = None
    ) -> list[Detection]: ...


class DetectorError(RuntimeError):
    """The model produced output this document cannot be trusted with."""


class DetectorUnavailable(DetectorError):
    """The model server cannot be reached; the document is not at fault."""


@dataclass
class _PageResult:
    page: PageArtifact
    items: list[tuple[str, PiiCategory]] = field(default_factory=list)
    detections: list[Detection] = field(default_factory=list)
    unanchored: list[tuple[str, PiiCategory]] = field(default_factory=list)


class TextAnchoredDetector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _request(self, path: str, payload: dict[str, Any] | None, timeout: int) -> Any:
        request = urllib.request.Request(  # noqa: S310
            f"{self.settings.vllm_base_url}{path}",
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="GET" if payload is None else "POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                if response.status != 200:
                    raise DetectorUnavailable("vllm_unhealthy")
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # A rejected request (context too long, bad schema) is a problem
            # with this document, not with the server.
            raise DetectorError(f"vllm_http_{exc.code}") from exc
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            ConnectionError,
            TimeoutError,
        ) as exc:
            raise DetectorUnavailable("vllm_unreachable") from exc

    def preflight(self) -> None:
        self._request("/models", None, timeout=10)

    def _complete(self, grid: str) -> list[tuple[str, PiiCategory]]:
        payload = {
            "model": self.settings.model_id,
            "messages": build_messages(grid),
            "temperature": 0,
            "max_tokens": self.settings.model_max_new_tokens,
            "response_format": RESPONSE_JSON_SCHEMA,
        }
        body = self._request("/chat/completions", payload, timeout=300)
        choice = body["choices"][0]
        if choice.get("finish_reason") == "length":
            # A cut-off JSON array would silently lose every later finding.
            raise DetectorError("model_output_truncated")
        content = choice["message"]["content"]
        try:
            raw_items = json.loads(content)["items"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise DetectorError("model_output_invalid") from exc
        if not isinstance(raw_items, list):
            raise DetectorError("model_output_invalid")
        items: list[tuple[str, PiiCategory]] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text", "")).strip()
            category = CATEGORY_MAP.get(str(raw.get("category", "")))
            if text and category is not None:
                items.append((text, category))
        return items

    def _anchor_items(self, result: _PageResult) -> int:
        anchored_count = 0
        result.unanchored = []
        for text, category in result.items:
            boxes = anchor_value(text, result.page.words)
            if boxes:
                anchored_count += 1
            else:
                result.unanchored.append((text, category))
            result.detections.extend(
                self._detection(result.page.page_index, category, box, 0.9) for box in boxes
            )
        return anchored_count

    def _detect_page(self, page: PageArtifact) -> _PageResult:
        result = _PageResult(page=page)
        if not page.words:
            return result
        result.items = self._complete(build_grid(page.words))
        anchored = self._anchor_items(result)
        pdf_only = all(w.source == "pdf" for w in page.words)
        if result.items and anchored * 2 < len(result.items) and pdf_only:
            from taxhance_pii.redaction.document import ocr_words

            logger.info("page_reocr_retry", extra={"page_index": page.page_index})
            page.words = ocr_words(page.image)
            result.items = self._complete(build_grid(page.words))
            result.detections = []
            self._anchor_items(result)
        return result

    @staticmethod
    def _detection(
        page_index: int, category: PiiCategory, box: BoundingBox, confidence: float
    ) -> Detection:
        return Detection(
            id=str(uuid.uuid4()),
            page_index=page_index,
            category=category,
            box=box,
            confidence=confidence,
            source="model",
        )

    def detect_document(
        self, pages: list[PageArtifact], progress: ProgressCallback | None = None
    ) -> list[Detection]:
        def detect(page: PageArtifact) -> _PageResult:
            result = self._detect_page(page)
            if progress is not None:
                progress()
            return result

        with ThreadPoolExecutor(max_workers=self.settings.detector_concurrency) as pool:
            results = list(pool.map(detect, pages))
        doc_values = {
            (text, category)
            for result in results
            for text, category in result.items
            if category in PROPAGATED_CATEGORIES
        }
        detections: list[Detection] = []
        for result in results:
            page = result.page
            page_detections = list(result.detections)
            for text, category in doc_values:
                page_detections.extend(
                    self._detection(page.page_index, category, box, 0.85)
                    for box in anchor_value(text, page.words)
                )
            page_detections.extend(ssn_safety_net(page.page_index, page.words))
            self._check_unanchored(result, page_detections)
            detections.extend(merge_page_detections(page_detections))
        return detections

    @staticmethod
    def _check_unanchored(result: _PageResult, page_detections: list[Detection]) -> None:
        if not result.unanchored:
            return
        logger.warning(
            "detections_unanchored",
            extra={"page_index": result.page.page_index, "count": len(result.unanchored)},
        )
        tin_named = any(category in _TIN_CATEGORIES for _, category in result.unanchored)
        tin_covered = any(d.category in _TIN_CATEGORIES for d in page_detections)
        if tin_named and not tin_covered:
            raise DetectorError("detection_unanchored")


class NoopDetector:
    """Local/no-GPU runs and pipeline tests."""

    def preflight(self) -> None:
        return None

    def detect_document(
        self, pages: list[PageArtifact], progress: ProgressCallback | None = None
    ) -> list[Detection]:
        del pages, progress
        return []
