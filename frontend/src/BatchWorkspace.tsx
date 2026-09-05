import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { BATCH_SESSION_KEY, BatchQueue, type BatchItem } from "./batch";
import { createBatchZip } from "./batchDownload";
import { saveBlobAndDelete } from "./download";

const STATUS_LABELS = {
  waiting: "Waiting",
  uploading: "Uploading",
  processing: "Redacting",
  receiving: "Receiving PDF",
  ready: "Ready",
  failed: "Needs attention",
};
type Filter = "all" | "ready" | "failed";

function PdfPreview({
  item,
  onClose,
}: {
  item: BatchItem;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [url, setUrl] = useState<string>();
  useEffect(() => {
    const objectUrl = URL.createObjectURL(item.result!);
    setUrl(objectUrl);
    dialog.current?.showModal();
    return () => URL.revokeObjectURL(objectUrl);
  }, [item.result]);
  return (
    <dialog
      ref={dialog}
      className="batch-preview"
      aria-labelledby="preview-title"
      onCancel={onClose}
    >
      <div className="batch-preview-heading">
        <div>
          <p className="eyebrow">Redacted PDF</p>
          <h2 id="preview-title">{item.label}</h2>
        </div>
        <button
          type="button"
          className="button button-secondary"
          onClick={onClose}
          autoFocus
        >
          Close preview
        </button>
      </div>
      {url && <iframe src={url} title={`Redacted preview of ${item.label}`} />}
    </dialog>
  );
}

export default function BatchWorkspace({
  queue,
  onClose,
}: {
  queue: BatchQueue;
  onClose: () => void;
}) {
  const { items, paused, pauseReason } = useSyncExternalStore(
    queue.subscribe,
    queue.getSnapshot,
  );
  const [filter, setFilter] = useState<Filter>("all");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [downloaded, setDownloaded] = useState<Set<number>>(new Set());
  const [preview, setPreview] = useState<BatchItem | null>(null);
  const actionInFlight = useRef(false);
  const title = useRef<HTMLHeadingElement>(null);
  const ready = items.filter((item) => item.status === "ready");
  const failed = items.filter((item) => item.status === "failed");
  const waiting = items.filter((item) => item.status === "waiting");
  const activeCount =
    items.length - ready.length - failed.length - waiting.length;
  const settled = !activeCount && !waiting.length;
  const cleanupNeeded = items.some((item) => item.cleanupError);
  const handled = ready.length + failed.length;
  const unsaved = items.some(
    (item) => item.result && !downloaded.has(item.position),
  );
  const hasLocalWork =
    unsaved ||
    waiting.length > 0 ||
    activeCount > 0 ||
    items.some((item) => item.credentials);

  useEffect(() => {
    queue.start();
    title.current?.focus();
  }, [queue]);
  useEffect(() => {
    if (!hasLocalWork) return;
    const preventLoss = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", preventLoss);
    return () => window.removeEventListener("beforeunload", preventLoss);
  }, [hasLocalWork]);

  async function download(selected: BatchItem[]) {
    if (actionInFlight.current) return;
    actionInFlight.current = true;
    setBusy(selected.length === 1 ? "Preparing PDF…" : "Preparing ZIP…");
    setError(null);
    try {
      const result =
        selected.length === 1
          ? selected[0].result!
          : await createBatchZip(
              selected.map((item) => ({
                filename: item.filename,
                result: item.result!,
              })),
            );
      await saveBlobAndDelete(
        result,
        async () => {},
        selected.length === 1 ? selected[0].filename : "redacted-documents.zip",
      );
      setDownloaded(
        (previous) =>
          new Set([...previous, ...selected.map((item) => item.position)]),
      );
    } catch {
      setError(
        "The download could not be prepared. Your completed PDFs are still here. Try again or download them individually; ZIP downloads must be under 4 GB.",
      );
    } finally {
      actionInFlight.current = false;
      setBusy(null);
    }
  }

  async function cleanup(close: boolean) {
    if (activeCount || actionInFlight.current) return;
    if (
      close &&
      (unsaved || waiting.length > 0) &&
      !window.confirm(
        "Clear this batch? Unsaved PDFs and waiting files will be removed from this tab.",
      )
    )
      return;
    actionInFlight.current = true;
    setBusy("Deleting remaining server copies…");
    setError(null);
    try {
      await queue.cleanup(close);
      if (close) {
        sessionStorage.removeItem(BATCH_SESSION_KEY);
        onClose();
      }
    } catch {
      setError(
        "Some server copies could not be deleted. Retry cleanup; automatic deletion within one hour remains active.",
      );
    } finally {
      actionInFlight.current = false;
      setBusy(null);
    }
  }

  return (
    <section className="batch-workspace" aria-labelledby="batch-title">
      <div className="batch-heading">
        <div>
          <p className="eyebrow">Automatic batch redaction</p>
          <h1 id="batch-title" ref={title} tabIndex={-1}>
            {settled
              ? failed.length
                ? "Your batch needs attention"
                : "Your documents are ready"
              : paused
                ? "Your batch is paused"
                : "Redacting your documents"}
          </h1>
          <p>
            {settled
              ? `${ready.length} of ${items.length} documents ready to download.`
              : "We’ll work through the entire batch. There’s no need to open each file."}
          </p>
        </div>
        <div className="batch-heading-actions">
          {!settled && (
            <button
              className="button button-secondary"
              type="button"
              onClick={paused ? queue.resume : queue.pause}
              disabled={Boolean(busy)}
            >
              {paused ? "Resume batch" : "Pause uploads"}
            </button>
          )}
          <button
            className="button button-secondary"
            type="button"
            disabled={Boolean(busy) || activeCount > 0}
            onClick={() => void cleanup(true)}
          >
            {settled ? "Start a new batch" : "Clear batch"}
          </button>
        </div>
      </div>

      <div className="batch-overview">
        <div className="batch-metric">
          <strong>{items.length}</strong>
          <span>Documents</span>
        </div>
        <div className="batch-metric batch-metric-ready">
          <strong>{ready.length}</strong>
          <span>Ready to download</span>
        </div>
        <div className="batch-metric">
          <strong>{activeCount + waiting.length}</strong>
          <span>In progress / waiting</span>
        </div>
        <div
          className={`batch-metric ${failed.length ? "batch-metric-failed" : ""}`}
        >
          <strong>{failed.length}</strong>
          <span>Need attention</span>
        </div>
        <div className="batch-overall-progress">
          <div>
            <span>
              {handled} of {items.length} processed
            </span>
            <strong>{Math.round((handled / items.length) * 100)}%</strong>
          </div>
          <div
            className="batch-progress"
            role="progressbar"
            aria-label="Batch progress"
            aria-valuemin={0}
            aria-valuemax={items.length}
            aria-valuenow={handled}
          >
            <span style={{ width: `${(handled / items.length) * 100}%` }} />
          </div>
        </div>
      </div>

      <p className="batch-live" role="status">
        {busy ??
          (paused
            ? (pauseReason ??
              "Uploads paused. Documents already processing will finish.")
            : settled
              ? `${ready.length} ready${failed.length ? `, ${failed.length} need attention` : ""}.`
              : `${activeCount} processing · ${waiting.length} waiting. Completed files are collected automatically.`)}
      </p>
      {error && (
        <p className="batch-alert" role="alert">
          {error}
        </p>
      )}
      {cleanupNeeded && (
        <div className="batch-alert">
          Some server copies still need cleanup. Completed PDFs are available
          below.
          <button
            type="button"
            className="button button-secondary"
            disabled={Boolean(busy) || activeCount > 0}
            onClick={() => void cleanup(false)}
          >
            Retry cleanup
          </button>
        </div>
      )}

      <div className="batch-documents">
        <div className="batch-toolbar">
          <div
            className="batch-filters"
            role="group"
            aria-label="Filter documents"
          >
            {(
              [
                ["all", "All documents", items.length],
                ["ready", "Ready", ready.length],
                ["failed", "Needs attention", failed.length],
              ] as const
            ).map(([value, label, count]) => (
              <button
                key={value}
                type="button"
                aria-pressed={filter === value}
                onClick={() => setFilter(value)}
              >
                {label} <span>{count}</span>
              </button>
            ))}
          </div>
          {failed.some((item) => item.file || item.credentials) && (
            <button
              type="button"
              className="button button-secondary"
              disabled={Boolean(busy)}
              onClick={() => queue.retry()}
            >
              Retry failed files
            </button>
          )}
        </div>
        <div className="batch-table-wrap">
          <table className="batch-table">
            <thead>
              <tr>
                <th scope="col">Document</th>
                <th scope="col">Pages</th>
                <th scope="col">Redactions</th>
                <th scope="col">Status</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items
                .filter((item) => filter === "all" || item.status === filter)
                .map((item) => (
                  <tr key={item.position}>
                    <th scope="row">
                      <div className="batch-file">
                        <span className="batch-file-number">
                          {String(item.position).padStart(2, "0")}
                        </span>
                        <div>
                          <strong>{item.label}</strong>
                          <small>{item.filename}</small>
                        </div>
                      </div>
                    </th>
                    <td>{item.job?.page_count ?? "—"}</td>
                    <td>
                      {item.job &&
                      (item.status === "ready" || item.status === "receiving")
                        ? item.job.finding_count
                        : "—"}
                    </td>
                    <td>
                      <span className={`batch-status-pill is-${item.status}`}>
                        {STATUS_LABELS[item.status]}
                      </span>
                      {item.status === "processing" &&
                        Boolean(item.job?.page_count) && (
                          <small className="batch-row-detail">
                            {item.job!.pages_completed} / {item.job!.page_count}{" "}
                            pages
                          </small>
                        )}
                      {item.error && (
                        <small className="batch-row-error">{item.error}</small>
                      )}
                      {item.cleanupError && (
                        <small className="batch-row-error">
                          Server cleanup pending
                        </small>
                      )}
                    </td>
                    <td>
                      <div className="batch-row-actions">
                        {item.status === "ready" && (
                          <>
                            <button
                              type="button"
                              className="batch-text-button"
                              onClick={() => setPreview(item)}
                              aria-label={`Preview ${item.label}`}
                            >
                              Preview
                            </button>
                            <button
                              type="button"
                              className="batch-text-button"
                              disabled={Boolean(busy)}
                              onClick={() => void download([item])}
                              aria-label={`Download ${item.label}`}
                            >
                              Download
                            </button>
                          </>
                        )}
                        {item.status === "failed" &&
                          (item.file || item.credentials) && (
                            <button
                              type="button"
                              className="batch-text-button"
                              disabled={Boolean(busy)}
                              onClick={() => queue.retry(item.position)}
                              aria-label={`Retry ${item.label}`}
                            >
                              Retry
                            </button>
                          )}
                      </div>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
          {!items.some(
            (item) => filter === "all" || item.status === filter,
          ) && <p className="batch-empty">No documents in this view yet.</p>}
        </div>
      </div>

      <div className="batch-download-bar">
        <div>
          <strong>
            {ready.length
              ? `${ready.length} redacted ${ready.length === 1 ? "PDF" : "PDFs"} ready`
              : "One download for the whole batch"}
          </strong>
          <span>
            {ready.length > 0 &&
            ready.every((item) => downloaded.has(item.position))
              ? "Download started. You can download again while this tab stays open."
              : failed.length
                ? "Downloads include completed PDFs only. Failed files stay available to retry."
                : "Each document stays a separate PDF in your download."}
          </span>
        </div>
        <button
          type="button"
          className="button button-primary"
          disabled={Boolean(busy) || !ready.length}
          onClick={() => void download(ready)}
        >
          {busy?.startsWith("Preparing")
            ? busy
            : !ready.length
              ? "Download PDFs when ready"
              : ready.length === 1
                ? "Download ready PDF"
                : settled && !failed.length
                  ? `Download all ${ready.length} PDFs (ZIP)`
                  : `Download ${ready.length} ready PDFs (ZIP)`}
        </button>
      </div>
      <div className="batch-footnotes">
        <p>
          <strong>Keep this tab open until you download.</strong> Refreshing
          clears waiting files and completed local PDFs. Server copies are
          deleted after receipt or within one hour.
        </p>
        <p>
          Automatic redaction can miss sensitive information. Preview or check
          the downloaded PDFs before sharing.
        </p>
      </div>
      {preview && (
        <PdfPreview item={preview} onClose={() => setPreview(null)} />
      )}
    </section>
  );
}
