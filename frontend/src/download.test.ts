import { afterEach, describe, expect, it, vi } from "vitest";

import { saveBlobAndDelete } from "./download";

describe("download and delete", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
    Reflect.deleteProperty(URL, "createObjectURL");
    Reflect.deleteProperty(URL, "revokeObjectURL");
  });

  it("starts the browser save before deleting the remote job", async () => {
    vi.useFakeTimers();
    const order: string[] = [];
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:private-result"),
    });
    const revoke = vi.fn();
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: revoke,
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {
      order.push("save");
    });

    await saveBlobAndDelete(new Blob(["pdf"]), async () => {
      order.push("delete");
    });

    expect(order).toEqual(["save", "delete"]);
    expect(document.querySelector('a[href="blob:private-result"]')).toBeNull();
    vi.runAllTimers();
    expect(revoke).toHaveBeenCalledWith("blob:private-result");
  });

  it("uses a custom download filename", async () => {
    vi.useFakeTimers();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:custom-result"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    let downloadName = "";
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      downloadName = this.download;
    });

    await saveBlobAndDelete(new Blob(["pdf"]), async () => undefined, "client-redacted.pdf");

    expect(downloadName).toBe("client-redacted.pdf");
    vi.runAllTimers();
  });
});
