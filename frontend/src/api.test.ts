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
