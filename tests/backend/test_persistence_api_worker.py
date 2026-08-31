from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.backend.app.db import connect, migrate
from apps.backend.app.main import create_app
from apps.backend.app.media import InspectionResult
import apps.backend.app.services as services_module


VALID_MP4_BYTES = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 32


def stub_upload_inspection(monkeypatch: pytest.MonkeyPatch, warnings: list[str] | None = None) -> None:
    monkeypatch.setattr(
        services_module,
        "inspect_media",
        lambda path: InspectionResult(
            metadata={
                "container_format": "mov,mp4,m4a,3gp,3g2,mj2",
                "video_codec": "h264",
                "duration_ms": 120000,
                "width": 1920,
                "height": 1080,
                "sample_aspect_ratio": "1:1",
                "display_aspect_ratio": "16:9",
                "nominal_frame_rate": "30/1",
                "average_frame_rate": "30/1",
                "stream_time_base": "1/90000",
                "start_pts": 0,
                "first_video_pts": 0,
                "frame_count": 3600,
                "variable_frame_rate": 0,
                "rotation_degrees": 0,
                "metadata_creation_time": None,
                "audio_present": 1,
            },
            warnings=warnings or [],
            tool_version="ffprobe test",
        ),
    )


def test_migration_upgrade_from_empty_database_and_append_only_records() -> None:
    connection = connect()
    migrate(connection)
    tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "auto_count_events" in tables
    connection.execute(
        """
        INSERT INTO projects(id, name, location, study_type, language, state, stale, created_at)
        VALUES ('prj', 'Project', 'Bangkok', 'intersection', 'th', 'draft', 0, 'now')
        """
    )
    connection.execute(
        """
        INSERT INTO video_sources(
          id, project_id, file_name, fingerprint_sha256, source_started_at, timezone_name,
          analysis_start_pts_ms, analysis_end_pts_ms, interval_origin_pts_ms, created_at
        ) VALUES ('src', 'prj', 'mock.mp4', 'sha', '2026-01-01T00:00:00+00:00', 'Asia/Bangkok', 0, 3600000, 0, 'now')
        """
    )
    connection.execute(
        """
        INSERT INTO scene_versions(id, project_id, version, template, geometry_json, config_hash, created_at)
        VALUES ('scn', 'prj', 1, 'intersection', '{}', 'hash', 'now')
        """
    )
    connection.execute(
        """
        INSERT INTO analysis_runs(id, project_id, source_id, scene_version_id, state, progress_percent, result_version, created_at)
        VALUES ('run', 'prj', 'src', 'scn', 'processing_complete', 100, 'result', 'now')
        """
    )
    connection.execute(
        """
        INSERT INTO auto_count_events(
          id, run_id, technical_key, pts_ms, track_id, rule_id, object_domain,
          classification, movement, confidence, qc_state, created_at
        ) VALUES ('evt', 'run', 'key', 1, 'trk', 'rule', 'vehicle', 'unknown', 'north', 0.1, 'ok', 'now')
        """
    )
    with pytest.raises(Exception, match="immutable"):
        connection.execute("UPDATE auto_count_events SET classification = 'changed' WHERE id = 'evt'")
    connection.execute(
        """
        INSERT INTO review_actions(id, event_id, action_type, reviewer, created_at)
        VALUES ('rev', 'evt', 'approve', 'tester', 'now')
        """
    )
    with pytest.raises(Exception, match="append-only"):
        connection.execute("UPDATE review_actions SET reviewer = 'changed' WHERE id = 'rev'")
    with pytest.raises(Exception, match="append-only"):
        connection.execute("DELETE FROM review_actions WHERE id = 'rev'")


