import { Zip, ZipPassThrough } from "fflate";

export async function createBatchZip(
  files: { filename: string; result: Blob }[],
): Promise<Blob> {
  // Classic ZIP offsets are 32-bit. Reject oversized bundles before producing an
  // invalid archive; individual PDF downloads remain available in the workspace.
  if (
    !files.length ||
    files.length > 65_535 ||
    files.reduce((sum, file) => sum + file.result.size + 1024, 0) >= 0xffff_ffff
  ) {
    throw new Error("archive_size_limit");
  }
  const chunks: Blob[] = [];
  const archive = new Zip((error, data) => {
    if (error) throw error;
    chunks.push(new Blob([new Uint8Array(data)]));
  });
  try {
    for (const file of files) {
      const entry = new ZipPassThrough(file.filename);
      archive.add(entry);
      // Read one small slice at a time; PDFs already contain compressed images.
      for (let offset = 0; offset < file.result.size; offset += 1024 * 1024) {
        entry.push(
          new Uint8Array(
            await file.result.slice(offset, offset + 1024 * 1024).arrayBuffer(),
          ),
        );
      }
      entry.push(new Uint8Array(), true);
    }
    archive.end();
    return new Blob(chunks, { type: "application/zip" });
  } catch (reason) {
    archive.terminate();
    throw reason;
  }
}
