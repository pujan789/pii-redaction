from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

import boto3
from botocore.config import Config

from taxhance_pii.domain import JobRecord, UploadPlan


@dataclass(frozen=True)
class BlobInfo:
    size: int
    content_type: str


class BlobStore(Protocol):
    def upload_plan(self, job: JobRecord, access_token: str, expires_in: int) -> UploadPlan: ...

    def put_stream(
        self,
        key: str,
        source: BinaryIO,
        size_limit: int,
        content_type: str,
    ) -> int: ...

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None: ...

    def put_file(self, key: str, path: Path, content_type: str) -> None: ...

    def head(self, key: str) -> BlobInfo | None: ...

    def read_prefix(self, key: str, length: int) -> bytes: ...

    def get_bytes(self, key: str) -> bytes: ...

    def download_file(self, key: str, destination: Path) -> None: ...

    def delete_prefix(self, prefix: str) -> None: ...

    def download_url(self, key: str, expires_in: int) -> str | None: ...


class LocalBlobStore:
    def __init__(self, root: Path, public_base_url: str) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.public_base_url = public_base_url.rstrip("/")

    def _path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("invalid_blob_key")
        return candidate

    def upload_plan(self, job: JobRecord, access_token: str, expires_in: int) -> UploadPlan:
        return UploadPlan(
            method="PUT",
            url=f"{self.public_base_url}/v1/jobs/{job.job_id}/content",
            headers={"X-Job-Token": access_token, "Content-Type": job.content_type},
            expires_in_seconds=expires_in,
        )

    def put_stream(
        self,
        key: str,
        source: BinaryIO,
        size_limit: int,
        content_type: str,
    ) -> int:
        del content_type
        destination = self._path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".uploading")
        total = 0
        try:
            with temporary.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    total += len(chunk)
                    if total > size_limit:
                        raise ValueError("upload_too_large")
                    output.write(chunk)
            temporary.replace(destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return total

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        del content_type
        destination = self._path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    def put_file(self, key: str, path: Path, content_type: str) -> None:
        del content_type
        destination = self._path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)

    def head(self, key: str) -> BlobInfo | None:
        path = self._path(key)
        if not path.is_file():
            return None
        return BlobInfo(size=path.stat().st_size, content_type="application/octet-stream")

    def read_prefix(self, key: str, length: int) -> bytes:
        with self._path(key).open("rb") as source:
            return source.read(length)

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def download_file(self, key: str, destination: Path) -> None:
        shutil.copyfile(self._path(key), destination)

    def delete_prefix(self, prefix: str) -> None:
        path = self._path(prefix)
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    def download_url(self, key: str, expires_in: int) -> str | None:
        del key, expires_in
        return None

    def local_path(self, key: str) -> Path:
        return self._path(key)


class S3BlobStore:
    def __init__(self, bucket: str, region: str) -> None:
        self.bucket = bucket
        # KMS-encrypted S3 uploads require Signature V4. In us-east-1 botocore can
        # otherwise generate legacy Signature V2 POST fields for presigned forms.
        self.client = boto3.client(
            "s3",
            region_name=region,
            config=Config(signature_version="s3v4"),
        )

    def upload_plan(self, job: JobRecord, access_token: str, expires_in: int) -> UploadPlan:
        del access_token
        response = self.client.generate_presigned_post(
            Bucket=self.bucket,
            Key=job.input_key,
            Fields={"Content-Type": job.content_type},
            Conditions=[
                {"Content-Type": job.content_type},
                ["content-length-range", 1, job.expected_bytes],
            ],
            ExpiresIn=expires_in,
        )
        return UploadPlan(
            method="POST",
            url=str(response["url"]),
            fields={str(key): str(value) for key, value in response["fields"].items()},
            expires_in_seconds=expires_in,
        )

    def put_stream(
        self,
        key: str,
        source: BinaryIO,
        size_limit: int,
        content_type: str,
    ) -> int:
        del key, source, size_limit, content_type
        raise NotImplementedError("AWS uploads use presigned S3 POSTs")

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def put_file(self, key: str, path: Path, content_type: str) -> None:
        self.client.upload_file(
            str(path),
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )

    def head(self, key: str) -> BlobInfo | None:
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
        except self.client.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return BlobInfo(
            size=int(response["ContentLength"]),
            content_type=str(response.get("ContentType", "application/octet-stream")),
        )

    def read_prefix(self, key: str, length: int) -> bytes:
        response = self.client.get_object(
            Bucket=self.bucket,
            Key=key,
            Range=f"bytes=0-{length - 1}",
        )
        return bytes(response["Body"].read())

    def get_bytes(self, key: str) -> bytes:
        return bytes(self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read())

    def download_file(self, key: str, destination: Path) -> None:
        self.client.download_file(self.bucket, key, str(destination))

    def delete_prefix(self, prefix: str) -> None:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix.rstrip("/") + "/"):
            objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if objects:
                response = self.client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": objects, "Quiet": True},
                )
                if response.get("Errors"):
                    raise RuntimeError("s3_delete_failed")

    def download_url(self, key: str, expires_in: int) -> str:
        return str(
            self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        )
