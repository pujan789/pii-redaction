from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Literal

import boto3
from botocore.exceptions import ClientError

from taxhance_pii.config import Settings
from taxhance_pii.evaluation.corpus import SampleEntry, SampleManifest, load_manifest
from taxhance_pii.evaluation.runner import EvaluationResult, run_evaluation
from taxhance_pii.redaction.prompt import PROMPT_VERSION
from taxhance_pii.worker.detector import DETECTOR_VERSION, TextAnchoredDetector

EvaluationSplit = Literal["tuning", "holdout"]
_PREFIX_PATTERN = re.compile(r"^private-evaluation/[0-9a-f]{32}$")


class AwsEvaluationError(RuntimeError):
    pass


def validate_private_prefix(prefix: str) -> str:
    normalized = prefix.strip("/")
    if not _PREFIX_PATTERN.fullmatch(normalized):
        raise AwsEvaluationError("invalid_private_evaluation_prefix")
    return normalized


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _selected(manifest: SampleManifest, split: EvaluationSplit) -> list[SampleEntry]:
    selected = [entry for entry in manifest.entries if entry.split == split]
    if not selected:
        raise AwsEvaluationError("evaluation_split_empty")
    return selected


def _checkpoint_config(settings: Settings, split: EvaluationSplit) -> dict[str, object]:
    return {
        "schema_version": 4,
        "split": split,
        "prompt_version": PROMPT_VERSION,
        "detector_version": DETECTOR_VERSION,
        "model_id": settings.model_id,
        "model_revision": settings.model_revision,
        "render_dpi": settings.render_dpi,
        "model_max_new_tokens": settings.model_max_new_tokens,
        "detector_concurrency": settings.detector_concurrency,
    }


def _load_checkpoint(
    s3: object,
    bucket: str,
    key: str,
    settings: Settings,
    split: EvaluationSplit,
) -> list[EvaluationResult]:
    try:
        response = s3.get_object(Bucket=bucket, Key=key)  # type: ignore[attr-defined]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
            return []
        raise
    payload = json.loads(response["Body"].read())
    expected = _checkpoint_config(settings, split)
    if not isinstance(payload, dict) or any(
        payload.get(key) != value for key, value in expected.items()
    ):
        raise AwsEvaluationError("evaluation_checkpoint_config_mismatch")
    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        raise AwsEvaluationError("evaluation_checkpoint_invalid")
    try:
        return [EvaluationResult.model_validate(result) for result in raw_results]
    except (TypeError, ValueError) as exc:
        raise AwsEvaluationError("evaluation_checkpoint_invalid") from exc


def _write_checkpoint(
    s3: object,
    bucket: str,
    key: str,
    settings: Settings,
    split: EvaluationSplit,
    results: list[EvaluationResult],
) -> None:
    payload = {
        **_checkpoint_config(settings, split),
        "results": [result.model_dump(mode="json") for result in results],
    }
    s3.put_object(  # type: ignore[attr-defined]
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, indent=2).encode(),
        ContentType="application/json",
    )


def stage_private_evaluation(
    manifest_path: Path,
    split: EvaluationSplit,
    bucket: str,
    prefix: str,
    region: str,
) -> dict[str, int | str]:
    prefix = validate_private_prefix(prefix)
    manifest = load_manifest(manifest_path)
    selected = _selected(manifest, split)
    s3 = boto3.client("s3", region_name=region)
    remote_entries: list[SampleEntry] = []
    for entry in selected:
        source = Path(entry.source_path)
        if (
            not source.is_file()
            or source.stat().st_size != entry.size_bytes
            or _sha256(source) != entry.content_sha256
        ):
            raise AwsEvaluationError(f"private_source_changed:{entry.evaluation_id}")
        key = f"{prefix}/input/{entry.evaluation_id}.pdf"
        s3.upload_file(
            str(source),
            bucket,
            key,
            ExtraArgs={"ContentType": "application/pdf"},
        )
        metadata = s3.head_object(Bucket=bucket, Key=key)
        if metadata.get("ServerSideEncryption") != "aws:kms":
            raise AwsEvaluationError("private_source_not_kms_encrypted")
        remote_entries.append(entry.model_copy(update={"source_path": key}))

    remote_manifest = manifest.model_copy(update={"entries": remote_entries})
    s3.put_object(
        Bucket=bucket,
        Key=f"{prefix}/manifest.json",
        Body=remote_manifest.model_dump_json(indent=2).encode(),
        ContentType="application/json",
    )
    return {
        "split": split,
        "documents": len(remote_entries),
        "pages": sum(entry.page_count for entry in remote_entries),
    }


def _safe_local_path(root: Path, relative_key: str) -> Path:
    relative = PurePosixPath(relative_key)
    if relative.is_absolute() or ".." in relative.parts:
        raise AwsEvaluationError("unsafe_result_key")
    destination = root.joinpath(*relative.parts)
    if not destination.resolve().is_relative_to(root.resolve()):
        raise AwsEvaluationError("unsafe_result_key")
    return destination


