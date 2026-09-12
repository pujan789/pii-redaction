import { useEffect, useRef, useState } from "react";

import { ApiError, finalizeJob, getJob, getManifest, getResultBlob } from "./api";
import { type BatchQueue, POLL_INTERVAL } from "./batch";
import { friendlyError, messageForCode } from "./errorMessages";
import ReviewCanvas from "./ReviewCanvas";
import { sameDetections, summarizeDetections } from "./reviewSummary";
import type { JobCredentials, Manifest, Rotation } from "./types";
import { useDetectionHistory } from "./useDetectionHistory";

const GONE_MESSAGE =
  "The server copy of this document is no longer available, so it cannot be edited. The PDF already received is unchanged.";

type Phase = "loading" | "editing" | "applying" | "closed";

export default function ReviewDialog({
  position,
  label,
  credentials,
  queue,
  onClose,
  pollIntervalMs = POLL_INTERVAL,
}: {
  position: number;
  label: string;
  credentials: JobCredentials;
  queue: BatchQueue;
  onClose: () => void;
  pollIntervalMs?: number;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const active = useRef(true);
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [rotation, setRotation] = useState<Rotation>(0);
  const [phase, setPhase] = useState<Phase>("loading");
  const [message, setMessage] = useState<string | null>(null);
  const { detections, canUndo, update, undo, reset } = useDetectionHistory();

  useEffect(() => {
    active.current = true;
    dialog.current?.showModal();
    return () => {
      active.current = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    getManifest(credentials)
      .then((loaded) => {
        if (cancelled) return;
        setManifest(loaded);
        reset(loaded.detections);
        setRotation(loaded.rotation);
        setPhase("editing");
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        if (reason instanceof ApiError && reason.status === 404) {
          queue.forgetRemote(position);
          setMessage(GONE_MESSAGE);
        } else {
          setMessage(friendlyError(reason));
        }
        setPhase("closed");
      });
    return () => {
      cancelled = true;
    };
  }, [credentials, position, queue, reset]);

  const unchanged =
    manifest !== null &&
    sameDetections(detections, manifest.detections) &&
    rotation === manifest.rotation;

  async function apply() {
    if (!manifest || phase !== "editing") return;
    setPhase("applying");
    setMessage(null);
    try {
      let job = await finalizeJob(credentials, detections, rotation);
      while (job.status === "queued_redaction" || job.status === "redacting") {
        await new Promise((resolve) => window.setTimeout(resolve, pollIntervalMs));
        if (!active.current) return;
        job = await getJob(credentials);
      }
      if (!active.current) return;
      if (job.status === "complete") {
        const result = await getResultBlob(credentials);
        if (!active.current) return;
        queue.replaceResult(position, job, result);
        onClose();
        return;
      }
      if (job.status === "review_required") {
        // The redaction check rejected the rebuild; the reviewer's boxes are
        // still here, so let them adjust and try again.
        setMessage(
          `${messageForCode(job.error_code ?? "processing_failed")} Adjust the boxes and apply again.`,
        );
        setPhase("editing");
        return;
      }
      setMessage(messageForCode(job.error_code ?? "processing_failed"));
      await queue.release([position]);
      setPhase("closed");
    } catch (reason) {
      if (!active.current) return;
      if (reason instanceof ApiError && reason.status === 404) {
        queue.forgetRemote(position);
        setMessage(GONE_MESSAGE);
        setPhase("closed");
      } else {
        setMessage(friendlyError(reason));
        setPhase("editing");
      }
    }
  }

  return (
    <dialog
      ref={dialog}
      className="batch-preview is-review"
      aria-labelledby="review-title"
      onCancel={(event) => {
        if (phase === "applying") event.preventDefault();
        else onClose();
      }}
    >
      <div className="batch-preview-heading">
        <div>
          <p className="eyebrow">Manual review</p>
          <h2 id="review-title">{label}</h2>
        </div>
        <div className="approval-actions">
          <button
            type="button"
            className="button button-secondary"
            onClick={onClose}
            disabled={phase === "applying"}
          >
            {phase === "closed" ? "Close" : "Cancel"}
          </button>
          {phase !== "closed" && (
            <button
              type="button"
              className="button button-primary"
              onClick={() => void apply()}
              disabled={phase !== "editing" || unchanged}
            >
              {phase === "applying" ? "Rebuilding the PDF…" : "Apply changes and rebuild PDF"}
            </button>
          )}
        </div>
      </div>
      <div className="review-dialog-body">
        {message && (
          <div className="review-alert" role="alert">
            {message}
          </div>
        )}
        {phase === "applying" && (
          <p className="job-busy-status" role="status">
            Rebuilding the PDF with your changes…
          </p>
        )}
        {phase === "loading" && (
          <p className="review-dialog-summary">Loading the suggested redactions…</p>
        )}
        {manifest && phase !== "closed" && (
          <>
            <p className="review-dialog-summary">
              <strong>
                {detections.length} {detections.length === 1 ? "redaction" : "redactions"}
              </strong>{" "}
              · {summarizeDetections(detections)}
            </p>
            <ReviewCanvas
              key={credentials.jobId}
              credentials={credentials}
              manifest={manifest}
              detections={detections}
              onChange={update}
              onUndo={undo}
              canUndo={canUndo}
              rotation={rotation}
              onRotate={setRotation}
            />
          </>
        )}
      </div>
    </dialog>
  );
}
