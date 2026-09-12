from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class JobStatus(StrEnum):
    AWAITING_UPLOAD = "awaiting_upload"
    QUEUED_DETECTION = "queued_detection"
    DETECTING = "detecting"
    REVIEW_REQUIRED = "review_required"
    QUEUED_REDACTION = "queued_redaction"
    REDACTING = "redacting"
    COMPLETE = "complete"
    FAILED = "failed"
    DELETED = "deleted"
    EXPIRED = "expired"


ACTIVE_STATUSES = {
    JobStatus.AWAITING_UPLOAD,
    JobStatus.QUEUED_DETECTION,
    JobStatus.DETECTING,
    JobStatus.REVIEW_REQUIRED,
    JobStatus.QUEUED_REDACTION,
    JobStatus.REDACTING,
}


class TaskType(StrEnum):
    DETECT = "detect"
    REDACT = "redact"


class PiiCategory(StrEnum):
    PERSON_NAME = "person_name"
    ORGANIZATION_NAME = "organization_name"
    STREET_ADDRESS = "street_address"
    EMAIL = "email"
    PHONE = "phone"
    SSN = "ssn"
    ITIN = "itin"
    EIN = "ein"
    PTIN = "ptin"
    EFIN = "efin"
    IP_PIN = "ip_pin"
    CAF_NUMBER = "caf_number"
    BANK_ACCOUNT = "bank_account"
    ROUTING_NUMBER = "routing_number"
    PAYMENT_CARD = "payment_card"
    STATE_TAX_ID = "state_tax_id"
    EMPLOYEE_ID = "employee_id"
    HEALTH_INSURANCE_ID = "health_insurance_id"
    OTHER_PRIVATE_ID = "other_private_id"
    DATE_OF_BIRTH = "date_of_birth"
    DATE_OF_DEATH = "date_of_death"
    DRIVER_LICENSE = "driver_license"
    PASSPORT = "passport"
    IP_ADDRESS = "ip_address"
    SIGNATURE = "signature"
    USER_ADDED = "user_added"


# Whole-document page rotation chosen during review, applied to the output.
Rotation = Literal[0, 90, 180, 270]


class BoundingBox(BaseModel):
    """A page-relative box using integer coordinates from 0 through 1000."""

    model_config = ConfigDict(extra="forbid")

    x1: int = Field(ge=0, le=999)
    y1: int = Field(ge=0, le=999)
    x2: int = Field(ge=1, le=1000)
    y2: int = Field(ge=1, le=1000)

    @model_validator(mode="after")
    def validate_order(self) -> BoundingBox:
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("bounding box must have positive area")
        return self


class Detection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    page_index: int = Field(ge=0)
    category: PiiCategory
    box: BoundingBox
    confidence: float = Field(ge=0, le=1)
    source: Literal["model", "regex", "ocr", "user"]
    fingerprint: str | None = Field(default=None, max_length=32)


class RedactionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    job_id: str
    page_count: int = Field(ge=1)
    detections: list[Detection]
    detector_version: str
    prompt_version: str
    model_id: str
    created_at: datetime
    rotation: Rotation = 0


class JobRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: JobStatus
    created_at: datetime
    expires_at: datetime
    updated_at: datetime
    token_hash: str
    client_hash: str
    input_key: str
    output_key: str
    draft_manifest_key: str
    approved_manifest_key: str
    file_extension: str
    content_type: str
    expected_bytes: int = Field(ge=1)
    actual_bytes: int | None = Field(default=None, ge=1)
    page_count: int | None = Field(default=None, ge=1)
    pages_completed: int = Field(default=0, ge=0)
    finding_count: int = Field(default=0, ge=0)
    error_code: str | None = Field(default=None, max_length=80)
    auto_finalize: bool = False
    version: int = Field(default=1, ge=1)


class QueueMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    job_id: str
    task: TaskType


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=1)
    content_type: str = Field(min_length=1, max_length=100)
    auto_finalize: bool = False


class UploadPlan(BaseModel):
    method: Literal["PUT", "POST"]
    url: str
    fields: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    expires_in_seconds: int


class BlobAccess(BaseModel):
    direct_url: str | None = None


class CreateJobResponse(BaseModel):
    job_id: str
    access_token: str
    expires_at: datetime
    upload: UploadPlan


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    expires_at: datetime
    page_count: int | None
    pages_completed: int
    finding_count: int
    error_code: str | None


class ManifestUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detections: list[Detection] = Field(max_length=20_000)
    rotation: Rotation = 0
