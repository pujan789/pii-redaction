from __future__ import annotations

import logging
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from mangum import Mangum
from starlette.middleware.base import RequestResponseEndpoint

from taxhance_pii.container import Container, get_container
from taxhance_pii.domain import (
    BlobAccess,
    CreateJobRequest,
    CreateJobResponse,
    JobResponse,
    ManifestUpdate,
    RedactionManifest,
)
from taxhance_pii.logging_config import configure_logging
from taxhance_pii.service import JobService, ServiceError
from taxhance_pii.storage import LocalBlobStore

container = get_container()
configure_logging(container.settings.log_level)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="TaxHance PII Redaction API",
    version="0.1.0",
    docs_url=None if container.settings.runtime == "aws" else "/docs",
    redoc_url=None,
    openapi_url=None if container.settings.runtime == "aws" else "/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=container.settings.origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Job-Token"],
    max_age=600,
)


@app.middleware("http")
async def security_headers(request: Request, call_next: RequestResponseEndpoint) -> Response:
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError) -> Response:
    del request
    return Response(
        content=f'{{"error":"{exc.code}"}}',
        status_code=exc.status_code,
        media_type="application/json",
    )


def _service(active: Container | None = None) -> JobService:
    selected = active or container
    return JobService(selected.settings, selected.repository, selected.blobs, selected.queue)


def _source_ip(request: Request) -> str:
    event = request.scope.get("aws.event")
    if isinstance(event, dict):
        context = event.get("requestContext", {})
        if isinstance(context, dict):
            http = context.get("http", {})
            if isinstance(http, dict) and http.get("sourceIp"):
                return str(http["sourceIp"])
            identity = context.get("identity", {})
            if isinstance(identity, dict) and identity.get("sourceIp"):
                return str(identity["sourceIp"])
    return request.client.host if request.client else "unknown"


def _token(value: str | None) -> str:
    if not value:
        raise HTTPException(status_code=404, detail="job_not_found")
    return value


@contextmanager
def _temporary_upload() -> Iterator[tuple[Path, object]]:
    with tempfile.NamedTemporaryFile(prefix="pii-upload-", delete=False) as temporary:
        path = Path(temporary.name)
    try:
        with path.open("w+b") as stream:
            yield path, stream
    finally:
        path.unlink(missing_ok=True)


@app.get("/health/live", include_in_schema=False)
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", include_in_schema=False)
def ready() -> dict[str, str]:
    # A repository call catches broken local mounts and AWS permissions without exposing data.
    container.repository.count_queued()
    return {"status": "ok"}


@app.post("/v1/jobs", response_model=CreateJobResponse, status_code=status.HTTP_201_CREATED)
def create_job(payload: CreateJobRequest, request: Request) -> CreateJobResponse:
    return _service().create_job(payload, _source_ip(request))


@app.put("/v1/jobs/{job_id}/content", status_code=status.HTTP_204_NO_CONTENT)
async def upload_local_content(
    job_id: str,
    request: Request,
    x_job_token: str | None = Header(default=None),
) -> Response:
    if container.settings.runtime != "local" or not isinstance(container.blobs, LocalBlobStore):
        raise HTTPException(status_code=405, detail="direct_upload_disabled")
    job = _service().require_job(job_id, _token(x_job_token))
    if job.status.value != "awaiting_upload":
        raise HTTPException(status_code=409, detail="job_not_uploadable")
    total = 0
    with _temporary_upload() as (path, stream):
        async for chunk in request.stream():
            total += len(chunk)
            if total > job.expected_bytes or total > container.settings.max_upload_bytes:
                raise HTTPException(status_code=413, detail="upload_too_large")
            stream.write(chunk)  # type: ignore[attr-defined]
        stream.flush()  # type: ignore[attr-defined]
        if total != job.expected_bytes:
            raise HTTPException(status_code=400, detail="upload_size_mismatch")
        container.blobs.put_file(job.input_key, path, job.content_type)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/v1/jobs/{job_id}/submit", response_model=JobResponse)
def submit_job(
    job_id: str,
    x_job_token: str | None = Header(default=None),
) -> JobResponse:
    return _service().submit(job_id, _token(x_job_token))


