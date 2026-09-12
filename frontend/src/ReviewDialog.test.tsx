import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";
import { BatchQueue } from "./batch";
import ReviewDialog from "./ReviewDialog";
import type { Job, Manifest } from "./types";

const apiMocks = vi.hoisted(() => ({
  createJob: vi.fn(),
  deleteJob: vi.fn(),
  finalizeJob: vi.fn(),
  getJob: vi.fn(),
  getManifest: vi.fn(),
  getPageBlob: vi.fn(),
  getResultBlob: vi.fn(),
  submitJob: vi.fn(),
  uploadFile: vi.fn(),
}));
vi.mock("./api", async () => ({
  ...(await vi.importActual<typeof import("./api")>("./api")),
  ...apiMocks,
}));

const credentials = { jobId: "alpha-job", token: "alpha-token" };

function job(status: Job["status"], extra: Partial<Job> = {}): Job {
  return {
    job_id: credentials.jobId,
    status,
    expires_at: "2099-01-01T00:00:00Z",
    page_count: 1,
    pages_completed: 1,
    finding_count: 2,
    error_code: null,
    ...extra,
  };
}

const manifest: Manifest = {
  schema_version: 1,
  job_id: credentials.jobId,
  page_count: 1,
  detections: [
    {
      id: "ssn-1",
      page_index: 0,
      category: "ssn",
      box: { x1: 100, y1: 100, x2: 300, y2: 140 },
      confidence: 0.95,
      source: "regex",
    },
    {
      id: "name-1",
      page_index: 0,
      category: "person_name",
      box: { x1: 100, y1: 200, x2: 300, y2: 240 },
      confidence: 0.9,
      source: "model",
    },
  ],
  detector_version: "t",
  prompt_version: "t",
  model_id: "t",
  created_at: "2099-01-01T00:00:00Z",
  rotation: 0,
};

const originalResult = new Blob(["original"], { type: "application/pdf" });

function readyQueue(): BatchQueue {
  const queue = new BatchQueue([], [{ position: 1, credentials }]);
  queue.replaceResult(1, job("complete"), originalResult);
  return queue;
}

function renderDialog(queue = readyQueue()) {
  const onClose = vi.fn();
  render(
    <ReviewDialog
      position={1}
      label="alpha.pdf"
      credentials={credentials}
      queue={queue}
      onClose={onClose}
      pollIntervalMs={5}
    />,
  );
  return { queue, onClose };
}

async function removeFirstBox() {
  const boxes = await screen.findAllByRole("button", { name: /redaction$/i });
  fireEvent.focus(boxes[0]);
  fireEvent.click(screen.getByRole("button", { name: /remove selected box/i }));
}

describe("batch review dialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("IntersectionObserver", undefined);
    apiMocks.getManifest.mockResolvedValue(manifest);
    apiMocks.getPageBlob.mockResolvedValue(new Blob(["jpg"], { type: "image/jpeg" }));
    apiMocks.finalizeJob.mockResolvedValue(job("queued_redaction"));
    apiMocks.getJob.mockResolvedValue(job("complete", { finding_count: 1 }));
    apiMocks.getResultBlob.mockResolvedValue(new Blob(["rebuilt"], { type: "application/pdf" }));
    apiMocks.deleteJob.mockResolvedValue(undefined);
  });

  it("opens the existing boxes without creating a new job", async () => {
    renderDialog();
    expect(await screen.findByRole("heading", { name: "alpha.pdf" })).toBeVisible();
    expect(await screen.findAllByRole("button", { name: /redaction$/i })).toHaveLength(2);
    expect(apiMocks.getManifest).toHaveBeenCalledWith(credentials);
    expect(apiMocks.createJob).not.toHaveBeenCalled();
    expect(apiMocks.submitJob).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /apply changes/i })).toBeDisabled();
    expect(screen.getByText(/2 redactions/i)).toBeVisible();
  });

  it("applies the edits, waits for the rebuild, and replaces the row's result", async () => {
    apiMocks.getJob
      .mockResolvedValueOnce(job("redacting"))
      .mockResolvedValueOnce(job("complete", { finding_count: 1 }));
    const { queue, onClose } = renderDialog();
    await removeFirstBox();
    fireEvent.click(screen.getByRole("button", { name: /rotate/i }));
    const apply = screen.getByRole("button", { name: /apply changes/i });
    expect(apply).toBeEnabled();

    fireEvent.click(apply);

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(apiMocks.finalizeJob).toHaveBeenCalledWith(credentials, [manifest.detections[1]], 90);
    expect(apiMocks.getJob).toHaveBeenCalledTimes(2);
    const row = queue.getSnapshot().items[0];
    expect(row.status).toBe("ready");
    expect(row.job?.finding_count).toBe(1);
    expect(row.result).not.toBe(originalResult);
    expect(row.credentials).toEqual(credentials);
    expect(apiMocks.deleteJob).not.toHaveBeenCalled();
  });

  it("stays open with the reason when the check sends the document back", async () => {
    apiMocks.getJob.mockResolvedValue(
      job("review_required", { error_code: "residual_identifier_detected" }),
    );
    const { onClose } = renderDialog();
    await removeFirstBox();
    fireEvent.click(screen.getByRole("button", { name: /apply changes/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/not released/i);
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /apply changes/i })).toBeEnabled();
    expect(screen.getAllByRole("button", { name: /redaction$/i })).toHaveLength(1);
  });

  it("releases the server copy when the rebuild fails outright", async () => {
    apiMocks.getJob.mockResolvedValue(job("failed", { error_code: "processing_failed" }));
    const { queue, onClose } = renderDialog();
    await removeFirstBox();
    fireEvent.click(screen.getByRole("button", { name: /apply changes/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/stopped safely/i);
    await waitFor(() => expect(apiMocks.deleteJob).toHaveBeenCalledWith(credentials));
    const row = queue.getSnapshot().items[0];
    expect(row.credentials).toBeUndefined();
    expect(row.result).toBe(originalResult);
    expect(screen.queryByRole("button", { name: /apply changes/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^close/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it("explains a server copy that is already gone", async () => {
    apiMocks.getManifest.mockRejectedValue(new ApiError("job_not_found", 404));
    const { queue } = renderDialog();

    expect(await screen.findByRole("alert")).toHaveTextContent(/no longer available/i);
    expect(queue.getSnapshot().items[0].credentials).toBeUndefined();
    expect(apiMocks.deleteJob).not.toHaveBeenCalled();
    expect(screen.queryByRole("toolbar")).not.toBeInTheDocument();
  });

  it("discards edits on cancel", async () => {
    const { queue, onClose } = renderDialog();
    await removeFirstBox();
    fireEvent.click(screen.getByRole("button", { name: /^cancel/i }));

    expect(onClose).toHaveBeenCalled();
    expect(apiMocks.finalizeJob).not.toHaveBeenCalled();
    expect(queue.getSnapshot().items[0].result).toBe(originalResult);
  });
});
