import { afterEach, expect, it, vi } from "vitest";
import { createJob } from "./api";

afterEach(() => vi.unstubAllGlobals());

it("requests automatic finalization explicitly while preserving manual review by default", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValue({
      ok: true,
      json: async () => ({ job_id: "synthetic" }),
    });
  vi.stubGlobal("fetch", fetch);
  const file = new File(["test"], "synthetic.pdf", { type: "application/pdf" });
  await createJob(file);
  expect(JSON.parse(fetch.mock.calls[0][1].body).auto_finalize).toBe(false);
  await createJob(file, true);
  expect(JSON.parse(fetch.mock.calls[1][1].body).auto_finalize).toBe(true);
});

it("turns a validation error body into a stable code instead of an object", async () => {
  const { getJob, ApiError } = await import("./api");
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({ detail: [{ loc: ["body", "filename"], msg: "too long" }] }),
    }),
  );
  await expect(getJob({ jobId: "j", token: "t" })).rejects.toMatchObject(
    new ApiError("invalid_request", 422),
  );
});

it("sends the chosen rotation with the approved boxes", async () => {
  const { finalizeJob } = await import("./api");
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: "queued_redaction" }) });
  vi.stubGlobal("fetch", fetch);
  await finalizeJob({ jobId: "j", token: "t" }, [], 90);
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ detections: [], rotation: 90 });
});
