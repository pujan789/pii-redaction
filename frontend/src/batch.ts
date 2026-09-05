import {
  ApiError,
  createJob,
  deleteJob,
  getJob,
  getResultBlob,
  submitJob,
  uploadFile,
} from "./api";
import { redactedFilename } from "./fileSelection";
import type { Job, JobCredentials } from "./types";

export const BATCH_SESSION_KEY = "taxhance-pii-batch";
const CONCURRENCY = 2;
const POLL_INTERVAL = 1800;

export type BatchStatus =
  "waiting" | "uploading" | "processing" | "receiving" | "ready" | "failed";
export interface BatchItem {
  position: number;
  label: string;
  filename: string;
  file?: File;
  credentials?: JobCredentials;
  status: BatchStatus;
  job?: Job;
  result?: Blob;
  error?: string;
  cleanupError?: boolean;
}

interface StoredItem {
  position: number;
  credentials?: JobCredentials;
}

export function readBatchSession(): StoredItem[] | null {
  try {
    const value: unknown = JSON.parse(
      sessionStorage.getItem(BATCH_SESSION_KEY) ?? "null",
    );
    if (!Array.isArray(value) || !value.length) return null;
    if (
      !value.every(
        (item) =>
          Number.isInteger(item?.position) &&
          item.position > 0 &&
          (!item.credentials ||
            (typeof item.credentials.jobId === "string" &&
              typeof item.credentials.token === "string")),
      )
    )
      return null;
    return value as StoredItem[];
  } catch {
    return null;
  }
}

function messageFor(reason: unknown): string {
  if (reason instanceof Error && reason.message === "source_unavailable") {
    return "The source file was cleared on reload. Reselect the original in a new batch.";
  }
  if (reason instanceof ApiError) {
    const messages: Record<string, string> = {
      hourly_abuse_limit:
        "The hourly processing allowance has been reached. Resume later; your waiting files are still here.",
      active_job_abuse_limit:
        "Other documents are still active on this network. Resume when they finish.",
      service_busy: "The processing queue is full. Resume in a moment.",
      job_not_found:
        "This document expired or was deleted. Retry to upload it again.",
      file_signature_mismatch: "The file contents do not match its extension.",
      residual_identifier_detected:
        "A sensitive identifier remained in the output. This document was not released.",
      model_output_invalid:
        "The detector could not produce a valid result. This document was not released.",
    };
    return (
      messages[reason.code] ??
      "This document could not be processed. Retry this file."
    );
  }
  return "The connection was interrupted. Retry this file; the rest of your batch can continue.";
}

// Only two documents occupy server slots at once. Results are received in full
// before deletion, and retained as browser Blobs until the client saves the ZIP.
export class BatchQueue {
  private listeners = new Set<() => void>();
  private active = new Set<number>();
  private started = false;
  private state: {
    items: BatchItem[];
    paused: boolean;
    pauseReason: string | null;
  };

  constructor(files: File[], restored: StoredItem[] | null = null) {
    this.state = {
      paused: false,
      pauseReason: null,
      items: restored
        ? restored.map((item) => ({
            ...item,
            label: `Document ${item.position}`,
            filename: redactedFilename(item.position, restored.length),
            status: item.credentials ? "waiting" : "failed",
            error: item.credentials
              ? undefined
              : "The local file was cleared on reload. Reselect the original in a new batch.",
          }))
        : files.map((file, index) => ({
            position: index + 1,
            label: file.webkitRelativePath || file.name,
            filename: redactedFilename(index + 1, files.length),
            file,
            status: "waiting",
          })),
    };
  }

  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  private emit() {
    // Never persist source names, source bytes, results, or detected values.
    try {
      sessionStorage.setItem(
        BATCH_SESSION_KEY,
        JSON.stringify(
          this.state.items.map(({ position, credentials }) => ({
            position,
            credentials,
          })),
        ),
      );
    } catch {
      /* The in-memory batch remains usable when tab storage is unavailable. */
    }
    this.listeners.forEach((listener) => listener());
  }

  private update(position: number, change: Partial<BatchItem>) {
    this.state = {
      ...this.state,
      items: this.state.items.map((item) =>
        item.position === position ? { ...item, ...change } : item,
      ),
    };
    this.emit();
  }

  start = () => {
    this.started = true;
    this.pump();
  };

  pause = () => {
    this.state = { ...this.state, paused: true, pauseReason: null };
    this.emit();
  };

  resume = () => {
    this.state = { ...this.state, paused: false, pauseReason: null };
    this.emit();
    this.pump();
  };

