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
import { BatchQueue, readBatchSession } from "./batch";
import BatchWorkspace from "./BatchWorkspace";
import {
  redactedFilename,
  selectUploadFiles,
  SUPPORTED_FILE_ACCEPT,
} from "./fileSelection";
import ReviewCanvas from "./ReviewCanvas";
import type { Detection, Job, JobCredentials, Manifest } from "./types";

const SESSION_KEY = "taxhance-pii-active-job";
const PROCESSING = new Set([
  "queued_detection",
  "detecting",
  "queued_redaction",
  "redacting",
]);

const ERROR_MESSAGES: Record<string, string> = {
  upload_too_large: "That file is over the 50 MB abuse-prevention limit.",
  upload_empty: "Empty files cannot be processed.",
  unsupported_file_type: "Choose a PDF, PNG, JPEG, or TIFF file.",
  hourly_abuse_limit: "This network has started many jobs recently. Please try again later.",
  active_job_abuse_limit: "Finish or delete the active job before starting another one.",
  service_busy: "The processing queue is full right now. Please try again shortly.",
  file_signature_mismatch: "The file contents do not match the file extension.",
  model_output_invalid: "The detector returned an unsafe result, so no document was released.",
  processing_failed: "Processing stopped safely. Your original is still scheduled for deletion.",
  delete_failed:
    "Immediate deletion could not be confirmed. Please retry; automatic expiry cleanup remains active.",
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
  const [automaticBatch, setAutomaticBatch] = useState(true);
  const [batchQueue, setBatchQueue] = useState<BatchQueue | null>(() => {
    const restored = readBatchSession();
    return restored ? new BatchQueue([], restored) : null;
  });
  const [credentials, setCredentials] = useState<JobCredentials | null>(() => readSession());
  const [job, setJob] = useState<Job | null>(null);
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [detections, setDetections] = useState<Detection[]>([]);
  const [busyLabel, setBusyLabel] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [clock, setClock] = useState(Date.now());
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [batchTotal, setBatchTotal] = useState(0);
  const [batchPosition, setBatchPosition] = useState(0);
  const [currentFileLabel, setCurrentFileLabel] = useState<string | null>(null);
  const [currentDownloadName, setCurrentDownloadName] = useState("redacted.pdf");
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const intakeTitle = useRef<HTMLHeadingElement>(null);
  const jobTitle = useRef<HTMLHeadingElement>(null);
  const retryFile = useRef<File | null>(null);
  const jobStartInFlight = useRef(false);
  const actionInFlight = useRef(false);
  const activeJobId = useRef(credentials?.jobId ?? null);
  const hasLoadedJob = useRef(false);
  const refreshSequence = useRef(0);
  const refreshInFlight = useRef<{ jobId: string; sequence: number } | null>(null);
  const lastWorkspaceFocusKey = useRef<string | null>(null);
  const [loadFailure, setLoadFailure] = useState<"status" | "review" | null>(null);

  const captureFolderInput = useCallback((element: HTMLInputElement | null) => {
    folderInput.current = element;
    if (element) {
      element.setAttribute("webkitdirectory", "");
      element.setAttribute("directory", "");
    }
  }, []);

  const clearCurrentJob = useCallback(() => {
    activeJobId.current = null;
    refreshInFlight.current = null;
    refreshSequence.current += 1;
    sessionStorage.removeItem(SESSION_KEY);
    hasLoadedJob.current = false;
    setCredentials(null);
    setJob(null);
    setManifest(null);
    setDetections([]);
    setBusyLabel(null);
    setError(null);
    setLoadFailure(null);
  }, []);

  const clearSession = useCallback(() => {
    clearCurrentJob();
    retryFile.current = null;
    setPendingFiles([]);
    setBatchTotal(0);
    setBatchPosition(0);
    setCurrentFileLabel(null);
    setCurrentDownloadName("redacted.pdf");
    setNotice(null);
  }, [clearCurrentJob]);

  const refresh = useCallback(async () => {
    if (!credentials || jobStartInFlight.current) return;
    const requestedJobId = credentials.jobId;
    if (refreshInFlight.current?.jobId === requestedJobId) return;
    const requestSequence = ++refreshSequence.current;
    const request = { jobId: requestedJobId, sequence: requestSequence };
    refreshInFlight.current = request;
    const isCurrentRequest = () =>
      activeJobId.current === requestedJobId && refreshSequence.current === requestSequence;
    let failedStage: "status" | "review" = "status";
    try {
      const current = await getJob(credentials);
      if (!isCurrentRequest()) return;
      hasLoadedJob.current = true;
      setJob(current);
      if (current.status === "review_required" && !manifest) {
        failedStage = "review";
        const draft = await getManifest(credentials);
        if (!isCurrentRequest()) return;
        setManifest(draft);
        setDetections(draft.detections);
      }
      setLoadFailure(null);
      if (current.status === "failed") {
        setError(ERROR_MESSAGES[current.error_code ?? ""] ?? "Processing stopped safely.");
      }
    } catch (reason) {
      if (!isCurrentRequest()) return;
      if (reason instanceof ApiError && reason.status === 404) {
        if (pendingFiles.length > 0) {
          clearCurrentJob();
          setError("This document is no longer available. Skip it to continue the batch.");
        } else {
          clearSession();
        }
      } else {
        if (failedStage === "review" || !hasLoadedJob.current) setLoadFailure(failedStage);
        setError(friendlyError(reason));
      }
    } finally {
      if (refreshInFlight.current === request) refreshInFlight.current = null;
    }
  }, [clearCurrentJob, clearSession, credentials, manifest, pendingFiles]);

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

  useEffect(() => {
    const focusKey =
      batchTotal > 0
        ? `batch:${batchPosition}`
        : credentials
          ? `restored:${credentials.jobId}`
          : null;
    if (focusKey === lastWorkspaceFocusKey.current) return;
    if (focusKey) {
      lastWorkspaceFocusKey.current = focusKey;
      jobTitle.current?.focus();
    } else if (lastWorkspaceFocusKey.current) {
      lastWorkspaceFocusKey.current = null;
      intakeTitle.current?.focus();
    }
  }, [batchPosition, batchTotal, credentials]);

  async function startFile(file: File, position: number, total: number) {
    if (jobStartInFlight.current) return;
    jobStartInFlight.current = true;
    retryFile.current = file;
    setError(null);
    const fileLabel = file.webkitRelativePath || file.name;
    setCurrentFileLabel(fileLabel);
    // Source and folder names can contain PII, so browser download history only
    // receives a neutral position-based filename.
    setCurrentDownloadName(redactedFilename(position, total));
    setBusyLabel("Starting a private job…");
    let secret: JobCredentials | null = null;
    try {
      const created = await createJob(file);
      secret = { jobId: created.job_id, token: created.access_token };
      activeJobId.current = secret.jobId;
      setCredentials(secret);
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(secret));
      setBusyLabel("Encrypting your upload…");
      await uploadFile(created, file);
      setBusyLabel("Adding the document to the queue…");
      const submitted = await submitJob(secret);
      hasLoadedJob.current = true;
      setJob(submitted);
      retryFile.current = null;
      setBusyLabel(null);
    } catch (reason) {
      setBusyLabel(null);
      if (!secret) {
        clearCurrentJob();
        setNotice(null);
      }
      setError(friendlyError(reason));
    } finally {
      jobStartInFlight.current = false;
    }
  }

  function beginBatch(files: Iterable<File>) {
    if (busyLabel || jobStartInFlight.current || actionInFlight.current) return;
    const selection = selectUploadFiles(files);
    setError(null);
    setNotice(null);

    const skippedNotes: string[] = [];
    if (selection.unsupportedCount > 0) {
      skippedNotes.push(
        `${selection.unsupportedCount} unsupported ${selection.unsupportedCount === 1 ? "file was" : "files were"} skipped.`,
      );
    }
    if (selection.oversizedCount > 0) {
      skippedNotes.push(
        `${selection.oversizedCount} ${selection.oversizedCount === 1 ? "file was" : "files were"} over 50 MB and skipped.`,
      );
    }
    if (selection.emptyCount > 0) {
      skippedNotes.push(
        `${selection.emptyCount} empty ${selection.emptyCount === 1 ? "file was" : "files were"} skipped.`,
      );
    }

    if (selection.accepted.length === 0) {
      if (skippedNotes.length === 0) return;
      if (selection.unsupportedCount > 0) {
        skippedNotes.push(ERROR_MESSAGES.unsupported_file_type);
      }
      setError(skippedNotes.join(" "));
      return;
    }

    if (selection.accepted.length > 1 && automaticBatch) {
      setNotice(skippedNotes.join(" ") || null);
      setBatchQueue(new BatchQueue(selection.accepted));
      return;
    }

    const [first, ...pending] = selection.accepted;
    const notes: string[] = [];
    if (selection.accepted.length > 1) {
      notes.push(`${selection.accepted.length} supported files queued.`);
    }
    notes.push(...skippedNotes);
    if (selection.accepted.length > 1) {
      notes.push("They will upload one at a time; refreshing this tab clears the local queue.");
    }

    setPendingFiles(pending);
    setBatchTotal(selection.accepted.length);
    setBatchPosition(1);
    setNotice(notes.join(" ") || null);
    void startFile(first, 1, selection.accepted.length);
  }

  function startNextFile(message: string): boolean {
    const [next, ...remaining] = pendingFiles;
    if (!next) return false;

    clearCurrentJob();
    retryFile.current = null;
    setPendingFiles(remaining);
    const nextPosition = batchPosition + 1;
    setBatchPosition(nextPosition);
    setNotice(message);
    void startFile(next, nextPosition, batchTotal);
    return true;
  }

  async function retryCurrentFile() {
    const file = retryFile.current;
    if (!file || actionInFlight.current || jobStartInFlight.current) return;

    actionInFlight.current = true;
    setBusyLabel("Resetting this file…");
    try {
      if (credentials) {
        try {
          await deleteJob(credentials);
        } catch (reason) {
          if (!(reason instanceof ApiError && reason.status === 404)) throw reason;
        }
      }
      clearCurrentJob();
      await startFile(file, batchPosition, batchTotal);
    } catch (reason) {
      setBusyLabel(null);
      setError(friendlyError(reason));
    } finally {
      actionInFlight.current = false;
    }
  }

  function cancelCurrentJob() {
    if (
      batchTotal > 1 &&
      pendingFiles.length > 0 &&
      !window.confirm(
        `Cancel this batch? The current document and ${pendingFiles.length} waiting ${pendingFiles.length === 1 ? "file" : "files"} will be removed.`,
      )
    ) {
      return;
    }
    void removeJob(false);
  }

  async function removeJob(continueBatch = false) {
    if (actionInFlight.current || jobStartInFlight.current) return;
    actionInFlight.current = true;
    const hadServerJob = Boolean(credentials);
    setBusyLabel("Deleting document data…");
    try {
      if (credentials) {
        try {
          await deleteJob(credentials);
        } catch (reason) {
          if (!(reason instanceof ApiError && reason.status === 404)) throw reason;
        }
      }
      retryFile.current = null;
      if (
        continueBatch &&
        startNextFile(`File ${batchPosition} was deleted. Starting the next file…`)
      ) {
        return;
      }
      const cancelledBatch = batchTotal > 1 && pendingFiles.length > 0;
      const endedBatch = batchTotal > 1;
      clearSession();
      if (cancelledBatch) {
        setNotice("The batch was cancelled and the remaining local queue was cleared.");
      } else if (endedBatch) {
        setNotice(
          hadServerJob
            ? "The batch ended and the final server-side job was deleted."
            : "The batch ended and its local queue was cleared.",
        );
      }
    } catch (reason) {
      setBusyLabel(null);
      setError(friendlyError(reason));
    } finally {
      actionInFlight.current = false;
    }
  }

  async function approve() {
    if (!credentials || actionInFlight.current || jobStartInFlight.current) return;
    actionInFlight.current = true;
    setBusyLabel("Flattening the approved redactions…");
    setError(null);
    try {
      const current = await finalizeJob(credentials, detections);
      setJob(current);
      setBusyLabel(null);
    } catch (reason) {
      setBusyLabel(null);
      setError(friendlyError(reason));
    } finally {
      actionInFlight.current = false;
    }
  }

  async function retryStatus() {
    if (!credentials || actionInFlight.current || jobStartInFlight.current) return;
    actionInFlight.current = true;
    setBusyLabel("Reloading job data…");
    setError(null);
    try {
      await refresh();
    } finally {
      setBusyLabel(null);
      actionInFlight.current = false;
    }
  }

  async function download() {
    if (!credentials || actionInFlight.current || jobStartInFlight.current) return;
    actionInFlight.current = true;
    setBusyLabel("Preparing your download…");
    setError(null);
    setNotice(null);
    let browserReceivedFile = false;
    try {
      const blob = await getResultBlob(credentials);
      browserReceivedFile = true;
      setBusyLabel("Deleting the server copy…");
      await saveBlobAndDelete(
        blob,
        async () => {
          try {
            await deleteJob(credentials);
          } catch (reason) {
            // Expiry cleanup may win this race; a missing job is already deleted.
            if (!(reason instanceof ApiError && reason.status === 404)) throw reason;
          }
        },
        currentDownloadName,
      );
      if (
        startNextFile(
          `Downloaded file ${batchPosition} of ${batchTotal}. Starting the next file…`,
        )
      ) {
        return;
      }
      const completedBatch = batchTotal;
      clearSession();
      setNotice(
        completedBatch > 1
          ? "The batch is complete. Downloaded PDFs reached this browser, and all server-side jobs were deleted."
          : "The PDF reached this browser and the server-side job was deleted.",
      );
    } catch (reason) {
      setBusyLabel(null);
      setError(
        browserReceivedFile
          ? "The PDF reached this browser, but immediate deletion could not be confirmed. Use a delete option; the one-hour expiry is still active."
          : friendlyError(reason),
      );
    } finally {
      actionInFlight.current = false;
    }
  }

  const progress = useMemo(() => {
    if (!job?.page_count) return null;
    return Math.min(100, Math.round((job.pages_completed / job.page_count) * 100));
  }, [job]);
  const reviewLoadFailed = Boolean(
    loadFailure === "review" && credentials && job?.status === "review_required" && !manifest,
  );
  const statusLoadFailed = Boolean(
    loadFailure === "status" && credentials && !job && !retryFile.current,
  );
  const batchHandled = Math.max(0, batchPosition - 1);
  const nextFile = pendingFiles[0];
  const nextFileLabel = nextFile ? nextFile.webkitRelativePath || nextFile.name : null;

  const siteRoot = window.location.pathname.replace(/\/app(?:\/index\.html|\/)?$/, "/");

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to document workspace
      </a>

      <header className="topbar">
        <div className="topbar-inner">
          <a className="wordmark" href={siteRoot} aria-label="Taxhance PII Redaction home">
            <img src={`${siteRoot}taxhance-logo.png`} alt="Taxhance" />
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
        {error ? (
          <div className="toast toast-error" role="alert">
            <p>{error}</p>
            <button
              type="button"
              onClick={() => {
                setError(null);
                setNotice(null);
              }}
              aria-label="Dismiss error"
            >
              ×
            </button>
          </div>
        ) : notice ? (
          <div className="toast toast-success" role="status">
            <p>{notice}</p>
            <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss message">
              ×
            </button>
          </div>
        ) : null}

        {batchQueue ? (
          <BatchWorkspace queue={batchQueue} onClose={() => {
            setBatchQueue(null);
            setNotice(null);
            window.setTimeout(() => intakeTitle.current?.focus(), 0);
          }} />
        ) : !job && !credentials && batchTotal === 0 ? (
          <section className="intake-layout" aria-labelledby="intake-title">
            <div className="intake-heading">
              <span className="utility-badge">Free to use · no account</span>
              <h1 id="intake-title" ref={intakeTitle} tabIndex={-1}>
                Redact a tax document
              </h1>
              <p>
                Upload a document or an entire folder. Redact automatically, or review
                each file before downloading.
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
                  <strong>Your choice of workflow</strong>
                  Automatic batches or detailed manual review.
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
              <fieldset className="batch-mode">
                <legend>When you upload multiple files</legend>
                <div className="batch-mode-options">
                  <label className={automaticBatch ? "is-selected" : ""}>
                    <input type="radio" name="batch-mode" checked={automaticBatch} onChange={() => setAutomaticBatch(true)} />
                    <span><strong>Redact automatically</strong><small>Process every file and download together.</small></span>
                  </label>
                  <label className={!automaticBatch ? "is-selected" : ""}>
                    <input type="radio" name="batch-mode" checked={!automaticBatch} onChange={() => setAutomaticBatch(false)} />
                    <span><strong>Review each document</strong><small>Adjust suggested redactions before export.</small></span>
                  </label>
                </div>
              </fieldset>
              <div
                className={`drop-zone ${dragging ? "is-dragging" : ""} ${busyLabel ? "is-busy" : ""}`}
                role="group"
                aria-label="Upload documents"
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
                  beginBatch(event.dataTransfer.files);
                }}
              >
                <span className="upload-glyph">
                  <UploadIcon />
                </span>
                <strong>{busyLabel ?? "Drop one or more files here"}</strong>
                <span>{busyLabel ? "Keep this tab open" : "or choose files or a folder below"}</span>
                <div className="upload-actions">
                  <button
                    className="button button-primary"
                    type="button"
                    onClick={() => fileInput.current?.click()}
                    disabled={Boolean(busyLabel)}
                  >
                    Choose files
                  </button>
                  <button
                    className="button button-secondary"
                    type="button"
                    onClick={() => folderInput.current?.click()}
                    disabled={Boolean(busyLabel)}
                  >
                    Choose folder
                  </button>
                </div>
                <small>PDF, PNG, JPEG, or TIFF · each file up to 50 MB</small>
              </div>
              <input
                ref={fileInput}
                data-testid="files-input"
                type="file"
                accept={SUPPORTED_FILE_ACCEPT}
                multiple
                hidden
                onChange={(event) => {
                  if (event.currentTarget.files) beginBatch(event.currentTarget.files);
                  event.currentTarget.value = "";
                }}
              />
              <input
                ref={captureFolderInput}
                data-testid="folder-input"
                type="file"
                accept={SUPPORTED_FILE_ACCEPT}
                multiple
                hidden
                onChange={(event) => {
                  if (event.currentTarget.files) beginBatch(event.currentTarget.files);
                  event.currentTarget.value = "";
                }}
              />
              <p className="upload-note">
                Use Choose folder to queue supported files from a folder and its subfolders. Document
                contents are not written to application logs or used for training.
              </p>
            </div>

            <a className="back-link" href={`${siteRoot}#privacy`} target="_blank" rel="noopener noreferrer">
              Read the privacy and redaction policy
            </a>
          </section>
        ) : (
          <section
            className="job-layout"
            aria-labelledby="job-title"
            aria-busy={Boolean(busyLabel)}
          >
            <div className="job-heading">
              <div>
                <p className="eyebrow">
                  {batchTotal > 1
                    ? `Private batch · file ${batchPosition} of ${batchTotal}`
                    : "Private job"}{" "}
                  · {job?.job_id.slice(0, 8) ?? "restoring"}
                </p>
                <h1 id="job-title" ref={jobTitle} tabIndex={-1}>
                  {job?.status === "review_required" ? "Review the suggested redactions" : "Document redaction"}
                </h1>
                {currentFileLabel && (
                  <p className="current-file" title={currentFileLabel}>
                    {currentFileLabel}
                  </p>
                )}
              </div>
              <div className="job-actions">
                {job && (
                  <span className="expiry-clock" key={clock}>
                    Auto-deletes in <strong>{remaining(job.expires_at)}</strong>
                  </span>
                )}
                <button
                  className="button button-danger"
                  type="button"
                  onClick={cancelCurrentJob}
                  disabled={Boolean(busyLabel)}
                >
                  {batchTotal > 1 ? "Cancel batch" : "Delete now"}
                </button>
              </div>
            </div>

            {busyLabel &&
              (job?.status === "review_required" || job?.status === "complete") && (
                <p className="job-busy-status" role="status">
                  {busyLabel}
                </p>
              )}

            <p className="visually-hidden" aria-live="polite">
              {job?.status === "review_required" && manifest
                ? "Document ready for redaction review."
                : job?.status === "complete"
                  ? "Redacted PDF ready to download."
                  : ""}
            </p>

            {batchTotal > 1 && (
              <div className="batch-status">
                <div className="batch-copy">
                  <strong>
                    File {batchPosition} of {batchTotal}
                  </strong>
                  <span>
                    {pendingFiles.length > 0
                      ? `${pendingFiles.length} ${pendingFiles.length === 1 ? "file is" : "files are"} waiting locally. Next: ${nextFileLabel}. Refreshing clears the queue.`
                      : "This is the final file in the batch."}
                  </span>
                </div>
                <div
                  className="batch-progress"
                  role="progressbar"
                  aria-label="Batch progress"
                  aria-valuemin={0}
                  aria-valuemax={batchTotal}
                  aria-valuenow={batchHandled}
                  aria-valuetext={`${batchHandled} of ${batchTotal} files handled`}
                >
                  <span style={{ width: `${(batchHandled / batchTotal) * 100}%` }} />
                </div>
              </div>
            )}

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
                  <button
                    className="button button-primary"
                    type="button"
                    onClick={() => void approve()}
                    disabled={Boolean(busyLabel)}
                  >
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
                  <button
                    className="button button-primary"
                    type="button"
                    onClick={() => void download()}
                    disabled={Boolean(busyLabel)}
                  >
                    {pendingFiles.length > 0
                      ? "Download and continue to next file"
                      : "Download and delete server copy"}
                  </button>
                  <button
                    className="button button-secondary"
                    type="button"
                    onClick={() => void removeJob(pendingFiles.length > 0)}
                    disabled={Boolean(busyLabel)}
                  >
                    {pendingFiles.length > 0 ? "Skip this file" : "Delete without downloading"}
                  </button>
                </div>
              </div>
            ) : (
              <div className="processing-card" aria-live="polite">
                <div className="processing-icon" aria-hidden="true">
                  <ShieldIcon />
                </div>
                <p className="eyebrow">{job?.status.replaceAll("_", " ") ?? "restoring job"}</p>
                <h2>
                  {busyLabel ??
                    (retryFile.current
                      ? "This file could not be started"
                      : job?.status === "failed"
                        ? "This file could not be processed"
                        : "Checking the document for sensitive data…")}
                </h2>
                <p>
                  The detector hides recipient-side PII while keeping payer information, account
                  numbers, form numbers, and city, state, and postal code available for review.
                </p>
                {!busyLabel &&
                  (retryFile.current ||
                    reviewLoadFailed ||
                    statusLoadFailed ||
                    (pendingFiles.length > 0 && (!credentials || job?.status === "failed"))) && (
                  <div className="batch-recovery">
                    {retryFile.current && (
                      <button
                        className="button button-primary"
                        type="button"
                        onClick={() => void retryCurrentFile()}
                        disabled={Boolean(busyLabel)}
                      >
                        Retry this file
                      </button>
                    )}
                    {(reviewLoadFailed || statusLoadFailed) && (
                      <button
                        className="button button-primary"
                        type="button"
                        onClick={() => void retryStatus()}
                        disabled={Boolean(busyLabel)}
                      >
                        {reviewLoadFailed ? "Retry review" : "Retry status"}
                      </button>
                    )}
                    {pendingFiles.length > 0 && (
                      <button
                        className="button button-secondary"
                        type="button"
                        onClick={() => void removeJob(true)}
                        disabled={Boolean(busyLabel)}
                      >
                        Skip file and continue
                      </button>
                    )}
                  </div>
                )}
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

      </main>

      <footer>
        <span>Gemma 4 E2B · hosted on AWS EC2</span>
        <nav aria-label="Project information">
          <a href="https://github.com/pujan789/pii-redaction" target="_blank" rel="noopener noreferrer">Source code</a>
          <a href="https://github.com/pujan789/pii-redaction/blob/main/SECURITY.md" target="_blank" rel="noopener noreferrer">Security</a>
          <a href={`${siteRoot}self-hosting/`} target="_blank" rel="noopener noreferrer">Self-hosting</a>
        </nav>
      </footer>
    </div>
  );
}
