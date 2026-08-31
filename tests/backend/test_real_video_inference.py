from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.backend.app.main import create_app
from apps.backend.app import real_inference as real_inference_module
from apps.backend.app.db import connect
from apps.backend.app.real_inference import (
    RealInferenceCancelled,
    RealInferenceError,
    RealInferenceResult,
    RealProcessingConfig,
    eligible_real_tracks_for_counting,
    resolve_real_runtime,
)
from apps.backend.app.synthetic_counting import (
    Observation,
    Point,
    REAL_TRACK_PROVENANCE,
    REAL_TRACK_SCHEMA_VERSION,
    SyntheticTrack,
    SyntheticTrackSet,
    execute_synthetic_counting,
    scene_from_geometry,
)
from apps.backend.app import services as service_module
from apps.backend.app.domain import TimeContract
from apps.backend.app.schemas import SourceCreate


def _ready_runtime() -> dict:
    return {"real_inference": {"ready": True, "state": "real_ready"}}


def _stub_runtime(monkeypatch, tmp_path: Path, *, cuda_available: bool) -> Path:
    root = tmp_path / "runtime"
    (root / ".local-tools" / "models").mkdir(parents=True)
    (root / ".local-tools" / "models" / "yolo11n.pt").write_bytes(b"weights")
    (root / "model_registry.json").write_text("{}", encoding="utf-8")
    registry = {
        "models": [
            {
                "model_id": "detector.ultralytics-yolo11n-coco",
                "model_filename": "yolo11n.pt",
                "sha256": "runtime-hash",
            },
            {"model_id": "tracker.trackers-bytetrack", "model_filename": None, "sha256": None},
        ]
    }
    monkeypatch.setattr(real_inference_module, "load_registry", lambda _path: registry)
    monkeypatch.setattr(real_inference_module, "file_sha256", lambda _path: "runtime-hash")
    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: cuda_available))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    return root


def test_auto_device_resolution_falls_back_to_cpu_without_loading_detector(tmp_path, monkeypatch) -> None:
    root = _stub_runtime(monkeypatch, tmp_path, cuda_available=False)
    resolved = resolve_real_runtime(root, RealProcessingConfig.from_payload({"device_mode": "AUTO"}))
    assert resolved.resolved_device == "cpu"


def test_explicit_cuda_fails_when_capability_is_unavailable(tmp_path, monkeypatch) -> None:
    root = _stub_runtime(monkeypatch, tmp_path, cuda_available=False)
    with pytest.raises(RealInferenceError) as error:
        resolve_real_runtime(root, RealProcessingConfig.from_payload({"device_mode": "CUDA"}))
    assert error.value.code == "cuda_unavailable"


def test_worker_consumes_resolved_parameters_and_ignores_request_metadata() -> None:
    config = RealProcessingConfig.from_payload(
        {
            "resolved_parameters": {"frame_stride": 7},
            "runtime_configuration_hash": "runtime-hash",
            "request_provenance_hash": "request-hash",
            "runtime_provenance_status": "CONFIGURED_EXPECTED_WEIGHT_SHA",
            "device_mode": "CPU",
            "confidence_threshold": 0.41,
            "frame_stride": 3,
            "minimum_track_observations": 5,
        }
    )

    assert config.device_mode == "CPU"
    assert config.confidence_threshold == 0.41
    assert config.frame_stride == 3
    assert config.minimum_track_observations == 5


