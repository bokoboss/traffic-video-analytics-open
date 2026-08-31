from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from apps.backend.app.db import connect, database_identity_hash, migrate
from apps.backend.app.domain import TimeContract
from apps.backend.app.main import create_app
from apps.backend.app.operational_profiles import OperationalProfileStore, PreviewConflict
from apps.backend.app.real_inference import RealInferenceError, RealInferenceResult, RealProcessingConfig
from apps.backend.app.services import FoundationService
from apps.backend.app.synthetic_counting import (
    REAL_TRACK_PROVENANCE,
    REAL_TRACK_SCHEMA_VERSION,
    CountingScene,
    Observation,
    Point,
    SyntheticTrack,
    SyntheticTrackSet,
    execute_synthetic_counting,
    scene_from_geometry,
)


@contextmanager
def _prepared_preview(tmp_path: Path) -> Iterator[dict[str, object]]:
    database_path = tmp_path / "preview-lifecycle.sqlite3"
    media_path = tmp_path / "safe-generated-preview.mp4"
    media_path.write_bytes(b"safe-generated-preview-media")
    client = TestClient(create_app(database_path=str(database_path)))
    client.__enter__()
    api_service: FoundationService = client.app.state.foundation_service
    worker_connection = None
    try:
        project = client.post(
            "/api/v1/projects",
            json={"name": "preview-lifecycle", "location": "test", "study_type": "intersection", "language": "en"},
        ).json()
        client.post(
            "/api/v1/sources",
            json={
                "project_id": project["id"],
                "file_name": media_path.name,
                "fingerprint_sha256": "safe-preview-fingerprint",
                "source_started_at": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
                "timezone_name": "UTC",
                "analysis_start_pts_ms": 0,
                "analysis_end_pts_ms": 30_000,
            },
        ).raise_for_status()
        scene = client.post("/api/v1/scenes", json={"project_id": project["id"], "template": "intersection"}).json()
        configuration = client.post(
            f"/api/v1/projects/{project['id']}/processing-configurations",
            json={"profile_code": "BALANCED", "expected_scene_version": scene["version"]},
        ).json()
        api_service.connection.execute(
            """
            UPDATE video_sources
            SET source_type = 'local_upload', readiness_state = 'media_ready', managed_media_path = ?,
                width = 2, height = 2, frame_count = 2, duration_ms = 30000
            WHERE project_id = ?
            """,
            (str(media_path), project["id"]),
        )
        api_service.connection.commit()
        preview = api_service.create_preview_run(
            project["id"],
            {
                "mode": "REAL_VIDEO",
                "processing_configuration_revision_id": configuration["id"],
                "start_pts_ms": 0,
                "end_pts_ms": 30_000,
            },
        )
        events: list[tuple[str, dict[str, object]]] = []
        worker_connection = connect(database_path, check_same_thread=True)
        migrate(worker_connection)
        worker = FoundationService(
            worker_connection,
            preview_connection_factory=lambda: connect(database_path, check_same_thread=False),
            preview_event_sink=lambda event, payload: events.append((event, dict(payload))),
        )
        yield {
            "api": api_service,
            "worker": worker,
            "preview": preview,
            "events": events,
            "database_path": database_path,
        }
    finally:
        if worker_connection is not None:
            worker_connection.close()
        api_service.connection.close()
        client.__exit__(None, None, None)


