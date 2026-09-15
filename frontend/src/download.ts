import { recordUsageAction } from "./pageViews";

export async function saveBlobAndDelete(
  blob: Blob,
  deleteRemote: () => Promise<void>,
  filename = "redacted.pdf",
  documentCount = 1,
): Promise<void> {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  try {
    anchor.click();
    recordUsageAction("download", documentCount);
    await deleteRemote();
  } finally {
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}
