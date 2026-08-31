from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.backend.app.db import MIGRATIONS_DIR, connect, migrate
from apps.backend.app.main import create_app
from apps.backend.app.operational_profiles import PreviewConflict
from apps.backend.app.processing_profiles import build_configuration
from apps.backend.app.services import FoundationService
from apps.backend.app.synthetic_counting import (
    CountingLine,
    CountingScene,
    Observation,
    Point,
    SyntheticTrack,
    SyntheticTrackSet,
    execute_synthetic_counting,
)


def _client() -> tuple[TestClient, dict, dict]:
    client = TestClient(create_app())
    project = client.post(
        "/api/v1/projects",
        json={"name": "6D", "location": "Bangkok", "study_type": "intersection", "language": "en"},
    ).json()
    client.post(
        "/api/v1/sources",
        json={
            "project_id": project["id"],
            "file_name": "approved-mock.mp4",
            "fingerprint_sha256": "6d-source",
            "source_started_at": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
            "timezone_name": "UTC",
            "analysis_start_pts_ms": 0,
            "analysis_end_pts_ms": 180_000,
        },
    ).raise_for_status()
    scene = client.post("/api/v1/scenes", json={"project_id": project["id"], "template": "intersection"}).json()
    return client, project, scene


def _mark_source_as_ready_upload(client: TestClient, project_id: str, managed_path: str = "test-managed-media.mp4") -> None:
    service = client.app.state.foundation_service
    service.connection.execute(
        "UPDATE video_sources SET source_type = 'local_upload', readiness_state = 'media_ready', managed_media_path = ? WHERE project_id = ?",
        (managed_path, project_id),
    )
    service.connection.commit()


def test_profile_catalog_contains_required_operational_codes() -> None:
    client, _project, _scene = _client()
    profiles = client.get("/api/v1/processing-profiles")
    assert profiles.status_code == 200
    assert [item["profile_code"] for item in profiles.json()] == [
        "BALANCED",
        "CUSTOM",
        "DENSE_TRAFFIC",
        "FAST_PROCESSING",
        "HIGH_ACCURACY",
        "MOTORCYCLE_HEAVY",
        "PEDESTRIAN_COUNTING",
        "SMALL_DISTANT_OBJECTS",
    ]


def test_configuration_validation_is_typed_and_explicit_about_cuda_fallback() -> None:
    result = build_configuration(
        "BALANCED",
        {"scene_type": "DENSE_TRAFFIC"},
        {"image_size": 1280, "device_mode": "AUTO"},
        media_duration_ms=180_000,
        analysis_start_pts_ms=0,
        analysis_end_pts_ms=30_000,
        cuda_available=False,
    )
    assert result["valid"] is True
    assert result["resolved_parameters"]["image_size"] == 1280
    assert any(item["code"] == "auto_device_fallback_cpu" for item in result["warnings"])
    assert result["resolved_device"] == "cpu"
    assert result["estimated_resource_impact"]["real_time_status"] == "NOT_ESTABLISHED"

    invalid = build_configuration("BALANCED", {}, {"image_size": 641})
    assert invalid["valid"] is False
    assert any(item["code"] == "unsupported_parameter_value" for item in invalid["errors"])

    profile_conflict = build_configuration("HIGH_ACCURACY", {}, {})
    assert profile_conflict["resolved_parameters"]["image_size"] == 1280
    assert not any(item["code"] == "guided_resolution_normalized" for item in profile_conflict["warnings"])

    malformed = build_configuration(
        "BALANCED",
        {"analysis_quality": "UNSUPPORTED"},
        {"confidence_threshold": "not-a-number"},
    )
    assert malformed["valid"] is False
    assert any(item["code"] == "unsupported_guided_value" for item in malformed["errors"])
    assert any(item["code"] == "invalid_parameter_type" for item in malformed["errors"])


