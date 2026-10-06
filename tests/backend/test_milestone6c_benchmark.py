from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from apps.backend.app.benchmark_service import BenchmarkService
from apps.backend.app.benchmarking import (
    BenchmarkValidationError,
    build_experiment_configuration,
    build_metric_bundle,
    class_metrics,
    count_metrics,
    environment_snapshot,
    event_metrics,
    evaluate_thresholds,
    fragmentation_metrics,
    match_events,
    pareto_frontier,
    parse_qualification_policy,
    parse_ground_truth_manifest,
    qualification_gates,
    reviewer_agreement,
    throughput_metrics,
    validate_corpus_manifest,
    validate_ground_truth_manifest,
)
from apps.backend.app.db import connect, migrate
from apps.backend.app.main import create_app
from apps.backend.app.schemas import SourceCreate
from apps.backend.app.services import FoundationService
from scripts.run_benchmark import run_benchmark


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tools" / "benchmark" / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def normalized_fixture_events() -> tuple[list[dict], list[dict]]:
    corpus = load_fixture("corpus_manifest_6c.json")
    source = corpus["sources"][0]
    ground_truth = parse_ground_truth_manifest(
        load_fixture("ground_truth_6c.json"),
        source=source,
        counting_line_ids={"line-main"},
    )
    return load_fixture("predicted_events_6c.json"), ground_truth["events"]


def qualification_fixture(*, split: str = "HOLDOUT", source_count: int = 1, conditions: list[str] | None = None) -> tuple[dict, dict, dict, dict]:
    corpus = validate_corpus_manifest(load_fixture("corpus_manifest_6c.json"))["manifest"]
    source = dict(corpus["sources"][0])
    source["benchmark_split"] = split
    source["source_count"] = source_count
    source["condition_coverage"] = list(conditions if conditions is not None else source["condition_tags"])
    truth = parse_ground_truth_manifest(load_fixture("ground_truth_6c.json"), source=source)["events"]
    ground_truth = {
        "status": "ADJUDICATED",
        "ground_truth_revision": "gt-policy-v1",
        "scene_revision": source["scene_revision"],
        "events": truth,
    }
    automatic_result = {
        "engineering_ready": True,
        "stale": False,
        "structurally_invalid": False,
        "reconciliation_status": "STRUCTURALLY_VALID",
        "taxonomy_revision": "taxonomy-v1",
        "mapping_revision": "mapping-v1",
        "classification_policy_revision": "classification-policy-v1",
        "code_commit_sha": "a" * 40,
        "configuration_hash": "configuration-v1",
        "runtime_configuration_hash": "runtime-configuration-v1-hash",
    }
    metrics = {
        "event_metrics": {
            "precision": 1.0,
            "recall": 1.0,
            "f1": 1.0,
            "denominators": {"precision": 2, "recall": 2, "duplicate_rate": 2, "miss_rate": 2},
        },
        "duplicate_metrics": {"duplicate_rate": 0.0, "miss_rate": 0.0},
        "timestamp_metrics": {"mean_absolute_error_ms": 55.0, "p95_absolute_error_ms": 95.5},
        "count_metrics": {"total": {"ground_truth_count": 2, "absolute_error": 0, "absolute_percentage_error": 0.0}},
    }
    return source, ground_truth, automatic_result, metrics


def approved_threshold_policy(source: dict, thresholds: list[dict] | None = None, **overrides: object) -> dict:
    return {
        "schema_version": "qualification-threshold-v1",
        "policy_revision": "owner-policy-v1",
        "approved_by": ('owner@' + 'example.test'),
        "approved_at": "2026-01-01T00:00:00+00:00",
        "applicable_corpus_revision": source["corpus_revision"],
        "required_split": source["benchmark_split"],
        "minimum_source_count": 1,
        "minimum_ground_truth_event_count": 1,
        "minimum_condition_coverage": ["daytime"],
        "thresholds": thresholds
        or [
            {"metric_path": "event_metrics.recall", "operator": "GTE", "required_value": 0.9, "unit": "ratio", "required": True, "undefined_behavior": "FAIL_CLOSED"},
            {"metric_path": "event_metrics.precision", "operator": "GTE", "required_value": 0.9, "unit": "ratio", "required": True, "undefined_behavior": "FAIL_CLOSED"},
            {"metric_path": "duplicate_metrics.duplicate_rate", "operator": "LTE", "required_value": 0.1, "unit": "ratio", "required": True, "undefined_behavior": "FAIL_CLOSED"},
            {"metric_path": "timestamp_metrics.mean_absolute_error_ms", "operator": "LTE", "required_value": 100, "unit": "milliseconds", "required": True, "undefined_behavior": "FAIL_CLOSED"},
        ],
        **overrides,
    }


