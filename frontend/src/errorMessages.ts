import { ApiError } from "./api";

// One vocabulary for every server-side code, shared by the single-document
// flow and the batch queue. Unknown codes never reach the screen verbatim.
const OUR_SIDE =
  "Something went wrong on our side. Delete this document and upload it again; if it keeps happening, use Report a problem in the footer.";

export const ERROR_MESSAGES: Record<string, string> = {
  // Intake and upload
  upload_too_large: "That file is over the 50 MB limit.",
  unsupported_file_type: "Choose a PDF, PNG, JPEG, or TIFF file.",
  file_signature_mismatch: "The file contents do not match the file extension.",
  upload_size_mismatch: "The upload did not arrive completely. Upload the file again.",
  upload_missing: "The upload did not arrive. Upload the file again.",
  upload_plan_failed: "The upload could not be set up. Try again in a moment.",
  job_not_uploadable: "This document has already been uploaded.",
  job_not_submittable: "This document is already being processed.",
  direct_upload_disabled: OUR_SIDE,
  invalid_request: OUR_SIDE,
  request_failed: "The server could not be reached. Check your connection and try again.",
  request_blocked:
    "The request was blocked before it reached the service. Try again; if it keeps happening, use Report a problem in the footer.",

  // Capacity (per office network, not per person)
  hourly_abuse_limit:
    "This office network has reached its allowance of 100 documents per hour. Try again later.",
  active_job_abuse_limit:
    "Up to 5 documents can be active at once per office network, including ones waiting in review. Try again in a minute.",
  service_busy: "The processing queue is full right now. Try again shortly.",
  queue_unavailable: "The processing queue is unavailable right now. Try again shortly.",

  // Job lifecycle
  job_not_found:
    "This document passed its 1-hour deletion deadline or was deleted from the server. Upload it again to continue.",
  job_not_reviewable: "This document is no longer waiting for review.",
  manifest_not_ready: "The suggested redactions are still being prepared. Try again in a moment.",
  manifest_invalid: OUR_SIDE,
  result_not_ready: "The redacted PDF is not ready yet.",
  blob_not_found: "That file is no longer on the server. Upload the document again.",
  page_not_found: "That page could not be loaded.",
  delete_failed:
    "Immediate deletion could not be confirmed. Please retry; automatic expiry cleanup remains active.",

  // Problems with the document itself (retrying the same file cannot help)
  too_many_pages:
    "This document is over 300 pages. Split it into smaller PDFs and upload them separately.",
  pdf_password_protected: "This PDF is password-protected. Remove the password and upload it again.",
  pdf_unreadable: "This PDF could not be opened. Re-save it from your PDF viewer and upload it again.",
  pdf_render_failed:
    "A page in this PDF could not be rendered. Re-save it from your PDF viewer and upload it again.",
  image_unreadable: "This image could not be opened. Save it as PNG or JPEG and upload it again.",
  document_empty: "No pages were found in this file.",
  page_dimensions_too_large:
    "A page in this document is too large to process. Reduce the page size or resolution and upload it again.",
  document_dimensions_too_large:
    "This document is too large to process at once. Split it into smaller files.",
  ocr_unavailable:
    "This document needs OCR (scanned pages, form fields, or typed annotations), which this server cannot run.",

  // Fail-closed detection and redaction checks
  model_output_invalid:
    "The detector could not produce a trustworthy result, so this document was not released.",
  model_output_truncated:
    "A page had more sensitive values than the detector can report at once, so this document was not released. Split the page or redact it manually.",
  detection_unanchored:
    "The detector found a taxpayer identifier it could not locate on the page, so this document was not released. Review it manually.",
  residual_identifier_detected:
    "A sensitive identifier remained visible after redaction, so this document was not released. Review it manually and cover the value.",
  degenerate_redaction_box:
    "One redaction box was too small to paint. Redraw it a little larger and approve again.",
  manifest_document_mismatch: OUR_SIDE,
  redaction_box_empty: OUR_SIDE,
  redaction_pixels_not_opaque: OUR_SIDE,
  output_contains_text_layer: OUR_SIDE,
  output_contains_active_content: OUR_SIDE,
  output_page_count_mismatch: OUR_SIDE,
  output_not_pdf: OUR_SIDE,
  output_validation_failed: OUR_SIDE,
  processing_failed: "Processing stopped safely. Your original is still scheduled for deletion.",
};

const PERMANENT_FAILURES = new Set([
  "upload_too_large",
  "unsupported_file_type",
  "file_signature_mismatch",
  "too_many_pages",
  "pdf_password_protected",
  "pdf_unreadable",
  "pdf_render_failed",
  "image_unreadable",
  "document_empty",
  "page_dimensions_too_large",
  "document_dimensions_too_large",
  "ocr_unavailable",
  "model_output_invalid",
  "model_output_truncated",
  "detection_unanchored",
  "residual_identifier_detected",
  "degenerate_redaction_box",
]);

export const CAPACITY_CODES = new Set([
  "hourly_abuse_limit",
  "active_job_abuse_limit",
  "service_busy",
]);

const CONNECTION_MESSAGE =
  "The connection was interrupted. Check your network and try again.";

export function messageForCode(code: string): string {
  return ERROR_MESSAGES[code] ?? OUR_SIDE;
}

export function isPermanentFailure(code: string): boolean {
  return PERMANENT_FAILURES.has(code);
}

export function friendlyError(error: unknown): string {
  if (error instanceof ApiError) return messageForCode(error.code);
  return CONNECTION_MESSAGE;
}

/** Batch-row wording: says whether retrying this file can help. */
export function describeFailure(reason: unknown): string {
  if (reason instanceof Error && reason.message === "source_unavailable") {
    return "The source file was cleared on reload. Reselect the original in a new batch.";
  }
  if (reason instanceof ApiError) {
    const known = ERROR_MESSAGES[reason.code];
    if (!known) return "This document could not be processed. Retry this file.";
    return isPermanentFailure(reason.code) ? known : `${known} Retry this file.`;
  }
  return "The connection was interrupted. Retry this file; the rest of your batch can continue.";
}