def test_configuration_revision_preview_isolation_comparison_and_candidate() -> None:
    client, project, scene = _client()
    base = {
        "profile_code": "BALANCED",
        "expected_scene_version": scene["version"],
        "created_by": "test",
    }
    first = client.post(f"/api/v1/projects/{project['id']}/processing-configurations", json=base)
    second = client.post(
        f"/api/v1/projects/{project['id']}/processing-configurations",
        json={
            **base,
            "profile_code": "FAST_PROCESSING",
            "expert_overrides": {"frame_stride": 2},
        },
    )
    assert first.status_code == second.status_code == 200
    first_config = first.json()
    second_config = second.json()
    assert first_config["id"] != second_config["id"]
    assert first_config["content_hash"] != second_config["content_hash"]

    preview = client.post(
        f"/api/v1/projects/{project['id']}/previews",
        json={
            "mode": "SYNTHETIC",
            "processing_configuration_revision_id": first_config["id"],
            "start_pts_ms": 0,
            "end_pts_ms": 120_000,
            "fixture_id": "api-acceptance",
        },
    )
    assert preview.status_code == 200
    preview_payload = preview.json()
    assert preview_payload["status"] == "COMPLETED"
    assert preview_payload["run_type"] == "PREVIEW_ONLY"
    assert "PREVIEW_ONLY" in preview_payload["disclosures"]
    service = client.app.state.foundation_service
    assert service.connection.execute("SELECT COUNT(*) FROM auto_count_events").fetchone()[0] == 0
    assert service.connection.execute("SELECT COUNT(*) FROM crossing_event_ledger").fetchone()[0] == 0

    comparison = client.post(
        f"/api/v1/projects/{project['id']}/configuration-comparisons",
        json={
            "processing_configuration_revision_ids": [first_config["id"], second_config["id"]],
            "preview_run_ids": [preview_payload["id"]],
            "start_pts_ms": 0,
            "end_pts_ms": 120_000,
            "expected_scene_version": scene["version"],
        },
    )
    assert comparison.status_code == 200
    assert comparison.json()["comparison"]["rows"][0]["accuracy_status"] == "NOT_AVAILABLE_WITHOUT_GROUND_TRUTH"

    diff = client.post(
        f"/api/v1/projects/{project['id']}/processing-configurations/diff",
        json={
            "first_processing_configuration_revision_id": first_config["id"],
            "second_processing_configuration_revision_id": second_config["id"],
        },
    )
    assert diff.status_code == 200
    assert diff.json()["changed_parameters"]["frame_stride"] == {"from": 1, "to": 2}

    candidate = client.post(
        f"/api/v1/projects/{project['id']}/operational-candidates",
        json={
            "processing_configuration_revision_id": first_config["id"],
            "selection_status": "OPERATOR_SELECTED",
            "rationale": "Operator selected for bounded preview follow-up.",
        },
    )
    assert candidate.status_code == 200
    assert candidate.json()["candidate_status"] == "OPERATIONAL_CANDIDATE"

    validation = client.post(
        f"/api/v1/projects/{project['id']}/capability-validations",
        json={
            "processing_configuration_revision_id": first_config["id"],
            "preview_run_id": preview_payload["id"],
            "validation_type": "REAL_MEDIA",
        },
    )
    assert validation.status_code == 200
    assert validation.json()["status"] == "PENDING"
    assert validation.json()["evidence"]["accuracy_status"] == "NOT_AVAILABLE_WITHOUT_GROUND_TRUTH"
    listed_validations = client.get(f"/api/v1/projects/{project['id']}/capability-validations")
    assert listed_validations.status_code == 200
    assert listed_validations.json()[0]["id"] == validation.json()["id"]

    _mark_source_as_ready_upload(client, project["id"])
    queued_preview = client.post(
        f"/api/v1/projects/{project['id']}/previews",
        json={
            "mode": "REAL_VIDEO",
            "processing_configuration_revision_id": first_config["id"],
            "start_pts_ms": 0,
            "end_pts_ms": 30_000,
        },
    )
    assert queued_preview.status_code == 200
    assert queued_preview.json()["status"] == "QUEUED"
    assert queued_preview.json()["mode"] == "REAL_VIDEO"
    assert queued_preview.json()["processing_configuration_revision_id"] == first_config["id"]
    assert queued_preview.json()["start_pts_ms"] == 0
    assert queued_preview.json()["end_pts_ms"] == 30_000
    assert "SYNTHETIC_ENGINEERING_ONLY" not in queued_preview.json()["disclosures"]
    cancelled_preview = client.post(
        f"/api/v1/previews/{queued_preview.json()['id']}/cancel",
        json={"reason": "operator stopped capability check"},
    )
    assert cancelled_preview.status_code == 200
    assert cancelled_preview.json()["status"] == "CANCELLED"
    assert service.connection.execute("SELECT COUNT(*) FROM auto_count_events").fetchone()[0] == 0
    assert service.connection.execute("SELECT COUNT(*) FROM crossing_event_ledger").fetchone()[0] == 0