def test_corpus_manifest_is_rights_aware_and_redacts_external_paths() -> None:
    payload = load_fixture("corpus_manifest_6c.json")
    payload["sources"][0]["file_name_or_external_reference"] = r"C:\private\survey.mp4"

    result = validate_corpus_manifest(payload)

    assert result["valid"] is True
    assert result["manifest"]["sources"][0]["file_name_or_external_reference"] == "external://survey.mp4"
    assert result["manifest"]["sources"][0]["rights_status"] == "CLEARED_FOR_REPOSITORY_DISTRIBUTION"
    assert len(result["content_hash"]) == 64

    not_cleared = deepcopy(payload)
    not_cleared["sources"][0]["rights_status"] = "NOT_CLEARED"
    assert validate_corpus_manifest(not_cleared)["valid"] is True


def test_ground_truth_validation_checks_fingerprint_lines_and_timestamps() -> None:
    corpus = load_fixture("corpus_manifest_6c.json")
    source = corpus["sources"][0]
    payload = load_fixture("ground_truth_6c.json")

    valid = validate_ground_truth_manifest(payload, source=source, counting_line_ids={"line-main"})
    assert valid["valid"] is True
    assert valid["ground_truth"]["events"][0]["source_fingerprint_sha256"] == source["source_fingerprint_sha256"]
    assert valid["ground_truth"]["reviewer_agreement"]["minimum_two_reviewers_available"] is True

    mismatch = deepcopy(payload)
    mismatch["source_fingerprint_sha256"] = "c" * 64
    invalid = validate_ground_truth_manifest(mismatch, source=source, counting_line_ids={"line-main"})
    assert invalid["valid"] is False
    assert any(error["code"] == "source_fingerprint_mismatch" for error in invalid["errors"])

    invalid_event = deepcopy(payload)
    invalid_event["events"][0]["counting_line_id"] = "line-missing"
    invalid_event["events"][0]["crossing_pts_ms"] = 3_600_000
    invalid = validate_ground_truth_manifest(invalid_event, source=source, counting_line_ids={"line-main"})
    codes = {error["code"] for error in invalid["errors"]}
    assert {"unknown_line", "outside_analysis_window"}.issubset(codes)


def test_matching_is_one_to_one_deterministic_and_exposes_error_categories() -> None:
    predicted, truth = normalized_fixture_events()
    base = match_events(predicted, truth)
    reordered = match_events(list(reversed(predicted)), list(reversed(truth)))

    assert base == reordered
    assert [row["primary_category"] for row in base["matches"]] == [
        "TRUE_POSITIVE",
        "TRUE_POSITIVE",
        "IGNORED",
    ]
    assert base["matches"][1]["secondary_error_flags"] == ["CLASS_ERROR"]

    duplicate = match_events(
        predicted + [
            {
                **predicted[0],
                "source_event_id": "auto-cal-003",
                "technical_key": "fixture-auto-003",
                "event_pts_ms": 1_020,
            }
        ],
        truth,
    )
    assert "auto-cal-003" in duplicate["duplicate_automatic_event_ids"]
    assert any(row["primary_category"] == "DUPLICATE_AUTOMATIC" for row in duplicate["matches"])

    direction_error = match_events([{**predicted[0], "canonical_direction": "B_TO_A"}, predicted[1]], truth)
    assert direction_error["direction_error_event_ids"] == ["auto-cal-001"]
    assert any(row["primary_category"] == "DIRECTION_ERROR" for row in direction_error["matches"])


def test_matching_direction_duplicates_are_one_to_one_and_preserve_accounting() -> None:
    predicted, truth = normalized_fixture_events()
    wrong_direction_duplicate = {
        **predicted[0],
        "source_event_id": "auto-wrong-duplicate",
        "technical_key": "wrong-direction-duplicate",
        "canonical_direction": "B_TO_A",
        "event_pts_ms": predicted[0]["event_pts_ms"] + 10,
    }
    result = match_events(predicted + [wrong_direction_duplicate], truth)
    duplicate = next(row for row in result["matches"] if row["automatic_event_id"] == "auto-wrong-duplicate")
    assert duplicate["primary_category"] == "DUPLICATE_AUTOMATIC"
    assert duplicate["secondary_error_flags"] == ["DIRECTION_ERROR"]
    assert result["false_negative_truth_ids"] == []
    assert result["primary_category_counts"]["TRUE_POSITIVE"] == 2
    assert result["primary_category_counts"]["DUPLICATE_AUTOMATIC"] == 1
    assert all(item["passed"] for item in result["accounting_invariants"].values())
    metrics = event_metrics(result)
    assert metrics["false_negative_plain"] == 0
    assert metrics["accounting_invariants"] == result["accounting_invariants"]

    wrong_only = match_events([{**predicted[0], "canonical_direction": "B_TO_A"}], [truth[0]])
    assert [row["primary_category"] for row in wrong_only["matches"]] == ["DIRECTION_ERROR"]
    assert wrong_only["false_negative_truth_ids"] == []
    assert all(item["passed"] for item in wrong_only["accounting_invariants"].values())

    competing = match_events(
        [
            {**predicted[0], "source_event_id": "wrong-a", "technical_key": "wrong-a", "canonical_direction": "B_TO_A"},
            {**predicted[0], "source_event_id": "wrong-b", "technical_key": "wrong-b", "canonical_direction": "B_TO_A", "event_pts_ms": predicted[0]["event_pts_ms"] + 1},
        ],
        [truth[0]],
    )
    assert sum(row["primary_category"] == "DIRECTION_ERROR" for row in competing["matches"]) == 1
    assert sum(row["primary_category"] == "DUPLICATE_AUTOMATIC" for row in competing["matches"]) == 1
    assert competing["false_negative_truth_ids"] == []
    assert all(item["passed"] for item in competing["accounting_invariants"].values())


