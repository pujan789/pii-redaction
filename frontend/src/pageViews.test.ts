import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { recordPageView, recordUsageAction } from "./pageViews";

beforeEach(() => {
  sessionStorage.clear();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true }));
});
afterEach(() => vi.unstubAllGlobals());

describe("aggregate usage", () => {
  it("counts page loads separately from visits and never sends browser identity", () => {
    recordPageView("landing");
    recordPageView("app");
    const calls = vi.mocked(fetch).mock.calls;
    expect(JSON.parse(calls[0][1]!.body as string)).toEqual({ page: "landing", new_visit: true });
    expect(JSON.parse(calls[1][1]!.body as string)).toEqual({ page: "app", new_visit: false });
    expect(calls[0][1]).toMatchObject({ credentials: "omit", referrerPolicy: "no-referrer" });
  });
  it("starts a new visit after thirty minutes without a page load", () => {
    sessionStorage.setItem("pii-usage-last-view", String(Date.now() - 31 * 60 * 1000));
    recordPageView("app");
    expect(JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string).new_visit).toBe(true);
  });
  it("counts the PDFs inside a ZIP without reporting names or contents", () => {
    recordUsageAction("download", 9);
    expect(JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string)).toEqual({ action: "download", documents: 9 });
    recordUsageAction("batch", Infinity);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("never interrupts use when collection fails", async () => {
    vi.mocked(fetch).mockRejectedValue(new Error("offline"));
    expect(() => recordUsageAction("download", 1)).not.toThrow();
    await Promise.resolve();
  });
});