def test_preview_api_requires_explicit_synthetic_fixture_and_rejects_synthetic_for_uploaded_source() -> None:
    client, project, scene = _client()
    configuration = client.post(
        f"/api/v1/projects/{project['id']}/processing-configurations",
        json={"profile_code": "BALANCED", "expected_scene_version": scene["version"]},
    ).json()

    missing_fixture = client.post(
        f"/api/v1/projects/{project['id']}/previews",
        json={
            "mode": "SYNTHETIC",
            "processing_configuration_revision_id": configuration["id"],
            "start_pts_ms": 0,
            "end_pts_ms": 30_000,
        },
    )
    assert missing_fixture.status_code == 422
    assert "synthetic preview requires fixture_id" in missing_fixture.text

    service = client.app.state.foundation_service
    service.connection.execute(
        "UPDATE video_sources SET source_type = 'local_upload', readiness_state = 'media_ready' WHERE project_id = ?",
        (project["id"],),
    )
    service.connection.commit()
    uploaded_source_synthetic = client.post(
        f"/api/v1/projects/{project['id']}/previews",
        json={
            "mode": "SYNTHETIC",
            "processing_configuration_revision_id": configuration["id"],
            "start_pts_ms": 0,
            "end_pts_ms": 30_000,
            "fixture_id": "api-acceptance",
        },
    )
    assert uploaded_source_synthetic.status_code == 422
    assert uploaded_source_synthetic.json()["detail"] == "synthetic_preview_requires_approved_fixture_source"


def test_real_preview_rejects_fixture_and_intervals_outside_the_source_analysis_window() -> None:
    client, project, scene = _client()
    configuration = client.post(
        f"/api/v1/projects/{project['id']}/processing-configurations",
        json={"profile_code": "BALANCED", "expected_scene_version": scene["version"]},
    ).json()
    real_payload = {
        "mode": "REAL_VIDEO",
        "processing_configuration_revision_id": configuration["id"],
        "start_pts_ms": 0,
        "end_pts_ms": 30_000,
    }

    fixture_response = client.post(
        f"/api/v1/projects/{project['id']}/previews",
        json={**real_payload, "fixture_id": "api-acceptance"},
    )
    assert fixture_response.status_code == 422
    assert "real-video preview does not accept fixture_id" in fixture_response.text

    outside_window = client.post(
        f"/api/v1/projects/{project['id']}/previews",
        json={**real_payload, "start_pts_ms": 170_000, "end_pts_ms": 200_000},
    )
    assert outside_window.status_code == 422
    assert outside_window.json()["detail"] == "preview interval must stay within the source analysis window"

    non_uploaded_source = client.post(
        f"/api/v1/projects/{project['id']}/previews",
        json=real_payload,
    )
    assert non_uploaded_source.status_code == 422
    assert non_uploaded_source.json()["detail"] == "real_video_preview_requires_ready_uploaded_source"