def _fake_result(service: FoundationService, run_id: str) -> RealInferenceResult:
    source = service.connection.execute("SELECT * FROM video_sources ORDER BY created_at DESC LIMIT 1").fetchone()
    scene_row = service.connection.execute("SELECT * FROM scene_versions ORDER BY version DESC LIMIT 1").fetchone()
    assert source is not None and scene_row is not None
    scene: CountingScene = scene_from_geometry(dict(scene_row))
    contract = TimeContract(
        source_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        timezone_name="UTC",
        analysis_start_pts_ms=0,
        analysis_end_pts_ms=2_000,
        interval_origin_pts_ms=0,
    )
    tracks = SyntheticTrackSet(
        schema_version=REAL_TRACK_SCHEMA_VERSION,
        source_fingerprint=str(source["fingerprint_sha256"]),
        fixture_id="real-video",
        scene_revision=scene.scene_revision,
        tracks=(
            SyntheticTrack(
                track_id="real-track-1",
                observations=(
                    Observation(0, Point(0.5, 0.40), source_frame_index=0),
                    Observation(1_000, Point(0.5, 0.70), source_frame_index=1),
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
    counting_result = execute_synthetic_counting(run_id, tracks, scene, contract, time_configured=False)
    return RealInferenceResult(
        counting_result=counting_result,
        config=RealProcessingConfig(resolved_device="cpu"),
        stats={
            "decoded_frames": 2,
            "processed_frames": 2,
            "detection_count": 1,
            "track_count": 1,
            "configuration_hash": "normalized-request-configuration",
            "request_provenance_hash": "operator-request-provenance",
            "runtime_configuration_hash": "resolved-runtime-configuration",
            "actual_runtime_configuration_hash": "resolved-runtime-configuration",
            "provenance_status": "VERIFIED_RUNTIME_PROVENANCE",
            "configured_runtime_payload": {"resolved_device": "cpu"},
            "actual_runtime_payload": {"resolved_device": "cpu"},
            "provenance_mismatches": [],
        },
        tracks=({"track_id": "real-track-1"},),
    )


def test_file_backed_preview_is_visible_to_api_worker_and_callback_connections(tmp_path: Path) -> None:
    with _prepared_preview(tmp_path) as prepared:
        api = prepared["api"]
        worker = prepared["worker"]
        preview = prepared["preview"]
        database_path = prepared["database_path"]
        assert isinstance(api, FoundationService)
        assert isinstance(worker, FoundationService)
        assert isinstance(preview, dict)
        assert isinstance(database_path, Path)

        claimed = worker.claim_next_preview_run("worker-visibility")
        assert claimed is not None
        raw = worker.connection.execute("SELECT claim_token FROM preview_runs WHERE id = ?", (preview["id"],)).fetchone()
        assert raw is not None
        callback_connection = connect(database_path, check_same_thread=True)
        try:
            callback_store = OperationalProfileStore(callback_connection)
            assert callback_store.check_preview_control(preview["id"], "worker-visibility", str(raw["claim_token"])) is False
            assert database_identity_hash(api.connection) == database_identity_hash(worker.connection)
            assert database_identity_hash(worker.connection) == database_identity_hash(callback_connection)
        finally:
            callback_connection.close()


def test_real_preview_callbacks_complete_under_concurrency_and_remain_preview_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TVA_LOCAL_DATA_DIR", str(tmp_path))
    with _prepared_preview(tmp_path) as prepared:
        worker = prepared["worker"]
        preview = prepared["preview"]
        events = prepared["events"]
        assert isinstance(worker, FoundationService)
        assert isinstance(preview, dict)
        assert isinstance(events, list)
        claimed = worker.claim_next_preview_run("worker-complete")
        assert claimed is not None

        from apps.backend.app import services as service_module

        monkeypatch.setattr(service_module, "media_runtime_status", lambda: type("Runtime", (), {"ffmpeg": type("FFmpeg", (), {"executable": "ffmpeg"})()})())

        def fake_inference(**kwargs):
            kwargs["status_callback"]("INITIALIZING_RUNTIME", {"resolved_device": "cpu"})
            kwargs["status_callback"]("PROCESSING", {"completed_units": 1})
            kwargs["status_callback"]("PROCESSING", {"completed_units": 2})
            barrier = threading.Barrier(2)
            failures: list[BaseException] = []

            def cancel_watcher() -> None:
                barrier.wait()
                try:
                    for _ in range(250):
                        assert kwargs["cancel_requested"]() is False
                except BaseException as exc:
                    failures.append(exc)

            watcher = threading.Thread(target=cancel_watcher, name="test-preview-cancel-watcher")
            watcher.start()
            barrier.wait()
            for _ in range(12):
                kwargs["heartbeat_callback"]()
            watcher.join()
            if failures:
                raise failures[0]
            return _fake_result(worker, kwargs["run_id"])

        monkeypatch.setattr(service_module, "run_real_video_inference", fake_inference)
        result = worker.execute_claimed_preview_run(preview["id"], "worker-complete")

        assert result["status"] == "COMPLETED"
        assert result["mode"] == "REAL_VIDEO"
        assert result["run_type"] == "PREVIEW_ONLY"
        assert result["statistics"]["decoded_frames"] == 2
        assert result["statistics"]["processed_frames"] == 2
        assert result["runtime_configuration_hash"] == "resolved-runtime-configuration"
        assert result["provenance_status"] == "VERIFIED_RUNTIME_PROVENANCE"
        assert result["statistics"]["actual_runtime_configuration_hash"] == "resolved-runtime-configuration"
        assert {"PREVIEW_ONLY", "NOT_PRODUCTION_RESULT"} <= set(result["disclosures"])
        assert worker.connection.execute("SELECT COUNT(*) FROM auto_count_events").fetchone()[0] == 0
        assert worker.connection.execute("SELECT COUNT(*) FROM crossing_event_ledger").fetchone()[0] == 0
        assert worker.connection.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0] == 0
        for table in (
            "engineering_result_revisions",
            "review_actions",
            "review_sessions",
            "review_reconciliation_runs",
            "certification_revisions",
            "export_revisions",
            "benchmark_runs",
            "benchmark_qualification_reports",
        ):
            assert worker.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        state_events = [
            row["event_type"]
            for row in worker.connection.execute(
                "SELECT event_type FROM preview_run_state_events WHERE preview_run_id = ?", (preview["id"],)
            )
        ]
        assert state_events[0:2] == ["QUEUED", "CLAIMED"]
        assert state_events.count("HEARTBEAT") == 12
        assert state_events[-1] == "COMPLETED"

        emitted = [event for event, _payload in events]
        assert {
            "preview_execute_started",
            "preview_control_verified",
            "preview_source_loaded",
            "preview_scene_loaded",
            "preview_configuration_loaded",
            "preview_inference_started",
            "preview_heartbeat",
            "preview_inference_completed",
            "preview_finalize_started",
            "preview_finalize_completed",
        } <= set(emitted)
        assert emitted.count("preview_inference_stage") == 2
        serialized_events = json.dumps(events)
        assert "claim_token" not in serialized_events
        identity_hashes = {payload["db_identity_hash"] for _event, payload in events}
        assert identity_hashes == {database_identity_hash(worker.connection)}
        callback_connection_ids = {
            payload["connection_identity"] for event, payload in events if event == "preview_heartbeat"
        }
        execution_connection_ids = {
            payload["connection_identity"] for event, payload in events if event == "preview_execute_started"
        }
        assert callback_connection_ids.isdisjoint(execution_connection_ids)


def test_controlled_inference_failure_is_specific_and_preview_remains_addressable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TVA_LOCAL_DATA_DIR", str(tmp_path))
    with _prepared_preview(tmp_path) as prepared:
        api = prepared["api"]
        worker = prepared["worker"]
        preview = prepared["preview"]
        assert isinstance(api, FoundationService)
        assert isinstance(worker, FoundationService)
        assert isinstance(preview, dict)
        assert worker.claim_next_preview_run("worker-controlled-failure") is not None

        from apps.backend.app import services as service_module

        monkeypatch.setattr(service_module, "media_runtime_status", lambda: type("Runtime", (), {"ffmpeg": type("FFmpeg", (), {"executable": "ffmpeg"})()})())

        def controlled_failure(**kwargs):
            for _ in range(3):
                kwargs["heartbeat_callback"]()
            raise RealInferenceError("controlled_inference_failure", "runtime", "Controlled safe failure.")

        monkeypatch.setattr(service_module, "run_real_video_inference", controlled_failure)
        result = worker.execute_claimed_preview_run(preview["id"], "worker-controlled-failure")

        assert result["status"] == "FAILED"
        assert result["statistics"]["error_code"] == "controlled_inference_failure"
        assert api.get_preview_run(preview["id"])["status"] == "FAILED"
        assert worker.connection.execute("SELECT COUNT(*) FROM preview_runs WHERE id = ?", (preview["id"],)).fetchone()[0] == 1


def test_file_backed_stale_worker_cannot_finalize_after_reclaim(tmp_path: Path) -> None:
    with _prepared_preview(tmp_path) as prepared:
        worker = prepared["worker"]
        preview = prepared["preview"]
        assert isinstance(worker, FoundationService)
        assert isinstance(preview, dict)
        assert worker.claim_next_preview_run("worker-old") is not None
        old = worker.connection.execute("SELECT claim_token FROM preview_runs WHERE id = ?", (preview["id"],)).fetchone()
        assert old is not None
        worker.connection.execute(
            "UPDATE preview_runs SET lease_expires_at = ? WHERE id = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), preview["id"]),
        )
        worker.connection.commit()
        reclaimed = worker.claim_next_preview_run("worker-new")
        assert reclaimed is not None
        with pytest.raises(PreviewConflict, match="ownership"):
            worker.processing_profile_store.set_preview_result(
                preview["id"],
                status="COMPLETED",
                statistics={"run_type": "PREVIEW_ONLY"},
                worker_id="worker-old",
                claim_token=str(old["claim_token"]),
            )
