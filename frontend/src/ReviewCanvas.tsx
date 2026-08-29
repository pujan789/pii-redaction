import { useEffect, useMemo, useRef, useState } from "react";

import { getPageBlob } from "./api";
import type {
  BoundingBox,
  Detection,
  JobCredentials,
  Manifest,
  PiiCategory,
} from "./types";

const CATEGORY_LABELS: Record<PiiCategory, string> = {
  person_name: "Person name",
  organization_name: "Private organization",
  street_address: "Street-address line",
  email: "Email",
  phone: "Phone",
  ssn: "SSN",
  itin: "ITIN",
  ein: "EIN",
  ptin: "PTIN",
  efin: "EFIN",
  ip_pin: "IP PIN",
  caf_number: "CAF number",
  bank_account: "Bank account (manual)",
  routing_number: "Routing number",
  payment_card: "Payment card",
  state_tax_id: "State tax ID",
  employee_id: "Employee ID",
  health_insurance_id: "Health insurance ID",
  other_private_id: "Other private ID",
  date_of_birth: "Date of birth",
  date_of_death: "Date of death",
  driver_license: "Driver license",
  passport: "Passport",
  ip_address: "IP address",
  signature: "Signature",
  user_added: "Other sensitive data",
};

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
  const stageRef = useRef<HTMLDivElement>(null);

  const pageDetections = useMemo(
    () => detections.filter((item) => item.page_index === pageIndex),
    [detections, pageIndex],
  );
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
    const remove = (event: KeyboardEvent) => {
      if ((event.key === "Delete" || event.key === "Backspace") && selectedId) {
        onChange(detections.filter((item) => item.id !== selectedId));
        setSelectedId(null);
      }
    };
    window.addEventListener("keydown", remove);
    return () => window.removeEventListener("keydown", remove);
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

  function removeSelected() {
    if (!selectedId) return;
    onChange(detections.filter((item) => item.id !== selectedId));
    setSelectedId(null);
  }

  return (
    <div className="review-workspace">
      <aside className="review-rail" aria-label="Review controls">
        <div className="rail-section">
          <p className="eyebrow">Page</p>
          <div className="page-stepper">
            <button
              type="button"
              aria-label="Previous page"
              onClick={() => setPageIndex((value) => Math.max(0, value - 1))}
              disabled={pageIndex === 0}
            >
              ←
            </button>
            <span>
              {pageIndex + 1} <i>/</i> {manifest.page_count}
            </span>
            <button
              type="button"
              aria-label="Next page"
              onClick={() =>
                setPageIndex((value) => Math.min(manifest.page_count - 1, value + 1))
              }
              disabled={pageIndex === manifest.page_count - 1}
            >
              →
            </button>
          </div>
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
            Defaults keep payer information, account numbers, and city/state/postal code. Drag to
            add any extra black redaction.
          </p>
        </div>

        <div className="rail-section finding-tally">
          <span>{pageDetections.length}</span>
          <p>boxes on this page</p>
        </div>

        <button
          className="button button-quiet remove-button"
          type="button"
          onClick={removeSelected}
          disabled={!selectedId}
        >
          Remove selected box
        </button>
      </aside>

      <section className="paper-bed" aria-label={`Document page ${pageIndex + 1}`}>
        <div className="paper-instruction">
          Click a box to select it. Drag across anything the detector missed.
        </div>
        {loadError ? (
          <div className="page-failure">The private page preview could not be loaded.</div>
        ) : imageUrl ? (
          <div
            className="document-stage"
            ref={stageRef}
            onPointerDown={pointerDown}
            onPointerMove={pointerMove}
            onPointerUp={pointerUp}
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
                title={`${CATEGORY_LABELS[item.category]} · ${item.source}`}
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
