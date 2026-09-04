"""Text-anchored detection: grid → LLM names strings → geometry gives boxes."""

from __future__ import annotations

import json
import logging
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Protocol

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


class DocumentDetector(Protocol):
    def preflight(self) -> None: ...

    def detect_document(self, pages: list[PageArtifact]) -> list[Detection]: ...


class DetectorError(RuntimeError):
    pass


@dataclass
class _PageResult:
    page: PageArtifact
    items: list[tuple[str, PiiCategory]] = field(default_factory=list)
    detections: list[Detection] = field(default_factory=list)


class TextAnchoredDetector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def preflight(self) -> None:
        request = urllib.request.Request(f"{self.settings.vllm_base_url}/models")  # noqa: S310
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            if response.status != 200:
                raise DetectorError("vllm_unhealthy")

    def _complete(self, grid: str) -> list[tuple[str, PiiCategory]]:
        payload = {
            "model": self.settings.model_id,
            "messages": build_messages(grid),
            "temperature": 0,
            "max_tokens": self.settings.model_max_new_tokens,
            "response_format": RESPONSE_JSON_SCHEMA,
        }
        request = urllib.request.Request(  # noqa: S310
            f"{self.settings.vllm_base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=300) as response:  # noqa: S310
            body = json.loads(response.read())
        content = body["choices"][0]["message"]["content"]
        try:
            parsed = json.loads(content)
            raw_items = parsed.get("items", [])
        except (json.JSONDecodeError, AttributeError, TypeError):
            logger.warning("model_output_unparseable")
            return []
        if not isinstance(raw_items, list):
            return []
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
        for text, category in result.items:
            boxes = anchor_value(text, result.page.words)
            if boxes:
                anchored_count += 1
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

    def detect_document(self, pages: list[PageArtifact]) -> list[Detection]:
        with ThreadPoolExecutor(max_workers=self.settings.detector_concurrency) as pool:
            results = list(pool.map(self._detect_page, pages))
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
            detections.extend(merge_page_detections(page_detections))
        return detections


class NoopDetector:
    """Local/no-GPU runs and pipeline tests."""

    def preflight(self) -> None:
        return None

    def detect_document(self, pages: list[PageArtifact]) -> list[Detection]:
        del pages
        return []
