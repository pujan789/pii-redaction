import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  createJob,
  deleteJob,
  finalizeJob,
  getJob,
  getManifest,
  getResultBlob,
  submitJob,
  uploadFile,
} from "./api";
import { saveBlobAndDelete } from "./download";
import ReviewCanvas from "./ReviewCanvas";
import type { Detection, Job, JobCredentials, Manifest } from "./types";

const SESSION_KEY = "taxhance-pii-active-job";
const ACCEPTED = ["application/pdf", "image/png", "image/jpeg", "image/tiff"];
const PROCESSING = new Set([
  "queued_detection",
  "detecting",
  "queued_redaction",
  "redacting",
]);

const ERROR_MESSAGES: Record<string, string> = {
  upload_too_large: "That file is over the 50 MB abuse-prevention limit.",
  unsupported_file_type: "Choose a PDF, PNG, JPEG, or TIFF file.",
  hourly_abuse_limit: "This network has started many jobs recently. Please try again later.",
  active_job_abuse_limit: "Finish or delete the active job before starting another one.",
  service_busy: "The processing queue is full right now. Please try again shortly.",
  file_signature_mismatch: "The file contents do not match the file extension.",
  model_output_invalid: "The detector returned an unsafe result, so no document was released.",
  processing_failed: "Processing stopped safely. Your original is still scheduled for deletion.",
};

function readSession(): JobCredentials | null {
  try {
    const value = sessionStorage.getItem(SESSION_KEY);
    return value ? (JSON.parse(value) as JobCredentials) : null;
  } catch {
    return null;
  }
}

