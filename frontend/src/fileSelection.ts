export const SUPPORTED_FILE_ACCEPT = ".pdf,.png,.jpg,.jpeg,.tif,.tiff";
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

const SUPPORTED_EXTENSION = /\.(pdf|png|jpe?g|tiff?)$/i;
const CONTENT_TYPES: Record<string, string> = {
  ".pdf": "application/pdf",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".tif": "image/tiff",
  ".tiff": "image/tiff",
};

export interface UploadFileSelection {
  accepted: File[];
  unsupportedCount: number;
  emptyCount: number;
  oversizedCount: number;
}

export function isSupportedUploadFile(file: File): boolean {
  const extension = file.name.match(SUPPORTED_EXTENSION)?.[0].toLowerCase();
  if (!extension) return false;

  const contentType = file.type.split(";", 1)[0].trim().toLowerCase();
  return (
    !contentType ||
    contentType === "application/octet-stream" ||
    contentType === CONTENT_TYPES[extension] ||
    (extension === ".jpg" && contentType === "image/jpg") ||
    (extension === ".jpeg" && contentType === "image/jpg")
  );
}

export function selectUploadFiles(files: Iterable<File>): UploadFileSelection {
  const selection: UploadFileSelection = {
    accepted: [],
    unsupportedCount: 0,
    emptyCount: 0,
    oversizedCount: 0,
  };

  for (const file of files) {
    if (!isSupportedUploadFile(file)) {
      selection.unsupportedCount += 1;
    } else if (file.size === 0) {
      selection.emptyCount += 1;
    } else if (file.size > MAX_UPLOAD_BYTES) {
      selection.oversizedCount += 1;
    } else {
      selection.accepted.push(file);
    }
  }

  return selection;
}

export function redactedFilename(position = 1, total = 1): string {
  if (total <= 1) return "redacted.pdf";
  const width = Math.max(2, String(total).length);
  return `redacted-${String(position).padStart(width, "0")}-of-${String(total).padStart(width, "0")}.pdf`;
}

/** "client/2025/W-2 Smith.pdf" becomes "W-2 Smith-redacted.pdf". */
export function originalRedactedFilename(label: string): string {
  const base = label.split(/[\\/]/).pop() ?? label;
  const stem = base.replace(/\.[^.]+$/, "");
  const safe = stem
    .replace(/[\\/:*?"<>|]+/g, "-")
    .replace(/\s+/g, " ")
    .replace(/^[-. ]+|[-. ]+$/g, "")
    .slice(0, 120);
  return `${safe || "document"}-redacted.pdf`;
}

/** Original-name downloads for a whole batch, made unique in order. */
export function uniqueRedactedFilenames(labels: string[]): string[] {
  const seen = new Map<string, number>();
  return labels.map((label) => {
    const name = originalRedactedFilename(label);
    const count = (seen.get(name) ?? 0) + 1;
    seen.set(name, count);
    return count === 1 ? name : name.replace(/\.pdf$/, `-${count}.pdf`);
  });
}
