"""Aggregate usage metrics. Never pass document data or request identifiers here."""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Literal

import boto3
from botocore.config import Config
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from taxhance_pii.config import Settings

logger = logging.getLogger(__name__)
NAMESPACE = "Taxhance/PIIRedaction"
SCOPE = "pii-analytics/read"
OWNER_GROUP = "analytics-owners"
PAGE_METRICS = {
    "landing": "LandingViews",
    "app": "AppViews",
    "self_hosting": "SelfHostingViews",
}
COUNTERS = {
    "Visits": "visits",
    "LandingViews": "landing_views",
    "AppViews": "app_views",
    "SelfHostingViews": "self_hosting_views",
    "DocumentsSubmitted": "documents_submitted",
    "DocumentsCompleted": "documents_completed",
    "PagesCompleted": "pages_completed",
    "DocumentsFailed": "documents_failed",
    "DocumentsDownloaded": "documents_downloaded",
    "DownloadsStarted": "downloads_started",
    "BatchesSelected": "batches_selected",
    "BatchDocumentsSelected": "batch_documents_selected",
}


class PageView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: Literal["landing", "app", "self_hosting"]
    new_visit: bool = False


class UsageAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["download", "batch"]
    documents: int = Field(ge=1, le=65_535)


class DailyMetrics(BaseModel):
    date: str
    visits: float = 0
    landing_views: float = 0
    app_views: float = 0
    self_hosting_views: float = 0
    documents_submitted: float = 0
    documents_completed: float = 0
    pages_completed: float = 0
    documents_failed: float = 0
    documents_downloaded: float = 0
    downloads_started: float = 0
    batches_selected: float = 0
    batch_documents_selected: float = 0
    processing_seconds: float = 0
    processing_tasks: float = 0


class Dashboard(BaseModel):
    generated_at: datetime
    timezone: Literal["UTC"] = "UTC"
    days: list[DailyMetrics]


def emit_metrics(settings: Settings, **metrics: float) -> None:
    """CloudWatch extracts numeric metrics from the existing encrypted log stream.

    No network call sits on the processing path. Fixed names and one deployment
    dimension prevent document identifiers from entering persistent analytics.
    """
    if not settings.analytics_enabled or settings.runtime != "aws":
        return
    if not metrics or any(
        name not in {*COUNTERS, "ProcessingSeconds"} or not math.isfinite(value) or value < 0
        for name, value in metrics.items()
    ):
        return
    try:
        logger.info(
            "usage_metrics",
            extra={
                "_aws": {
                    "Timestamp": int(datetime.now(UTC).timestamp() * 1000),
                    "CloudWatchMetrics": [
                        {
                            "Namespace": NAMESPACE,
                            "Dimensions": [["Deployment"]],
                            "Metrics": [
                                {
                                    "Name": name,
                                    "Unit": "Seconds" if name == "ProcessingSeconds" else "Count",
                                }
                                for name in metrics
                            ],
                        }
                    ],
                },
                "Deployment": settings.analytics_deployment,
                **metrics,
            },
        )
    except Exception:
        # Optional metrics must never interrupt document processing.
        return


def require_owner(request: Request, settings: Settings) -> None:
    """Trust only API Gateway's verified authorizer context, never HTTP headers.

    The default unauthenticated API route cannot supply this context. Local
    installations have no authentication bypass and keep the owner API closed.
    """
    if not settings.analytics_enabled or not settings.analytics_user_pool_id:
        raise HTTPException(status_code=404, detail="not_found")
    event = request.scope.get("aws.event")
    claims: object = None
    if settings.runtime == "aws" and isinstance(event, dict):
        context = event.get("requestContext", {})
        if isinstance(context, dict):
            authorizer = context.get("authorizer", {})
            if isinstance(authorizer, dict) and isinstance(authorizer.get("jwt"), dict):
                claims = authorizer["jwt"].get("claims")
    if not isinstance(claims, dict):
        raise HTTPException(status_code=401, detail="sign_in_required")
    issuer = (
        f"https://cognito-idp.{settings.aws_region}.amazonaws.com/{settings.analytics_user_pool_id}"
    )
    # API Gateway validates signature, expiration, issuer, audience and scope.
    # Repeat the expected identity boundaries and require explicit owner membership.
    groups = claims.get("cognito:groups", "")
    if isinstance(groups, str):
        groups = groups.strip("[]").replace('"', "").split(",")
        groups = [group.strip() for group in groups]
    if (
        claims.get("iss") != issuer
        or claims.get("client_id") != settings.analytics_client_id
        or claims.get("token_use") != "access"
        or SCOPE not in str(claims.get("scope", "")).split()
        or not claims.get("sub")
        or not isinstance(groups, list)
        or OWNER_GROUP not in groups
    ):
        raise HTTPException(status_code=403, detail="owner_access_required")


@lru_cache
def _cloudwatch(region: str) -> Any:
    return boto3.client(
        "cloudwatch",
        region_name=region,
        config=Config(connect_timeout=2, read_timeout=5, retries={"max_attempts": 1}),
    )


def read_dashboard(settings: Settings, days: int) -> Dashboard:
    now = datetime.now(UTC)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    rows = {
        (start + timedelta(days=offset)).date().isoformat(): DailyMetrics(
            date=(start + timedelta(days=offset)).date().isoformat()
        )
        for offset in range(days)
    }
    series = [(name, field, "Sum") for name, field in COUNTERS.items()] + [
        ("ProcessingSeconds", "processing_seconds", "Sum"),
        ("ProcessingSeconds", "processing_tasks", "SampleCount"),
    ]
    queries = [
        {
            "Id": field,
            "MetricStat": {
                "Metric": {
                    "Namespace": NAMESPACE,
                    "MetricName": name,
                    "Dimensions": [{"Name": "Deployment", "Value": settings.analytics_deployment}],
                },
                "Period": 86400,
                "Stat": statistic,
            },
            "ReturnData": True,
        }
        for name, field, statistic in series
    ]
    client = _cloudwatch(settings.aws_region)
    # Bounded daily points; no caller-selected namespaces or metric names.
    response = client.get_metric_data(
        MetricDataQueries=queries,
        StartTime=start,
        EndTime=now,
        MaxDatapoints=10000,
    )
    if response.get("NextToken"):
        raise ValueError("incomplete_metrics")
    for result in response.get("MetricDataResults", []):
        if result.get("StatusCode") != "Complete":
            raise ValueError("incomplete_metrics")
        field = result["Id"]
        if field not in {item[1] for item in series}:
            continue
        for timestamp, value in zip(result["Timestamps"], result["Values"], strict=True):
            row = rows.get(timestamp.astimezone(UTC).date().isoformat())
            if row is not None and math.isfinite(value):
                setattr(row, field, max(0, value))
    return Dashboard(generated_at=now, days=list(rows.values()))
