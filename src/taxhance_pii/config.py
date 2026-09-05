from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import boto3
from pydantic import Field, PrivateAttr, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration shared by the API and worker."""

    model_config = SettingsConfigDict(
        env_prefix="PII_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    runtime: Literal["local", "aws"] = "local"
    data_dir: Path = Path("./data")
    public_base_url: str = "http://localhost:8080"
    allowed_origins: str = "http://localhost:5173,http://localhost:8080"
    token_pepper: SecretStr = SecretStr("local-development-pepper-change-me")
    token_pepper_secret_arn: str | None = None
    _loaded_pepper: bytes | None = PrivateAttr(default=None)

    retention_hours: int = Field(default=1, ge=1, le=1)
    retention_cleanup_margin_minutes: int = Field(default=5, ge=5, le=60)
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, ge=1024, le=500 * 1024 * 1024)
    max_pages: int = Field(default=300, ge=1, le=2_000)
    max_jobs_per_ip_per_hour: int = Field(default=100, ge=1, le=10_000)
    max_active_jobs_per_ip: int = Field(default=5, ge=1, le=1_000)
    max_queue_depth: int = Field(default=500, ge=1, le=1_000_000)
    upload_url_ttl_seconds: int = Field(default=900, ge=60, le=3_600)
    download_url_ttl_seconds: int = Field(default=300, ge=30, le=3_600)

    model_id: str = "google/gemma-4-E2B-it"
    model_revision: str = "3e22461f65e89153144f8adb70e3b8c2cc9845a7"
    model_max_new_tokens: int = Field(default=1500, ge=64, le=16_384)
    vllm_base_url: str = "http://127.0.0.1:8000/v1"
    vllm_launch: bool = True
    vllm_port: int = Field(default=8000, ge=1024, le=65_535)
    vllm_max_model_len: int = Field(default=16_384, ge=4_096, le=131_072)
    vllm_gpu_memory_utilization: float = Field(default=0.90, ge=0.3, le=0.98)
    vllm_startup_timeout_seconds: int = Field(default=600, ge=60, le=1_800)
    detector_concurrency: int = Field(default=8, ge=1, le=64)
    # Documents processed in parallel by the worker and the evaluation runner.
    # CPU stages (render, OCR, residual checks) dominate per-document time, so
    # overlapping documents keeps the GPU fed; bounded low because each
    # document holds all rendered pages in memory.
    document_concurrency: int = Field(default=3, ge=1, le=8)
    render_dpi: int = Field(default=200, ge=96, le=400)
    ocr_enabled: bool = True
    worker_poll_seconds: float = Field(default=2.0, ge=0.1, le=60)
    worker_visibility_timeout_seconds: int = Field(default=1_800, ge=60, le=43_200)

    aws_region: str = "us-east-1"
    s3_bucket: str | None = None
    dynamodb_table: str | None = None
    sqs_queue_url: str | None = None

    log_level: str = "INFO"

    @property
    def origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def pepper_bytes(self) -> bytes:
        if self._loaded_pepper is not None:
            return self._loaded_pepper
        if self.token_pepper_secret_arn:
            response = boto3.client("secretsmanager", region_name=self.aws_region).get_secret_value(
                SecretId=self.token_pepper_secret_arn
            )
            secret = response.get("SecretString")
            if not isinstance(secret, str) or len(secret) < 32:
                raise ValueError("token pepper secret is invalid")
            self._loaded_pepper = secret.encode("utf-8")
        else:
            self._loaded_pepper = self.token_pepper.get_secret_value().encode("utf-8")
        return self._loaded_pepper

    @model_validator(mode="after")
    def validate_runtime(self) -> Settings:
        if self.retention_cleanup_margin_minutes >= self.retention_hours * 60:
            raise ValueError("retention cleanup margin must be shorter than retention")
        if self.runtime == "aws":
            missing = [
                name
                for name, value in (
                    ("PII_S3_BUCKET", self.s3_bucket),
                    ("PII_DYNAMODB_TABLE", self.dynamodb_table),
                    ("PII_SQS_QUEUE_URL", self.sqs_queue_url),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"AWS runtime requires: {', '.join(missing)}")
            if not self.token_pepper_secret_arn and len(self.token_pepper.get_secret_value()) < 32:
                raise ValueError("PII_TOKEN_PEPPER must contain at least 32 characters in AWS mode")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