def test_real_processing_job_uses_saved_revision_and_rejects_fixture_identity() -> None:
    client, project, scene = _client()
    configuration = client.post(
        f"/api/v1/projects/{project['id']}/processing-configurations",
        json={"profile_code": "BALANCED", "expected_scene_version": scene["version"]},
    ).json()
    service = client.app.state.foundation_service
    service.processing_readiness = lambda: {"real_inference": {"ready": True, "state": "real_ready"}}
    _mark_source_as_ready_upload(client, project["id"])

    rejected = client.post(
        f"/api/v1/projects/{project['id']}/processing-jobs",
        json={
            "mode": "REAL_VIDEO",
            "fixture_id": "api-acceptance",
            "processing_configuration_revision_id": configuration["id"],
            "expected_scene_version": scene["version"],
        },
    )
    assert rejected.status_code == 422
    assert "real-video processing does not accept fixture_id" in rejected.text

    before_count = service.connection.execute("SELECT COUNT(*) FROM processing_configuration_revisions").fetchone()[0]
    queued = client.post(
        f"/api/v1/projects/{project['id']}/processing-jobs",
        json={
            "mode": "REAL_VIDEO",
            "processing_configuration_revision_id": configuration["id"],
            "expected_scene_version": scene["version"],
        },
    )
    assert queued.status_code == 200
    payload = queued.json()
    assert payload["job_state"] == "QUEUED"
    assert payload["processing_mode"] == "REAL_VIDEO"
    assert payload["processing_configuration_revision_id"] == configuration["id"]
    assert payload["fixture_id"] is None
    assert payload["configuration"]["profile_code"] == "BALANCED"
    assert payload["configuration"]["configuration_hash"] == configuration["configuration_hash"]
    assert "fixture_id" not in payload["configuration"]
    assert service.connection.execute("SELECT COUNT(*) FROM processing_configuration_revisions").fetchone()[0] == before_count
    raw_config = service.connection.execute(
        "SELECT processing_config_json FROM analysis_runs WHERE id = ?", (payload["id"],)
    ).fetchone()
    assert raw_config is not None
    assert "fixture_id" not in raw_config["processing_config_json"]
    event_detail = service.connection.execute(
        "SELECT detail_json FROM processing_job_events WHERE run_id = ? AND event_type = 'created'", (payload["id"],)
    ).fetchone()
    assert event_detail is not None
    assert "fixture_id" not in event_detail["detail_json"]


def test_real_processing_job_rejects_configuration_from_another_project() -> None:
    client, project, scene = _client()
    other_project = client.post(
        "/api/v1/projects",
        json={"name": "Other project", "location": "Bangkok", "study_type": "intersection", "language": "en"},
    ).json()
    client.post(
        "/api/v1/sources",
        json={
            "project_id": other_project["id"],
            "file_name": "other.mp4",
            "fingerprint_sha256": "other-source",
            "source_started_at": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
            "timezone_name": "UTC",
            "analysis_start_pts_ms": 0,
            "analysis_end_pts_ms": 180_000,
        },
    ).raise_for_status()
    other_scene = client.post(
        "/api/v1/scenes", json={"project_id": other_project["id"], "template": "intersection"}
    ).json()
    other_configuration = client.post(
        f"/api/v1/projects/{other_project['id']}/processing-configurations",
        json={"profile_code": "BALANCED", "expected_scene_version": other_scene["version"]},
    ).json()
    service = client.app.state.foundation_service
    service.processing_readiness = lambda: {"real_inference": {"ready": True, "state": "real_ready"}}
    _mark_source_as_ready_upload(client, project["id"])

    rejected = client.post(
        f"/api/v1/projects/{project['id']}/processing-jobs",
        json={
            "mode": "REAL_VIDEO",
            "processing_configuration_revision_id": other_configuration["id"],
            "expected_scene_version": scene["version"],
        },
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "processing configuration belongs to another project"


def test_synthetic_processing_job_keeps_default_fixture_compatibility() -> None:
    client, project, scene = _client()
    queued = client.post(
        f"/api/v1/projects/{project['id']}/processing-jobs",
        json={"mode": "SYNTHETIC", "expected_scene_version": scene["version"]},
    )
    assert queued.status_code == 200
    assert queued.json()["fixture_id"] == "default-crossing-fixture"
    assert queued.json()["processing_mode"] == "SYNTHETIC"


def test_migration_and_immutable_configuration_revision() -> None:
    connection = connect()
    migrate(connection)
    FoundationService(connection)
    tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "processing_profile_revisions",
        "processing_configuration_revisions",
        "preview_runs",
        "configuration_comparisons",
        "operational_candidate_selections",
        "capability_validation_records",
    } <= tables

    with pytest.raises(Exception, match="immutable"):
        connection.execute(
            "UPDATE processing_profile_revisions SET status = 'CHANGED' WHERE profile_code = 'BALANCED'"
        )