def test_api_acceptance_flow_and_segment_retry_idempotency() -> None:
    client = TestClient(create_app())
    project = client.post(
        "/api/v1/projects",
        json={"name": "Mock Rama IX", "location": "Bangkok", "study_type": "intersection", "language": "th"},
    ).json()
    client.post(
        "/api/v1/sources",
        json={
            "project_id": project["id"],
            "file_name": "approved-mock.mp4",
            "fingerprint_sha256": "abc123",
            "source_started_at": datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc).isoformat(),
            "timezone_name": "Asia/Bangkok",
            "analysis_start_pts_ms": 0,
            "analysis_end_pts_ms": 3600000,
        },
    ).raise_for_status()
    client.post("/api/v1/scenes", json={"project_id": project["id"], "template": "intersection"}).raise_for_status()
    run = client.post(f"/api/v1/projects/{project['id']}/mock-analysis").json()
    events_before = client.get(f"/api/v1/runs/{run['id']}/events").json()
    client.post(f"/api/v1/runs/{run['id']}/segments/1/retry").raise_for_status()
    events_after = client.get(f"/api/v1/runs/{run['id']}/events").json()
    assert len(events_before) == len(events_after)
    for event in events_after:
        if event["qc_state"] == "needs_review":
            client.post(
                "/api/v1/review-actions",
                json={"event_id": event["id"], "action_type": "approve", "reviewer": "tester"},
            ).raise_for_status()
    client.post(f"/api/v1/projects/{project['id']}/review-complete").raise_for_status()
    client.post(f"/api/v1/projects/{project['id']}/runs/{run['id']}/certifications").raise_for_status()
    export = client.post(f"/api/v1/projects/{project['id']}/runs/{run['id']}/exports/csv").json()
    assert export["status"] == "manifest_created"
    second_export = client.post(f"/api/v1/projects/{project['id']}/runs/{run['id']}/exports/xlsx").json()
    assert second_export["status"] == "manifest_created"


def test_synthetic_counting_api_persistence_review_reversal_certification_export_and_stale() -> None:
    client = TestClient(create_app())
    project = client.post(
        "/api/v1/projects",
        json={"name": "Synthetic", "location": "Bangkok", "study_type": "intersection", "language": "th"},
    ).json()
    client.post(
        "/api/v1/sources",
        json={
            "project_id": project["id"],
            "file_name": "synthetic.mp4",
            "fingerprint_sha256": "src-synthetic",
            "source_started_at": datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc).isoformat(),
            "timezone_name": "Asia/Bangkok",
            "analysis_start_pts_ms": 0,
            "analysis_end_pts_ms": 3600000,
        },
    ).raise_for_status()
    scene = client.post("/api/v1/scenes", json={"project_id": project["id"], "template": "intersection"}).json()

    run = client.post(
        f"/api/v1/projects/{project['id']}/synthetic-runs",
        json={"fixture_id": "api-acceptance", "expected_scene_version": scene["version"]},
    ).json()
    assert run["synthetic"] is True
    assert run["event_count"] == 1

    crossing_events = client.get(f"/api/v1/runs/{run['id']}/crossing-events").json()
    assert crossing_events[0]["provenance"] == "synthetic"
    assert crossing_events[0]["calculation_method"] == "finite_segment_intersection_linear_time_interpolation"
    assert crossing_events[0]["crossing_direction"] == "A_TO_B"
    assert crossing_events[0]["side_a_label"] == "A side"
    assert crossing_events[0]["side_b_label"] == "B side"
    assert crossing_events[0]["readable_direction_label"] == "A side -> B side"

    aggregate = client.get(f"/api/v1/runs/{run['id']}/aggregates").json()
    assert aggregate["hourly_total"] == 1
    assert aggregate["fifteen_minute_counts"] == [1, 0, 0, 0]

    event_id = crossing_events[0]["id"]
    rejection = client.post(
        "/api/v1/review-actions",
        json={"event_id": event_id, "action_type": "exclude", "reviewer": "tester", "reason": "validation check"},
    ).json()
    reviewed = client.get(f"/api/v1/runs/{run['id']}/reviewed-totals").json()
    assert reviewed["raw_total"] == 1
    assert reviewed["accepted_total"] == 0
    assert reviewed["excluded_total"] == 1

    client.post(
        "/api/v1/review-actions",
        json={
            "event_id": event_id,
            "action_type": "reverse",
            "reviewer": "tester",
            "reverses_action_id": rejection["id"],
        },
    ).raise_for_status()
    restored = client.get(f"/api/v1/runs/{run['id']}/reviewed-totals").json()
    assert restored["accepted_total"] == 1
    assert restored["excluded_total"] == 0

    client.post(f"/api/v1/projects/{project['id']}/review-complete").raise_for_status()
    certification = client.post(f"/api/v1/projects/{project['id']}/runs/{run['id']}/certifications").json()
    assert certification["result_version"] == run["result_version"]
    export = client.post(f"/api/v1/projects/{project['id']}/runs/{run['id']}/exports/csv").json()
    assert export["status"] == "manifest_created"
    assert "synthetic" in export["provenance_json"]
    exported_snapshot = client.get(f"/api/v1/projects/{project['id']}").json()
    assert exported_snapshot["export"]["status"] == "manifest_created"

    service = client.app.state.foundation_service
    service.invalidate_for_calculation_change(project["id"])
    stale_export = client.post(f"/api/v1/projects/{project['id']}/runs/{run['id']}/exports/csv")
    assert stale_export.status_code == 409
    stale_run = service.connection.execute(
        "SELECT stale FROM synthetic_counting_runs WHERE run_id = ?",
        (run["id"],),
    ).fetchone()
    assert stale_run["stale"] == 1


