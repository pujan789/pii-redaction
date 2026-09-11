import { useEffect, useMemo, useRef, useState } from "react";

import { getPageBlob } from "./api";
import {
  CATEGORY_LABELS,
  pageBoxCounts,
  sameDetections,
  SOURCE_LABELS,
} from "./reviewSummary";
import type {
  BoundingBox,
  Detection,
  JobCredentials,
  Manifest,
  PiiCategory,
} from "./types";

const BASE_STAGE_WIDTH = 900;
const ZOOM_LEVELS = [75, 100, 150, 200, 300];

interface ReviewCanvasProps {
  credentials: JobCredentials;
  manifest: Manifest;
  detections: Detection[];
  onChange: (detections: Detection[]) => void;
}

interface Point {
  x: number;
  y: number;
}

function orderedBox(first: Point, second: Point): BoundingBox | null {
  const box = {
    x1: Math.round(Math.min(first.x, second.x)),
    y1: Math.round(Math.min(first.y, second.y)),
    x2: Math.round(Math.max(first.x, second.x)),
    y2: Math.round(Math.max(first.y, second.y)),
  };
  return box.x2 - box.x1 >= 3 && box.y2 - box.y1 >= 3 ? box : null;
}

export default function ReviewCanvas({
  credentials,
  manifest,
  detections,
  onChange,
}: ReviewCanvasProps) {
  const [pageIndex, setPageIndex] = useState(0);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [start, setStart] = useState<Point | null>(null);
  const [cursor, setCursor] = useState<Point | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [category, setCategory] = useState<PiiCategory>("user_added");
  const [zoom, setZoom] = useState(100);
  const [finalLook, setFinalLook] = useState(false);
  const stageRef = useRef<HTMLDivElement>(null);

  const pageDetections = useMemo(
    () => detections.filter((item) => item.page_index === pageIndex),
    [detections, pageIndex],
  );
  const counts = useMemo(
    () => pageBoxCounts(detections, manifest.page_count),
    [detections, manifest.page_count],
  );
  const suggestionsIntact = sameDetections(detections, manifest.detections);
  const draft = start && cursor ? orderedBox(start, cursor) : null;

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    setImageUrl(null);
    setLoadError(false);
    getPageBlob(credentials, pageIndex)
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setImageUrl(objectUrl);
      })
      .catch(() => active && setLoadError(true));
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [credentials, pageIndex]);

  useEffect(() => {
    const keys = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSelectedId(null);
        setStart(null);
        setCursor(null);
        return;
      }
      if (!selectedId || (event.key !== "Delete" && event.key !== "Backspace")) return;
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName)) return;
      onChange(detections.filter((item) => item.id !== selectedId));
      setSelectedId(null);
    };
    window.addEventListener("keydown", keys);
    return () => window.removeEventListener("keydown", keys);
  }, [detections, onChange, selectedId]);

  function pointFromEvent(event: React.PointerEvent): Point {
    const bounds = stageRef.current!.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(1000, ((event.clientX - bounds.left) / bounds.width) * 1000)),
      y: Math.max(0, Math.min(1000, ((event.clientY - bounds.top) / bounds.height) * 1000)),
    };
  }

  function pointerDown(event: React.PointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || !imageUrl) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const point = pointFromEvent(event);
    setSelectedId(null);
    setStart(point);
    setCursor(point);
  }

  function pointerMove(event: React.PointerEvent<HTMLDivElement>) {
    if (start) setCursor(pointFromEvent(event));
  }

  function pointerUp(event: React.PointerEvent<HTMLDivElement>) {
    if (!start) return;
    const box = orderedBox(start, pointFromEvent(event));
    setStart(null);
    setCursor(null);
    if (!box) return;
    const detection: Detection = {
      id: crypto.randomUUID(),
      page_index: pageIndex,
      category,
      box,
      confidence: 1,
      source: "user",
      fingerprint: null,
    };
    onChange([...detections, detection]);
    setSelectedId(detection.id);
  }

  function pointerCancel() {
    setStart(null);
    setCursor(null);
  }

  function removeSelected() {
    if (!selectedId) return;
    onChange(detections.filter((item) => item.id !== selectedId));
    setSelectedId(null);
  }

  function goToPage(next: number) {
    setPageIndex(Math.max(0, Math.min(manifest.page_count - 1, next)));
    setSelectedId(null);
  }

  return (
    <div className="review-workspace">
      <aside className="review-rail" aria-label="Review controls">
        <div className="rail-section">
          <label className="eyebrow" htmlFor="page-select">
            Page
          </label>
          <div className="page-stepper">
            <button
              type="button"
              aria-label="Previous page"
              onClick={() => goToPage(pageIndex - 1)}
              disabled={pageIndex === 0}
            >
              ←
            </button>
            <select
              id="page-select"
              value={pageIndex}
              onChange={(event) => goToPage(Number(event.target.value))}
            >
              {counts.map((count, index) => (
                <option key={index} value={index}>
                  Page {index + 1} · {count ? `${count} ${count === 1 ? "box" : "boxes"}` : "no boxes"}
                </option>
              ))}
            </select>
            <button
              type="button"
              aria-label="Next page"
              onClick={() => goToPage(pageIndex + 1)}
              disabled={pageIndex === manifest.page_count - 1}
            >
              →
            </button>
          </div>
          <div className="zoom-row">
            <label htmlFor="zoom-select">Zoom</label>
            <select
              id="zoom-select"
              value={zoom}
              onChange={(event) => setZoom(Number(event.target.value))}
            >
              {ZOOM_LEVELS.map((level) => (
                <option key={level} value={level}>
                  {level}%
                </option>
              ))}
            </select>
          </div>
          <label className="final-look">
            <input
              type="checkbox"
              checked={finalLook}
              onChange={(event) => setFinalLook(event.target.checked)}
            />
            Preview final look
          </label>
        </div>

        <div className="rail-section">
          <p className="eyebrow">Boxes on this page</p>
          {pageDetections.length ? (
            <ul className="rail-list" aria-label="Boxes on this page">
              {pageDetections.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className={selectedId === item.id ? "is-selected" : ""}
                    aria-label={`Select ${CATEGORY_LABELS[item.category]}`}
                    aria-pressed={selectedId === item.id}
                    onClick={() => setSelectedId(item.id)}
                  >
                    <strong>{CATEGORY_LABELS[item.category]}</strong>
                    <span>{SOURCE_LABELS[item.source]}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="rail-help">
              No boxes on this page. If it shows sensitive information, drag across it.
            </p>
          )}
          <button
            className="button button-quiet remove-button"
            type="button"
            onClick={removeSelected}
            disabled={!selectedId}
          >
            Remove selected box
          </button>
          <button
            className="button button-quiet remove-button"
            type="button"
            onClick={() => {
              onChange(manifest.detections);
              setSelectedId(null);
            }}
            disabled={suggestionsIntact}
          >
            Restore suggestions
          </button>
        </div>

        <div className="rail-section">
          <label className="eyebrow" htmlFor="redaction-category">
            New box type
          </label>
          <select
            id="redaction-category"
            value={category}
            onChange={(event) => setCategory(event.target.value as PiiCategory)}
          >
            {Object.entries(CATEGORY_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <p className="rail-help">
            Defaults keep payer information, account numbers, and city/state/postal code. Drag
            across anything the detector missed. Delete removes the selected box; Esc clears the
            selection.
          </p>
        </div>
      </aside>

      <section className="paper-bed" aria-label={`Document page ${pageIndex + 1}`}>
        <div className="paper-instruction">
          Boxes are see-through while you review. Click one to select it; drag across anything
          the detector missed.
        </div>
        {loadError ? (
          <div className="page-failure">The private page preview could not be loaded.</div>
        ) : imageUrl ? (
          <div
            className={`document-stage ${finalLook ? "is-final" : ""}`}
            data-testid="document-stage"
            ref={stageRef}
            style={zoom === 100 ? undefined : { width: `${(BASE_STAGE_WIDTH * zoom) / 100}px` }}
            onPointerDown={pointerDown}
            onPointerMove={pointerMove}
            onPointerUp={pointerUp}
            onPointerCancel={pointerCancel}
          >
            <img src={imageUrl} alt={`Private document page ${pageIndex + 1}`} draggable={false} />
            {pageDetections.map((item) => (
              <button
                type="button"
                key={item.id}
                className={`redaction-box ${selectedId === item.id ? "is-selected" : ""}`}
                style={{
                  left: `${item.box.x1 / 10}%`,
                  top: `${item.box.y1 / 10}%`,
                  width: `${(item.box.x2 - item.box.x1) / 10}%`,
                  height: `${(item.box.y2 - item.box.y1) / 10}%`,
                }}
                aria-label={`${CATEGORY_LABELS[item.category]} redaction`}
                title={CATEGORY_LABELS[item.category]}
                onFocus={() => setSelectedId(item.id)}
                onClick={() => setSelectedId(item.id)}
                onPointerDown={(event) => {
                  event.stopPropagation();
                  setSelectedId(item.id);
                }}
              />
            ))}
            {draft && (
              <span
                className="redaction-box is-draft"
                style={{
                  left: `${draft.x1 / 10}%`,
                  top: `${draft.y1 / 10}%`,
                  width: `${(draft.x2 - draft.x1) / 10}%`,
                  height: `${(draft.y2 - draft.y1) / 10}%`,
                }}
              />
            )}
          </div>
        ) : (
          <div className="page-loading" aria-label="Loading private page preview">
            <span />
          </div>
        )}
      </section>
    </div>
  );
}
