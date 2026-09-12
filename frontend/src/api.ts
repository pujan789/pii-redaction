import type { CreatedJob, Detection, Job, JobCredentials, Manifest, Rotation } from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    public readonly status: number,
  ) {
    super(code);
  }
}

async function checked(response: Response): Promise<Response> {
  if (response.ok) {
    // A web page where JSON or a file was expected means something in front
    // of the API (a security filter, a captive portal) answered instead.
    const contentType = response.headers?.get?.("content-type") ?? "";
    if (contentType.startsWith("text/html")) {
      throw new ApiError("request_blocked", response.status);
    }
    return response;
  }
  let code = "request_failed";
  try {
    const body = (await response.json()) as { error?: unknown; detail?: unknown };
    const candidate = body.error ?? body.detail;
    if (typeof candidate === "string") code = candidate;
    else if (candidate !== undefined) code = "invalid_request";
  } catch {
    // The status is still surfaced without retaining a possibly sensitive response body.
  }
  throw new ApiError(code, response.status);
}

function headers(credentials: JobCredentials): HeadersInit {
  return { "X-Job-Token": credentials.token };
}

export async function createJob(file: File, autoFinalize = false): Promise<CreatedJob> {
  const response = await checked(
    await fetch(`${API_BASE}/v1/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filename: file.name,
        size_bytes: file.size,
        content_type: file.type || "application/octet-stream",
        auto_finalize: autoFinalize,
      }),
    }),
  );
  return (await response.json()) as CreatedJob;
}

export async function uploadFile(job: CreatedJob, file: File): Promise<void> {
  if (job.upload.method === "POST") {
    const form = new FormData();
    Object.entries(job.upload.fields).forEach(([key, value]) => form.append(key, value));
    form.append("file", file);
    await checked(await fetch(job.upload.url, { method: "POST", body: form }));
    return;
  }
  await checked(
    await fetch(job.upload.url, {
      method: "PUT",
      headers: job.upload.headers,
      body: file,
    }),
  );
}

export async function submitJob(credentials: JobCredentials): Promise<Job> {
  const response = await checked(
    await fetch(`${API_BASE}/v1/jobs/${credentials.jobId}/submit`, {
      method: "POST",
      headers: headers(credentials),
    }),
  );
  return (await response.json()) as Job;
}

export async function getJob(credentials: JobCredentials): Promise<Job> {
  const response = await checked(
    await fetch(`${API_BASE}/v1/jobs/${credentials.jobId}`, {
      headers: headers(credentials),
      cache: "no-store",
    }),
  );
  return (await response.json()) as Job;
}

export async function getManifest(credentials: JobCredentials): Promise<Manifest> {
  const response = await checked(
    await fetch(`${API_BASE}/v1/jobs/${credentials.jobId}/manifest`, {
      headers: headers(credentials),
      cache: "no-store",
    }),
  );
  return (await response.json()) as Manifest;
}

export async function finalizeJob(
  credentials: JobCredentials,
  detections: Detection[],
  rotation: Rotation = 0,
): Promise<Job> {
  const response = await checked(
    await fetch(`${API_BASE}/v1/jobs/${credentials.jobId}/finalize`, {
      method: "POST",
      headers: { ...headers(credentials), "Content-Type": "application/json" },
      body: JSON.stringify({ detections, rotation }),
    }),
  );
  return (await response.json()) as Job;
}

export async function deleteJob(credentials: JobCredentials): Promise<void> {
  await checked(
    await fetch(`${API_BASE}/v1/jobs/${credentials.jobId}`, {
      method: "DELETE",
      headers: headers(credentials),
    }),
  );
}

async function blobFor(
  credentials: JobCredentials,
  resourcePath: string,
  accessPath: string,
): Promise<Blob> {
  const access = await checked(
    await fetch(`${API_BASE}${accessPath}`, {
      headers: headers(credentials),
      cache: "no-store",
    }),
  );
  const { direct_url: directUrl } = (await access.json()) as { direct_url: string | null };
  if (directUrl) return (await checked(await fetch(directUrl, { cache: "no-store" }))).blob();
  return (
    await checked(
      await fetch(`${API_BASE}${resourcePath}`, {
        headers: headers(credentials),
        cache: "no-store",
      }),
    )
  ).blob();
}

export function getPageBlob(credentials: JobCredentials, pageIndex: number): Promise<Blob> {
  const base = `/v1/jobs/${credentials.jobId}/pages/${pageIndex}`;
  return blobFor(credentials, base, `${base}/access`);
}

export function getResultBlob(credentials: JobCredentials): Promise<Blob> {
  const base = `/v1/jobs/${credentials.jobId}/result`;
  return blobFor(credentials, base, `${base}/access`);
}