def test_processing_job_submission_idempotency_cancel_claim_and_lost_worker() -> None:
    client = TestClient(create_app())
    project = client.post(
        "/api/v1/projects",
        json={"name": "Jobs", "location": "Bangkok", "study_type": "intersection", "language": "th"},
    ).json()
    client.post(
        "/api/v1/sources",
        json={
            "project_id": project["id"],
            "file_name": "jobs.mp4",
            "fingerprint_sha256": "src-jobs",
            "source_started_at": datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc).isoformat(),
            "timezone_name": "Asia/Bangkok",
            "analysis_start_pts_ms": 0,
            "analysis_end_pts_ms": 3600000,
        },
    ).raise_for_status()
    scene = client.post("/api/v1/scenes", json={"project_id": project["id"], "template": "intersection"}).json()

    queued = client.post(
        f"/api/v1/projects/{project['id']}/processing-jobs",
        json={
            "fixture_id": "api-acceptance",
            "expected_scene_version": scene["version"],
            "idempotency_key": "same-submit",
            "auto_start": False,
        },
    ).json()
    assert queued["job_state"] == "QUEUED"
    duplicate = client.post(
        f"/api/v1/projects/{project['id']}/processing-jobs",
        json={
            "fixture_id": "api-acceptance",
            "expected_scene_version": scene["version"],
            "idempotency_key": "same-submit",
            "auto_start": False,
        },
    ).json()
    assert duplicate["id"] == queued["id"]
    assert duplicate["duplicate_policy"] == "idempotency_key_reused"

    active = client.get(f"/api/v1/projects/{project['id']}/processing-jobs/active").json()
    assert active["id"] == queued["id"]
    cancelled = client.post(f"/api/v1/processing-jobs/{queued['id']}/cancel", json={"reason": "operator request"}).json()
    assert cancelled["job_state"] == "CANCELLED"
    assert cancelled["result_ready"] is False
    assert client.post(f"/api/v1/projects/{project['id']}/runs/{cancelled['id']}/certifications").status_code == 409

    service = client.app.state.foundation_service
    stale_job = service.submit_processing_job(
        project["id"],
        fixture_id="api-acceptance",
        expected_scene_version=scene["version"],
        auto_start=False,
        run_again=True,
    )
    claimed = service.claim_next_processing_job("worker-a", lease_seconds=-1)
    assert claimed["id"] == stale_job["id"]
    assert service.claim_next_processing_job("worker-b") is None
    lost = service.fail_stale_processing_jobs()
    assert lost[0]["id"] == stale_job["id"]
    assert lost[0]["job_state"] == "FAILED"
    assert lost[0]["error_code"] == "WORKER_LOST"


