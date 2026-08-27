from datetime import UTC, datetime
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from taxhance_pii.config import Settings
from taxhance_pii.evaluation.aws_runner import (
    AwsEvaluationError,
    _load_checkpoint,
    _safe_local_path,
    _write_checkpoint,
    validate_private_prefix,
)
from taxhance_pii.evaluation.corpus import SampleEntry, SampleManifest
from taxhance_pii.evaluation.runner import EvaluationResult, run_evaluation
from taxhance_pii.worker.detector import NoopDetector


def test_private_evaluation_prefix_is_opaque_and_scoped() -> None:
    prefix = "private-evaluation/0123456789abcdef0123456789abcdef"
    assert validate_private_prefix(f"/{prefix}/") == prefix


@pytest.mark.parametrize(
    "prefix",
    [
        "evaluation/0123456789abcdef0123456789abcdef",
        "private-evaluation/client-name",
        "private-evaluation/../../jobs",
    ],
)
def test_private_evaluation_prefix_rejects_unsafe_values(prefix: str) -> None:
    with pytest.raises(AwsEvaluationError, match="invalid_private_evaluation_prefix"):
        validate_private_prefix(prefix)


def test_result_download_cannot_escape_private_output(tmp_path: Path) -> None:
    with pytest.raises(AwsEvaluationError, match="unsafe_result_key"):
        _safe_local_path(tmp_path, "../../outside.pdf")


class _Body:
    def __init__(self, value: bytes) -> None:
        self.value = value

    def read(self) -> bytes:
        return self.value


class _CheckpointS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, _Body]:  # noqa: N803
        del Bucket
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": _Body(self.objects[Key])}

    def put_object(
        self,
        *,
        Bucket: str,  # noqa: N803
        Key: str,  # noqa: N803
        Body: bytes,  # noqa: N803
        ContentType: str,  # noqa: N803
    ) -> None:
        del Bucket, ContentType
        self.objects[Key] = Body


def _result(evaluation_id: str) -> EvaluationResult:
    return EvaluationResult(
        evaluation_id=evaluation_id,
        split="tuning",
        page_count=1,
        elapsed_seconds=1,
        finding_count=1,
        deterministic_findings=1,
        model_findings=0,
        residual_deterministic=0,
        residual_model=0,
        flattened=True,
        passed_automatic_checks=True,
    )


def test_private_checkpoint_round_trip_and_config_binding() -> None:
    s3 = _CheckpointS3()
    settings = Settings()
    key = "private-evaluation/run/output/tuning/checkpoint.json"
    assert _load_checkpoint(s3, "bucket", key, settings, "tuning") == []

    expected = [_result("first")]
    _write_checkpoint(s3, "bucket", key, settings, "tuning", expected)
    assert _load_checkpoint(s3, "bucket", key, settings, "tuning") == expected

    with pytest.raises(AwsEvaluationError, match="checkpoint_config_mismatch"):
        _load_checkpoint(s3, "bucket", key, Settings(model_id="different/model"), "tuning")


def test_evaluation_resume_skips_checkpointed_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = [
        SampleEntry(
            evaluation_id=evaluation_id,
            source_path=f"{evaluation_id}.pdf",
            content_sha256="0" * 64,
            size_bytes=1,
            page_count=1,
            text_kind="digital",
            split="tuning",
        )
        for evaluation_id in ("first", "second")
    ]
    manifest = SampleManifest(
        created_at=datetime.now(UTC),
        seed="test",
        corpus_file_count=2,
        entries=entries,
    )
    evaluated: list[str] = []

    def fake_evaluate(
        entry: SampleEntry,
        settings: Settings,
        detector: NoopDetector,
        paths: object,
    ) -> EvaluationResult:
        del settings, detector, paths
        evaluated.append(entry.evaluation_id)
        return _result(entry.evaluation_id)

    monkeypatch.setattr("taxhance_pii.evaluation.runner.evaluate_entry", fake_evaluate)
    callbacks: list[str] = []
    results = run_evaluation(
        manifest,
        "tuning",
        Settings(),
        NoopDetector(),
        tmp_path,
        existing_results=[_result("first")],
        on_result=lambda result, path: callbacks.append(result.evaluation_id),
    )
    assert [result.evaluation_id for result in results] == ["first", "second"]
    assert evaluated == ["second"]
    assert callbacks == ["second"]