function remaining(expiresAt: string): string {
  const milliseconds = Math.max(0, new Date(expiresAt).getTime() - Date.now());
  const minutes = Math.ceil(milliseconds / 60_000);
  return minutes <= 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} hr ${minutes % 60} min`;
}

function friendlyError(error: unknown): string {
  if (error instanceof ApiError) return ERROR_MESSAGES[error.code] ?? `Request failed: ${error.code}`;
  return "Something interrupted the private workflow. Please try again.";
}

function UploadIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5M5 14.5v3A2.5 2.5 0 0 0 7.5 20h9a2.5 2.5 0 0 0 2.5-2.5v-3" />
    </svg>
  );
}

function ShieldIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 3 5.5 5.5v5.8c0 4.1 2.6 7.8 6.5 9.7 3.9-1.9 6.5-5.6 6.5-9.7V5.5L12 3Z" />
      <path d="m9.2 12 1.8 1.8 3.9-4" />
    </svg>
  );
}

export default function App() {
  const [credentials, setCredentials] = useState<JobCredentials | null>(() => readSession());
  const [job, setJob] = useState<Job | null>(null);
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [detections, setDetections] = useState<Detection[]>([]);
  const [busyLabel, setBusyLabel] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [clock, setClock] = useState(Date.now());
  const fileInput = useRef<HTMLInputElement>(null);

  function clearSession() {
    sessionStorage.removeItem(SESSION_KEY);
    setCredentials(null);
    setJob(null);
    setManifest(null);
    setDetections([]);
    setBusyLabel(null);
    setError(null);
  }

  const refresh = useCallback(async () => {
    if (!credentials) return;
    try {
      const current = await getJob(credentials);
      setJob(current);
      if (current.status === "review_required" && !manifest) {
        const draft = await getManifest(credentials);
        setManifest(draft);
        setDetections(draft.detections);
      }
      if (current.status === "failed") {
        setError(ERROR_MESSAGES[current.error_code ?? ""] ?? "Processing stopped safely.");
      }
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 404) clearSession();
      else setError(friendlyError(reason));
    }
  }, [credentials, manifest]);

  useEffect(() => {
    if (credentials) void refresh();
  }, [credentials, refresh]);

  useEffect(() => {
    if (!job || !PROCESSING.has(job.status)) return;
    const timer = window.setInterval(() => void refresh(), 1800);
    return () => window.clearInterval(timer);
  }, [job, refresh]);

  useEffect(() => {
    if (!job) return;
    const timer = window.setInterval(() => setClock(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, [job]);

  async function begin(file: File) {
    setError(null);
    setNotice(null);
    if (!ACCEPTED.includes(file.type) && !/\.(pdf|png|jpe?g|tiff?)$/i.test(file.name)) {
      setError(ERROR_MESSAGES.unsupported_file_type);
      return;
    }
    if (file.size > 50 * 1024 * 1024) {
      setError(ERROR_MESSAGES.upload_too_large);
      return;
    }
    setBusyLabel("Starting a private job…");
    try {
      const created = await createJob(file);
      const secret = { jobId: created.job_id, token: created.access_token };
      setCredentials(secret);
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(secret));
      setBusyLabel("Encrypting your upload…");
      await uploadFile(created, file);
      setBusyLabel("Adding the document to the queue…");
      const submitted = await submitJob(secret);
      setJob(submitted);
      setBusyLabel(null);
    } catch (reason) {
      setBusyLabel(null);
      setError(friendlyError(reason));
    }
  }

  async function removeJob() {
    if (!credentials) return;
    setBusyLabel("Deleting document data…");
    try {
      await deleteJob(credentials);
      clearSession();
    } catch (reason) {
      setBusyLabel(null);
      setError(friendlyError(reason));
    }
  }

  async function approve() {
    if (!credentials) return;
    setBusyLabel("Flattening the approved redactions…");
    setError(null);
    try {
      const current = await finalizeJob(credentials, detections);
      setJob(current);
      setBusyLabel(null);
    } catch (reason) {
      setBusyLabel(null);
      setError(friendlyError(reason));
    }
  }

  async function download() {
    if (!credentials) return;
    setBusyLabel("Preparing your download…");
    setError(null);
    setNotice(null);
    let browserReceivedFile = false;
    try {
      const blob = await getResultBlob(credentials);
      browserReceivedFile = true;
      setBusyLabel("Deleting the server copy…");
      await saveBlobAndDelete(blob, () => deleteJob(credentials));
      clearSession();
      setNotice("The PDF reached this browser and the server-side job was deleted.");
    } catch (reason) {
      setBusyLabel(null);
      setError(
        browserReceivedFile
          ? "The PDF reached this browser, but immediate deletion could not be confirmed. Use Delete now; the one-hour expiry is still active."
          : friendlyError(reason),
      );
    }
  }

  const progress = useMemo(() => {
    if (!job?.page_count) return null;
    return Math.min(100, Math.round((job.pages_completed / job.page_count) * 100));
  }, [job]);

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to document workspace
      </a>

      <header className="topbar">
        <div className="topbar-inner">
          <a className="wordmark" href="/pii-redaction" aria-label="Taxhance PII Redaction home">
            <img src="/taxhance-logo.png" alt="Taxhance" />
            <span className="wordmark-divider" aria-hidden="true" />
            <strong>PII Redaction</strong>
          </a>
          <div className="trust-signal">
            <span className="status-dot" aria-hidden="true" />
            Deleted after download or within 1 hour
          </div>
        </div>
      </header>

      <main id="main-content">
        {!job && !credentials ? (
          <section className="intake-layout" aria-labelledby="intake-title">
            <div className="intake-heading">
              <span className="utility-badge">Free to use · no account</span>
              <h1 id="intake-title">Redact a tax document</h1>
              <p>
                Upload a document, check every suggested redaction, and download a flattened PDF.
                The model runs on our own AWS infrastructure—not a third-party AI API.
              </p>
            </div>

            <div className="privacy-summary" aria-label="Privacy safeguards">
              <div>
                <ShieldIcon />
                <span>
                  <strong>Private by design</strong>
                  Your access key stays in this tab.
                </span>
              </div>
              <div>
                <ShieldIcon />
                <span>
                  <strong>You approve every box</strong>
                  Add or remove redactions before export.
                </span>
              </div>
              <div>
                <ShieldIcon />
                <span>
                  <strong>Short-lived files</strong>
                  Delete now, after download, or within one hour.
                </span>
              </div>
            </div>

            <div className="upload-card">
              <button
                type="button"
                className={`drop-zone ${dragging ? "is-dragging" : ""}`}
                onClick={() => fileInput.current?.click()}
                onDragEnter={(event) => {
                  event.preventDefault();
                  setDragging(true);
                }}
                onDragOver={(event) => event.preventDefault()}
                onDragLeave={(event) => {
                  if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  setDragging(false);
                  const file = event.dataTransfer.files[0];
                  if (file) void begin(file);
                }}
                disabled={Boolean(busyLabel)}
              >
                <span className="upload-glyph">
                  <UploadIcon />
                </span>
                <strong>{busyLabel ?? "Drop a tax document here"}</strong>
                <span>{busyLabel ? "Keep this tab open" : "or choose a file"}</span>
                <small>PDF, PNG, JPEG, or TIFF · up to 50 MB</small>
              </button>
              <input
                ref={fileInput}
                className="visually-hidden"
                type="file"
                accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void begin(file);
                }}
              />
              <p className="upload-note">
                Document contents are not written to application logs or used for training. File and
                request limits exist only to prevent abuse.
              </p>
            </div>

            <a className="back-link" href="/pii-redaction#privacy">
              Read the privacy and redaction policy
            </a>
          </section>
        ) : (
          <section className="job-layout" aria-labelledby="job-title">
            <div className="job-heading">
              <div>
                <p className="eyebrow">Private job · {job?.job_id.slice(0, 8) ?? "restoring"}</p>
                <h1 id="job-title">
                  {job?.status === "review_required" ? "Review the suggested redactions" : "Document redaction"}
                </h1>
              </div>
              <div className="job-actions">
                {job && (
                  <span className="expiry-clock" key={clock}>
                    Auto-deletes in <strong>{remaining(job.expires_at)}</strong>
                  </span>
                )}
                <button className="button button-danger" type="button" onClick={() => void removeJob()}>
                  Delete now
                </button>
              </div>
            </div>

            {job?.status === "review_required" && manifest ? (
              <>
                <div className="review-warning" role="note">
                  <ShieldIcon />
                  <p>
                    <strong>Check every page before exporting.</strong> Automated detection can miss PII
                    or mark something that should stay visible.
                  </p>
                </div>
                <ReviewCanvas
                  credentials={credentials!}
                  manifest={manifest}
                  detections={detections}
                  onChange={setDetections}
                />
                <div className="approval-bar">
                  <div>
                    <strong>{detections.length} redactions ready</strong>
                    <span>The approved pages will be rebuilt as a flattened PDF.</span>
                  </div>
                  <button className="button button-primary" type="button" onClick={() => void approve()}>
                    Approve and flatten PDF
                  </button>
                </div>
              </>
            ) : job?.status === "complete" ? (
              <div className="completion-card">
                <span className="completion-seal" aria-hidden="true">
                  <ShieldIcon />
                </span>
                <p className="eyebrow">Ready to download</p>
                <h2>Your redacted PDF is ready</h2>
                <p>
                  The output is rebuilt from rendered page images, without selectable source text,
                  form fields, attachments, or original metadata. Once the full file reaches this
                  browser, we request immediate deletion of the server copy.
                </p>
                <div className="completion-actions">
                  <button className="button button-primary" type="button" onClick={() => void download()}>
                    Download and delete server copy
                  </button>
                  <button className="button button-secondary" type="button" onClick={() => void removeJob()}>
                    Delete without downloading
                  </button>
                </div>
              </div>
            ) : (
              <div className="processing-card" aria-live="polite">
                <div className="processing-icon" aria-hidden="true">
                  <ShieldIcon />
                </div>
                <p className="eyebrow">{job?.status.replaceAll("_", " ") ?? "restoring job"}</p>
                <h2>{busyLabel ?? "Checking the document for sensitive data…"}</h2>
                <p>
                  The detector hides recipient-side PII while keeping payer information, account
                  numbers, form numbers, and city, state, and postal code available for review.
                </p>
                <div
                  className="progress-track"
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={progress ?? undefined}
                  aria-label="Document processing progress"
                >
                  <span style={{ width: `${progress ?? 8}%` }} />
                </div>
                <small>
                  {job?.page_count
                    ? `${job.pages_completed} of ${job.page_count} pages`
                    : "Waiting securely in the queue"}
                </small>
              </div>
            )}
          </section>
        )}

        {error && (
          <div className="toast toast-error" role="alert">
            <p>{error}</p>
            <button type="button" onClick={() => setError(null)} aria-label="Dismiss error">
              ×
            </button>
          </div>
        )}
        {notice && (
          <div className="toast toast-success" role="status">
            <p>{notice}</p>
            <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss message">
              ×
            </button>
          </div>
        )}
      </main>

      <footer>
        <span>Qwen3-VL-8B-Instruct · self-hosted on AWS</span>
        <nav aria-label="Project information">
          <a href="https://github.com/TaxHance/pii-redaction">Source code</a>
          <a href="https://github.com/TaxHance/pii-redaction/blob/main/SECURITY.md">Security</a>
          <a href="https://github.com/TaxHance/pii-redaction/blob/main/README.md">Self-hosting</a>
        </nav>
      </footer>
    </div>
  );
}