def test_matching_ties_lines_and_competing_truths_are_order_independent() -> None:
    predicted, truth = normalized_fixture_events()
    two_truths = [
        {**truth[0], "ground_truth_event_id": "truth-a", "crossing_pts_ms": predicted[0]["event_pts_ms"]},
        {**truth[0], "ground_truth_event_id": "truth-b", "crossing_pts_ms": predicted[0]["event_pts_ms"] + 20},
    ]
    one_prediction = [{**predicted[0], "source_event_id": "prediction-a"}]
    result = match_events(one_prediction, two_truths)
    reversed_result = match_events(list(reversed(one_prediction)), list(reversed(two_truths)))
    assert result == reversed_result
    assert result["primary_category_counts"] == {"FALSE_NEGATIVE": 1, "TRUE_POSITIVE": 1}
    assert all(item["passed"] for item in result["accounting_invariants"].values())

    line_b = {**predicted[1], "counting_line_id": "line-b", "line_id": "line-b"}
    truth_b = {**truth[1], "counting_line_id": "line-b", "line_id": "line-b"}
    multi_line = match_events([predicted[0], line_b], [truth[0], truth_b])
    assert multi_line["true_positive"] == 2
    assert all(item["passed"] for item in multi_line["accounting_invariants"].values())


def test_metric_families_use_pts_and_make_zero_denominators_explicit() -> None:
    predicted, truth = normalized_fixture_events()
    matches = match_events(predicted, truth)
    intervals = [
        {"interval_index": 0, "source_relative_start_pts_ms": 0, "source_relative_end_pts_ms": 10_000},
        {"interval_index": 1, "source_relative_start_pts_ms": 10_000, "source_relative_end_pts_ms": 3_600_000},
    ]
    bundle = build_metric_bundle(predicted, truth, matches, intervals)

    assert bundle["event_metrics"]["precision"] == 1.0
    assert bundle["event_metrics"]["recall"] == 1.0
    assert bundle["timestamp_metrics"]["time_authority"] == "source_relative_pts_ms"
    assert bundle["timestamp_metrics"]["p95_absolute_error_ms"] == 95.5
    assert bundle["count_metrics"]["by_interval"][1]["ground_truth_count"] == 1

    empty = count_metrics([], [], intervals)
    assert empty["total"]["automatic_count"] == 0
    assert empty["total"]["ground_truth_count"] == 0
    assert empty["total"]["percentage_error_status"] == "UNDEFINED_ZERO_GROUND_TRUTH"

    classes = class_metrics([], [], {"matches": []})
    assert classes["macro_f1"] is None
    assert classes["does_not_claim_tims_13_class_accuracy"] is True

    class_error_metrics = class_metrics(
        [{"source_event_id": "pred-class", "engineering_class": "MOTORCYCLE"}],
        [{"ground_truth_event_id": "truth-class", "engineering_class": "PASSENGER_VEHICLE"}],
        {
            "matches": [
                {
                    "primary_category": "TRUE_POSITIVE",
                    "automatic_event_id": "pred-class",
                    "ground_truth_event_id": "truth-class",
                    "automatic_class": "MOTORCYCLE",
                    "ground_truth_class": "PASSENGER_VEHICLE",
                }
            ]
        },
    )
    assert class_error_metrics["per_class"]["PASSENGER_VEHICLE"]["recall"] == 0.0
    assert class_error_metrics["per_class"]["MOTORCYCLE"]["precision"] == 0.0


def test_fragmentation_and_throughput_report_availability_without_overclaiming() -> None:
    predicted, truth = normalized_fixture_events()
    matches = match_events(predicted, truth)
    available = fragmentation_metrics(predicted, truth, matches)
    no_track_predicted = [
        {
            key: value
            for key, value in event.items()
            if key not in {"track_id", "track_observation_count", "track_duration_ms"}
        }
        for event in predicted
    ]
    unavailable = fragmentation_metrics(no_track_predicted, truth, match_events(no_track_predicted, truth))

    assert available["availability"] == "AVAILABLE"
    assert available["fragmented_ground_truth_vehicle_count"] == 0
    assert unavailable["availability"] == "NO_MATCHED_IDENTITIES"
    assert unavailable["reason"] is not None

    runtime = throughput_metrics(
        {"processed_frames": 30, "elapsed_ms": 1_000, "source_duration_ms": 2_000, "device": "cpu"}
    )
    assert runtime["processing_duration_video_duration_ratio"] == 0.5
    assert runtime["real_time_criterion"] == "NOT_CLAIMED"
    assert runtime["gpu_memory_status"] == "UNAVAILABLE_WITH_REASON"
    assert environment_snapshot()["cwd"] == "<redacted>"


