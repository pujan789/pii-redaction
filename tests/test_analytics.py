from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from taxhance_pii import analytics
from taxhance_pii.api import main as api_main
from taxhance_pii.config import Settings
from taxhance_pii.container import build_container
from taxhance_pii.logging_config import JsonFormatter


def owner_settings() -> Settings:
    return Settings(
        _env_file=None,
        runtime="aws",
        s3_bucket="synthetic-bucket",
        dynamodb_table="synthetic-table",
        sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123456789012/synthetic",
        token_pepper="synthetic-secret-for-testing-at-least-32-characters",  # noqa: S106
        analytics_enabled=True,
        analytics_user_pool_id="us-east-1_synthetic",
        analytics_client_id="synthetic-client",
    )


def owner_claims() -> dict[str, Any]:
    return {
        "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_synthetic",
        "client_id": "synthetic-client",
        "token_use": "access",
        "scope": "openid email pii-analytics/read",
        "sub": "synthetic-owner",
        "cognito:groups": "[analytics-owners]",
    }


def owner_request(claims: dict[str, Any] | None = None) -> Request:
    scope: dict[str, Any] = {"type": "http", "headers": []}
    if claims is not None:
        scope["aws.event"] = {"requestContext": {"authorizer": {"jwt": {"claims": claims}}}}
    return Request(scope)


def test_owner_access_requires_gateway_validation_and_group() -> None:
    settings = owner_settings()
    analytics.require_owner(owner_request(owner_claims()), settings)
    with pytest.raises(HTTPException) as error:
        analytics.require_owner(owner_request(), settings)
    assert error.value.status_code == 401
    with pytest.raises(HTTPException):
        analytics.require_owner(
            owner_request(owner_claims()), settings.model_copy(update={"runtime": "local"})
        )


@pytest.mark.parametrize(
    "claim,value",
    [
        ("iss", "https://attacker.example/pool"),
        ("client_id", "different-client"),
        ("token_use", "id"),
        ("scope", "openid email"),
        ("cognito:groups", "not-analytics-owners"),
        ("cognito:groups", []),
        ("sub", ""),
    ],
)
def test_non_owner_tokens_are_rejected(claim: str, value: Any) -> None:
    claims = owner_claims()
    claims[claim] = value
    with pytest.raises(HTTPException) as error:
        analytics.require_owner(owner_request(claims), owner_settings())
    assert error.value.status_code == 403


def test_anonymous_http_cannot_read_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = Settings(_env_file=None, data_dir=tmp_path, analytics_enabled=True)
    active = build_container(local)
    active = replace(active, settings=owner_settings())
    monkeypatch.setattr(api_main, "container", active)
    reader = Mock()
    monkeypatch.setattr(api_main, "read_dashboard", reader)
    client = TestClient(api_main.app)
    for headers in ({}, {"Authorization": "Bearer forged", "X-Owner": "true"}):
        assert client.get("/v1/owner/analytics", headers=headers).status_code == 401
    reader.assert_not_called()


def test_metric_payload_has_fixed_numeric_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    log = Mock()
    monkeypatch.setattr(analytics.logger, "info", log)
    analytics.emit_metrics(owner_settings(), DocumentsCompleted=1, PagesCompleted=4)
    extra = log.call_args.kwargs["extra"]
    assert set(extra) == {"_aws", "Deployment", "DocumentsCompleted", "PagesCompleted"}
    assert extra["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [["Deployment"]]
    log.reset_mock()
    analytics.emit_metrics(owner_settings(), filename=1)
    analytics.emit_metrics(owner_settings(), PagesCompleted=float("nan"))
    log.assert_not_called()
    log.side_effect = RuntimeError("logging unavailable")
    analytics.emit_metrics(owner_settings(), DocumentsCompleted=1)  # Must not stop a job.


def test_metrics_are_top_level_json_for_cloudwatch(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO", logger=analytics.__name__):
        analytics.emit_metrics(owner_settings(), ProcessingSeconds=2.5)
    record = next(record for record in caplog.records if record.message == "usage_metrics")
    payload = json.loads(JsonFormatter().format(record))
    assert payload["ProcessingSeconds"] == 2.5
    assert payload["_aws"]["CloudWatchMetrics"][0]["Namespace"] == analytics.NAMESPACE
    assert not {"filename", "document_text", "job_id", "ip", "access_token"} & payload.keys()


def test_collection_rejects_identifiers_and_bad_origins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = build_container(Settings(_env_file=None, data_dir=tmp_path, analytics_enabled=True))
    monkeypatch.setattr(api_main, "container", active)
    emit = Mock()
    monkeypatch.setattr(api_main, "emit_metrics", emit)
    client = TestClient(api_main.app)
    headers = {"Origin": "http://localhost:5173"}
    assert client.post("/v1/analytics/page-view", json={"page": "app"}).status_code == 403
    assert (
        client.post(
            "/v1/analytics/page-view",
            headers=headers,
            json={"page": "app", "filename": "synthetic.pdf"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/v1/analytics/page-view",
            headers=headers,
            content=b"x" * 129,
        ).status_code
        == 413
    )
    assert (
        client.post(
            "/v1/analytics/action",
            headers=headers,
            json={"action": "download", "documents": 0},
        ).status_code
        == 400
    )
    emit.assert_not_called()
    assert (
        client.post(
            "/v1/analytics/page-view",
            headers=headers,
            json={"page": "app", "new_visit": True},
        ).status_code
        == 204
    )
    assert emit.call_args.kwargs == {"AppViews": 1, "Visits": 1}
    assert (
        client.post(
            "/v1/analytics/action",
            headers=headers,
            json={"action": "download", "documents": 12},
        ).status_code
        == 204
    )
    assert emit.call_args.kwargs == {"DownloadsStarted": 1, "DocumentsDownloaded": 12}


def test_dashboard_queries_only_aggregate_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    cloudwatch = Mock()
    cloudwatch.get_metric_data.return_value = {
        "MetricDataResults": [
            {
                "Id": "documents_completed",
                "StatusCode": "Complete",
                "Timestamps": [now],
                "Values": [8],
            }
        ]
    }
    monkeypatch.setattr(analytics, "_cloudwatch", lambda region: cloudwatch)
    dashboard = analytics.read_dashboard(owner_settings(), 7)
    assert len(dashboard.days) == 7
    assert dashboard.days[-1].documents_completed == 8
    assert sum(row.documents_completed for row in dashboard.days) == 8
    for query in cloudwatch.get_metric_data.call_args.kwargs["MetricDataQueries"]:
        metric = query["MetricStat"]["Metric"]
        assert metric["Namespace"] == analytics.NAMESPACE
        assert metric["Dimensions"] == [{"Name": "Deployment", "Value": "pii-redaction-prod"}]
    cloudwatch.get_metric_data.return_value["MetricDataResults"][0]["StatusCode"] = "PartialData"
    with pytest.raises(ValueError, match="incomplete_metrics"):
        analytics.read_dashboard(owner_settings(), 7)