def run_private_evaluation(
    settings: Settings,
    bucket: str,
    prefix: str,
    split: EvaluationSplit,
) -> dict[str, int | str]:
    prefix = validate_private_prefix(prefix)
    s3 = boto3.client("s3", region_name=settings.aws_region)
    with tempfile.TemporaryDirectory(prefix="pii-private-evaluation-") as raw_directory:
        root = Path(raw_directory)
        manifest_path = root / "manifest.json"
        s3.download_file(bucket, f"{prefix}/manifest.json", str(manifest_path))
        remote_manifest = load_manifest(manifest_path)
        selected = _selected(remote_manifest, split)
        inputs = root / "input"
        inputs.mkdir()
        local_entries: list[SampleEntry] = []
        for entry in selected:
            expected_key = f"{prefix}/input/{entry.evaluation_id}.pdf"
            if entry.source_path != expected_key:
                raise AwsEvaluationError("private_manifest_key_mismatch")
            local_path = inputs / f"{entry.evaluation_id}.pdf"
            s3.download_file(bucket, expected_key, str(local_path))
            if (
                local_path.stat().st_size != entry.size_bytes
                or _sha256(local_path) != entry.content_sha256
            ):
                raise AwsEvaluationError("private_source_integrity_failed")
            local_entries.append(entry.model_copy(update={"source_path": str(local_path)}))

        local_manifest = remote_manifest.model_copy(update={"entries": local_entries})
        output = root / "output"
        detector = TextAnchoredDetector(settings)
        detector.preflight()
        output_prefix = f"{prefix}/output/{split}"
        checkpoint_key = f"{output_prefix}/checkpoint.json"
        checkpoint_results = _load_checkpoint(s3, bucket, checkpoint_key, settings, split)

        def upload_result(result: EvaluationResult, document_dir: Path) -> None:
            for path in sorted(item for item in document_dir.rglob("*") if item.is_file()):
                relative = path.relative_to(output).as_posix()
                content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                s3.upload_file(
                    str(path),
                    bucket,
                    f"{output_prefix}/{relative}",
                    ExtraArgs={"ContentType": content_type},
                )
            checkpoint_results.append(result)
            _write_checkpoint(
                s3,
                bucket,
                checkpoint_key,
                settings,
                split,
                checkpoint_results,
            )

        results = run_evaluation(
            local_manifest,
            split,
            settings,
            detector,
            output,
            existing_results=checkpoint_results,
            on_result=upload_result,
        )
        for path in sorted(item for item in output.iterdir() if item.is_file()):
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            s3.upload_file(
                str(path),
                bucket,
                f"{output_prefix}/{path.name}",
                ExtraArgs={"ContentType": content_type},
            )

        completion: dict[str, int | str] = {
            "schema_version": 1,
            "split": split,
            "documents": len(results),
            "pages": sum(result.page_count for result in results),
            "automatic_passes": sum(result.passed_automatic_checks for result in results),
            "failures": sum(result.error_code is not None for result in results),
        }
        s3.put_object(
            Bucket=bucket,
            Key=f"{output_prefix}/completion.json",
            Body=json.dumps(completion, indent=2).encode(),
            ContentType="application/json",
        )
        return completion


def reset_private_evaluation_output(
    bucket: str,
    prefix: str,
    split: EvaluationSplit,
    region: str,
) -> dict[str, int | str]:
    prefix = validate_private_prefix(prefix)
    output_prefix = f"{prefix}/output/{split}/"
    s3 = boto3.client("s3", region_name=region)
    paginator = s3.get_paginator("list_objects_v2")
    keys: list[dict[str, str]] = []
    deleted = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=output_prefix):
        for item in page.get("Contents", []):
            key = str(item["Key"])
            if not key.startswith(output_prefix):
                raise AwsEvaluationError("unsafe_result_key")
            keys.append({"Key": key})
            if len(keys) == 1000:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": keys, "Quiet": True})
                deleted += len(keys)
                keys = []
    if keys:
        s3.delete_objects(Bucket=bucket, Delete={"Objects": keys, "Quiet": True})
        deleted += len(keys)
    return {"split": split, "deleted_output_objects": deleted}


def fetch_private_evaluation(
    bucket: str,
    prefix: str,
    split: EvaluationSplit,
    output: Path,
    region: str,
    delete_remote: bool,
) -> dict[str, int | str]:
    prefix = validate_private_prefix(prefix)
    s3 = boto3.client("s3", region_name=region)
    output_prefix = f"{prefix}/output/{split}/"
    completion_key = f"{output_prefix}completion.json"
    try:
        completion_object = s3.get_object(Bucket=bucket, Key=completion_key)
    except s3.exceptions.NoSuchKey as exc:
        raise AwsEvaluationError("evaluation_not_complete") from exc
    completion = json.loads(completion_object["Body"].read())
    if completion.get("split") != split:
        raise AwsEvaluationError("evaluation_completion_invalid")

    downloaded = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=output_prefix):
        for item in page.get("Contents", []):
            key = str(item["Key"])
            relative_key = key.removeprefix(output_prefix)
            if not relative_key:
                continue
            destination = _safe_local_path(output, relative_key)
            destination.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, key, str(destination))
            downloaded += 1

    if delete_remote:
        keys: list[dict[str, str]] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/"):
            for item in page.get("Contents", []):
                keys.append({"Key": str(item["Key"])})
                if len(keys) == 1000:
                    s3.delete_objects(Bucket=bucket, Delete={"Objects": keys, "Quiet": True})
                    keys = []
        if keys:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": keys, "Quiet": True})

    return {
        "split": split,
        "documents": int(completion["documents"]),
        "pages": int(completion["pages"]),
        "downloaded_files": downloaded,
        "remote_deleted": int(delete_remote),
    }