def _fake_result(service, run_id: str) -> RealInferenceResult:
    source = service.connection.execute("SELECT * FROM video_sources ORDER BY created_at DESC LIMIT 1").fetchone()
    scene_row = service.connection.execute("SELECT * FROM scene_versions ORDER BY version DESC LIMIT 1").fetchone()
    run_row = service.connection.execute("SELECT processing_config_json FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()
    assert run_row is not None
    saved_config = json.loads(str(run_row["processing_config_json"]))
    scene = scene_from_geometry(dict(scene_row))
    contract = TimeContract(
        source_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        timezone_name="UTC",
        analysis_start_pts_ms=0,
        analysis_end_pts_ms=2000,
        interval_origin_pts_ms=0,
    )
    tracks = SyntheticTrackSet(
        schema_version=REAL_TRACK_SCHEMA_VERSION,
        source_fingerprint=source["fingerprint_sha256"],
        fixture_id="real-video",
        scene_revision=scene.scene_revision,
        tracks=(
            SyntheticTrack(
                track_id="real-track-1",
                observations=(
                    Observation(0, Point(0.5, 0.40), source_frame_index=0),
                    Observation(1000, Point(0.5, 0.70), source_frame_index=1),
                ),
                synthetic_class="passenger_vehicle",
                confidence=0.91,
                provenance=REAL_TRACK_PROVENANCE,
                raw_class_id=2,
                raw_class_name="car",
                provisional_class="passenger_vehicle",
                classification_review_state="accepted",
            ),
        ),
    )
    result = execute_synthetic_counting(run_id, tracks, scene, contract, time_configured=False)
    return RealInferenceResult(
        counting_result=result,
        config=RealProcessingConfig(resolved_device="cpu"),
        stats={
            "decoded_frames": 2,
            "processed_frames": 2,
            "event_count": len(result.events),
            "detector_version": "8.4.103",
            "detector_revision": "pypi:8.4.103",
            "tracker_version": "2.5.0.post0",
            "tracker_revision": "pypi:2.5.0.post0",
            "weight_identifier": "yolo11n.pt",
            "weight_sha256": "pilot-weight-sha256",
            "configuration_hash": saved_config["configuration_hash"],
            "request_provenance_hash": saved_config["request_provenance_hash"],
            "runtime_configuration_hash": "resolved-runtime-configuration",
            "actual_runtime_configuration_hash": "resolved-runtime-configuration",
            "provenance_status": "VERIFIED_RUNTIME_PROVENANCE",
            "configured_runtime_payload": {"resolved_device": "cpu"},
            "actual_runtime_payload": {"resolved_device": "cpu"},
            "provenance_mismatches": [],
        },
        tracks=(
            {
                "track_id": "real-track-1",
                "raw_class_id": 2,
                "raw_class_name": "car",
                "provisional_class": "passenger_vehicle",
                "confidence": 0.91,
                "review_state": "accepted",
                "observation_count": 2,
            },
        ),
    )


def _prepare_service(tmp_path: Path):
    database_path = tmp_path / "real.sqlite3"
    app = create_app(str(database_path))
    service = app.state.foundation_service

    def processing_connection_factory():
        return connect(database_path, check_same_thread=False)

    service.processing_connection_factory = processing_connection_factory
    project = service.create_project("Real", "Bangkok", "intersection", "en")
    service.register_source(
        SourceCreate(
            project_id=project["id"],
            file_name="real.mp4",
            fingerprint_sha256="real-source",
            source_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            timezone_name="UTC",
            analysis_start_pts_ms=0,
            analysis_end_pts_ms=2000,
        )
    )
    media_path = tmp_path / "media.mp4"
    media_path.write_bytes(b"approved synthetic test media")
    service.connection.execute(
        """
        UPDATE video_sources
        SET managed_media_path = ?, original_extension = '.mp4', readiness_state = 'media_ready',
            width = 640, height = 384, duration_ms = 2000, frame_count = 2,
            first_video_pts = 0, stream_time_base = '1/1000', recording_time_configured = 0
        """,
        (str(media_path),),
    )
    scene = service.create_scene(project["id"], "intersection")
    return app, service, project, scene


def test_real_schema_reuses_canonical_direction_and_unconfigured_time() -> None:
    track_set = SyntheticTrackSet(
        schema_version=REAL_TRACK_SCHEMA_VERSION,
        source_fingerprint="source",
        fixture_id="real-video",
        tracks=(
            SyntheticTrack(
                track_id="t1",
                observations=(Observation(0, Point(0.5, 0.4)), Observation(1000, Point(0.5, 0.7))),
                synthetic_class="passenger_vehicle",
                provenance=REAL_TRACK_PROVENANCE,
            ),
        ),
    )
    from apps.backend.app.synthetic_counting import CountingLine, CountingScene

    scene = CountingScene(
        scene_revision="scene-1",
        source_fingerprint="source",
        counting_lines=(
            CountingLine("line", "Line", Point(0.25, 0.55), Point(0.75, 0.55), "BIDIRECTIONAL"),
        ),
    )
    result = execute_synthetic_counting(
        "run-real-contract",
        track_set,
        scene,
        TimeContract(datetime(2026, 1, 1, tzinfo=timezone.utc), "UTC", 0, 2000, 0),
        time_configured=False,
    )
    assert result.events[0].direction.value == "B_TO_A"
    assert result.events[0].provenance == REAL_TRACK_PROVENANCE
    assert result.events[0].real_world_time == "UNCONFIGURED"
    assert result.events[0].absolute_event_time is None


def test_short_real_tracks_do_not_invalidate_eligible_counting_tracks() -> None:
    valid_track = SyntheticTrack(
        track_id="valid",
        observations=(Observation(0, Point(0.5, 0.8)), Observation(1000, Point(0.5, 0.4))),
        synthetic_class="passenger_vehicle",
        provenance=REAL_TRACK_PROVENANCE,
    )
    short_track = SyntheticTrack(
        track_id="short",
        observations=(Observation(0, Point(0.5, 0.8)),),
        synthetic_class="passenger_vehicle",
        provenance=REAL_TRACK_PROVENANCE,
    )

    eligible = eligible_real_tracks_for_counting((valid_track, short_track))

    assert [track.track_id for track in eligible] == ["valid"]


def test_real_job_persists_detector_provenance_and_directional_event(tmp_path, monkeypatch) -> None:
    app, service, project, scene = _prepare_service(tmp_path)
    monkeypatch.setenv("TVA_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(service, "processing_readiness", _ready_runtime)
    monkeypatch.setattr(
        service_module,
        "media_runtime_status",
        lambda: SimpleNamespace(ffmpeg=SimpleNamespace(executable="ffmpeg")),
    )
    monkeypatch.setattr(
        service_module,
        "run_real_video_inference",
        lambda **kwargs: _fake_result(service, kwargs["run_id"]),
    )
    configuration = service.create_processing_configuration_revision(
        project["id"],
        {"profile_code": "BALANCED", "expected_scene_version": scene["version"], "created_by": "operator"},
    )
    job = service.submit_processing_job(
        project["id"],
        mode="REAL_VIDEO",
        processing_configuration_revision_id=configuration["id"],
        expected_scene_version=scene["version"],
        auto_start=False,
    )
    claimed = service.claim_next_processing_job("real-test-worker")
    assert claimed is not None
    completed = service.execute_claimed_job(job["id"], "real-test-worker")
    assert completed["job_state"] == "COMPLETED", completed.get("error_detail")
    assert completed["processing_configuration_revision_id"] == configuration["id"]
    assert completed["configuration"]["profile_code"] == "BALANCED"
    event = service.list_crossing_events(job["id"])[0]
    assert event["provenance"] == REAL_TRACK_PROVENANCE
    assert event["processing_mode"] == "REAL_VIDEO"
    assert event["raw_class_id"] == 2
    assert event["raw_class_name"] == "car"
    assert event["provisional_class"] == "passenger_vehicle"
    assert event["absolute_event_time"] is None
    assert event["event_time_status"] == "unconfigured"
    auto_event = service.list_events(job["id"])[0]
    assert auto_event["classification"] == "passenger_vehicle"
    assert auto_event["qc_state"] == "needs_review"
    run_row = service.connection.execute(
        """
        SELECT model_revision, tracker_revision, weight_identifier, weight_sha256,
               runtime_configuration_hash, actual_runtime_configuration_hash, provenance_status
        FROM analysis_runs WHERE id = ?
        """,
        (job["id"],),
    ).fetchone()
    assert dict(run_row) == {
        "model_revision": "pypi:8.4.103",
        "tracker_revision": "pypi:2.5.0.post0",
        "weight_identifier": "yolo11n.pt",
        "weight_sha256": "pilot-weight-sha256",
        "runtime_configuration_hash": "resolved-runtime-configuration",
        "actual_runtime_configuration_hash": "resolved-runtime-configuration",
        "provenance_status": "VERIFIED_RUNTIME_PROVENANCE",
    }
    saved_revision = service.connection.execute(
        "SELECT request_provenance_hash FROM processing_configuration_revisions WHERE id = ?",
        (configuration["id"],),
    ).fetchone()
    assert saved_revision is not None
    assert completed["configuration"]["configuration_hash"] == configuration["configuration_hash"]
    assert completed["configuration"]["request_provenance_hash"] == saved_revision["request_provenance_hash"]
    inference_row = service.connection.execute(
        """
        SELECT model_revision, tracker_revision, weight_identifier, weight_sha256,
               runtime_configuration_hash, actual_runtime_configuration_hash, provenance_status
        FROM real_inference_runs WHERE run_id = ?
        """,
        (job["id"],),
    ).fetchone()
    assert dict(inference_row) == dict(run_row)
    track_row = service.connection.execute(
        "SELECT runtime_configuration_hash FROM track_summaries WHERE run_id = ?",
        (job["id"],),
    ).fetchone()
    assert track_row["runtime_configuration_hash"] == "resolved-runtime-configuration"
    assert event["runtime_configuration_hash"] == "resolved-runtime-configuration"
    assert auto_event["runtime_configuration_hash"] == "resolved-runtime-configuration"


def test_real_job_cancellation_is_terminal_without_persisting_events(tmp_path, monkeypatch) -> None:
    app, service, project, scene = _prepare_service(tmp_path)
    monkeypatch.setenv("TVA_LOCAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(service, "processing_readiness", _ready_runtime)
    monkeypatch.setattr(
        service_module,
        "media_runtime_status",
        lambda: SimpleNamespace(ffmpeg=SimpleNamespace(executable="ffmpeg")),
    )
    monkeypatch.setattr(service_module, "run_real_video_inference", lambda **_kwargs: (_ for _ in ()).throw(RealInferenceCancelled()))
    configuration = service.create_processing_configuration_revision(
        project["id"],
        {"profile_code": "BALANCED", "expected_scene_version": scene["version"], "created_by": "operator"},
    )
    job = service.submit_processing_job(
        project["id"],
        mode="REAL_VIDEO",
        processing_configuration_revision_id=configuration["id"],
        expected_scene_version=scene["version"],
        auto_start=False,
    )
    service.claim_next_processing_job("real-test-worker")
    cancelled = service.execute_claimed_job(job["id"], "real-test-worker")
    assert cancelled["job_state"] == "CANCELLED", cancelled.get("error_detail")
    assert service.list_crossing_events(job["id"]) == []


def test_real_submission_rejects_invalid_device_selection(tmp_path, monkeypatch) -> None:
    app, service, project, scene = _prepare_service(tmp_path)
    monkeypatch.setattr(service, "processing_readiness", _ready_runtime)
    with pytest.raises(ValueError, match="invalid_device_mode"):
        service.submit_processing_job(
            project["id"],
            mode="REAL_VIDEO",
            configuration={"device_mode": "TPU"},
            expected_scene_version=scene["version"],
            auto_start=False,
        )
