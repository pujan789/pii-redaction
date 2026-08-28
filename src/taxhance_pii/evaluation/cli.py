from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Literal, cast

from taxhance_pii.config import get_settings
from taxhance_pii.evaluation.aws_runner import (
    fetch_private_evaluation,
    reset_private_evaluation_output,
    run_private_evaluation,
    stage_private_evaluation,
)
from taxhance_pii.evaluation.corpus import (
    build_sample,
    load_manifest,
    save_manifest,
    shard_manifest,
)
from taxhance_pii.evaluation.runner import run_evaluation
from taxhance_pii.logging_config import configure_logging
from taxhance_pii.worker.detector import TextAnchoredDetector


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Private PII-redaction evaluation")
    commands = parser.add_subparsers(dest="command", required=True)
    sample = commands.add_parser("sample", help="Create a private stratified sample manifest")
    sample.add_argument("--corpus", type=Path, required=True)
    sample.add_argument("--output", type=Path, required=True)
    sample.add_argument("--count", type=int, default=50)
    sample.add_argument("--tuning-count", type=int, default=30)
    sample.add_argument("--seed", default="taxhance-pii-v1")

    shard = commands.add_parser(
        "shard", help="Balance one private split into independent page-count shards"
    )
    shard.add_argument("--manifest", type=Path, required=True)
    shard.add_argument("--output-directory", type=Path, required=True)
    shard.add_argument("--split", choices=("tuning", "holdout"), required=True)
    shard.add_argument("--count", type=int, required=True)

    evaluate = commands.add_parser("run", help="Run one evaluation split with Qwen")
    evaluate.add_argument("--manifest", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument("--split", choices=("tuning", "holdout"), required=True)

    stage = commands.add_parser("stage-aws", help="Upload one private split under an opaque key")
    stage.add_argument("--manifest", type=Path, required=True)
    stage.add_argument("--bucket", required=True)
    stage.add_argument("--prefix", required=True)
    stage.add_argument("--split", choices=("tuning", "holdout"), required=True)

    aws_run = commands.add_parser("aws-run", help="Run a staged split inside the GPU task")
    aws_run.add_argument("--bucket", required=True)
    aws_run.add_argument("--prefix", required=True)
    aws_run.add_argument("--split", choices=("tuning", "holdout"), required=True)

    fetch = commands.add_parser(
        "fetch-aws", help="Download private results and optionally purge AWS"
    )
    fetch.add_argument("--bucket", required=True)
    fetch.add_argument("--prefix", required=True)
    fetch.add_argument("--split", choices=("tuning", "holdout"), required=True)
    fetch.add_argument("--output", type=Path, required=True)
    fetch.add_argument("--delete-remote", action="store_true")

    reset = commands.add_parser(
        "reset-aws-output", help="Delete only a failed run's output checkpoint for a clean retry"
    )
    reset.add_argument("--bucket", required=True)
    reset.add_argument("--prefix", required=True)
    reset.add_argument("--split", choices=("tuning", "holdout"), required=True)
    return parser


def run() -> None:
    arguments = _parser().parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    if arguments.command == "sample":
        manifest = build_sample(
            arguments.corpus,
            arguments.count,
            arguments.tuning_count,
            arguments.seed,
        )
        save_manifest(manifest, arguments.output)
        tuning = sum(entry.split == "tuning" for entry in manifest.entries)
        print(
            f"Created private sample with {len(manifest.entries)} PDFs "
            f"({tuning} tuning, {len(manifest.entries) - tuning} holdout)."
        )
        return
    if arguments.command == "shard":
        manifest = load_manifest(arguments.manifest)
        split = cast(Literal["tuning", "holdout"], arguments.split)
        shards = shard_manifest(manifest, split, arguments.count)
        summary = []
        for index, shard_manifest_value in enumerate(shards, start=1):
            destination = arguments.output_directory / f"{split}-shard-{index:02d}.json"
            save_manifest(shard_manifest_value, destination)
            summary.append(
                {
                    "path": str(destination),
                    "documents": len(shard_manifest_value.entries),
                    "pages": sum(entry.page_count for entry in shard_manifest_value.entries),
                }
            )
        print(json.dumps({"split": split, "shards": summary}))
        return
    split = cast(Literal["tuning", "holdout"], arguments.split)
    if arguments.command == "stage-aws":
        result = stage_private_evaluation(
            arguments.manifest,
            split,
            arguments.bucket,
            arguments.prefix,
            settings.aws_region,
        )
        print(json.dumps(result))
        return
    if arguments.command == "aws-run":
        result = run_private_evaluation(settings, arguments.bucket, arguments.prefix, split)
        print(json.dumps(result))
        return
    if arguments.command == "fetch-aws":
        result = fetch_private_evaluation(
            arguments.bucket,
            arguments.prefix,
            split,
            arguments.output,
            settings.aws_region,
            arguments.delete_remote,
        )
        print(json.dumps(result))
        return
    if arguments.command == "reset-aws-output":
        result = reset_private_evaluation_output(
            arguments.bucket,
            arguments.prefix,
            split,
            settings.aws_region,
        )
        print(json.dumps(result))
        return
    manifest = load_manifest(arguments.manifest)
    detector = TextAnchoredDetector(settings)
    detector.preflight()
    results = run_evaluation(manifest, split, settings, detector, arguments.output)
    passed = sum(item.passed_automatic_checks for item in results)
    pages = sum(item.page_count for item in results)
    seconds = sum(item.elapsed_seconds for item in results)
    rate = pages / seconds * 3600 if seconds else 0.0
    print(
        f"Completed {len(results)} private PDFs; {passed} passed automatic residual "
        f"checks; {pages} pages in {seconds:.0f}s = {rate:.0f} pages/hour."
    )


if __name__ == "__main__":
    run()