def test_reviewer_agreement_experiment_hash_and_pareto_policy_are_explicit() -> None:
    annotations = [
        {
            "reviewer_id": "a",
            "ground_truth_event_id": "event-1",
            "event_decision": "VALID",
            "class_decision": "PASSENGER_VEHICLE",
            "direction_decision": "A_TO_B",
            "timestamp_decision": 1_000,
        },
        {
            "reviewer_id": "b",
            "ground_truth_event_id": "event-1",
            "event_decision": "VALID",
            "class_decision": "PASSENGER_VEHICLE",
            "direction_decision": "A_TO_B",
            "timestamp_decision": 1_010,
        },
    ]
    agreement = reviewer_agreement(annotations)
    assert agreement["pairwise"][0]["direction_agreement"] == 1.0
    assert agreement["pairwise"][0]["timestamp_difference_ms"]["maximum"] == 10

    config = build_experiment_configuration(
        {
            "name": "candidate-a",
            "parameters": {
                "detector_confidence_threshold": 0.5,
                "inference_image_size": 640,
                "frame_stride": 1,
            },
        }
    )
    assert config["configuration_revision"].startswith("cfg-")
    assert config["content_hash"] == build_experiment_configuration(
        {
            "name": "candidate-a",
            "parameters": {
                "detector_confidence_threshold": 0.5,
                "inference_image_size": 640,
                "frame_stride": 1,
            },
        }
    )["content_hash"]
    with pytest.raises(BenchmarkValidationError, match="unavailable"):
        build_experiment_configuration({"parameters": {"tracker_match_threshold": 0.4}})

    frontier = pareto_frontier(
        [
            {"id": "a", "recall": 0.80, "precision": 0.90, "duplicate_rate": 0.10},
            {"id": "b", "recall": 0.70, "precision": 0.80, "duplicate_rate": 0.20},
            {"id": "c", "recall": 0.90, "precision": 0.90, "duplicate_rate": 0.05},
        ]
    )
    assert frontier["frontier_ids"] == ["c"]
    assert frontier["status"] == "PARETO_CANDIDATES"

    source = load_fixture("corpus_manifest_6c.json")["sources"][0]
    qualification = qualification_gates(
        source=source,
        ground_truth={"status": "ADJUDICATED", "scene_revision": source["scene_revision"]},
        automatic_result={
            "engineering_ready": True,
            "stale": False,
            "taxonomy_revision": "taxonomy-v1",
            "mapping_revision": "mapping-v1",
            "classification_policy_revision": "policy-v1",
            "code_commit_sha": "a" * 40,
            "configuration_hash": "config-hash",
        },
        metrics={
            "event_metrics": {"f1": 1.0},
            "count_metrics": {"total": {"automatic_count": 1}},
            "timestamp_metrics": {"match_count": 1},
        },
    )
    assert qualification["status"] == "CALIBRATION_CANDIDATE"
    assert qualification["gates"]["approved_threshold_policy"]["passed"] is False


def test_qualification_policy_is_typed_versioned_and_rejects_unsafe_thresholds() -> None:
    source, ground_truth, automatic_result, metrics = qualification_fixture()
    base = approved_threshold_policy(source)
    parsed = parse_qualification_policy(base)
    assert parsed.as_dict()["schema_version"] == "qualification-threshold-v1"
    assert parsed.as_dict()["thresholds"][0]["metric_path"] == "event_metrics.recall"

    for field, value in (
        ("metric_path", "event_metrics.unknown"),
        ("operator", "CALL"),
        ("required_value", "0.9"),
        ("unit", "milliseconds"),
    ):
        invalid = deepcopy(base)
        invalid["thresholds"][0][field] = value
        with pytest.raises(BenchmarkValidationError):
            parse_qualification_policy(invalid)

    with pytest.raises(BenchmarkValidationError):
        parse_qualification_policy({"thresholds": base["thresholds"]})

    passing = qualification_gates(
        source=source,
        ground_truth=ground_truth,
        automatic_result=automatic_result,
        metrics=metrics,
        approved_policy=base,
    )
    assert passing["status"] == "QUALIFIED_FOR_PILOT"
    assert passing["threshold_evaluation"]["all_required_passed"] is True
    assert all(
        {"metric_path", "observed_value", "required_value", "operator", "unit", "passed", "availability", "failure_reason"}.issubset(result)
        for result in passing["threshold_evaluation"]["results"]
    )


def test_qualification_does_not_claim_reproducibility_without_runtime_hash() -> None:
    source, ground_truth, automatic_result, metrics = qualification_fixture()
    automatic_result.pop("runtime_configuration_hash")
    policy = approved_threshold_policy(source)

    result = qualification_gates(
        source=source,
        ground_truth=ground_truth,
        automatic_result=automatic_result,
        metrics=metrics,
        approved_policy=policy,
    )

    assert result["status"] == "NOT_QUALIFIED"
    assert result["gates"]["reproducibility_metadata"]["passed"] is False


