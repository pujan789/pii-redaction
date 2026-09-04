import { describe, expect, it } from "vitest";

import {
  isSupportedUploadFile,
  MAX_UPLOAD_BYTES,
  redactedFilename,
  selectUploadFiles,
} from "./fileSelection";

function fileWithSize(name: string, size: number): File {
  const file = new File(["content"], name);
  Object.defineProperty(file, "size", { value: size });
  return file;
}

describe("upload file selection", () => {
  it.each([
    "return.pdf",
    "scan.PNG",
    "photo.jpg",
    "photo.JPEG",
    "fax.tif",
    "fax.TIFF",
  ])("accepts the supported extension in %s", (name) => {
    expect(isSupportedUploadFile(new File([], name))).toBe(true);
  });

  it.each(["notes.txt", "return.pdf.exe", "scan.jpg.tmp", "no-extension"])(
    "rejects unsupported filename %s",
    (name) => {
      expect(isSupportedUploadFile(new File([], name))).toBe(false);
    },
  );

  it("keeps supported files within the size limit and counts skipped files", () => {
    const first = fileWithSize("first.pdf", MAX_UPLOAD_BYTES);
    const oversized = fileWithSize("large.tiff", MAX_UPLOAD_BYTES + 1);
    const unsupported = fileWithSize("notes.docx", 100);
    const second = fileWithSize("second.jpeg", 100);

    expect(selectUploadFiles([first, oversized, unsupported, second])).toEqual({
      accepted: [first, second],
      unsupportedCount: 1,
      emptyCount: 0,
      oversizedCount: 1,
    });
  });

  it("matches backend MIME and non-empty file validation", () => {
    const empty = new File([], "empty.pdf", { type: "application/pdf" });
    const mismatched = new File(["content"], "renamed.pdf", { type: "image/png" });
    const unknownMime = new File(["content"], "scan.tif");

    expect(selectUploadFiles([empty, mismatched, unknownMime])).toEqual({
      accepted: [unknownMime],
      unsupportedCount: 1,
      emptyCount: 1,
      oversizedCount: 0,
    });
  });

  it("creates neutral, ordered download names without source filename PII", () => {
    expect(redactedFilename()).toBe("redacted.pdf");
    expect(redactedFilename(1, 3)).toBe("redacted-01-of-03.pdf");
    expect(redactedFilename(12, 125)).toBe("redacted-012-of-125.pdf");
  });
});
