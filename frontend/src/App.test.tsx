import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";
import App from "./App";
import { SUPPORTED_FILE_ACCEPT } from "./fileSelection";
import type { CreatedJob, Job } from "./types";

const apiMocks = vi.hoisted(() => ({
  createJob: vi.fn(),
  deleteJob: vi.fn(),
  finalizeJob: vi.fn(),
  getJob: vi.fn(),
  getManifest: vi.fn(),
  getPageBlob: vi.fn(),
  getResultBlob: vi.fn(),
  submitJob: vi.fn(),
  uploadFile: vi.fn(),
}));

const downloadMocks = vi.hoisted(() => ({
  saveBlobAndDelete: vi.fn(),
}));
const zipMocks = vi.hoisted(() => ({ createBatchZip: vi.fn() }));

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return { ...actual, ...apiMocks };
});

vi.mock("./download", () => downloadMocks);
vi.mock("./batchDownload", () => zipMocks);

function jobIdFor(file: File): string {
  return `job-${file.name.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`;
}

function createdJob(file: File): CreatedJob {
  return {
    job_id: jobIdFor(file),
    access_token: `token-${file.name}`,
    expires_at: "2099-01-01T00:00:00Z",
    upload: {
      method: "PUT",
      url: `/uploads/${file.name}`,
      fields: {},
      headers: {},
      expires_in_seconds: 900,
    },
  };
}

function completeJob(jobId: string): Job {
  return {
    job_id: jobId,
    status: "complete",
    expires_at: "2099-01-01T00:00:00Z",
    page_count: 1,
    pages_completed: 1,
    finding_count: 0,
    error_code: null,
  };
}

function fileInFolder(name: string, path: string, type: string): File {
  const file = new File([name], name, { type });
  Object.defineProperty(file, "webkitRelativePath", { value: path });
  return file;
}

function renderManualDesk() {
  const view = render(<App />);
  const manualMode = screen.queryByRole("radio", { name: /review each document/i });
  if (manualMode) fireEvent.click(manualMode);
  return view;
}