def test_cli_evaluation_config_propagates_code_commit_to_reproducibility() -> None:
    report = run_benchmark(
        load_fixture("corpus_manifest_6c.json"),
        load_fixture("ground_truth_6c.json"),
        load_fixture("predicted_events_6c.json"),
        source_id="synthetic-calibration-01",
        evaluation_payload={
            "engineering_ready": True,
            "reconciliation_status": "STRUCTURALLY_VALID",
            "taxonomy_revision": "pilot-observable-taxonomy-v1",
            "mapping_revision": "pilot-observable-mapping-v1",
            "classification_policy_revision": "track-vote-policy-v1",
            "code_commit_sha": "config-commit-6c",
            "configuration_hash": "config-hash-6c",
        },
    )
    assert report["report"]["reproducibility"]["code_commit_sha"] == "config-commit-6c"


@pytest.mark.parametrize(
    ("metric_path", "operator", "required_value", "unit", "metric_update"),
    [
        ("event_metrics.recall", "GTE", 0.95, "ratio", {"event_metrics": {"recall": 0.8}}),
        ("event_metrics.precision", "GTE", 0.95, "ratio", {"event_metrics": {"precision": 0.8}}),
        ("duplicate_metrics.duplicate_rate", "LTE", 0.05, "ratio", {"duplicate_metrics": {"duplicate_rate": 0.2}}),
        ("timestamp_metrics.mean_absolute_error_ms", "LTE", 50, "milliseconds", {"timestamp_metrics": {"mean_absolute_error_ms": 75}}),
    ],
)
def test_qualification_threshold_failures_block_pilot(
    metric_path: str, operator: str, required_value: int | float, unit: str, metric_update: dict
) -> None:
    source, ground_truth, automatic_result, metrics = qualification_fixture()
    failing_metrics = deepcopy(metrics)
    for family, values in metric_update.items():
        failing_metrics[family].update(values)
    policy = approved_threshold_policy(
        source,
        thresholds=[
            {
                "metric_path": metric_path,
                "operator": operator,
                "required_value": required_value,
                "unit": unit,
                "required": True,
                "undefined_behavior": "FAIL_CLOSED",
            }
        ],
    )
    result = qualification_gates(source=source, ground_truth=ground_truth, automatic_result=automatic_result, metrics=failing_metrics, approved_policy=policy)
    threshold = result["threshold_evaluation"]["results"][0]
    assert result["status"] == "NOT_QUALIFIED"
    assert threshold["passed"] is False
    assert threshold["failure_reason"]


def test_qualification_null_and_zero_denominator_metrics_fail_closed() -> None:
    source, ground_truth, automatic_result, metrics = qualification_fixture()
    policy = approved_threshold_policy(
        source,
        thresholds=[
            {"metric_path": "event_metrics.recall", "operator": "GTE", "required_value": 0.9, "unit": "ratio", "required": True, "undefined_behavior": "FAIL_CLOSED"},
            {"metric_path": "event_metrics.precision", "operator": "GTE", "required_value": 0.9, "unit": "ratio", "required": True, "undefined_behavior": "FAIL_CLOSED"},
        ],
    )
    undefined = deepcopy(metrics)
    undefined["event_metrics"]["recall"] = None
    undefined["event_metrics"]["denominators"]["recall"] = 0
    undefined["event_metrics"]["precision"] = None
    undefined["event_metrics"]["denominators"]["precision"] = 0
    result = qualification_gates(source=source, ground_truth=ground_truth, automatic_result=automatic_result, metrics=undefined, approved_policy=policy)
    assert result["status"] == "NOT_QUALIFIED"
    assert [item["availability"] for item in result["threshold_evaluation"]["results"]] == ["UNDEFINED_ZERO_DENOMINATOR", "UNDEFINED_ZERO_DENOMINATOR"]
    assert all(item["passed"] is False for item in result["threshold_evaluation"]["results"])
    assert evaluate_thresholds(undefined, policy)["all_required_passed"] is False


