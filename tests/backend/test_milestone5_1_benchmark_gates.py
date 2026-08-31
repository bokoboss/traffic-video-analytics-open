from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.benchmark.dataset import validate_ground_truth, validate_manifest
from tools.benchmark.metrics import aggregate_count_errors, match_crossing_events
from tools.benchmark.runtime import benchmark_runtime_status


ROOT = Path(__file__).resolve().parents[2]


def manifest() -> dict:
    return {
        "manifest_version": "benchmark-dataset-manifest-v1",
        "dataset_id": "m5-1-smoke",
        "clips": [
            {
                "clip_id": "clip_1",
                "source_fingerprint": "abc123",
                "rights_status": "synthetic_fixture",
                "local_artifact_ref": ".local-data/benchmark-media/clip_1.mp4",
                "clip_start_ms": 0,
                "clip_end_ms": 10_000,
                "resolution": {"width": 640, "height": 360},
                "time_base": "1/1000",
                "fps_metadata": {"nominal": "30/1", "average": "30/1"},
                "vfr_status": "not_detected",
                "camera_view": "synthetic_oblique",
                "road_type": "technical_smoke",
                "lighting": "daylight",
                "weather": "clear",
                "congestion": "free_flow",
                "expected_classes": ["car"],
                "scene_revision": "scene_1",
                "roi": [],
                "counting_lines": [{"line_id": "line_main", "direction_mode": "bidirectional"}],
                "exclusions": [],
                "annotation_status": "reviewed",
                "reviewer_status": "single_reviewer",
            }
        ],
    }


def ground_truth() -> dict:
    return {
        "ground_truth_version": "benchmark-ground-truth-v1",
        "annotation_set_id": "gt_1",
        "events": [
            {
                "event_id": "truth_1",
                "clip_id": "clip_1",
                "object_ref": "vehicle_1",
                "line_id": "line_main",
                "direction": "a_to_b",
                "crossing_timestamp_ms": 1000,
                "timestamp_tolerance_ms": 100,
                "observable_class": "car",
                "tims_class": "",
                "included": True,
                "uncertainty": "certain",
                "annotator": "fixture",
                "reviewer": "fixture",
                "review_status": "reviewed",
            }
        ],
    }


def test_benchmark_runtime_status_keeps_optional_runtime_out_of_core() -> None:
    status = benchmark_runtime_status(ROOT)
    assert status.schema_version == "benchmark-runtime-status-v1"
    assert status.benchmark_requirements_file.endswith("requirements-benchmark.txt")
    assert "torch" in status.packages
    assert isinstance(status.blockers, tuple)


def test_dataset_manifest_and_ground_truth_accept_reviewed_fixture() -> None:
    doc = manifest()
    gt = ground_truth()
    assert validate_manifest(doc) == ()
    assert validate_ground_truth(gt, doc) == ()


def test_dataset_validation_rejects_unsafe_paths_and_forced_tims_class() -> None:
    doc = manifest()
    doc["clips"][0]["local_artifact_ref"] = "../private/client.mp4"
    gt = ground_truth()
    gt["events"][0]["uncertainty"] = "ambiguous"
    gt["events"][0]["tims_class"] = "tims_pending_01"
    errors = validate_manifest(doc) + validate_ground_truth(gt, manifest())
    assert {error.code for error in errors} == {"unsafe_local_artifact_ref", "unsupported_tims_assignment"}


def test_crossing_metrics_handle_matches_misses_duplicates_and_zero_denominator() -> None:
    predicted = [
        {
            "event_id": "pred_1",
            "line_id": "line_main",
            "direction": "a_to_b",
            "crossing_timestamp_ms": 1040,
            "class": "car",
        },
        {
            "event_id": "pred_false",
            "line_id": "line_main",
            "direction": "a_to_b",
            "crossing_timestamp_ms": 5000,
            "class": "car",
        },
    ]
    metrics = match_crossing_events(predicted, ground_truth()["events"])
    assert metrics["true_positive"] == 1
    assert metrics["false_positive"] == 1
    assert metrics["false_negative"] == 0
    assert metrics["precision"] == pytest.approx(0.5)
    assert metrics["recall"] == pytest.approx(1.0)
    assert metrics["timestamp_error_ms"]["mean_absolute"] == 40

    empty = match_crossing_events([], [])
    assert empty["precision"] is None
    assert empty["recall"] is None


def test_aggregate_count_errors_reconcile_dimensions() -> None:
    predicted = [
        {"line_id": "line_main", "direction": "a_to_b", "class": "car"},
        {"line_id": "line_main", "direction": "b_to_a", "class": "motorcycle"},
    ]
    truth = ground_truth()["events"]
    errors = aggregate_count_errors(predicted, truth)
    assert errors["totals"]["predicted_total"] == 2
    assert errors["totals"]["truth_total"] == 1
    assert errors["totals"]["overcount"] == 1
    assert errors["dimensions"]["by_class"]["motorcycle"]["absolute_error"] == 1


def test_dataset_validation_script_payload_shape(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    ground_truth_path = tmp_path / "gt.json"
    manifest_path.write_text(json.dumps(manifest()), encoding="utf-8")
    ground_truth_path.write_text(json.dumps(ground_truth()), encoding="utf-8")
    assert manifest_path.exists()
    assert ground_truth_path.exists()