  retry = (position?: number) => {
    this.state = {
      ...this.state,
      items: this.state.items.map((item) =>
        item.status === "failed" &&
        (item.file || item.credentials) &&
        (position === undefined || position === item.position)
          ? { ...item, status: "waiting", error: undefined }
          : item,
      ),
    };
    this.emit();
    this.pump();
  };

  private pump() {
    if (!this.started || this.state.paused) return;
    for (const item of this.state.items) {
      if (this.active.size >= CONCURRENCY) break;
      if (item.status !== "waiting" || this.active.has(item.position)) continue;
      this.active.add(item.position);
      void this.process(item).finally(() => {
        this.active.delete(item.position);
        this.pump();
      });
    }
  }

  private async removeRemote(credentials: JobCredentials) {
    try {
      await deleteJob(credentials);
    } catch (reason) {
      if (!(reason instanceof ApiError && reason.status === 404)) throw reason;
    }
  }

  private async process(item: BatchItem) {
    let credentials = item.credentials;
    let job: Job | undefined;
    let createdThisAttempt = false;
    this.update(item.position, {
      status: credentials ? "processing" : "uploading",
      error: undefined,
    });
    try {
      if (credentials) {
        job = await getJob(credentials);
        // Failed/partial uploads need a fresh job. A status/download retry resumes
        // the existing job instead of submitting and charging for it twice.
        if (
          ["failed", "awaiting_upload", "expired", "deleted"].includes(
            job.status,
          )
        ) {
          await this.removeRemote(credentials);
          credentials = undefined;
          this.update(item.position, { credentials: undefined });
        }
      }
      if (!credentials) {
        if (!item.file) throw new Error("source_unavailable");
        const created = await createJob(item.file, true);
        createdThisAttempt = true;
        credentials = { jobId: created.job_id, token: created.access_token };
        this.update(item.position, {
          credentials,
          status: "uploading",
          cleanupError: false,
        });
        await uploadFile(created, item.file);
        job = await submitJob(credentials);
      }
      if (!job) throw new Error("job_unavailable");
      this.update(item.position, { status: "processing", job });
      while (
        [
          "queued_detection",
          "detecting",
          "queued_redaction",
          "redacting",
        ].includes(job.status)
      ) {
        await new Promise((resolve) =>
          window.setTimeout(resolve, POLL_INTERVAL),
        );
        job = await getJob(credentials);
        this.update(item.position, { job });
      }
      if (job.status !== "complete")
        throw new ApiError(job.error_code ?? "processing_failed", 409);
      this.update(item.position, { status: "receiving" });
      const result = await getResultBlob(credentials);
      this.update(item.position, { result });
      try {
        await this.removeRemote(credentials);
        this.update(item.position, {
          credentials: undefined,
          cleanupError: false,
        });
      } catch {
        this.update(item.position, { cleanupError: true });
      }
      this.update(item.position, { status: "ready" });
    } catch (reason) {
      const capacityError =
        reason instanceof ApiError &&
        [
          "hourly_abuse_limit",
          "active_job_abuse_limit",
          "service_busy",
        ].includes(reason.code);
      if (capacityError) {
        this.state = {
          ...this.state,
          paused: true,
          pauseReason: messageFor(reason),
        };
        this.update(item.position, { status: "waiting" });
      } else {
        // Keep credentials on transient errors so retries can recover the result.
        if (reason instanceof ApiError && reason.status === 404) {
          this.update(item.position, { credentials: undefined });
        } else if (
          credentials &&
          (job?.status === "failed" || (createdThisAttempt && !job))
        ) {
          try {
            await this.removeRemote(credentials);
            this.update(item.position, { credentials: undefined });
          } catch {
            this.update(item.position, { cleanupError: true });
          }
        }
        this.update(item.position, {
          status: "failed",
          error: messageFor(reason),
        });
      }
    }
  }

  // Used after processing stops, or for cleanup failures on completed results.
  async cleanup(all = false) {
    let failed = false;
    for (const item of this.state.items) {
      if (
        !item.credentials ||
        this.active.has(item.position) ||
        (!all && !item.cleanupError)
      )
        continue;
      try {
        await this.removeRemote(item.credentials);
        this.update(item.position, {
          credentials: undefined,
          cleanupError: false,
        });
      } catch {
        failed = true;
        this.update(item.position, { cleanupError: true });
      }
    }
    if (failed) throw new Error("cleanup_failed");
  }
}