def test_qualification_requires_holdout_and_policy_coverage() -> None:
    source, ground_truth, automatic_result, metrics = qualification_fixture(split="CALIBRATION")
    policy = approved_threshold_policy(source)
    calibration = qualification_gates(source=source, ground_truth=ground_truth, automatic_result=automatic_result, metrics=metrics, approved_policy=policy)
    assert calibration["status"] == "CALIBRATION_CANDIDATE"
    assert calibration["gates"]["required_holdout_evidence"]["passed"] is False

    insufficient_source, gt, auto, metric_bundle = qualification_fixture(source_count=0)
    policy = approved_threshold_policy(insufficient_source)
    source_gate = qualification_gates(source=insufficient_source, ground_truth=gt, automatic_result=auto, metrics=metric_bundle, approved_policy=policy)
    assert source_gate["status"] == "NOT_QUALIFIED"
    assert source_gate["gates"]["minimum_source_count"]["passed"] is False

    insufficient_truth, gt, auto, metric_bundle = qualification_fixture()
    gt["events"] = []
    policy = approved_threshold_policy(insufficient_truth)
    truth_gate = qualification_gates(source=insufficient_truth, ground_truth=gt, automatic_result=auto, metrics=metric_bundle, approved_policy=policy)
    assert truth_gate["status"] == "NOT_QUALIFIED"
    assert truth_gate["gates"]["minimum_ground_truth_event_count"]["passed"] is False

    missing_condition, gt, auto, metric_bundle = qualification_fixture(conditions=["daytime"])
    policy = approved_threshold_policy(missing_condition, minimum_condition_coverage=["daytime", "night"])
    condition_gate = qualification_gates(source=missing_condition, ground_truth=gt, automatic_result=auto, metrics=metric_bundle, approved_policy=policy)
    assert condition_gate["status"] == "NOT_QUALIFIED"
    assert condition_gate["gates"]["minimum_condition_coverage"]["passed"] is False


def test_benchmark_service_imports_transactionally_and_preserves_6b_append_only_contracts() -> None:
    connection = connect()
    migrate(connection)
    service = BenchmarkService(connection)
    corpus = load_fixture("corpus_manifest_6c.json")
    ground_truth = load_fixture("ground_truth_6c.json")

    imported = service.import_manifest(corpus)
    assert imported["revision"] == corpus["corpus_revision"]
    assert service.import_manifest(corpus)["id"] == imported["id"]
    assert service.import_ground_truth(ground_truth)["revision"] == ground_truth["ground_truth_revision"]
    connection.execute(
        "INSERT INTO benchmark_experiment_suites(id, revision, corpus_revision_id, calibration_split, holdout_split, baseline_configuration_id, candidate_configurations_json, status, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("suite-test", "suite-revision-v1", imported["id"], "CALIBRATION", "HOLDOUT", None, json.dumps([{"configuration_revision": "cfg-test"}]), "CANDIDATES_READY", "2026-01-01T00:00:00+00:00", "test"),
    )
    comparison = service.list_experiment_comparisons()[0]
    assert comparison["suite_revision"] == "suite-revision-v1"
    assert comparison["pareto"]["status"] == "NOT_RUN"
    assert comparison["qualification"]["no_pilot_qualification_claim"] is True
    assert connection.execute("SELECT COUNT(*) FROM benchmark_sources").fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM ground_truth_events").fetchone()[0] == 3

    before = connection.execute("SELECT COUNT(*) FROM ground_truth_revisions").fetchone()[0]
    invalid = deepcopy(ground_truth)
    invalid["source_fingerprint_sha256"] = "d" * 64
    with pytest.raises(BenchmarkValidationError):
        service.import_ground_truth(invalid)
    assert connection.execute("SELECT COUNT(*) FROM ground_truth_revisions").fetchone()[0] == before

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute(
            "UPDATE ground_truth_events SET notes = 'changed' WHERE ground_truth_event_id = 'gt-cal-001'"
        )


def make_service_fixture(*, split: str = "CALIBRATION") -> tuple[sqlite3.Connection, BenchmarkService, dict, dict]:
    connection = connect()
    migrate(connection)
    foundation = FoundationService(connection)
    service = BenchmarkService(connection)
    project = foundation.create_project("6C state", "Bangkok", "intersection", "en")
    source = foundation.register_source(
        SourceCreate(
            project_id=project["id"],
            file_name="synthetic-state.mp4",
            fingerprint_sha256="f" * 64,
            source_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            timezone_name="Asia/Bangkok",
            analysis_start_pts_ms=0,
            analysis_end_pts_ms=3_600_000,
        )
    )
    scene = foundation.create_scene(project["id"], "intersection")
    run = foundation.run_mock_analysis(project["id"])
    corpus = {
        "manifest_version": "benchmark-corpus-v1",
        "corpus_revision": "synthetic-state-corpus-v1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "created_by": "test",
        "sources": [
            {
                "benchmark_source_id": "synthetic-state-source",
                "source_fingerprint_sha256": source["fingerprint_sha256"],
                "file_name_or_external_reference": "synthetic://synthetic-state.mp4",
                "media_duration_ms": 3_600_000,
                "source_width": 1280,
                "source_height": 720,
                "nominal_fps_if_known": 30,
                "recording_start_status": "UNCONFIGURED",
                "timezone_name": "Asia/Bangkok",
                "rights_status": "CLEARED_FOR_LOCAL_BENCHMARK",
                "rights_basis": "repository-authored synthetic fixture",
                "permission_reference": "test-fixture",
                "redistribution_status": "NOT_REDISTRIBUTABLE",
                "storage_status": "SYNTHETIC_METADATA_ONLY",
                "checksum_verified": True,
                "scene_revision": scene["id"],
                "annotation_revision": "synthetic-state-gt-v1",
                "benchmark_split": split,
                "condition_tags": ["daytime", "short clip"],
                "created_at": "2026-01-01T00:00:00+00:00",
                "created_by": "test",
            }
        ],
    }
    ground_truth = {
        "ground_truth_manifest_version": "ground-truth-v1",
        "benchmark_source_id": "synthetic-state-source",
        "source_fingerprint_sha256": source["fingerprint_sha256"],
        "ground_truth_revision": "synthetic-state-gt-v1",
        "scene_revision": scene["id"],
        "status": "ADJUDICATED",
        "created_at": "2026-01-01T00:00:00+00:00",
        "created_by": "test",
        "events": [],
        "reviewer_annotations": [],
    }
    service.import_manifest(corpus)
    service.import_ground_truth(ground_truth)
    return connection, service, run, source