@app.get("/v1/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, x_job_token: str | None = Header(default=None)) -> JobResponse:
    return _service().status(job_id, _token(x_job_token))


@app.get("/v1/jobs/{job_id}/manifest", response_model=RedactionManifest)
def get_manifest(
    job_id: str,
    x_job_token: str | None = Header(default=None),
) -> RedactionManifest:
    return _service().manifest(job_id, _token(x_job_token))


@app.post("/v1/jobs/{job_id}/finalize", response_model=JobResponse)
def finalize_job(
    job_id: str,
    payload: ManifestUpdate,
    x_job_token: str | None = Header(default=None),
) -> JobResponse:
    return _service().finalize(job_id, _token(x_job_token), payload)


def _blob_response(job_id: str, token: str, key: str, media_type: str, filename: str) -> Response:
    job = _service().require_job(job_id, token)
    del job
    signed_url = container.blobs.download_url(key, container.settings.download_url_ttl_seconds)
    if signed_url:
        return RedirectResponse(signed_url, status_code=307)
    if isinstance(container.blobs, LocalBlobStore):
        path = container.blobs.local_path(key)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="blob_not_found")
        return FileResponse(
            path,
            media_type=media_type,
            filename=filename,
            headers={"Cache-Control": "no-store"},
        )
    raise HTTPException(status_code=404, detail="blob_not_found")


@app.get("/v1/jobs/{job_id}/pages/{page_index}")
def get_page_preview(
    job_id: str,
    page_index: int,
    x_job_token: str | None = Header(default=None),
) -> Response:
    job = _service().require_job(job_id, _token(x_job_token))
    if job.page_count is None or page_index < 0 or page_index >= job.page_count:
        raise HTTPException(status_code=404, detail="page_not_found")
    return _blob_response(
        job_id,
        _token(x_job_token),
        f"jobs/{job_id}/previews/{page_index:04d}.jpg",
        "image/jpeg",
        f"page-{page_index + 1}.jpg",
    )


@app.get("/v1/jobs/{job_id}/pages/{page_index}/access", response_model=BlobAccess)
def get_page_access(
    job_id: str,
    page_index: int,
    x_job_token: str | None = Header(default=None),
) -> BlobAccess:
    job = _service().require_job(job_id, _token(x_job_token))
    if job.page_count is None or page_index < 0 or page_index >= job.page_count:
        raise HTTPException(status_code=404, detail="page_not_found")
    key = f"jobs/{job_id}/previews/{page_index:04d}.jpg"
    return BlobAccess(
        direct_url=container.blobs.download_url(key, container.settings.download_url_ttl_seconds)
    )


@app.get("/v1/jobs/{job_id}/result")
def get_result(job_id: str, x_job_token: str | None = Header(default=None)) -> Response:
    job = _service().require_job(job_id, _token(x_job_token))
    if job.status.value != "complete":
        raise HTTPException(status_code=409, detail="result_not_ready")
    return _blob_response(
        job_id,
        _token(x_job_token),
        job.output_key,
        "application/pdf",
        "redacted.pdf",
    )


@app.get("/v1/jobs/{job_id}/result/access", response_model=BlobAccess)
def get_result_access(
    job_id: str,
    x_job_token: str | None = Header(default=None),
) -> BlobAccess:
    job = _service().require_job(job_id, _token(x_job_token))
    if job.status.value != "complete":
        raise HTTPException(status_code=409, detail="result_not_ready")
    return BlobAccess(
        direct_url=container.blobs.download_url(
            job.output_key,
            container.settings.download_url_ttl_seconds,
        )
    )


@app.delete("/v1/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job_id: str, x_job_token: str | None = Header(default=None)) -> Response:
    _service().delete(job_id, _token(x_job_token))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


handler = Mangum(app, lifespan="off")


def cleanup_handler(event: object, context: object) -> dict[str, int]:
    del event, context
    return {"deleted": _service().cleanup_expired(limit=500)}


def run() -> None:
    import uvicorn

    uvicorn.run(
        "taxhance_pii.api.main:app",
        host="0.0.0.0",  # noqa: S104
        port=8000,
        access_log=False,
    )
