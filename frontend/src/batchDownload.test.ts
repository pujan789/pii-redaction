// @vitest-environment node
import { unzipSync } from "fflate";
import { describe, expect, it } from "vitest";
import { createBatchZip } from "./batchDownload";

describe("batch ZIP export", () => {
  it("preserves all 50 distinct PDFs and their neutral filenames", async () => {
    const files = Array.from({ length: 50 }, (_, index) => ({
      filename: `redacted-${index + 1}.pdf`,
      result: new Blob([`%PDF-synthetic-${index}`]),
    }));
    const zip = await createBatchZip(files);
    expect(zip.type).toBe("application/zip");
    const entries = unzipSync(new Uint8Array(await zip.arrayBuffer()));
    expect(Object.keys(entries)).toHaveLength(50);
    for (let index = 0; index < 50; index++) {
      expect(new TextDecoder().decode(entries[files[index].filename])).toBe(
        `%PDF-synthetic-${index}`,
      );
    }
  });

  it("preserves binary content across ZIP input chunks", async () => {
    const bytes = new Uint8Array(2 * 1024 * 1024 + 37).map(
      (_, index) => index % 251,
    );
    const zip = await createBatchZip([
      { filename: "redacted.pdf", result: new Blob([bytes]) },
    ]);
    const extracted = unzipSync(new Uint8Array(await zip.arrayBuffer()))[
      "redacted.pdf"
    ];
    expect(extracted.length).toBe(bytes.length);
    expect(extracted.every((value, index) => value === bytes[index])).toBe(
      true,
    );
  });

  it("rejects archives beyond classic ZIP limits before reading the files", async () => {
    const result = new Blob(["test"]);
    Object.defineProperty(result, "size", { value: 0xffff_ffff });
    await expect(
      createBatchZip([{ filename: "redacted.pdf", result }]),
    ).rejects.toThrow("archive_size_limit");
  });
});

it("adds an index file that maps neutral names back to the originals", async () => {
  const zip = await createBatchZip(
    [{ filename: "redacted-01-of-01.pdf", result: new Blob(["%PDF-1"]) }],
    { filename: "index.csv", content: "position,original\n1,client/w2.pdf\n" },
  );
  const entries = unzipSync(new Uint8Array(await zip.arrayBuffer()));
  expect(Object.keys(entries).sort()).toEqual(["index.csv", "redacted-01-of-01.pdf"]);
  expect(new TextDecoder().decode(entries["index.csv"])).toContain("client/w2.pdf");
});