def test_acceptance_migration_preserves_012_rows_and_marks_ambiguous_provenance() -> None:
    connection = connect()
    connection.execute(
        "CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if migration.stem == "013_milestone_6d_acceptance_remediation":
            break
        connection.executescript(migration.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, datetime('now'))",
            (migration.stem,),
        )
    connection.execute(
        "INSERT INTO projects(id, name, location, study_type, language, state, stale, created_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
        ("legacy-project", "Legacy", "Bangkok", "intersection", "th", "draft", "now"),
    )
    connection.execute(
        """
        INSERT INTO video_sources(
          id, project_id, file_name, fingerprint_sha256, source_started_at, timezone_name,
          analysis_start_pts_ms, analysis_end_pts_ms, interval_origin_pts_ms, created_at
        ) VALUES ('legacy-source', 'legacy-project', 'legacy.mp4', 'legacy-sha', '2026-01-01T00:00:00+00:00', 'UTC', 0, 30000, 0, 'now')
        """
    )
    connection.execute(
        "INSERT INTO scene_versions(id, project_id, version, template, geometry_json, config_hash, created_at) VALUES ('legacy-scene', 'legacy-project', 1, 'intersection', '{}', 'legacy-scene-hash', 'now')"
    )
    connection.execute(
        """
        INSERT INTO processing_configuration_revisions(
          id, profile_id, profile_revision, guided_settings_json, requested_expert_overrides_json,
          resolved_parameters_json, parameter_schema_revision, model_revision, weight_sha256,
          tracker_revision, crossing_policy_revision, classification_policy_revision, device_request,
          created_by, created_at, content_hash, validation_result_json, status
        ) VALUES ('legacy-config', 'BALANCED', 'legacy-profile-v1', '{}', '{}', '{}', 'legacy-schema', NULL, NULL, NULL, 'legacy-crossing', 'legacy-classification', 'AUTO', 'test', 'now', 'legacy-config-hash', '{}', 'VALID')
        """
    )
    connection.execute(
        """
        INSERT INTO preview_runs(
          id, project_id, source_id, source_fingerprint_sha256, scene_version_id, scene_revision,
          scene_semantic_hash, start_pts_ms, end_pts_ms, processing_configuration_revision_id,
          run_type, mode, status, idempotency_key, created_by, created_at
        ) VALUES ('legacy-preview', 'legacy-project', 'legacy-source', 'legacy-sha', 'legacy-scene', '1', 'legacy-scene-hash', 0, 30000, 'legacy-config', 'PREVIEW_ONLY', 'SYNTHETIC', 'COMPLETED', 'legacy-key', 'test', 'now')
        """
    )
    connection.commit()

    migrate(connection)

    preserved = connection.execute("SELECT status, runtime_configuration_hash, provenance_status FROM preview_runs WHERE id = 'legacy-preview'").fetchone()
    assert preserved is not None
    assert preserved["status"] == "COMPLETED"
    assert preserved["runtime_configuration_hash"] is None
    assert preserved["provenance_status"] == "LEGACY_UNRESOLVED"
    config = connection.execute("SELECT runtime_configuration_hash, request_provenance_hash, provenance_status FROM processing_configuration_revisions WHERE id = 'legacy-config'").fetchone()
    assert config is not None
    assert config["runtime_configuration_hash"] is None
    assert config["request_provenance_hash"] is None
    assert config["provenance_status"] == "LEGACY_UNRESOLVED"


def test_three_line_bidirectional_and_mixed_domain_preview_semantics_are_deterministic() -> None:
    lines = tuple(
        CountingLine(
            line_id=f"line_{index}",
            label=f"Line {index}",
            start=Point(0.2, y),
            end=Point(0.8, y),
            direction_mode="BIDIRECTIONAL",
            side_a_label=f"Line {index} side A",
            side_b_label=f"Line {index} side B",
        )
        for index, y in enumerate((0.3, 0.5, 0.7), start=1)
    )
    scene = CountingScene("scene-6d", "source-6d", lines, ())
    track_values = (
        ("car-1", "car", 0.2, 0.8),
        ("person-1", "person", 0.8, 0.2),
        ("motorcycle-1", "motorcycle", 0.2, 0.8),
    )
    track_set = SyntheticTrackSet(
        schema_version="synthetic-tracks-v1",
        source_fingerprint="source-6d",
        fixture_id="six-d-preview",
        scene_revision="scene-6d",
        tracks=tuple(
            SyntheticTrack(
                track_id=track_id,
                synthetic_class=synthetic_class,
                observations=(Observation(0, Point(0.5, start)), Observation(3_000, Point(0.5, end))),
            )
            for track_id, synthetic_class, start, end in track_values
        ),
    )
    from apps.backend.app.domain import TimeContract

    result = execute_synthetic_counting(
        "preview-6d",
        track_set,
        scene,
        TimeContract(datetime(2026, 1, 1, tzinfo=timezone.utc), "UTC", 0, 30_000, 0),
    )

    assert len(result.events) == 9
    assert set(result.aggregates["by_line"]) == {"line_1", "line_2", "line_3"}
    assert set(result.aggregates["by_direction"]) == {"A_TO_B", "B_TO_A"}
    assert set(result.aggregates["by_class"]) == {"passenger_vehicle", "pedestrian", "motorcycle"}
    assert sum(result.aggregates["by_line"].values()) == result.aggregates["grand_total"]
    assert all("side A" in event.readable_direction_label or "side B" in event.readable_direction_label for event in result.events)
    assert all(event.line_id in {"line_1", "line_2", "line_3"} for event in result.events)