def test_project_reload_update_and_stale_write_conflict(tmp_path: Path) -> None:
    db_path = tmp_path / "projects.sqlite"
    client = TestClient(create_app(str(db_path)))
    project = client.post(
        "/api/v1/projects",
        json={"name": "Persistent", "location": "Bangkok", "study_type": "intersection", "language": "en"},
    ).json()
    updated = client.put(
        f"/api/v1/projects/{project['id']}",
        json={
            "name": "Persistent updated",
            "location": "Bangkok",
            "study_type": "intersection",
            "language": "en",
            "expected_version": project["version"],
        },
    ).json()
    assert updated["version"] == project["version"] + 1
    conflict = client.put(
        f"/api/v1/projects/{project['id']}",
        json={
            "name": "Stale",
            "location": "Bangkok",
            "study_type": "intersection",
            "language": "en",
            "expected_version": project["version"],
        },
    )
    assert conflict.status_code == 409

    restarted = TestClient(create_app(str(db_path)))
    snapshot = restarted.get(f"/api/v1/projects/{project['id']}").json()
    assert snapshot["project"]["name"] == "Persistent updated"


@pytest.mark.skip(reason="Superseded by Milestone 5.2C strict upload validation tests.")
def test_upload_source_records_full_file_fingerprint_and_ffprobe_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TVA_LOCAL_DATA_DIR", str(tmp_path / "local-data"))
    client = TestClient(create_app())
    project = client.post(
        "/api/v1/projects",
        json={"name": "Upload", "location": "Bangkok", "study_type": "intersection", "language": "en"},
    ).json()
    response = client.post(
        f"/api/v1/projects/{project['id']}/sources/upload",
        files={"file": ("ทดสอบ source.mp4", b"not a real video", "video/mp4")},
    )
    response.raise_for_status()
    source = response.json()
    assert source["source_type"] == "local_upload"
    assert source["byte_size"] == len(b"not a real video")
    assert source["fingerprint_sha256"] != "not a real video"
    assert "ffprobe_unavailable" in source["inspection_warnings_json"] or "ffprobe_failed" in source["inspection_warnings_json"]


def test_upload_source_records_full_file_fingerprint_and_redacts_local_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TVA_LOCAL_DATA_DIR", str(tmp_path / "local-data"))
    stub_upload_inspection(monkeypatch)
    client = TestClient(create_app())
    project = client.post(
        "/api/v1/projects",
        json={"name": "Upload", "location": "Bangkok", "study_type": "intersection", "language": "en"},
    ).json()
    response = client.post(
        f"/api/v1/projects/{project['id']}/sources/upload",
        files={"file": ("thai source & safe.mp4", VALID_MP4_BYTES, "video/mp4")},
    )
    response.raise_for_status()
    source = response.json()
    assert source["source_type"] == "local_upload"
    assert source["byte_size"] == len(VALID_MP4_BYTES)
    assert source["fingerprint_sha256"] != VALID_MP4_BYTES.decode("latin1")
    assert "managed_media_path" not in source
    assert source["media_id"].startswith("med_")
    assert source["stored_internal_filename"] == "source.mp4"
    snapshot = client.get(f"/api/v1/projects/{project['id']}").json()
    assert "managed_media_path" not in snapshot["source"]
    assert snapshot["readiness"]["state"] in {"preview_unavailable", "stale_scene"}


def test_upload_rejects_invalid_extension_signature_duplicate_and_no_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TVA_LOCAL_DATA_DIR", str(tmp_path / "local-data"))
    stub_upload_inspection(monkeypatch)
    client = TestClient(create_app())
    project = client.post(
        "/api/v1/projects",
        json={"name": "Upload validation", "location": "Bangkok", "study_type": "intersection", "language": "en"},
    ).json()
    bad_extension = client.post(
        f"/api/v1/projects/{project['id']}/sources/upload",
        files={"file": ("source.txt", VALID_MP4_BYTES, "text/plain")},
    )
    assert bad_extension.status_code == 422
    mismatch = client.post(
        f"/api/v1/projects/{project['id']}/sources/upload",
        files={"file": ("source.mp4", b"not-video", "video/mp4")},
    )
    assert mismatch.status_code == 422
    client.post(
        f"/api/v1/projects/{project['id']}/sources/upload",
        files={"file": ("source.mp4", VALID_MP4_BYTES, "video/mp4")},
    ).raise_for_status()
    duplicate = client.post(
        f"/api/v1/projects/{project['id']}/sources/upload",
        files={"file": ("renamed.mp4", VALID_MP4_BYTES, "video/mp4")},
    )
    assert duplicate.status_code == 422

    other = client.post(
        "/api/v1/projects",
        json={"name": "No video", "location": "Bangkok", "study_type": "intersection", "language": "en"},
    ).json()
    stub_upload_inspection(monkeypatch, warnings=["no_video_stream"])
    no_video = client.post(
        f"/api/v1/projects/{other['id']}/sources/upload",
        files={"file": ("audio.mp4", VALID_MP4_BYTES + b"audio", "video/mp4")},
    )
    assert no_video.status_code == 422