describe("PII redaction desk", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
    window.history.replaceState({}, "", "/");
  });

  beforeEach(() => {
    sessionStorage.clear();
    vi.clearAllMocks();

    apiMocks.createJob.mockImplementation(async (file: File) => createdJob(file));
    apiMocks.uploadFile.mockResolvedValue(undefined);
    apiMocks.submitJob.mockImplementation(async ({ jobId }: { jobId: string }) =>
      completeJob(jobId),
    );
    apiMocks.getJob.mockImplementation(async ({ jobId }: { jobId: string }) =>
      completeJob(jobId),
    );
    apiMocks.getResultBlob.mockResolvedValue(new Blob(["redacted"], { type: "application/pdf" }));
    apiMocks.getPageBlob.mockResolvedValue(new Blob(["preview"], { type: "image/jpeg" }));
    apiMocks.deleteJob.mockResolvedValue(undefined);
    zipMocks.createBatchZip.mockResolvedValue(new Blob(["zip"], { type: "application/zip" }));
    vi.stubGlobal("confirm", vi.fn(() => true));
    downloadMocks.saveBlobAndDelete.mockImplementation(
      async (_blob: Blob, deleteRemote: () => Promise<void>) => deleteRemote(),
    );
  });

  it.each([
    ["/app/", "/"],
    ["/app", "/"],
    ["/app/index.html", "/"],
    ["/pii-redaction/app/", "/pii-redaction/"],
    ["/pii-redaction/app", "/pii-redaction/"],
    ["/pii-redaction/app/index.html", "/pii-redaction/"],
  ])("keeps project navigation in its hosting base at %s", (path, root) => {
    window.history.replaceState({}, "", path);
    render(<App />);
    expect(screen.getByRole("link", { name: "Taxhance PII Redaction home" })).toHaveAttribute("href", root);
    expect(screen.getByRole("img", { name: "Taxhance" })).toHaveAttribute("src", `${root}taxhance-logo.png`);
    expect(screen.getByRole("link", { name: /privacy and redaction policy/i })).toHaveAttribute("href", `${root}#privacy`);
    expect(screen.getByRole("link", { name: "Self-hosting" })).toHaveAttribute("href", `${root}self-hosting/`);
    expect(screen.getByRole("link", { name: "Self-hosting" })).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("link", { name: "Source code" })).toHaveAttribute("href", "https://github.com/pujan789/pii-redaction");
  });

  it("defaults a 50-file upload to a batch workspace and one ZIP download", async () => {
    render(<App />);
    expect(screen.getByRole("radio", { name: /redact automatically/i })).toBeChecked();
    const files = Array.from({ length: 50 }, (_, index) => new File(["synthetic"], `document-${index}.pdf`, { type: "application/pdf" }));
    fireEvent.change(screen.getByTestId("files-input"), { target: { files } });
    await waitFor(() => expect(apiMocks.deleteJob).toHaveBeenCalledTimes(50));
    const download = screen.getByRole("button", { name: /download all 50 PDFs/i });
    expect(apiMocks.createJob).toHaveBeenCalledTimes(50);
    expect(apiMocks.finalizeJob).not.toHaveBeenCalled();
    expect(apiMocks.deleteJob).toHaveBeenCalledTimes(50);
    expect(screen.getByRole("progressbar", { name: /batch progress/i })).toHaveAttribute("aria-valuenow", "50");
    expect(screen.getByRole("heading", { name: /your documents are ready/i })).toHaveFocus();
    fireEvent.click(download);
    await waitFor(() => expect(downloadMocks.saveBlobAndDelete).toHaveBeenCalledWith(expect.any(Blob), expect.any(Function), "redacted-documents.zip"));
    expect(zipMocks.createBatchZip.mock.calls[0][0]).toHaveLength(50);
    expect(screen.getByText(/download started/i)).toBeVisible();
  });

  it("filters failed files without hiding completed batch downloads", async () => {
    apiMocks.createJob.mockRejectedValueOnce(new Error("offline"));
    render(<App />);
    const files = ["first", "second", "third"].map((name) => new File(["synthetic"], `${name}.pdf`, { type: "application/pdf" }));
    fireEvent.change(screen.getByTestId("files-input"), { target: { files } });
    expect(await screen.findByRole("button", { name: /download 2 ready PDFs/i })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: /needs attention 1/i }));
    expect(screen.getByText("first.pdf")).toBeVisible();
    expect(screen.queryByText("second.pdf")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /retry failed files/i }));
    expect(await screen.findByRole("button", { name: /download all 3 PDFs/i })).toBeEnabled();
    expect(apiMocks.createJob).toHaveBeenCalledTimes(4);
  });

  it("explains the auditable workflow and deletion window", () => {
    renderManualDesk();
    expect(screen.getByRole("heading", { name: /redact a tax document/i })).toBeVisible();
    expect(screen.getByText(/deleted after download or within 1 hour/i)).toBeVisible();
    expect(screen.getByText(/free to use · no account/i)).toBeVisible();
  });

  it("offers accessible multi-file and folder pickers", () => {
    renderManualDesk();

    expect(screen.getByRole("button", { name: /choose files/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /choose folder/i })).toBeEnabled();

    const files = screen.getByTestId("files-input") as HTMLInputElement;
    expect(files).toHaveAttribute("type", "file");
    expect(files).toHaveAttribute("accept", SUPPORTED_FILE_ACCEPT);
    expect(files).toHaveAttribute("multiple");
    expect(files.multiple).toBe(true);

    const folder = screen.getByTestId("folder-input") as HTMLInputElement;
    expect(folder).toHaveAttribute("type", "file");
    expect(folder).toHaveAttribute("accept", SUPPORTED_FILE_ACCEPT);
    expect(folder).toHaveAttribute("multiple");
    expect(folder).toHaveAttribute("webkitdirectory");
    expect(folder).toHaveAttribute("directory");
  });

  it("queues supported files from a mixed folder selection", async () => {
    renderManualDesk();
    const folder = screen.getByTestId("folder-input");
    const pdf = fileInFolder("return.PDF", "client/2025/return.PDF", "");
    const jpeg = fileInFolder("scan.jpeg", "client/receipts/scan.jpeg", "image/jpeg");
    const unsupported = fileInFolder("notes.txt", "client/notes.txt", "text/plain");

    fireEvent.change(folder, { target: { files: [pdf, unsupported, jpeg] } });

    expect(await screen.findByText(/2 supported files queued/i)).toBeVisible();
    expect(screen.getByText(/1 unsupported file was skipped/i)).toBeVisible();
    expect(screen.getByText("client/2025/return.PDF")).toBeVisible();
    expect(screen.getAllByText(/file 1 of 2/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/next: client\/receipts\/scan\.jpeg/i)).toBeVisible();
    expect(screen.getByRole("heading", { name: /document redaction/i })).toHaveFocus();
    expect(screen.getByRole("progressbar", { name: /batch progress/i })).toHaveAttribute(
      "aria-valuenow",
      "0",
    );
    await waitFor(() => expect(apiMocks.createJob).toHaveBeenCalledTimes(1));
    expect(apiMocks.createJob).toHaveBeenCalledWith(pdf);

    fireEvent.click(screen.getByRole("button", { name: /download and continue/i }));
    await waitFor(() => expect(apiMocks.createJob).toHaveBeenCalledTimes(2));
    expect(apiMocks.createJob).toHaveBeenNthCalledWith(2, jpeg);
    expect(screen.getByRole("progressbar", { name: /batch progress/i })).toHaveAttribute(
      "aria-valuenow",
      "1",
    );
    expect(downloadMocks.saveBlobAndDelete).toHaveBeenCalledWith(
      expect.any(Blob),
      expect.any(Function),
      "redacted-01-of-02.pdf",
    );
  });

  it("filters every file dropped together", async () => {
    renderManualDesk();
    const first = new File(["first"], "first.pdf", { type: "application/pdf" });
    const unsupported = new File(["notes"], "notes.txt", { type: "text/plain" });
    const second = new File(["second"], "second.tif", { type: "image/tiff" });

    fireEvent.drop(screen.getByRole("group", { name: /upload documents/i }), {
      dataTransfer: { files: [first, unsupported, second] },
    });

    expect(await screen.findByText(/2 supported files queued/i)).toBeVisible();
    expect(screen.getByText(/1 unsupported file was skipped/i)).toBeVisible();
    expect(apiMocks.createJob).toHaveBeenCalledWith(first);

    fireEvent.click(screen.getByRole("button", { name: /download and continue/i }));
    await waitFor(() => expect(apiMocks.createJob).toHaveBeenCalledTimes(2));
    expect(apiMocks.createJob).toHaveBeenNthCalledWith(2, second);
  });

  it("starts the next queued file after downloading the current result", async () => {
    renderManualDesk();
    const files = screen.getByTestId("files-input");
    const first = new File(["first"], "first.pdf", { type: "application/pdf" });
    const second = new File(["second"], "second.png", { type: "image/png" });
    const third = new File(["third"], "third.jpeg", { type: "image/jpeg" });

    fireEvent.change(files, { target: { files: [first, second, third] } });

    expect(await screen.findByRole("heading", { name: /your redacted pdf is ready/i })).toBeVisible();
    expect(apiMocks.createJob).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: /download and continue to next file/i }));

    await waitFor(() => expect(apiMocks.createJob).toHaveBeenCalledTimes(2));
    expect(apiMocks.createJob).toHaveBeenNthCalledWith(1, first);
    expect(apiMocks.createJob).toHaveBeenNthCalledWith(2, second);
    expect(apiMocks.deleteJob).toHaveBeenCalledWith({
      jobId: jobIdFor(first),
      token: `token-${first.name}`,
    });
    expect(downloadMocks.saveBlobAndDelete).toHaveBeenCalledWith(
      expect.any(Blob),
      expect.any(Function),
      "redacted-01-of-03.pdf",
    );
    await waitFor(() => expect(screen.getByText("second.png")).toBeVisible());
    expect(screen.getAllByText(/file 2 of 3/i).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /download and continue to next file/i }));
    await waitFor(() => expect(apiMocks.createJob).toHaveBeenCalledTimes(3));
    expect(apiMocks.createJob).toHaveBeenNthCalledWith(3, third);
    await waitFor(() => expect(screen.getByText("third.jpeg")).toBeVisible());
    expect(screen.getAllByText(/file 3 of 3/i).length).toBeGreaterThan(0);
  });

  it("keeps destructive actions disabled until an upload settles", async () => {
    let finishUpload: (() => void) | undefined;
    apiMocks.uploadFile.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          finishUpload = resolve;
        }),
    );
    renderManualDesk();
    const file = new File(["first"], "first.pdf", { type: "application/pdf" });

    fireEvent.change(screen.getByTestId("files-input"), { target: { files: [file] } });

    expect(await screen.findByText("first.pdf")).toBeVisible();
    expect(screen.getByRole("button", { name: /delete now/i })).toBeDisabled();
    await act(async () => finishUpload?.());
    await waitFor(() => expect(screen.getByRole("button", { name: /delete now/i })).toBeEnabled());
  });

  it("preserves the remaining queue when a file cannot start", async () => {
    apiMocks.createJob.mockRejectedValueOnce(new Error("offline"));
    renderManualDesk();
    const first = new File(["first"], "first.pdf", { type: "application/pdf" });
    const second = new File(["second"], "second.pdf", { type: "application/pdf" });

    fireEvent.change(screen.getByTestId("files-input"), {
      target: { files: [first, second] },
    });

    expect(await screen.findByRole("heading", { name: /could not be started/i })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /skip file and continue/i }));

    await waitFor(() => expect(apiMocks.createJob).toHaveBeenCalledTimes(2));
    expect(apiMocks.createJob).toHaveBeenNthCalledWith(2, second);
    await waitFor(() => expect(screen.getByText("second.pdf")).toBeVisible());
  });

  it("deletes the active job and clears pending files when a batch is cancelled", async () => {
    renderManualDesk();
    const first = new File(["first"], "first.pdf", { type: "application/pdf" });
    const second = new File(["second"], "second.pdf", { type: "application/pdf" });

    fireEvent.change(screen.getByTestId("files-input"), {
      target: { files: [first, second] },
    });

    expect(await screen.findByRole("heading", { name: /redacted pdf is ready/i })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /cancel batch/i }));

    const intakeHeading = await screen.findByRole("heading", { name: /redact a tax document/i });
    expect(intakeHeading).toBeVisible();
    expect(intakeHeading).toHaveFocus();
    expect(screen.getByText(/batch was cancelled/i)).toBeVisible();
    expect(apiMocks.deleteJob).toHaveBeenCalledWith({
      jobId: jobIdFor(first),
      token: `token-${first.name}`,
    });
    expect(apiMocks.createJob).toHaveBeenCalledTimes(1);
  });

  it("can delete a partial upload and continue after an upload failure", async () => {
    apiMocks.uploadFile.mockRejectedValueOnce(new Error("upload failed"));
    apiMocks.getJob.mockImplementation(async ({ jobId }: { jobId: string }) => ({
      ...completeJob(jobId),
      status: "awaiting_upload" as const,
      page_count: null,
      pages_completed: 0,
    }));
    renderManualDesk();
    const first = new File(["first"], "first.pdf", { type: "application/pdf" });
    const second = new File(["second"], "second.pdf", { type: "application/pdf" });

    fireEvent.change(screen.getByTestId("files-input"), {
      target: { files: [first, second] },
    });

    expect(await screen.findByRole("heading", { name: /could not be started/i })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /skip file and continue/i }));

    await waitFor(() => expect(apiMocks.createJob).toHaveBeenCalledTimes(2));
    expect(apiMocks.deleteJob).toHaveBeenCalledWith({
      jobId: jobIdFor(first),
      token: `token-${first.name}`,
    });
    expect(apiMocks.createJob).toHaveBeenNthCalledWith(2, second);
  });

  it("continues a batch when expiry cleanup already deleted the downloaded job", async () => {
    apiMocks.deleteJob.mockRejectedValueOnce(new ApiError("job_not_found", 404));
    renderManualDesk();
    const first = new File(["first"], "first.pdf", { type: "application/pdf" });
    const second = new File(["second"], "second.pdf", { type: "application/pdf" });

    fireEvent.change(screen.getByTestId("files-input"), {
      target: { files: [first, second] },
    });

    expect(await screen.findByRole("heading", { name: /redacted pdf is ready/i })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /download and continue/i }));

    await waitFor(() => expect(apiMocks.createJob).toHaveBeenCalledTimes(2));
    expect(apiMocks.createJob).toHaveBeenNthCalledWith(2, second);
    expect(screen.queryByText(/deletion could not be confirmed/i)).not.toBeInTheDocument();
  });

  it("can retry a transient status failure for a restored job", async () => {
    sessionStorage.setItem(
      "taxhance-pii-active-job",
      JSON.stringify({ jobId: "restored-job", token: "restored-token" }),
    );
    apiMocks.getJob
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(completeJob("restored-job"));

    renderManualDesk();

    const retry = await screen.findByRole("button", { name: /retry status/i });
    fireEvent.click(screen.getByRole("button", { name: /dismiss error/i }));
    expect(retry).toBeVisible();
    fireEvent.click(retry);

    expect(await screen.findByRole("heading", { name: /redacted pdf is ready/i })).toBeVisible();
    expect(apiMocks.getJob).toHaveBeenCalledTimes(2);
  });

  it("finishes a batch without claiming skipped files were downloaded", async () => {
    renderManualDesk();
    const first = new File(["first"], "first.pdf", { type: "application/pdf" });
    const second = new File(["second"], "second.pdf", { type: "application/pdf" });

    fireEvent.change(screen.getByTestId("files-input"), {
      target: { files: [first, second] },
    });

    expect(await screen.findByRole("heading", { name: /redacted pdf is ready/i })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /skip this file/i }));
    await waitFor(() => expect(screen.getByText("second.pdf")).toBeVisible());
    fireEvent.click(screen.getByRole("button", { name: /download and delete server copy/i }));

    expect(await screen.findByText(/the batch is complete/i)).toBeVisible();
    expect(screen.queryByText(/all 2 redacted pdfs reached/i)).not.toBeInTheDocument();
  });

  it("does not leave a stale advancement message after deleting the final batch file", async () => {
    renderManualDesk();
    const first = new File(["first"], "first.pdf", { type: "application/pdf" });
    const second = new File(["second"], "second.pdf", { type: "application/pdf" });

    fireEvent.change(screen.getByTestId("files-input"), {
      target: { files: [first, second] },
    });

    expect(await screen.findByRole("heading", { name: /redacted pdf is ready/i })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /download and continue/i }));
    await waitFor(() => expect(screen.getByText("second.pdf")).toBeVisible());
    fireEvent.click(screen.getByRole("button", { name: /delete without downloading/i }));

    expect(await screen.findByText(/the batch ended/i)).toBeVisible();
    expect(screen.queryByText(/starting the next file/i)).not.toBeInTheDocument();
  });

  it("describes every rejection reason when no selected file can be queued", () => {
    renderManualDesk();
    const oversized = new File(["large"], "large.pdf", { type: "application/pdf" });
    Object.defineProperty(oversized, "size", { value: 50 * 1024 * 1024 + 1 });
    const empty = new File([], "empty.png", { type: "image/png" });
    const unsupported = new File(["notes"], "notes.txt", { type: "text/plain" });

    fireEvent.change(screen.getByTestId("files-input"), {
      target: { files: [oversized, empty, unsupported] },
    });

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/1 unsupported file was skipped/i);
    expect(alert).toHaveTextContent(/1 file was over 50 MB and skipped/i);
    expect(alert).toHaveTextContent(/1 empty file was skipped/i);
    expect(apiMocks.createJob).not.toHaveBeenCalled();
  });

  it("keeps the local queue when batch cancellation is declined", async () => {
    vi.mocked(window.confirm).mockReturnValueOnce(false);
    renderManualDesk();
    const first = new File(["first"], "first.pdf", { type: "application/pdf" });
    const second = new File(["second"], "second.pdf", { type: "application/pdf" });

    fireEvent.change(screen.getByTestId("files-input"), {
      target: { files: [first, second] },
    });

    expect(await screen.findByRole("heading", { name: /redacted pdf is ready/i })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /cancel batch/i }));

    expect(window.confirm).toHaveBeenCalledWith(expect.stringMatching(/1 waiting file/i));
    expect(apiMocks.deleteJob).not.toHaveBeenCalled();
    expect(screen.getByText(/1 file is waiting locally/i)).toBeVisible();
  });

  it("keeps review recovery available after its error toast is dismissed", async () => {
    sessionStorage.setItem(
      "taxhance-pii-active-job",
      JSON.stringify({ jobId: "review-job", token: "review-token" }),
    );
    apiMocks.getJob.mockResolvedValue({
      ...completeJob("review-job"),
      status: "review_required" as const,
    });
    apiMocks.getManifest
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce({
        schema_version: 1,
        job_id: "review-job",
        page_count: 1,
        detections: [],
        detector_version: "test",
        prompt_version: "test",
        model_id: "test",
        created_at: "2099-01-01T00:00:00Z",
      });

    renderManualDesk();

    const retry = await screen.findByRole("button", { name: /retry review/i });
    fireEvent.click(screen.getByRole("button", { name: /dismiss error/i }));
    expect(retry).toBeVisible();
    fireEvent.click(retry);

    expect(await screen.findByRole("button", { name: /apply redactions/i })).toBeVisible();
    expect(apiMocks.getManifest).toHaveBeenCalledTimes(2);
  });

  it("shows and announces a slow download action", async () => {
    let finishDownload: ((blob: Blob) => void) | undefined;
    apiMocks.getResultBlob.mockImplementation(
      () =>
        new Promise<Blob>((resolve) => {
          finishDownload = resolve;
        }),
    );
    renderManualDesk();
    const file = new File(["first"], "first.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByTestId("files-input"), { target: { files: [file] } });

    expect(await screen.findByText("Redacted PDF ready to download.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /download and delete/i }));

    expect(screen.getByRole("status")).toHaveTextContent(/preparing your download/i);
    expect(screen.getByRole("region", { name: /document redaction/i })).toHaveAttribute(
      "aria-busy",
      "true",
    );

    await act(async () => finishDownload?.(new Blob(["redacted"], { type: "application/pdf" })));
    expect(await screen.findByRole("heading", { name: /redact a tax document/i })).toHaveFocus();
  });

  it("explains an expired job instead of silently returning to the intake screen", async () => {
    sessionStorage.setItem(
      "taxhance-pii-active-job",
      JSON.stringify({ jobId: "old-job", token: "old-token" }),
    );
    apiMocks.getJob.mockRejectedValueOnce(new ApiError("job_not_found", 404));

    renderManualDesk();

    expect(await screen.findByRole("heading", { name: /redact a tax document/i })).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent(/1-hour deletion deadline/i);
  });

  it("renders a failure card with a plain explanation and a way out", async () => {
    sessionStorage.setItem(
      "taxhance-pii-active-job",
      JSON.stringify({ jobId: "failed-job", token: "failed-token" }),
    );
    apiMocks.getJob.mockResolvedValue({
      ...completeJob("failed-job"),
      status: "failed" as const,
      error_code: "too_many_pages",
    });

    renderManualDesk();

    expect(await screen.findByRole("heading", { name: /could not be processed/i })).toBeVisible();
    expect(screen.getByText(/300 pages/i)).toBeVisible();
    expect(screen.getByText(/could not process/i)).toBeVisible();
    expect(screen.queryByRole("progressbar", { name: /document processing/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/too_many_pages/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /report a problem/i })).toHaveAttribute(
      "href",
      expect.stringMatching(/^mailto:/),
    );

    fireEvent.click(screen.getByRole("button", { name: /upload another document/i }));

    expect(await screen.findByRole("heading", { name: /redact a tax document/i })).toBeVisible();
    expect(apiMocks.deleteJob).toHaveBeenCalledWith({ jobId: "failed-job", token: "failed-token" });
  });

  it("labels a queued job plainly and explains a long cold start", async () => {
    vi.useFakeTimers();
    sessionStorage.setItem(
      "taxhance-pii-active-job",
      JSON.stringify({ jobId: "queued-job", token: "queued-token" }),
    );
    apiMocks.getJob.mockResolvedValue({
      ...completeJob("queued-job"),
      status: "queued_detection" as const,
      page_count: null,
      pages_completed: 0,
    });

    renderManualDesk();
    await act(async () => vi.advanceTimersByTimeAsync(0));

    expect(screen.getByText(/waiting in line/i)).toBeVisible();
    expect(screen.queryByText(/queued detection/i)).not.toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: /document processing/i })).toHaveClass("is-indeterminate");
    expect(screen.queryByText(/worker is starting/i)).not.toBeInTheDocument();

    await act(async () => vi.advanceTimersByTimeAsync(95_000));
    expect(screen.getByText(/worker is starting/i)).toBeVisible();
  });

  it("does not claim to encrypt the upload", async () => {
    let finishUpload: (() => void) | undefined;
    apiMocks.uploadFile.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          finishUpload = resolve;
        }),
    );
    renderManualDesk();
    fireEvent.change(screen.getByTestId("files-input"), {
      target: { files: [new File(["first"], "first.pdf", { type: "application/pdf" })] },
    });

    expect(await screen.findByText(/uploading securely/i)).toBeVisible();
    expect(screen.queryByText(/encrypting/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/restoring/i)).not.toBeInTheDocument();
    await act(async () => finishUpload?.());
  });

  it("previews the finished PDF once and reuses it for the download", async () => {
    // jsdom has no dialog implementation; a closed dialog hides its content.
    HTMLDialogElement.prototype.showModal = function showModal() {
      this.setAttribute("open", "");
    };
    HTMLDialogElement.prototype.close = function close() {
      this.removeAttribute("open");
    };
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:result") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    sessionStorage.setItem(
      "taxhance-pii-active-job",
      JSON.stringify({ jobId: "done-job", token: "done-token" }),
    );

    renderManualDesk();

    fireEvent.click(await screen.findByRole("button", { name: /^preview/i }));
    expect(await screen.findByTitle(/redacted preview/i)).toBeInTheDocument();
    expect(apiMocks.getResultBlob).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: /close preview/i }));

    fireEvent.click(screen.getByRole("button", { name: /download and delete/i }));
    await waitFor(() => expect(downloadMocks.saveBlobAndDelete).toHaveBeenCalled());
    expect(apiMocks.getResultBlob).toHaveBeenCalledTimes(1);
  });

  function reviewJob(jobId: string, errorCode: string | null = null): Job {
    return { ...completeJob(jobId), status: "review_required", error_code: errorCode };
  }

  const reviewManifest = {
    schema_version: 1 as const,
    job_id: "review-job",
    page_count: 2,
    detections: [
      {
        id: "ssn-1",
        page_index: 0,
        category: "ssn" as const,
        box: { x1: 100, y1: 100, x2: 300, y2: 140 },
        confidence: 0.95,
        source: "regex" as const,
      },
      {
        id: "name-1",
        page_index: 0,
        category: "person_name" as const,
        box: { x1: 100, y1: 200, x2: 300, y2: 240 },
        confidence: 0.9,
        source: "model" as const,
      },
    ],
    detector_version: "test",
    prompt_version: "test",
    model_id: "test",
    created_at: "2099-01-01T00:00:00Z",
  };

  function renderReview(errorCode: string | null = null) {
    sessionStorage.setItem(
      "taxhance-pii-active-job",
      JSON.stringify({ jobId: "review-job", token: "review-token" }),
    );
    apiMocks.getJob.mockResolvedValue(reviewJob("review-job", errorCode));
    apiMocks.getManifest.mockResolvedValue(reviewManifest);
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:page") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    return renderManualDesk();
  }

  it("summarizes the review by category, supports undo, and warns before leaving with edits", async () => {
    const listen = vi.spyOn(window, "addEventListener");
    renderReview();

    expect(await screen.findByText(/1 SSN, 1 person name/i)).toBeVisible();
    expect(screen.getByText(/cannot return to editing/i)).toBeVisible();
    expect(screen.getByRole("button", { name: /^undo/i })).toBeDisabled();

    fireEvent.click(await screen.findByRole("button", { name: /select ssn/i }));
    fireEvent.click(screen.getByRole("button", { name: /remove selected box/i }));
    expect(screen.getByText(/^1 person name$/i)).toBeVisible();
    expect(listen.mock.calls.some(([type]) => type === "beforeunload")).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: /^undo/i }));
    expect(screen.getByText(/1 SSN, 1 person name/i)).toBeVisible();
    listen.mockRestore();
  });

  it("shows why the redaction check sent the document back to review", async () => {
    renderReview("residual_identifier_detected");

    expect(await screen.findByRole("alert")).toHaveTextContent(/not released/i);
    expect(screen.getByRole("button", { name: /apply redactions/i })).toBeEnabled();
  });

  it("warns when the deletion deadline is close", async () => {
    sessionStorage.setItem(
      "taxhance-pii-active-job",
      JSON.stringify({ jobId: "review-job", token: "review-token" }),
    );
    apiMocks.getJob.mockResolvedValue({
      ...reviewJob("review-job"),
      expires_at: new Date(Date.now() + 4 * 60_000).toISOString(),
    });
    apiMocks.getManifest.mockResolvedValue(reviewManifest);
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:page") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });

    renderManualDesk();

    expect(await screen.findByText(/deleted in about 4 min/i)).toBeVisible();
  });

  it("flags batch documents with no redactions and indexes the ZIP", async () => {
    render(<App />);
    const files = ["alpha", "beta"].map((name) => new File(["x"], `${name}.pdf`, { type: "application/pdf" }));
    fireEvent.change(screen.getByTestId("files-input"), { target: { files } });

    await waitFor(() => expect(apiMocks.deleteJob).toHaveBeenCalledTimes(2));
    expect(screen.getAllByText(/^no redactions$/i, { selector: "span" })).toHaveLength(2);
    expect(screen.getByText(/2 documents have no redactions/i)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /no redactions 2/i }));
    expect(screen.getByText("alpha.pdf")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: /download all 2 PDFs/i }));
    await waitFor(() => expect(zipMocks.createBatchZip).toHaveBeenCalled());
    const [entries, index] = zipMocks.createBatchZip.mock.calls[0];
    expect(entries.map((entry: { filename: string }) => entry.filename)).toEqual([
      "redacted-01-of-02.pdf",
      "redacted-02-of-02.pdf",
    ]);
    expect(index.filename).toBe("index.csv");
    expect(index.content).toContain("alpha.pdf");
    expect(index.content).toContain("redacted-01-of-02.pdf");
  });

  it("can name batch downloads after the originals on request", async () => {
    render(<App />);
    const files = ["alpha", "beta"].map((name) => new File(["x"], `${name}.pdf`, { type: "application/pdf" }));
    fireEvent.change(screen.getByTestId("files-input"), { target: { files } });
    await waitFor(() => expect(apiMocks.deleteJob).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByRole("checkbox", { name: /name pdfs after the originals/i }));
    fireEvent.click(screen.getByRole("button", { name: /download all 2 PDFs/i }));
    await waitFor(() => expect(zipMocks.createBatchZip).toHaveBeenCalled());
    expect(zipMocks.createBatchZip.mock.calls[0][0].map((entry: { filename: string }) => entry.filename)).toEqual([
      "alpha-redacted.pdf",
      "beta-redacted.pdf",
    ]);
  });

  it("hands one batch document to manual review and returns to the batch", async () => {
    render(<App />);
    const files = ["alpha", "beta"].map((name) => new File(["x"], `${name}.pdf`, { type: "application/pdf" }));
    fireEvent.change(screen.getByTestId("files-input"), { target: { files } });
    await waitFor(() => expect(apiMocks.deleteJob).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByRole("button", { name: /review alpha\.pdf manually/i }));

    expect(await screen.findByRole("heading", { name: /your redacted pdf is ready/i })).toBeVisible();
    expect(apiMocks.createJob).toHaveBeenLastCalledWith(files[0]);
    expect(apiMocks.createJob).toHaveBeenCalledTimes(3);

    fireEvent.click(screen.getByRole("button", { name: /delete now/i }));
    expect(await screen.findByRole("heading", { name: /your documents are ready/i })).toBeVisible();
  });

  it("states the page and hourly allowances before upload", () => {
    renderManualDesk();
    expect(screen.getByText(/300 pages/i)).toBeVisible();
    expect(screen.getByText(/100 documents per hour/i)).toBeVisible();
  });

  it("does not overlap status polls when a request is slow", async () => {
    vi.useFakeTimers();
    sessionStorage.setItem(
      "taxhance-pii-active-job",
      JSON.stringify({ jobId: "processing-job", token: "processing-token" }),
    );
    const processingJob: Job = {
      ...completeJob("processing-job"),
      status: "detecting",
      pages_completed: 0,
    };
    let finishPoll: ((job: Job) => void) | undefined;
    apiMocks.getJob
      .mockResolvedValueOnce(processingJob)
      .mockImplementationOnce(
        () =>
          new Promise<Job>((resolve) => {
            finishPoll = resolve;
          }),
      );

    const { unmount } = renderManualDesk();
    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(apiMocks.getJob).toHaveBeenCalledTimes(1);

    await act(async () => vi.advanceTimersByTimeAsync(1800));
    expect(apiMocks.getJob).toHaveBeenCalledTimes(2);
    await act(async () => vi.advanceTimersByTimeAsync(5400));
    expect(apiMocks.getJob).toHaveBeenCalledTimes(2);

    await act(async () => finishPoll?.(processingJob));
    unmount();
  });
});
