import { waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";
import { BATCH_SESSION_KEY, BatchQueue, readBatchSession } from "./batch";
import type { Job } from "./types";

const api = vi.hoisted(() => ({
  createJob: vi.fn(),
  deleteJob: vi.fn(),
  getJob: vi.fn(),
  getResultBlob: vi.fn(),
  submitJob: vi.fn(),
  uploadFile: vi.fn(),
}));
vi.mock("./api", async () => ({
  ...(await vi.importActual<typeof import("./api")>("./api")),
  ...api,
}));

function complete(jobId: string): Job {
  return {
    job_id: jobId,
    status: "complete",
    expires_at: "2099-01-01T00:00:00Z",
    page_count: 1,
    pages_completed: 1,
    finding_count: 3,
    error_code: null,
  };
}
const files = (count: number) =>
  Array.from(
    { length: count },
    (_, index) =>
      new File(["synthetic"], `file-${index}.pdf`, { type: "application/pdf" }),
  );

describe("automatic batch processing", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    sessionStorage.clear();
    api.createJob.mockImplementation(async (file: File) => ({
      job_id: file.name,
      access_token: "token",
      upload: {},
    }));
    api.uploadFile.mockResolvedValue(undefined);
    api.submitJob.mockImplementation(async ({ jobId }: { jobId: string }) =>
      complete(jobId),
    );
    api.getJob.mockImplementation(async ({ jobId }: { jobId: string }) =>
      complete(jobId),
    );
    api.getResultBlob.mockResolvedValue(new Blob(["redacted"]));
    api.deleteJob.mockResolvedValue(undefined);
  });
  afterEach(() => vi.useRealTimers());

  it("completes 50 documents without individual approvals or downloads", async () => {
    const queue = new BatchQueue(files(50));
    queue.start();
    queue.start(); // React StrictMode must not double-submit.
    await waitFor(() =>
      expect(
        queue.getSnapshot().items.filter((item) => item.status === "ready"),
      ).toHaveLength(50),
    );
    expect(api.createJob).toHaveBeenCalledTimes(50);
    expect(api.createJob).toHaveBeenCalledWith(expect.any(File), true);
    // Server copies stay until download or clear so any document can be reopened.
    expect(api.deleteJob).not.toHaveBeenCalled();
    expect(queue.getSnapshot().items.every((item) => item.credentials)).toBe(true);
    expect(
      new Set(queue.getSnapshot().items.map((item) => item.filename)).size,
    ).toBe(50);
    // Only positions and credentials persist; never labels, files, or results.
    const stored = JSON.parse(sessionStorage.getItem(BATCH_SESSION_KEY) ?? "[]");
    expect(Object.keys(stored[0]).sort()).toEqual(["credentials", "position"]);
  });

  it("limits active jobs to two and starts the next as a result is received", async () => {
    let finishFirst: ((value: Blob) => void) | undefined;
    let finishSecond: ((value: Blob) => void) | undefined;
    api.getResultBlob
      .mockImplementationOnce(
        () =>
          new Promise<Blob>((resolve) => {
            finishFirst = resolve;
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise<Blob>((resolve) => {
            finishSecond = resolve;
          }),
      );
    const queue = new BatchQueue(files(3));
    queue.start();
    await waitFor(() => expect(api.getResultBlob).toHaveBeenCalledTimes(2));
    expect(api.createJob).toHaveBeenCalledTimes(2);
    expect(api.deleteJob).not.toHaveBeenCalled();
    finishFirst!(new Blob(["first"]));
    await waitFor(() => expect(api.createJob).toHaveBeenCalledTimes(3));
    finishSecond!(new Blob(["second"]));
    await waitFor(() =>
      expect(
        queue.getSnapshot().items.every((item) => item.status === "ready"),
      ).toBe(true),
    );
  });

  it("continues past a bad upload and retries only that file", async () => {
    api.uploadFile.mockRejectedValueOnce(new Error("offline"));
    const queue = new BatchQueue(files(3));
    queue.start();
    await waitFor(() =>
      expect(queue.getSnapshot().items.map((item) => item.status)).toEqual([
        "failed",
        "ready",
        "ready",
      ]),
    );
    queue.retry();
    await waitFor(() =>
      expect(
        queue.getSnapshot().items.every((item) => item.status === "ready"),
      ).toBe(true),
    );
    expect(api.createJob).toHaveBeenCalledTimes(4);
  });

  it("retries an interrupted result fetch without creating another job or losing it", async () => {
    api.getResultBlob.mockRejectedValueOnce(new Error("offline"));
    const queue = new BatchQueue(files(1));
    queue.start();
    await waitFor(() =>
      expect(queue.getSnapshot().items[0].status).toBe("failed"),
    );
    expect(api.deleteJob).not.toHaveBeenCalled();
    queue.retry();
    await waitFor(() =>
      expect(queue.getSnapshot().items[0].status).toBe("ready"),
    );
    expect(api.createJob).toHaveBeenCalledTimes(1);
    expect(api.getResultBlob).toHaveBeenCalledTimes(2);
  });

  it("does not delete a restored job when its status request fails", async () => {
    api.getJob.mockRejectedValueOnce(new Error("offline"));
    const queue = new BatchQueue(
      [],
      [{ position: 1, credentials: { jobId: "existing", token: "secret" } }],
    );
    queue.start();
    await waitFor(() =>
      expect(queue.getSnapshot().items[0].status).toBe("failed"),
    );
    expect(api.deleteJob).not.toHaveBeenCalled();
    queue.retry();
    await waitFor(() =>
      expect(queue.getSnapshot().items[0].status).toBe("ready"),
    );
    expect(api.createJob).not.toHaveBeenCalled();
  });

  it("keeps a downloaded PDF when its server copy cannot be deleted, then allows retry", async () => {
    api.deleteJob.mockRejectedValueOnce(new Error("offline"));
    const queue = new BatchQueue(files(1));
    queue.start();
    await waitFor(() =>
      expect(queue.getSnapshot().items[0].status).toBe("ready"),
    );
    expect(queue.getSnapshot().items[0].cleanupError).toBeFalsy();

    expect(await queue.release([1])).toBe(false);
    expect(queue.getSnapshot().items[0]).toMatchObject({
      cleanupError: true,
      result: expect.any(Blob),
      credentials: expect.any(Object),
    });
    await queue.cleanup();
    expect(queue.getSnapshot().items[0]).toMatchObject({
      cleanupError: false,
      credentials: undefined,
      result: expect.any(Blob),
    });
  });

  it("releases only the requested server copies and can replace a result", async () => {
    const queue = new BatchQueue(files(2));
    queue.start();
    await waitFor(() =>
      expect(queue.getSnapshot().items.every((item) => item.status === "ready")).toBe(true),
    );

    expect(await queue.release([2])).toBe(true);
    expect(api.deleteJob).toHaveBeenCalledTimes(1);
    expect(api.deleteJob).toHaveBeenCalledWith({ jobId: "file-1.pdf", token: "token" });
    expect(queue.getSnapshot().items[0].credentials).toBeDefined();
    expect(queue.getSnapshot().items[1].credentials).toBeUndefined();

    const rebuilt = new Blob(["rebuilt"]);
    queue.replaceResult(1, { ...complete("file-0.pdf"), finding_count: 7 }, rebuilt);
    expect(queue.getSnapshot().items[0]).toMatchObject({
      status: "ready",
      result: rebuilt,
      job: expect.objectContaining({ finding_count: 7 }),
    });

    queue.forgetRemote(1);
    expect(queue.getSnapshot().items[0].credentials).toBeUndefined();
    expect(api.deleteJob).toHaveBeenCalledTimes(1);
  });

  it("pauses a capacity-limited queue without failing every waiting document", async () => {
    api.createJob.mockRejectedValueOnce(
      new ApiError("hourly_abuse_limit", 429),
    );
    const queue = new BatchQueue(files(5));
    queue.start();
    await waitFor(() => expect(queue.getSnapshot().paused).toBe(true));
    await waitFor(() =>
      expect(queue.getSnapshot().items[1].status).toBe("ready"),
    );
    expect(api.createJob).toHaveBeenCalledTimes(2);
    expect(
      queue.getSnapshot().items.filter((item) => item.status === "waiting"),
    ).toHaveLength(4);
    queue.resume();
    await waitFor(() =>
      expect(
        queue.getSnapshot().items.every((item) => item.status === "ready"),
      ).toBe(true),
    );
  });

  it("cleanup retries preserve unrelated results waiting for download recovery", async () => {
    api.getResultBlob.mockRejectedValueOnce(new Error("offline"));
    const queue = new BatchQueue(files(2));
    queue.start();
    await waitFor(() =>
      expect(queue.getSnapshot().items.map((item) => item.status)).toEqual([
        "failed",
        "ready",
      ]),
    );
    await queue.cleanup();
    expect(queue.getSnapshot().items[0].credentials).toBeDefined();
    expect(queue.getSnapshot().items[1].credentials).toBeDefined();
    expect(api.deleteJob).not.toHaveBeenCalled();
    await queue.cleanup(true);
    expect(queue.getSnapshot().items[0].credentials).toBeUndefined();
    expect(queue.getSnapshot().items[1].credentials).toBeUndefined();
  });

  it("lets active jobs finish while paused and resumes waiting files", async () => {
    let finish: (() => void) | undefined;
    api.uploadFile.mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    const queue = new BatchQueue(files(3));
    queue.start();
    queue.pause();
    await waitFor(() =>
      expect(queue.getSnapshot().items[1].status).toBe("ready"),
    );
    expect(api.createJob).toHaveBeenCalledTimes(2);
    finish!();
    await waitFor(() =>
      expect(queue.getSnapshot().items[0].status).toBe("ready"),
    );
    queue.resume();
    await waitFor(() =>
      expect(queue.getSnapshot().items[2].status).toBe("ready"),
    );
  });

  it("polls until redaction completes without overlapping slow status requests", async () => {
    vi.useFakeTimers();
    api.submitJob.mockImplementation(async ({ jobId }: { jobId: string }) => ({
      ...complete(jobId),
      status: "detecting",
    }));
    let finish: ((job: Job) => void) | undefined;
    api.getJob.mockImplementationOnce(
      () =>
        new Promise<Job>((resolve) => {
          finish = resolve;
        }),
    );
    const queue = new BatchQueue(files(1));
    queue.start();
    await vi.advanceTimersByTimeAsync(1800);
    expect(api.getJob).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(6000);
    expect(api.getJob).toHaveBeenCalledTimes(1);
    expect(api.getResultBlob).not.toHaveBeenCalled();
    finish!(complete("file-0.pdf"));
    await vi.advanceTimersByTimeAsync(0);
    expect(queue.getSnapshot().items[0].status).toBe("ready");
  });

  it("restores server work while marking lost local copies honestly", async () => {
    sessionStorage.setItem(
      BATCH_SESSION_KEY,
      JSON.stringify([
        { position: 1 },
        { position: 2, credentials: { jobId: "existing", token: "secret" } },
      ]),
    );
    const queue = new BatchQueue([], readBatchSession());
    queue.start();
    await waitFor(() =>
      expect(queue.getSnapshot().items[1].status).toBe("ready"),
    );
    expect(queue.getSnapshot().items[0]).toMatchObject({
      status: "failed",
      label: "Document 1",
    });
    expect(api.createJob).not.toHaveBeenCalled();
  });
});

describe("batch failure messages", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    sessionStorage.clear();
    api.createJob.mockImplementation(async (file: File) => ({
      job_id: file.name,
      access_token: "token",
      upload: {},
    }));
    api.uploadFile.mockResolvedValue(undefined);
    api.deleteJob.mockResolvedValue(undefined);
  });

  it("explains a permanent failure without telling the user to retry", async () => {
    api.submitJob.mockImplementation(async ({ jobId }: { jobId: string }) => ({
      ...complete(jobId),
      status: "failed",
      error_code: "too_many_pages",
    }));
    const queue = new BatchQueue(files(1));
    queue.start();
    await waitFor(() => expect(queue.getSnapshot().items[0].status).toBe("failed"));
    const message = queue.getSnapshot().items[0].error ?? "";
    expect(message).toMatch(/300 pages/i);
    expect(message).not.toMatch(/retry/i);
  });
});
