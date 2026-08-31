from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from tools.ai_stack.class_fusion import ClassEvidence, fuse_track_class
from tools.ai_stack.artifacts import (
    artifact_hash,
    observable_class,
    validate_normalized_track_artifact,
)
from tools.ai_stack.contracts import (
    AdapterManifest,
    DetectorBox,
    FrameRef,
    NormalizedTrack,
    TrackObservation,
    representative_point,
    validate_normalized_box,
)
from tools.ai_stack.registry import load_registry, validate_registry
from tools.ai_stack.runtime import runtime_status

ROOT = Path(__file__).resolve().parents[2]


def test_model_registry_contract_accepts_primary_and_fallback_policy() -> None:
    registry = load_registry(ROOT / "model_registry.json")
    errors = validate_registry(registry)
    assert errors == ()
    models = {record["model_id"]: record for record in registry["models"]}
    assert models["detector.ultralytics-yolo11n-coco"]["approval_state"] == "technical evaluation only"
    assert models["detector.ultralytics-yolo11n-coco"]["readiness_state"] == "runtime_smoke_qualified"
    assert models["tracker.trackers-bytetrack"]["approval_state"] == "technical evaluation only"
    assert models["tracker.trackers-bytetrack"]["readiness_state"] == "runtime_smoke_qualified"
    assert models["detector.rtdetr-r18-coco-official"]["approval_state"] == "approved fallback"
    assert "axle count" in " ".join(models["detector.ultralytics-yolo11n-coco"]["unsupported_distinctions"])


def test_model_registry_rejects_duplicate_ids_and_bad_hash() -> None:
    registry = load_registry(ROOT / "model_registry.json")
    duplicate = dict(registry)
    first = dict(registry["models"][0])
    second = dict(registry["models"][0])
    second["sha256"] = "not-a-sha"
    duplicate["models"] = [first, second]
    errors = validate_registry(duplicate)
    assert {"duplicate_model_id", "invalid_sha256"} <= {error.code for error in errors}


def test_ai_runtime_reports_absent_optional_environment_without_touching_core() -> None:
    registry = load_registry(ROOT / "model_registry.json")
    status = runtime_status(ROOT, registry)
    payload = asdict(status)
    assert payload["schema_version"] == "ai-runtime-status-v1"
    assert payload["ai_venv_expected"].endswith(".venv-ai")
    assert "packages" in payload
    assert isinstance(status.blockers, tuple)
    assert all("mmdet" not in blocker for blocker in status.blockers)


def test_observable_class_mapping_stays_coarse_and_never_invents_tims_codes() -> None:
    assert observable_class("person") == "pedestrian"
    assert observable_class("car") == "passenger_vehicle"
    assert observable_class("bus") == "bus"
    assert observable_class("truck") == "truck"
    assert observable_class("train") == "unknown"
    assert observable_class("traffic light") == "unknown"


def test_normalized_smoke_artifact_validation_rejects_unordered_or_private_payloads() -> None:
    payload = {
        "schema_version": "normalized-track-artifact-v1",
        "tracks": [
            {
                "track_id": "1",
                "track_class": "car",
                "observable_class": "passenger_vehicle",
                "track_confidence": 0.9,
                "review_state": "needs_review",
                "observations": [
                    {
                        "pts_ms": 0,
                        "frame_index": 0,
                        "bbox_xywh": [0.1, 0.2, 0.3, 0.4],
                        "representative_point": [0.25, 0.6],
                        "native_class": "car",
                        "observable_class": "passenger_vehicle",
                        "confidence": 0.9,
                        "evidence": {"decoded_frame": "frame_0000.jpg"},
                    }
                ],
            }
        ],
    }
    assert validate_normalized_track_artifact(payload) == ()
    assert len(artifact_hash(payload)) == 64

    payload["tracks"][0]["observations"].append(
        {
            "_framework_box": "leak",
            "pts_ms": -1,
            "frame_index": 1,
            "bbox_xywh": [0.1, 1.2, 0.3, 0.4],
            "representative_point": [0.25, 0.6],
        }
    )
    errors = validate_normalized_track_artifact(payload)
    assert "track_observations_not_ordered_by_pts" in errors
    assert "framework_private_key_leaked" in errors
    assert "observation_bbox_out_of_bounds" in errors


def test_normalized_adapter_contract_preserves_pts_and_provenance() -> None:
    detector_manifest = AdapterManifest(
        schema_version="adapter-manifest-v1",
        implementation_id="detector.test",
        model_family="test",
        package_name="testpkg",
        package_version="1.0.0",
        model_version="weights-v1",
        weight_sha256="0" * 64,
        license_classification="test-only",
        runtime_provider="cpu",
        device="cpu",
        configuration={"confidence": 0.25},
        supported_classes=("car",),
        unsupported_distinctions=("TIMS fine-grained classes",),
    )
    tracker_manifest = AdapterManifest(
        schema_version="adapter-manifest-v1",
        implementation_id="tracker.test",
        model_family="ByteTrack",
        package_name="trackers",
        package_version="2.5.0.post0",
        model_version="none",
        weight_sha256=None,
        license_classification="Apache-2.0",
        runtime_provider="cpu",
        device="cpu",
        configuration={},
    )
    observation = TrackObservation(
        pts_ms=1234,
        frame_index=7,
        bbox_xywh=(0.1, 0.2, 0.3, 0.4),
        representative_point=representative_point((0.1, 0.2, 0.3, 0.4), "bottom_center"),
        native_class="car",
        confidence=0.9,
        evidence={"frame_pts_ms": 1234},
    )
    track = NormalizedTrack(
        schema_version="normalized-track-artifact-v1",
        track_id="track_1",
        source_fingerprint="abc",
        detector_manifest=detector_manifest,
        tracker_manifest=tracker_manifest,
        observations=(observation,),
        track_class="car",
        track_confidence=0.9,
        review_state="accepted",
        provenance={"source": "unit-test"},
    )
    encoded = json.dumps(asdict(track), sort_keys=True)
    assert "1234" in encoded
    assert "detector.test" in encoded


def test_box_validation_and_frame_reference_contract() -> None:
    frame = FrameRef(frame_index=1, pts_ms=40, width=1920, height=1080, source_fingerprint="src")
    assert frame.pts_ms == 40
    assert validate_normalized_box(DetectorBox(0.1, 0.2, 0.4, 0.8, "car", 0.7, "detector")) == ()
    errors = validate_normalized_box(DetectorBox(0.4, 0.2, 0.1, 0.8, "car", 1.4, "detector"))
    assert set(errors) == {"box_has_non_positive_area", "confidence_out_of_range"}


def test_class_fusion_stable_conflict_low_confidence_and_empty_evidence() -> None:
    stable = fuse_track_class(
        (
            ClassEvidence(0, "car", 0.9),
            ClassEvidence(40, "car", 0.8),
            ClassEvidence(80, "truck", 0.1),
        )
    )
    assert stable.label == "car"
    assert stable.review_state == "accepted"
    assert "conflicting_frame_classes" in stable.warnings

    ambiguous = fuse_track_class((ClassEvidence(0, "car", 0.5), ClassEvidence(40, "truck", 0.48)))
    assert ambiguous.review_state == "ambiguous"

    unknown = fuse_track_class(())
    assert unknown.label == "unknown"
    assert unknown.review_state == "unknown"

    low = fuse_track_class((ClassEvidence(0, "car", 0.1),), min_confidence=0.95)
    assert low.review_state == "needs_review"
