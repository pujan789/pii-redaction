"""Frozen job reader from release 0238510, before aggregate analytics.

Keep this schema independent of the current JobRecord so new fields cannot
silently change what the compatibility tests accept.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from taxhance_pii.domain import JobStatus


class LegacyJobRecord(BaseModel):
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