def test_time_configuration_rejects_invalid_timezone_and_reversed_window() -> None:
    client = TestClient(create_app())
    project = client.post(
        "/api/v1/projects",
        json={"name": "Time", "location": "Bangkok", "study_type": "intersection", "language": "en"},
    ).json()
    client.post(
        "/api/v1/sources",
        json={
            "project_id": project["id"],
            "file_name": "mock.mp4",
            "fingerprint_sha256": "abc",
            "source_started_at": datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc).isoformat(),
            "timezone_name": "Asia/Bangkok",
            "analysis_start_pts_ms": 0,
            "analysis_end_pts_ms": 3600000,
        },
    ).raise_for_status()
    bad_timezone = client.put(
        f"/api/v1/projects/{project['id']}/time-configuration",
        json={
            "source_started_at": datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc).isoformat(),
            "timezone_name": "Invalid/Zone",
            "analysis_start_pts_ms": 0,
            "analysis_end_pts_ms": 3600000,
        },
    )
    assert bad_timezone.status_code == 422
    reversed_window = client.put(
        f"/api/v1/projects/{project['id']}/time-configuration",
        json={
            "source_started_at": datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc).isoformat(),
            "timezone_name": "Asia/Bangkok",
            "analysis_start_pts_ms": 10,
            "analysis_end_pts_ms": 1,
        },
    )
    assert reversed_window.status_code == 422


def test_stale_invalidation_propagates_to_project_and_aggregates() -> None:
    client = TestClient(create_app())
    project = client.post(
        "/api/v1/projects",
        json={"name": "Mock Rama IX", "location": "Bangkok", "study_type": "intersection", "language": "th"},
    ).json()
    client.post(
        "/api/v1/sources",
        json={
            "project_id": project["id"],
            "file_name": "approved-mock.mp4",
            "fingerprint_sha256": "abc123",
            "source_started_at": datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc).isoformat(),
            "timezone_name": "Asia/Bangkok",
            "analysis_start_pts_ms": 0,
            "analysis_end_pts_ms": 3600000,
        },
    ).raise_for_status()
    client.post("/api/v1/scenes", json={"project_id": project["id"], "template": "intersection"}).raise_for_status()
    app = client.app
    service = app.state.foundation_service
    run = client.post(f"/api/v1/projects/{project['id']}/mock-analysis").json()
    service.invalidate_for_calculation_change(project["id"])
    row = service.connection.execute(
        "SELECT stale FROM aggregate_snapshots WHERE run_id = ?",
        (run["id"],),
    ).fetchone()
    assert row["stale"] == 1


def test_openapi_contains_versioned_contract() -> None:
    client = TestClient(create_app())
    schema = client.get("/openapi.json").json()
    assert "/api/v1/projects" in schema["paths"]
    assert "/api/v1/projects/{project_id}/mock-analysis" in schema["paths"]


def test_mock_worker_runs_outside_fastapi_process_boundary() -> None:
    result = subprocess.run(
        [sys.executable, "apps/worker/mock_worker.py", "--run-id", "run_test"],
        check=True,
        text=True,
        capture_output=True,
    )
    assert '"boundary": "separate_process"' in result.stdout
    assert '"progress_percent": 100' in result.stdout
