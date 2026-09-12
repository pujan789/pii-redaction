import { useEffect, useMemo, useRef, useState } from "react";

import { getPageBlob } from "./api";
import { nextRotation, orderedBox, type Point, unrotatePoint } from "./reviewGeometry";
import { CATEGORY_LABELS, pageBoxCounts, sameDetections } from "./reviewSummary";
import type { Detection, JobCredentials, Manifest, Rotation } from "./types";

const ZOOM_LEVELS = [75, 100, 150, 200, 300];
const LETTER_ASPECT = 8.5 / 11;

interface ReviewCanvasProps {
  credentials: JobCredentials;
  manifest: Manifest;
  detections: Detection[];
  onChange: (detections: Detection[]) => void;
  onUndo: () => void;
  canUndo: boolean;
  rotation: Rotation;
  onRotate: (rotation: Rotation) => void;
}

interface Draft {
  page: number;
  start: Point;
  cursor: Point;
}

function boxStyle(box: Detection["box"]) {
  return {
    left: `${box.x1 / 10}%`,
    top: `${box.y1 / 10}%`,
    width: `${(box.x2 - box.x1) / 10}%`,
    height: `${(box.y2 - box.y1) / 10}%`,
  };
}

export default function ReviewCanvas({
  credentials,
  manifest,
  detections,
  onChange,
  onUndo,
  canUndo,
  rotation,
  onRotate,
}: ReviewCanvasProps) {
  const pageCount = manifest.page_count;
  const [currentPage, setCurrentPage] = useState(0);
  const [zoom, setZoom] = useState(100);
  const [finalLook, setFinalLook] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [wanted, setWanted] = useState<Set<number>>(
    () => new Set([0, 1].filter((index) => index < pageCount)),
  );
  const [images, setImages] = useState<Record<number, string>>({});
  const [failed, setFailed] = useState<Set<number>>(() => new Set());
  const [aspects, setAspects] = useState<Record<number, number>>({});
  const scroller = useRef<HTMLDivElement>(null);
  const pageRefs = useRef<(HTMLDivElement | null)[]>([]);
  const inFlight = useRef<Set<number>>(new Set());
  const urls = useRef<Map<number, string>>(new Map());
  const ratios = useRef<Map<number, number>>(new Map());
  const mounted = useRef(true);

  const counts = useMemo(() => pageBoxCounts(detections, pageCount), [detections, pageCount]);
  const suggestionsIntact = sameDetections(detections, manifest.detections);
  const turned = rotation === 90 || rotation === 270;

  useEffect(() => {
    mounted.current = true;
    const cache = urls.current;
    return () => {
      mounted.current = false;
      cache.forEach((url) => URL.revokeObjectURL(url));
      cache.clear();
    };
  }, []);

  // Lazy page images: fetch each wanted page once and keep the object URL for
  // the life of the canvas so scrolling back never refetches.
  useEffect(() => {
    wanted.forEach((index) => {
      if (urls.current.has(index) || inFlight.current.has(index) || failed.has(index)) return;
      inFlight.current.add(index);
      getPageBlob(credentials, index)
        .then((blob) => {
          if (!mounted.current) return;
          const url = URL.createObjectURL(blob);
          urls.current.set(index, url);
          setImages((previous) => ({ ...previous, [index]: url }));
        })
        .catch(() => {
          if (mounted.current) setFailed((previous) => new Set(previous).add(index));
        })
        .finally(() => inFlight.current.delete(index));
    });
  }, [credentials, wanted, failed]);

  // Visible pages (plus one neighbour each way) drive loading and the page
  // indicator. Without an observer every page loads and the controls lead.
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") {
      setWanted(new Set(Array.from({ length: pageCount }, (_, index) => index)));
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        const additions = new Set<number>();
        for (const entry of entries) {
          const index = Number((entry.target as HTMLElement).dataset.pageIndex);
          ratios.current.set(index, entry.isIntersecting ? entry.intersectionRatio : 0);
          if (!entry.isIntersecting) continue;
          for (const near of [index - 1, index, index + 1]) {
            if (near >= 0 && near < pageCount) additions.add(near);
          }
        }
        if (additions.size) {
          setWanted((previous) => {
            const merged = new Set(previous);
            additions.forEach((index) => merged.add(index));
            return merged.size === previous.size ? previous : merged;
          });
        }
        let best = -1;
        let bestRatio = 0;
        ratios.current.forEach((ratio, index) => {
          if (ratio > bestRatio || (ratio === bestRatio && ratio > 0 && index < best)) {
            best = index;
            bestRatio = ratio;
          }
        });
        if (best >= 0) setCurrentPage(best);
      },
      { root: scroller.current, threshold: [0, 0.25, 0.5, 0.75, 1] },
    );
    pageRefs.current.forEach((element) => element && observer.observe(element));
    return () => observer.disconnect();
  }, [pageCount]);

  useEffect(() => {
    const keys = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSelectedId(null);
        setDraft(null);
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

  function goToPage(index: number) {
    const target = Math.max(0, Math.min(pageCount - 1, index));
    setCurrentPage(target);
    setSelectedId(null);
    pageRefs.current[target]?.scrollIntoView({ block: "start" });
  }

  function pointFromEvent(event: React.PointerEvent<HTMLDivElement>): Point {
    const bounds = event.currentTarget.getBoundingClientRect();
    const u = (event.clientX - bounds.left) / bounds.width;
    const v = (event.clientY - bounds.top) / bounds.height;
    const point = unrotatePoint(u, v, rotation);
    return {
      x: Math.max(0, Math.min(1000, point.x * 1000)),
      y: Math.max(0, Math.min(1000, point.y * 1000)),
    };
  }

  function pointerDown(page: number, event: React.PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const point = pointFromEvent(event);
    setSelectedId(null);
    setDraft({ page, start: point, cursor: point });
  }

  function pointerMove(event: React.PointerEvent<HTMLDivElement>) {
    if (draft) setDraft({ ...draft, cursor: pointFromEvent(event) });
  }

  function pointerUp(event: React.PointerEvent<HTMLDivElement>) {
    if (!draft) return;
    const box = orderedBox(draft.start, pointFromEvent(event));
    const page = draft.page;
    setDraft(null);
    if (!box) return;
    const detection: Detection = {
      id: crypto.randomUUID(),
      page_index: page,
      category: "user_added",
      box,
      confidence: 1,
      source: "user",
      fingerprint: null,
    };
    onChange([...detections, detection]);
    setSelectedId(detection.id);
  }

  function removeSelected() {
    if (!selectedId) return;
    onChange(detections.filter((item) => item.id !== selectedId));
    setSelectedId(null);
  }

  const draftBox = draft ? orderedBox(draft.start, draft.cursor) : null;

  return (
    <div className="review-workspace">
      <div className="review-toolbar" role="toolbar" aria-label="Review controls">
        <div className="review-toolbar-group">
          <button
            type="button"
            aria-label="Previous page"
            onClick={() => goToPage(currentPage - 1)}
            disabled={currentPage === 0}
          >
            ←
          </button>
          <label className="visually-hidden" htmlFor="page-select">
            Page
          </label>
          <select
            id="page-select"
            value={currentPage}
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
            onClick={() => goToPage(currentPage + 1)}
            disabled={currentPage >= pageCount - 1}
          >
            →
          </button>
        </div>
        <div className="review-toolbar-group">
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
          <button type="button" onClick={() => onRotate(nextRotation(rotation))}>
            Rotate 90°
          </button>
          <label className="review-toggle">
            <input
              type="checkbox"
              checked={finalLook}
              onChange={(event) => setFinalLook(event.target.checked)}
            />
            Preview final look
          </label>
        </div>
        <div className="review-toolbar-group review-toolbar-actions">
          <button type="button" onClick={removeSelected} disabled={!selectedId}>
            Remove selected box
          </button>
          <button type="button" onClick={onUndo} disabled={!canUndo}>
            Undo
          </button>
          <button
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
        <span className="review-hint">Drag to add a box. Delete removes the selected box.</span>
      </div>

      <div className="review-pages" ref={scroller}>
        {counts.map((_, index) => {
          const aspect = aspects[index] ?? LETTER_ASPECT;
          const image = images[index];
          return (
            <div
              key={index}
              className="review-page"
              data-testid="review-page"
              data-page-index={index}
              aria-current={index === currentPage ? "page" : undefined}
              aria-label={`Document page ${index + 1}`}
              ref={(element) => {
                pageRefs.current[index] = element;
              }}
              style={{ width: `${zoom}%`, aspectRatio: turned ? 1 / aspect : aspect }}
            >
              {failed.has(index) ? (
                <div className="page-failure">The private page preview could not be loaded.</div>
              ) : image ? (
                <div
                  className={`document-stage ${finalLook ? "is-final" : ""}`}
                  data-testid="document-stage"
                  data-rotation={rotation}
                  onPointerDown={(event) => pointerDown(index, event)}
                  onPointerMove={pointerMove}
                  onPointerUp={pointerUp}
                  onPointerCancel={() => setDraft(null)}
                >
                  <img
                    src={image}
                    alt={`Private document page ${index + 1}`}
                    draggable={false}
                    onLoad={(event) => {
                      const { naturalWidth, naturalHeight } = event.currentTarget;
                      if (naturalWidth && naturalHeight) {
                        setAspects((previous) => ({
                          ...previous,
                          [index]: naturalWidth / naturalHeight,
                        }));
                      }
                    }}
                  />
                  {detections
                    .filter((item) => item.page_index === index)
                    .map((item) => (
                      <button
                        type="button"
                        key={item.id}
                        className={`redaction-box ${selectedId === item.id ? "is-selected" : ""}`}
                        style={boxStyle(item.box)}
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
                  {draftBox && draft?.page === index && (
                    <span className="redaction-box is-draft" style={boxStyle(draftBox)} />
                  )}
                </div>
              ) : (
                <div className="page-loading" aria-label={`Loading page ${index + 1}`}>
                  <span />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