@pytest.mark.parametrize("state", ["stale", "missing_reconciliation"])
def test_benchmark_service_blocks_stale_or_unreconciled_results_without_metric_snapshots(state: str) -> None:
    connection, service, run, source = make_service_fixture()
    result = connection.execute("SELECT * FROM engineering_result_revisions WHERE run_id = ?", (run["id"],)).fetchone()
    assert result is not None
    if state == "stale":
        connection.execute("UPDATE engineering_result_revisions SET stale = 1, result_status = 'STALE', engineering_ready = 0 WHERE id = ?", (result["id"],))
    else:
        connection.execute("DELETE FROM reconciliation_reports WHERE engineering_result_revision_id = ?", (result["id"],))
    connection.commit()
    before_events = [dict(row) for row in connection.execute("SELECT * FROM auto_count_events WHERE run_id = ? ORDER BY id", (run["id"],))]

    evaluated = service.evaluate(automatic_run_id=run["id"], benchmark_source_id="synthetic-state-source", code_commit_sha="f" * 40)

    assert evaluated["status"] == "INCOMPLETE"
    assert evaluated["metrics"] == {}
    assert evaluated["match_result"]["status"] == "NOT_SCORABLE"
    assert connection.execute("SELECT COUNT(*) FROM benchmark_metric_snapshots WHERE benchmark_run_id = ?", (evaluated["id"],)).fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM benchmark_event_matches WHERE benchmark_run_id = ?", (evaluated["id"],)).fetchone()[0] == 0
    assert before_events == [dict(row) for row in connection.execute("SELECT * FROM auto_count_events WHERE run_id = ? ORDER BY id", (run["id"],))]
    blockers = set(evaluated["qualification"]["automatic_result_blockers"])
    assert any("STALE" in blocker or "RECONCILIATION_MISSING" in blocker for blocker in blockers)


def test_benchmark_qualification_threshold_evaluation_is_persisted_and_returned_by_api() -> None:
    connection, service, run, _source = make_service_fixture(split="HOLDOUT")
    result = connection.execute("SELECT * FROM engineering_result_revisions WHERE run_id = ?", (run["id"],)).fetchone()
    assert result is not None
    connection.execute("UPDATE engineering_result_revisions SET engineering_ready = 1, result_status = 'STRUCTURALLY_VALID', stale = 0 WHERE id = ?", (result["id"],))
    connection.execute("UPDATE reconciliation_reports SET status = 'STRUCTURALLY_VALID' WHERE engineering_result_revision_id = ?", (result["id"],))
    connection.commit()
    policy = {
        "policy_revision": "owner-policy-service-v1",
        "approved_by": ('owner@' + 'example.test'),
        "approved_at": "2026-01-01T00:00:00+00:00",
        "applicable_corpus_revision": "synthetic-state-corpus-v1",
        "required_split": "HOLDOUT",
        "minimum_source_count": 1,
        "minimum_ground_truth_event_count": 1,
        "minimum_condition_coverage": ["daytime"],
        "thresholds": [
            {"metric_path": "event_metrics.recall", "operator": "GTE", "required_value": 0.9, "unit": "ratio", "required": True, "undefined_behavior": "FAIL_CLOSED"}
        ],
    }
    evaluated = service.evaluate(automatic_run_id=run["id"], benchmark_source_id="synthetic-state-source", code_commit_sha="f" * 40, approved_policy=policy)
    qualification = service.qualification(evaluated["id"])
    assert qualification["policy"]["policy_revision"] == "owner-policy-service-v1"
    assert qualification["threshold_evaluation"]["results"][0]["metric_path"] == "event_metrics.recall"
    assert qualification["threshold_evaluation"]["results"][0]["availability"] == "UNDEFINED_ZERO_DENOMINATOR"

    app = create_app()
    app.state.benchmark_service.connection = connection
    response = TestClient(app).get(f"/api/v1/benchmarks/runs/{evaluated['id']}/qualification")
    assert response.status_code == 200
    assert response.json()["threshold_evaluation"]["results"][0]["metric_path"] == "event_metrics.recall"

    invalid_api_policy = deepcopy(policy)
    invalid_api_policy["thresholds"][0]["required_value"] = "0.9"
    invalid_response = TestClient(app).post(
        "/api/v1/benchmarks/runs/evaluate",
        json={
            "automatic_run_id": run["id"],
            "benchmark_source_id": "synthetic-state-source",
            "approved_policy": invalid_api_policy,
        },
    )
    assert invalid_response.status_code == 422