def test_runtime_identity_is_separate_from_request_provenance_and_canonicalizes_aliases() -> None:
    balanced = build_configuration("BALANCED", {}, {}, cuda_available=False)
    equivalent = build_configuration(
        "CUSTOM",
        {},
        {
            **balanced["resolved_parameters"],
            "class_allowlist": list(reversed(balanced["resolved_parameters"]["class_allowlist"])),
            "confidence_threshold": 0.25,
        },
        cuda_available=False,
    )
    changed = build_configuration("CUSTOM", {}, {**balanced["resolved_parameters"], "frame_stride": 2}, cuda_available=False)

    assert balanced["configuration_hash"] == equivalent["configuration_hash"]
    assert balanced["request_provenance_hash"] != equivalent["request_provenance_hash"]
    assert balanced["configuration_hash"] != changed["configuration_hash"]
    assert balanced["runtime_configuration_hash"] is equivalent["runtime_configuration_hash"] is None
    assert balanced["runtime_provenance_status"] == "AWAITING_RUNTIME_RESOLUTION"
    assert balanced["runtime_provenance"]["model_revision"]
    assert balanced["runtime_provenance"]["tracker_revision"]


def test_preview_idempotency_fingerprint_reuses_only_identical_requests() -> None:
    client, project, scene = _client()
    configuration = client.post(
        f"/api/v1/projects/{project['id']}/processing-configurations",
        json={"profile_code": "BALANCED", "expected_scene_version": scene["version"]},
    ).json()
    request = {
        "mode": "SYNTHETIC",
        "processing_configuration_revision_id": configuration["id"],
        "start_pts_ms": 0,
        "end_pts_ms": 30_000,
        "fixture_id": "api-acceptance",
        "idempotency_key": "same-preview-request",
    }
    first = client.post(f"/api/v1/projects/{project['id']}/previews", json=request)
    second = client.post(f"/api/v1/projects/{project['id']}/previews", json=request)
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    mismatch = client.post(
        f"/api/v1/projects/{project['id']}/previews",
        json={**request, "end_pts_ms": 29_000},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert "claim_token" not in first.json()
    assert "worker_id" not in first.json()

    implicit_request = {key: value for key, value in request.items() if key != "processing_configuration_revision_id"}
    implicit_request["idempotency_key"] = "implicit-preview-request"
    implicit_first = client.post(f"/api/v1/projects/{project['id']}/previews", json=implicit_request)
    implicit_second = client.post(f"/api/v1/projects/{project['id']}/previews", json=implicit_request)
    assert implicit_first.status_code == implicit_second.status_code == 200
    assert implicit_first.json()["id"] == implicit_second.json()["id"]


def test_preview_lease_heartbeat_reclaim_and_terminal_fencing() -> None:
    client, project, scene = _client()
    service: FoundationService = client.app.state.foundation_service
    configuration = service.create_processing_configuration_revision(
        project["id"],
        {"profile_code": "BALANCED", "expected_scene_version": scene["version"]},
    )
    _mark_source_as_ready_upload(client, project["id"])
    preview = service.create_preview_run(
        project["id"],
        {
            "mode": "REAL_VIDEO",
            "processing_configuration_revision_id": configuration["id"],
            "start_pts_ms": 0,
            "end_pts_ms": 30_000,
        },
    )
    first_claim = service.claim_next_preview_run("worker-a", lease_seconds=120)
    assert first_claim is not None
    raw = service.connection.execute("SELECT * FROM preview_runs WHERE id = ?", (preview["id"],)).fetchone()
    assert raw is not None
    old_token = str(raw["claim_token"])
    assert first_claim["attempt_number"] == 1
    assert first_claim["heartbeat_at"]
    assert first_claim["ownership_state"] == "RUNNING"

    renewed = service.processing_profile_store.heartbeat_preview(preview["id"], "worker-a", old_token, lease_seconds=120)
    assert renewed["heartbeat_at"]
    service.connection.execute(
        "UPDATE preview_runs SET lease_expires_at = ? WHERE id = ?",
        ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), preview["id"]),
    )
    service.connection.commit()
    second_claim = service.claim_next_preview_run("worker-b", lease_seconds=120)
    assert second_claim is not None
    assert second_claim["id"] == preview["id"]
    assert second_claim["attempt_number"] == 2
    new_raw = service.connection.execute("SELECT * FROM preview_runs WHERE id = ?", (preview["id"],)).fetchone()
    assert new_raw is not None
    with pytest.raises(PreviewConflict, match="ownership"):
        service.processing_profile_store.set_preview_result(
            preview["id"],
            status="COMPLETED",
            statistics={"event_count": 1},
            worker_id="worker-a",
            claim_token=old_token,
        )
    cancelled = service.cancel_preview_run(preview["id"], "operator stopped the preview")
    assert cancelled["status"] == "CANCELLED"
    assert service.connection.execute("SELECT COUNT(*) FROM preview_run_statistics WHERE preview_run_id = ?", (preview["id"],)).fetchone()[0] == 1
    state_events = [row["event_type"] for row in service.connection.execute("SELECT event_type FROM preview_run_state_events WHERE preview_run_id = ?", (preview["id"],))]
    assert state_events.count("CLAIMED") == 2
    assert {"QUEUED", "HEARTBEAT", "LEASE_EXPIRED_REQUEUED", "CANCELLED"} <= set(state_events)
    with pytest.raises(Exception, match="append-only"):
        service.connection.execute(
            "UPDATE preview_run_state_events SET event_type = 'TAMPERED' WHERE preview_run_id = ?",
            (preview["id"],),
        )


