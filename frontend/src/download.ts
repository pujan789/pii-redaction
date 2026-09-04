export async function saveBlobAndDelete(
  blob: Blob,
  deleteRemote: () => Promise<void>,
  filename = "redacted.pdf",
): Promise<void> {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  try {
    anchor.click();
    await deleteRemote();
  } finally {
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}