def test_benchmark_service_evaluates_a_completed_6b_run_and_persists_report_layers() -> None:
    connection = connect()
    migrate(connection)
    foundation = FoundationService(connection)
    service = BenchmarkService(connection)
    project = foundation.create_project("6C evaluation", "Bangkok", "intersection", "en")
    source = foundation.register_source(
        SourceCreate(
            project_id=project["id"],
            file_name="synthetic-evaluation.mp4",
            fingerprint_sha256="e" * 64,
            source_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            timezone_name="Asia/Bangkok",
            analysis_start_pts_ms=0,
            analysis_end_pts_ms=3_600_000,
        )
    )
    scene = foundation.create_scene(project["id"], "intersection")
    run = foundation.run_mock_analysis(project["id"])
    corpus = {
        "manifest_version": "benchmark-corpus-v1",
        "corpus_revision": "synthetic-evaluation-corpus-v1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "created_by": "test",
        "sources": [
            {
                "benchmark_source_id": "synthetic-evaluation-source",
                "source_fingerprint_sha256": source["fingerprint_sha256"],
                "file_name_or_external_reference": "synthetic://synthetic-evaluation.mp4",
                "media_duration_ms": 3_600_000,
                "source_width": 1280,
                "source_height": 720,
                "nominal_fps_if_known": 30,
                "recording_start_status": "UNCONFIGURED",
                "timezone_name": "Asia/Bangkok",
                "rights_status": "CLEARED_FOR_LOCAL_BENCHMARK",
                "rights_basis": "repository-authored synthetic fixture",
                "permission_reference": "test-fixture",
                "redistribution_status": "NOT_REDISTRIBUTABLE",
                "storage_status": "SYNTHETIC_METADATA_ONLY",
                "checksum_verified": True,
                "scene_revision": scene["id"],
                "annotation_revision": "synthetic-evaluation-gt-v1",
                "benchmark_split": "CALIBRATION",
                "condition_tags": ["daytime", "short clip"],
                "created_at": "2026-01-01T00:00:00+00:00",
                "created_by": "test",
            }
        ],
    }
    ground_truth = {
        "ground_truth_manifest_version": "ground-truth-v1",
        "benchmark_source_id": "synthetic-evaluation-source",
        "source_fingerprint_sha256": source["fingerprint_sha256"],
        "ground_truth_revision": "synthetic-evaluation-gt-v1",
        "scene_revision": scene["id"],
        "status": "ADJUDICATED",
        "created_at": "2026-01-01T00:00:00+00:00",
        "created_by": "test",
        "events": [],
        "reviewer_annotations": [],
    }
    imported_corpus = service.import_manifest(corpus)
    service.import_ground_truth(ground_truth)

    evaluated = service.evaluate(
        automatic_run_id=run["id"],
        benchmark_source_id="synthetic-evaluation-source",
        code_commit_sha="e" * 40,
    )

    assert imported_corpus["source_count"] == 1
    assert evaluated["status"] == "INCOMPLETE"
    assert evaluated["qualification_status"] == "INCOMPLETE"
    assert evaluated["report"]["reproducibility"]["source_checksum"] == source["fingerprint_sha256"]
    assert evaluated["metrics"] == {}
    assert evaluated["match_result"]["status"] == "NOT_SCORABLE"
    assert evaluated["qualification"]["automatic_result_blockers"]
    assert connection.execute("SELECT COUNT(*) FROM benchmark_metric_snapshots").fetchone()[0] == 0
    assert service.list_matches(evaluated["id"])["items"] == []
    rerun = service.evaluate(
        automatic_run_id=run["id"],
        benchmark_source_id="synthetic-evaluation-source",
        code_commit_sha="e" * 40,
    )
    assert rerun["id"] == evaluated["id"]
    assert connection.execute("SELECT COUNT(*) FROM benchmark_runs").fetchone()[0] == 1


def test_benchmark_api_exposes_empty_and_validation_states() -> None:
    client = TestClient(create_app())
    overview = client.get("/api/v1/benchmarks/overview")
    assert overview.status_code == 200
    assert overview.json()["status"] == "EMPTY"
    experiments = client.get("/api/v1/benchmarks/experiments")
    assert experiments.status_code == 200
    assert experiments.json() == []

    corpus = load_fixture("corpus_manifest_6c.json")
    validated = client.post("/api/v1/benchmarks/corpora/validate", json={"manifest": corpus})
    assert validated.status_code == 200
    assert validated.json()["valid"] is True

    bad = deepcopy(corpus)
    bad["sources"][0]["source_fingerprint_sha256"] = "not-a-sha"
    response = client.post("/api/v1/benchmarks/corpora/validate", json={"manifest": bad})
    assert response.status_code == 200
    assert response.json()["valid"] is False


def test_active_migration_keeps_6b_event_immutability() -> None:
    connection = connect()
    migrate(connection)
    tables = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "benchmark_runs" in tables
    assert "engineering_event_projections" in tables