def test_running_preview_cancellation_reaches_inference_and_leaks_no_events(monkeypatch, tmp_path) -> None:
    client, project, scene = _client()
    monkeypatch.setenv("TVA_LOCAL_DATA_DIR", str(tmp_path))
    service: FoundationService = client.app.state.foundation_service
    configuration = service.create_processing_configuration_revision(
        project["id"],
        {"profile_code": "BALANCED", "expected_scene_version": scene["version"]},
    )
    media_path = tmp_path / "approved.mp4"
    media_path.write_bytes(b"synthetic-approved-media")
    source = service.connection.execute("SELECT id FROM video_sources WHERE project_id = ?", (project["id"],)).fetchone()
    assert source is not None
    service.connection.execute(
        "UPDATE video_sources SET source_type = 'local_upload', readiness_state = 'media_ready', managed_media_path = ?, width = 2, height = 2, frame_count = 1, duration_ms = 30000 WHERE id = ?",
        (str(media_path), source["id"]),
    )
    service.connection.commit()
    preview = service.create_preview_run(
        project["id"],
        {
            "mode": "REAL_VIDEO",
            "processing_configuration_revision_id": configuration["id"],
            "start_pts_ms": 0,
            "end_pts_ms": 1_000,
        },
    )
    claim = service.claim_next_preview_run("worker-cancel")
    assert claim is not None

    from apps.backend.app import services as service_module

    monkeypatch.setattr(service_module, "media_runtime_status", lambda: SimpleNamespace(ffmpeg=SimpleNamespace(executable="ffmpeg")))

    def fake_inference(**kwargs):
        service.cancel_preview_run(kwargs["run_id"], "cancelled during inference")
        assert kwargs["cancel_requested"]() is True
        from apps.backend.app.real_inference import RealInferenceCancelled

        raise RealInferenceCancelled()

    monkeypatch.setattr(service_module, "run_real_video_inference", fake_inference)
    result = service.execute_claimed_preview_run(preview["id"], "worker-cancel")
    assert result["status"] == "CANCELLED"
    assert service.connection.execute("SELECT COUNT(*) FROM preview_run_events WHERE preview_run_id = ?", (preview["id"],)).fetchone()[0] == 0
    assert service.connection.execute("SELECT COUNT(*) FROM preview_run_statistics WHERE preview_run_id = ?", (preview["id"],)).fetchone()[0] == 1
