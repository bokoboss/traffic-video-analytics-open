from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.backend.app.benchmarking import (  # noqa: E402
    BenchmarkValidationError,
    automatic_result_blockers,
    build_metric_bundle,
    build_report_payload,
    environment_snapshot,
    match_events,
    parse_ground_truth_manifest,
    parse_matching_policy,
    qualification_gates,
    render_report_markdown,
    throughput_metrics,
    validate_corpus_manifest,
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def intervals_for_source(source: dict[str, Any], bucket_minutes: int) -> list[dict[str, int]]:
    start = 0
    end = int(source.get("media_duration_ms") or 0)
    bucket = bucket_minutes * 60 * 1_000
    intervals: list[dict[str, int]] = []
    index = 0
    while start < end:
        interval_end = min(end, start + bucket)
        intervals.append(
            {
                "interval_index": index,
                "source_relative_start_pts_ms": start,
                "source_relative_end_pts_ms": interval_end,
            }
        )
        start = interval_end
        index += 1
    return intervals


def run_benchmark(
    corpus_payload: dict[str, Any],
    ground_truth_payload: dict[str, Any],
    predicted_payload: Any,
    *,
    source_id: str | None = None,
    evaluation_payload: dict[str, Any] | None = None,
    processing_stats: dict[str, Any] | None = None,
    code_commit_sha: str | None = None,
    configuration_hash: str | None = None,
) -> dict[str, Any]:
    corpus_validation = validate_corpus_manifest(corpus_payload)
    if not corpus_validation["valid"]:
        raise BenchmarkValidationError("invalid benchmark corpus manifest", corpus_validation["errors"])
    sources = list(corpus_validation["manifest"]["sources"])
    selected = next(
        (source for source in sources if source_id and source["benchmark_source_id"] == source_id),
        sources[0] if len(sources) == 1 else None,
    )
    if selected is None:
        raise BenchmarkValidationError("source selection is required for a multi-source corpus")
    truth = parse_ground_truth_manifest(ground_truth_payload, source=selected)["events"]
    predicted = predicted_payload.get("events", predicted_payload) if isinstance(predicted_payload, dict) else predicted_payload
    if not isinstance(predicted, list):
        raise BenchmarkValidationError("predicted event payload must be a list or an events object")
    evaluation = dict(evaluation_payload or {})
    policy = parse_matching_policy(evaluation)
    intervals = intervals_for_source(selected, policy.interval_bucket_minutes)
    automatic_result = {
        "engineering_ready": bool(evaluation.get("engineering_ready", False)),
        "stale": bool(evaluation.get("stale", False)),
        "structurally_invalid": bool(evaluation.get("structurally_invalid", False)),
        "reconciliation_status": evaluation.get("reconciliation_status"),
        "blockers": evaluation.get("blockers", []),
        "taxonomy_revision": evaluation.get("taxonomy_revision", "unavailable"),
        "mapping_revision": evaluation.get("mapping_revision", "unavailable"),
        "classification_policy_revision": evaluation.get("classification_policy_revision", "unavailable"),
        "code_commit_sha": code_commit_sha or evaluation.get("code_commit_sha") or os.getenv("GIT_COMMIT_SHA", "unavailable"),
        "configuration_hash": configuration_hash or evaluation.get("configuration_hash", "unavailable"),
    }
    automatic_result["blockers"] = automatic_result_blockers(automatic_result)
    if automatic_result["blockers"]:
        matches = {
            "schema_version": "event-match-v1",
            "status": "NOT_SCORABLE",
            "blockers": automatic_result["blockers"],
            "matches": [],
            "predicted_count": 0,
            "ground_truth_count": 0,
            "accounting_invariants": {},
        }
        metrics: dict[str, Any] = {}
    else:
        matches = match_events(predicted, truth, policy)
        throughput = throughput_metrics(processing_stats)
        metrics = build_metric_bundle(predicted, truth, matches, intervals, throughput=throughput)
    selected_for_qualification = {
        **selected,
        "source_count": len(sources),
        "condition_coverage": sorted({tag for source in sources for tag in source.get("condition_tags", [])}),
    }
    qualification = qualification_gates(
        source=selected_for_qualification,
        ground_truth={
            "status": ground_truth_payload.get("status"),
            "ground_truth_revision": ground_truth_payload.get("ground_truth_revision"),
            "scene_revision": ground_truth_payload.get("scene_revision"),
            "events": truth,
        },
        automatic_result=automatic_result,
        metrics=metrics,
        approved_policy=evaluation.get("approved_policy"),
    )
    corpus_summary = {
        "corpus_revision": corpus_payload.get("corpus_revision"),
        "benchmark_source_id": selected["benchmark_source_id"],
        "rights_status": selected["rights_status"],
        "rights_disclosure": "media bytes remain external to the repository",
        "source_count": len(sources),
        "benchmark_split": selected["benchmark_split"],
        "condition_tags": selected.get("condition_tags", []),
    }
    report = build_report_payload(
        corpus=corpus_summary,
        ground_truth={
            "ground_truth_revision": ground_truth_payload.get("ground_truth_revision"),
            "status": ground_truth_payload.get("status"),
            "reviewer_agreement": ground_truth_payload.get("reviewer_agreement", {}),
        },
        automatic_configuration=evaluation,
        matching_policy=policy.as_dict(),
        match_result=matches,
        metrics=metrics,
        qualification=qualification,
        reproducibility={
            **automatic_result,
            "source_checksum": selected["source_fingerprint_sha256"],
            "ground_truth_revision": ground_truth_payload.get("ground_truth_revision"),
            "environment": environment_snapshot(),
        },
    )
    return {"report": report, "report_markdown": render_report_markdown(report)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic 6C event and metric evaluation.")
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--ground-truth", required=True, type=Path)
    parser.add_argument("--predicted-events", required=True, type=Path)
    parser.add_argument("--source-id")
    parser.add_argument("--evaluation-config", type=Path)
    parser.add_argument("--processing-stats", type=Path)
    parser.add_argument("--code-commit-sha")
    parser.add_argument("--configuration-hash")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    result = run_benchmark(
        read_json(args.corpus),
        read_json(args.ground_truth),
        read_json(args.predicted_events),
        source_id=args.source_id,
        evaluation_payload=read_json(args.evaluation_config) if args.evaluation_config else None,
        processing_stats=read_json(args.processing_stats) if args.processing_stats else None,
        code_commit_sha=args.code_commit_sha,
        configuration_hash=args.configuration_hash,
    )
    serialized = json.dumps(result["report"], ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        write_text(args.output, serialized)
    else:
        print(serialized, end="")
    if args.markdown:
        write_text(args.markdown, result["report_markdown"] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
