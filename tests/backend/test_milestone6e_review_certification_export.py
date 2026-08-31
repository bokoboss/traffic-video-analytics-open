from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from apps.backend.app.db import MIGRATIONS_DIR, connect, migrate
from apps.backend.app.main import create_app
from apps.backend.app.review_service import (
    ArtifactStore,
    ArtifactPathError,
    ArtifactSymlinkError,
    _safe_component,
    _xlsx_cell,
    certification_content_hash,
    safe_spreadsheet_text,
)
from apps.backend.app.reviewing import (
    REVIEW_ACTION_TYPES,
    interval_index_for_event,
    project_human_added_event,
    project_review_event,
    reconcile_reviewed_events,
    sha256_json,
)


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> tuple[TestClient, dict, dict, dict]:
    monkeypatch.setenv("TVA_EXPORT_ROOT", str(tmp_path / "exports"))
    client = TestClient(create_app())
    project = client.post(
        "/api/v1/projects",
        json={"name": "6E review fixture", "location": "Bangkok", "study_type": "intersection", "language": "th"},
    ).json()
    source = client.post(
        "/api/v1/sources",
        json={
            "project_id": project["id"],
            "file_name": "synthetic.mp4",
            "fingerprint_sha256": "6e-source-fingerprint",
            "source_started_at": datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc).isoformat(),
            "timezone_name": "Asia/Bangkok",
            "analysis_start_pts_ms": 0,
            "analysis_end_pts_ms": 3_600_000,
        },
    )
    source.raise_for_status()
    scene = client.post("/api/v1/scenes", json={"project_id": project["id"], "template": "intersection"})
    scene.raise_for_status()
    run = client.post(
        f"/api/v1/projects/{project['id']}/synthetic-runs",
        json={"fixture_id": "milestone-6e", "expected_scene_version": scene.json()["version"]},
    )
    run.raise_for_status()
    session = client.post(
        f"/api/v1/projects/{project['id']}/review-sessions",
        json={"processing_run_id": run.json()["id"], "created_by": "test-operator"},
    )
    session.raise_for_status()
    return client, project, run.json(), session.json()


def _seed_second_automatic_event(client: TestClient, run_id: str) -> str:
    """Add one deterministic synthetic ledger row for duplicate-action tests."""

    connection = client.app.state.foundation_service.review_service.connection
    projection = connection.execute(
        "SELECT * FROM engineering_event_projections WHERE run_id = ? ORDER BY event_pts_ms, source_event_id LIMIT 1",
        (run_id,),
    ).fetchone()
    assert projection is not None
    source_event_id = str(projection["source_event_id"])
    second_event_id = "fixture-auto-second"
    connection.execute(
        """
        INSERT INTO auto_count_events(
          id, run_id, technical_key, pts_ms, track_id, rule_id,
          object_domain, classification, movement, confidence, qc_state, created_at
        )
        SELECT ?, run_id, 'fixture-second', pts_ms + 30000, 'fixture-track-second', rule_id,
               object_domain, classification, movement, confidence, qc_state, created_at
        FROM auto_count_events
        WHERE id = ?
        """,
        (second_event_id, source_event_id),
    )
    connection.execute(
        """
        INSERT INTO engineering_event_projections(
          id, engineering_result_revision_id, run_id, source_event_id,
          technical_key, source_fingerprint_sha256, scene_revision,
          counting_line_id, counting_line_name, side_a_name, side_b_name,
          canonical_direction, readable_direction_name, track_id,
          event_pts_ms, source_frame_index, source_frame_pts_ms,
          absolute_event_time, event_timezone_name, event_time_status,
          raw_detector_class_id, raw_detector_class_name,
          track_voted_raw_class_id, track_voted_raw_class_name,
          provisional_class, engineering_class, classification_status,
          classification_reason, taxonomy_revision, mapping_revision,
          classification_policy_revision, detector_confidence_summary_json,
          track_evidence_ref, processing_provenance_json, qc_state, stale,
          created_at
        )
        SELECT
          ?, engineering_result_revision_id, run_id, ?,
          'fixture-second', source_fingerprint_sha256, scene_revision,
          counting_line_id, counting_line_name, side_a_name, side_b_name,
          canonical_direction, readable_direction_name, 'fixture-track-second',
          event_pts_ms + 30000, source_frame_index, source_frame_pts_ms,
          absolute_event_time, event_timezone_name, event_time_status,
          raw_detector_class_id, raw_detector_class_name,
          track_voted_raw_class_id, track_voted_raw_class_name,
          provisional_class, engineering_class, classification_status,
          classification_reason, taxonomy_revision, mapping_revision,
          classification_policy_revision, detector_confidence_summary_json,
          track_evidence_ref, processing_provenance_json, qc_state, stale,
          created_at
        FROM engineering_event_projections
        WHERE source_event_id = ?
        """,
        (f"{projection['engineering_result_revision_id']}:event:{second_event_id}", second_event_id, source_event_id),
    )
    connection.commit()
    return second_event_id


def _seed_fixture_action_history(client: TestClient, session_id: str, target_event_id: str, *, start_order: int, count: int) -> None:
    """Insert deterministic append-only actions without detector work."""

    connection = client.app.state.foundation_service.review_service.connection
    session = connection.execute("SELECT * FROM review_sessions WHERE id = ?", (session_id,)).fetchone()
    assert session is not None
    rows = []
    for action_order in range(start_order, start_order + count):
        action_id = f"fixture-action-{action_order:04d}"
        if action_order == 1:
            action_type = "CONFIRM_EVENT"
            payload = {}
        elif action_order == 3:
            action_type = "REVERSE_ACTION"
            payload = {"reverses_action_id": "fixture-action-0002"}
        else:
            action_type = "ADD_NOTE"
            payload = {"note": f"fixture-note-{action_order:04d}"}
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        rows.append(
            (
                action_id,
                action_id,
                session_id,
                target_event_id,
                "AUTOMATIC_EVENT",
                target_event_id,
                None,
                action_type,
                payload_json,
                "fixture_history",
                payload.get("note", ""),
                "fixture-reviewer",
                "fixture-reviewer",
                None,
                None,
                "fixture_history",
                "2026-01-01T07:00:00+00:00",
                action_order - 1,
                action_order,
                None,
                payload.get("reverses_action_id"),
                str(session["engineering_result_revision_id"]),
                sha256_json({"id": action_id, "action_order": action_order, "action_type": action_type, "payload": payload}),
            )
        )
    connection.executemany(
        """
        INSERT INTO review_actions(
          id, review_action_id, review_session_id, event_id, target_type,
          target_event_id, target_review_event_id, action_type, payload_json,
          reason_code, comment, reviewer, reviewer_id, new_classification,
          new_movement, reason, created_at, expected_review_revision,
          action_order, client_request_id, reverses_action_id,
          source_event_revision, content_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    latest_revision = start_order + count - 1
    connection.execute(
        "UPDATE review_sessions SET review_revision = ?, review_status = 'IN_REVIEW' WHERE id = ?",
        (latest_revision, session_id),
    )
    connection.commit()


def test_real_video_runtime_provenance_mismatch_blocks_certification(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    client, _project, run, session = _client(monkeypatch, tmp_path)
    connection = client.app.state.foundation_service.review_service.connection
    configured_runtime_hash = session["runtime_configuration_hash"]
    connection.execute(
        """
        UPDATE analysis_runs
        SET processing_mode = 'REAL_VIDEO', runtime_configuration_hash = ?,
            actual_runtime_configuration_hash = 'actual-runtime',
            provenance_status = 'RUNTIME_PROVENANCE_MISMATCH'
        WHERE id = ?
        """,
        (configured_runtime_hash, run["id"]),
    )
    connection.commit()

    queue = client.get(f"/api/v1/review-sessions/{session['id']}/queue?order_by=timestamp")
    queue.raise_for_status()
    event_id = queue.json()["items"][0]["source_event_id"]
    confirmed = client.post(
        f"/api/v1/review-sessions/{session['id']}/actions",
        json={
            "action_type": "CONFIRM_EVENT",
            "target_event_id": event_id,
            "reviewer_id": "reviewer",
            "expected_review_revision": 0,
        },
    )
    confirmed.raise_for_status()
    completed = client.post(
        f"/api/v1/review-sessions/{session['id']}/complete",
        json={"completed_by": "reviewer", "expected_review_revision": 1},
    )
    completed.raise_for_status()

    certification = client.post(
        f"/api/v1/review-sessions/{session['id']}/certification",
        json={"certified_by": "reviewer"},
    )

    assert certification.status_code == 409
    assert certification.json()["detail"]["code"] == "RUNTIME_PROVENANCE_UNVERIFIED"


def test_scoped_review_conflict_reversal_projection_certification_and_exports(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client, project, run, session = _client(monkeypatch, tmp_path)
    session_id = session["id"]
    queue = client.get(f"/api/v1/review-sessions/{session_id}/queue?order_by=timestamp")
    queue.raise_for_status()
    event = queue.json()["items"][0]
    event_id = event["source_event_id"]
    assert event["review_status"] == "UNREVIEWED"

    diagnostic_scope = client.post(
        f"/api/v1/projects/{project['id']}/review-sessions",
        json={
            "processing_run_id": run["id"],
            "review_scope_type": "DIAGNOSTIC_SUBSET",
            "review_scope_filter": {"event_ids": [event_id]},
            "created_by": "diagnostic-operator",
        },
    )
    diagnostic_scope.raise_for_status()
    assert diagnostic_scope.json()["id"] != session_id
    diagnostic_scope_replay = client.post(
        f"/api/v1/projects/{project['id']}/review-sessions",
        json={
            "processing_run_id": run["id"],
            "review_scope_type": "DIAGNOSTIC_SUBSET",
            "review_scope_filter": {"event_ids": [event_id]},
            "created_by": "diagnostic-operator",
        },
    )
    diagnostic_scope_replay.raise_for_status()
    assert diagnostic_scope_replay.json()["id"] == diagnostic_scope.json()["id"]

    changed = client.post(
        f"/api/v1/review-sessions/{session_id}/actions",
        json={
            "action_type": "CHANGE_CLASS",
            "target_event_id": event_id,
            "payload": {"engineering_class": "OTHER"},
            "reason_code": "manual_classification_check",
            "reviewer_id": "reviewer-a",
            "expected_review_revision": 0,
            "client_request_id": "change-class-1",
        },
    )
    changed.raise_for_status()
    assert changed.json()["action_type"] == "CHANGE_CLASS"

    conflict = client.post(
        f"/api/v1/review-sessions/{session_id}/actions",
        json={
            "action_type": "CONFIRM_EVENT",
            "target_event_id": event_id,
            "reviewer_id": "reviewer-b",
            "expected_review_revision": 0,
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "REVIEW_REVISION_CONFLICT"
    assert conflict.json()["detail"]["actions_since_expected"][0]["action_type"] == "CHANGE_CLASS"

    reversed_action = client.post(
        f"/api/v1/review-sessions/{session_id}/actions/reverse",
        json={
            "action_type": "REVERSE_ACTION",
            "target_event_id": event_id,
            "reverses_action_id": changed.json()["id"],
            "reviewer_id": "reviewer-a",
            "expected_review_revision": 1,
        },
    )
    reversed_action.raise_for_status()
    confirmed = client.post(
        f"/api/v1/review-sessions/{session_id}/actions",
        json={
            "action_type": "CONFIRM_EVENT",
            "target_event_id": event_id,
            "reason_code": "reviewed_against_source_evidence",
            "reviewer_id": "reviewer-a",
            "expected_review_revision": 2,
            "client_request_id": "confirm-1",
        },
    )
    confirmed.raise_for_status()

    projection = client.post(f"/api/v1/review-sessions/{session_id}/projections")
    projection.raise_for_status()
    assert projection.json()["events"][0]["review_status"] == "CONFIRMED"
    assert projection.json()["events"][0]["effective_class"] != "OTHER"
    history = client.get(f"/api/v1/review-sessions/{session_id}/projections")
    history.raise_for_status()
    assert len(history.json()) >= 1

    progress = client.get(f"/api/v1/review-sessions/{session_id}/progress")
    progress.raise_for_status()
    assert progress.json()["reconciliation"]["status"] == "PASSED"
    assert progress.json()["unreviewed_count"] == 0

    completed = client.post(
        f"/api/v1/review-sessions/{session_id}/complete",
        json={"completed_by": "reviewer-a", "expected_review_revision": 3},
    )
    completed.raise_for_status()
    assert completed.json()["review_status"] == "REVIEW_COMPLETE"

    certification = client.post(
        f"/api/v1/review-sessions/{session_id}/certification",
        json={"certified_by": "reviewer-a", "rights_disclosure": "Fixture rights are approved for testing."},
    )
    certification.raise_for_status()
    certification_id = certification.json()["id"]
    assert certification.json()["status"] == "CERTIFIED"
    assert certification.json()["review_scope"]["partial"] is False

    downloaded: dict[str, bytes] = {}
    export_ids: dict[str, str] = {}
    xlsx_manifest: dict = {}
    schema_revisions = {"CSV": "production-export-csv-v2", "XLSX": "production-export-xlsx-v2", "JSON": "audit-export-v2"}
    mime_types = {
        "CSV": "text/csv",
        "XLSX": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "JSON": "application/json",
    }
    for export_format in ("CSV", "XLSX", "JSON"):
        export = client.post(
            f"/api/v1/certifications/{certification_id}/exports",
            json={"format": export_format, "language": "en", "client_request_id": f"export-{export_format}"},
        )
        export.raise_for_status()
        payload = export.json()
        assert payload["status"] == "COMPLETED"
        assert payload["artifact_generation_status"] == "COMPLETED"
        assert payload["source_certification_status"] == "CURRENT"
        assert payload["effective_export_status"] == "COMPLETED"
        assert payload["reviewed_projection_revision_id"] == certification.json()["reviewed_projection_revision_id"]
        assert payload["export_schema_revision"] == schema_revisions[export_format]
        assert payload["language"] == "en"
        assert payload["options"] == {}
        assert payload["content_hash"] == payload["artifact_sha256"]
        assert payload["manifest"]["row_count"] == 1
        assert payload["artifact_manifest"]["sha256"] == payload["artifact_sha256"]
        artifact = payload["artifact"]
        assert artifact["safe_filename"].endswith(export_format.lower())
        assert "relative_path" not in artifact
        if export_format == "XLSX":
            xlsx_manifest = payload["manifest"]
        download = client.get(f"/api/v1/export-artifacts/{artifact['artifact_id']}/download")
        download.raise_for_status()
        assert download.headers["content-type"].startswith(mime_types[export_format])
        assert artifact["safe_filename"] in download.headers["content-disposition"]
        assert str(tmp_path) not in json.dumps(payload, ensure_ascii=False)
        assert str(tmp_path) not in download.headers["content-disposition"]
        assert hashlib.sha256(download.content).hexdigest() == artifact["sha256"]
        downloaded[export_format] = download.content
        export_ids[export_format] = payload["id"]

    csv_header = downloaded["CSV"].decode("utf-8").splitlines()[0]
    assert csv_header.startswith("project_id,source_id,source_fingerprint")
    assert "actual_runtime_configuration_hash" in csv_header
    assert "runtime_provenance_status" in csv_header
    assert b"reviewed_event_id" in downloaded["CSV"]
    audit_export = json.loads(downloaded["JSON"])
    assert audit_export["schema_revision"] == "audit-export-v2"
    assert {
        "runtime_configuration_hash",
        "actual_runtime_configuration_hash",
        "status",
    } <= set(audit_export["runtime_provenance"])
    with zipfile.ZipFile(io.BytesIO(downloaded["XLSX"])) as workbook:
        names = set(workbook.namelist())
        assert "xl/workbook.xml" in names
        assert "xl/worksheets/sheet1.xml" in names
        assert all(not name.startswith(("/", "..")) and ".." not in name.split("/") for name in names)
        workbook_xml = workbook.read("xl/workbook.xml").decode("utf-8")
        for sheet_name in ("Summary", "15-min Counts", "Event Ledger", "Review Adjustments", "QC and Reconciliation", "Methodology", "Provenance"):
            assert f'name="{sheet_name}"' in workbook_xml
    assert xlsx_manifest["sheet_count"] == 7

    revoked = client.post(
        f"/api/v1/certifications/{certification_id}/revoke",
        json={"reason": "fixture revocation test", "revoked_by": "reviewer-a"},
    )
    revoked.raise_for_status()
    assert revoked.json()["status"] == "REVOKED"
    historical = client.get(f"/api/v1/exports/{export_ids['CSV']}")
    historical.raise_for_status()
    assert historical.json()["status"] == "COMPLETED"
    assert historical.json()["artifact_generation_status"] == "COMPLETED"
    assert historical.json()["source_certification_status"] == "REVOKED"
    assert historical.json()["effective_export_status"] == "REVOKED_SOURCE"
    historical_download = client.get(f"/api/v1/export-artifacts/{historical.json()['artifact']['artifact_id']}/download")
    historical_download.raise_for_status()
    assert hashlib.sha256(historical_download.content).hexdigest() == historical.json()["artifact"]["sha256"]
    assert historical_download.content == downloaded["CSV"]
    blocked_export = client.post(
        f"/api/v1/certifications/{certification_id}/exports",
        json={"format": "CSV", "language": "en", "client_request_id": "after-revoke"},
    )
    assert blocked_export.status_code == 409
    assert blocked_export.json()["detail"]["code"] == "CERTIFICATION_NOT_CURRENT"


def test_all_review_action_endpoints_persist_with_explicit_reversal(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client, _project, run, session = _client(monkeypatch, tmp_path)
    session_id = session["id"]
    second_event_id = _seed_second_automatic_event(client, run["id"])
    queue = client.get(f"/api/v1/review-sessions/{session_id}/queue")
    queue.raise_for_status()
    first_event_id = next(
        str(item["source_event_id"])
        for item in queue.json()["items"]
        if str(item.get("source_event_id")) != second_event_id
    )

    def append(action_type: str, revision: int, *, target_event_id: str | None = first_event_id, payload: dict | None = None, reason: str = "fixture review") -> dict:
        response = client.post(
            f"/api/v1/review-sessions/{session_id}/actions",
            json={
                "action_type": action_type,
                "target_event_id": target_event_id,
                "payload": payload or {},
                "reason_code": reason,
                "comment": reason,
                "reviewer_id": "fixture-reviewer",
                "expected_review_revision": revision,
            },
        )
        response.raise_for_status()
        return response.json()

    saved = [
        append("CONFIRM_EVENT", 0, reason="confirm source evidence"),
        append("REJECT_FALSE_POSITIVE", 1, target_event_id=second_event_id, reason="reject duplicate-looking source"),
        append("CHANGE_CLASS", 2, payload={"engineering_class": "BUS"}),
        append("CHANGE_DIRECTION", 3, payload={"direction": "B_TO_A"}),
        append("CHANGE_LINE", 4, payload={"counting_line_id": "line_main"}),
        append("ADJUST_TIMESTAMP", 5, payload={"crossing_pts_ms": 901_000}),
        append("MARK_DUPLICATE", 6, payload={"duplicate_of": second_event_id}),
        append("MARK_UNSCORABLE", 7, target_event_id=second_event_id, reason="occluded source evidence"),
        append("ADD_NOTE", 8, payload={"note": "ตรวจหลักฐานซ้ำ"}, reason="บันทึกหมายเหตุภาษาไทย"),
        append(
            "ADD_MISSED_EVENT",
            9,
            target_event_id=None,
            payload={
                "counting_line_id": "line_main",
                "direction": "A_TO_B",
                "crossing_pts_ms": 1_200_000,
                "engineering_class": "UNKNOWN",
                "evidence_reference": "fixture-observation-1",
                "manual_observation_note": "operator observed a missed event",
                "reason": "manual observation",
            },
        ),
    ]
    reversal = client.post(
        f"/api/v1/review-sessions/{session_id}/actions/reverse",
        json={
            "action_type": "REVERSE_ACTION",
            "target_event_id": first_event_id,
            "reverses_action_id": saved[6]["id"],
            "reason_code": "duplicate relationship disproved",
            "reviewer_id": "fixture-reviewer",
            "expected_review_revision": 10,
        },
    )
    reversal.raise_for_status()
    saved.append(reversal.json())
    assert {item["action_type"] for item in saved} == set(REVIEW_ACTION_TYPES)

    projected = client.post(f"/api/v1/review-sessions/{session_id}/projections")
    projected.raise_for_status()
    events = {str(item["source_event_id"]): item for item in projected.json()["events"] if item.get("source_event_id")}
    assert events[first_event_id]["review_status"] == "CORRECTED"
    assert events[first_event_id]["effective_class"] == "BUS"
    assert events[second_event_id]["review_status"] == "UNSCORABLE"
    assert any(item["origin"] == "HUMAN_ADDED" and item["review_status"] == "CONFIRMED" for item in projected.json()["events"])


def test_review_overlay_tables_are_append_only_and_artifact_store_rejects_escape(tmp_path) -> None:
    from apps.backend.app.db import connect, migrate

    connection = connect()
    migrate(connection)
    connection.execute(
        "INSERT INTO review_actions(id, action_type, reviewer, created_at) VALUES ('action-test', 'CONFIRM_EVENT', 'tester', 'now')"
    )
    with pytest.raises(Exception, match="append-only"):
        connection.execute("UPDATE review_actions SET reviewer = 'changed' WHERE id = 'action-test'")
    with pytest.raises(ValueError, match="managed"):
        ArtifactStore(tmp_path / "root")._checked_path("../outside.csv")
    with pytest.raises(ValueError, match="managed"):
        ArtifactStore(tmp_path / "root")._checked_path("C:/outside.csv")
    with pytest.raises(ValueError, match="managed"):
        ArtifactStore(tmp_path / "root")._checked_path("/outside.csv")
    with pytest.raises(ArtifactPathError) as unc_error:
        ArtifactStore(tmp_path / "root")._checked_path("\\\\server\\share\\outside.csv")
    assert unc_error.value.code == "ARTIFACT_PATH_NOT_MANAGED"
    assert safe_spreadsheet_text("=SUM(1,1)") == "'=SUM(1,1)"
    for marker in ("=", "+", "-", "@"):
        assert safe_spreadsheet_text(f" \t\n{marker}payload").startswith("'")
        assert "<f>" not in _xlsx_cell("A1", f"\t\n{marker}payload")
    assert safe_spreadsheet_text("ยูนิโค้ด ความเห็น") == "ยูนิโค้ด ความเห็น"
    assert "<f>" not in _xlsx_cell("A1", "=SUM(1,1)")

    root = tmp_path / "root"
    store = ArtifactStore(root)
    store.write_atomic("safe/result.csv", b"ok")
    assert store.resolve_download("safe/result.csv").read_bytes() == b"ok"
    if hasattr(__import__("os"), "symlink"):
        outside = tmp_path / "outside"
        outside.mkdir()
        link = root / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            pass
        else:
            with pytest.raises(ArtifactSymlinkError) as directory_symlink_error:
                store.write_atomic("escape/result.csv", b"blocked")
            assert directory_symlink_error.value.code == "ARTIFACT_SYMLINK_NOT_ALLOWED"
            target = root / "target.csv"
            target.write_bytes(b"safe")
            target_link = root / "target-link.csv"
            try:
                target_link.symlink_to(target)
            except OSError:
                pass
            else:
                with pytest.raises(ArtifactSymlinkError):
                    store.resolve_download("target-link.csv")

    assert _safe_component("../bad\\name:with?characters") == "bad-name-with-characters"


BASE_EVENT = {
    "source_event_id": "event-1",
    "counting_line_id": "line-main",
    "counting_line_name": "Main line",
    "canonical_direction": "A_TO_B",
    "event_pts_ms": 1_000,
    "engineering_class": "PASSENGER_VEHICLE",
    "classification_status": "CONFIRMED_MAPPING",
}


def _overlay_action(action_id: str, action_type: str, order: int, **payload) -> dict:
    return {
        "id": action_id,
        "action_type": action_type,
        "target_event_id": "event-1",
        "action_order": order,
        "created_at": f"2026-01-01T00:00:{order:02d}+00:00",
        "payload": payload,
    }


@pytest.mark.parametrize(
    ("name", "actions", "expected_status", "expected_class"),
    [
        (
            "reject_then_confirm",
            [_overlay_action("reject", "REJECT_FALSE_POSITIVE", 1, reason="false positive"), _overlay_action("confirm", "CONFIRM_EVENT", 2)],
            "REJECTED_FALSE_POSITIVE",
            "PASSENGER_VEHICLE",
        ),
        (
            "reject_then_correction",
            [_overlay_action("reject", "REJECT_FALSE_POSITIVE", 1, reason="false positive"), _overlay_action("class", "CHANGE_CLASS", 2, engineering_class="BUS")],
            "REJECTED_FALSE_POSITIVE",
            "BUS",
        ),
        (
            "reject_confirm_reverse_reject",
            [
                _overlay_action("reject", "REJECT_FALSE_POSITIVE", 1, reason="false positive"),
                _overlay_action("confirm", "CONFIRM_EVENT", 2),
                _overlay_action("reverse-reject", "REVERSE_ACTION", 3, reverses_action_id="reject"),
            ],
            "CONFIRMED",
            "PASSENGER_VEHICLE",
        ),
        (
            "unscorable_then_confirm",
            [_overlay_action("unscorable", "MARK_UNSCORABLE", 1, reason="occluded"), _overlay_action("confirm", "CONFIRM_EVENT", 2)],
            "UNSCORABLE",
            "PASSENGER_VEHICLE",
        ),
        (
            "unscorable_then_correction",
            [_overlay_action("unscorable", "MARK_UNSCORABLE", 1, reason="occluded"), _overlay_action("class", "CHANGE_CLASS", 2, engineering_class="BUS")],
            "UNSCORABLE",
            "BUS",
        ),
        (
            "unscorable_reverse_then_confirm",
            [
                _overlay_action("unscorable", "MARK_UNSCORABLE", 1, reason="occluded"),
                _overlay_action("reverse-unscorable", "REVERSE_ACTION", 2, reverses_action_id="unscorable"),
                _overlay_action("confirm", "CONFIRM_EVENT", 3),
            ],
            "CONFIRMED",
            "PASSENGER_VEHICLE",
        ),
        (
            "duplicate_then_confirm",
            [_overlay_action("duplicate", "MARK_DUPLICATE", 1, duplicate_of="event-2"), _overlay_action("confirm", "CONFIRM_EVENT", 2)],
            "DUPLICATE_SUPPRESSED",
            "PASSENGER_VEHICLE",
        ),
        (
            "duplicate_reverse_then_confirm",
            [
                _overlay_action("duplicate", "MARK_DUPLICATE", 1, duplicate_of="event-2"),
                _overlay_action("reverse-duplicate", "REVERSE_ACTION", 2, reverses_action_id="duplicate"),
                _overlay_action("confirm", "CONFIRM_EVENT", 3),
            ],
            "CONFIRMED",
            "PASSENGER_VEHICLE",
        ),
        (
            "correction_and_rejection",
            [_overlay_action("class", "CHANGE_CLASS", 1, engineering_class="BUS"), _overlay_action("reject", "REJECT_FALSE_POSITIVE", 2, reason="false positive")],
            "REJECTED_FALSE_POSITIVE",
            "BUS",
        ),
        (
            "correction_rejection_reverse",
            [
                _overlay_action("class", "CHANGE_CLASS", 1, engineering_class="BUS"),
                _overlay_action("reject", "REJECT_FALSE_POSITIVE", 2, reason="false positive"),
                _overlay_action("reverse-reject", "REVERSE_ACTION", 3, reverses_action_id="reject"),
            ],
            "CORRECTED",
            "BUS",
        ),
        (
            "reversal_of_reversal",
            [
                _overlay_action("reject", "REJECT_FALSE_POSITIVE", 1, reason="false positive"),
                _overlay_action("reverse-reject", "REVERSE_ACTION", 2, reverses_action_id="reject"),
                _overlay_action("reverse-reverse", "REVERSE_ACTION", 3, reverses_action_id="reverse-reject"),
            ],
            "REJECTED_FALSE_POSITIVE",
            "PASSENGER_VEHICLE",
        ),
    ],
)
def test_review_status_precedence_and_reversal_chains(name: str, actions: list[dict], expected_status: str, expected_class: str) -> None:
    projected = project_review_event(BASE_EVENT, actions)
    assert projected["review_status"] == expected_status, name
    assert projected["effective_class"] == expected_class


def test_review_action_vocabulary_and_deterministic_content_hash() -> None:
    action_types = {
        "CONFIRM_EVENT", "REJECT_FALSE_POSITIVE", "CHANGE_CLASS", "CHANGE_DIRECTION",
        "CHANGE_LINE", "ADJUST_TIMESTAMP", "MARK_DUPLICATE", "ADD_MISSED_EVENT",
        "MARK_UNSCORABLE", "ADD_NOTE", "REVERSE_ACTION",
    }
    assert action_types == set(REVIEW_ACTION_TYPES)
    content = {"review_revision": 3, "review_scope": {"type": "FULL_RESULT"}, "summary": {"count": 1}}
    assert sha256_json(content) == sha256_json(json.loads(json.dumps(content)))
    permuted = {"summary": {"count": 1}, "review_scope": {"type": "FULL_RESULT"}, "review_revision": 3}
    assert certification_content_hash(content) == certification_content_hash(permuted)
    assert certification_content_hash(content) != certification_content_hash({**content, "review_revision": 4})


def test_timestamp_interval_and_human_added_projection_are_deterministic() -> None:
    context = {"analysis_start_pts_ms": 0, "analysis_end_pts_ms": 3_600_000, "interval_origin_pts_ms": 0}
    adjusted = project_review_event(
        BASE_EVENT,
        [_overlay_action("adjust", "ADJUST_TIMESTAMP", 1, crossing_pts_ms=901_000), _overlay_action("confirm", "CONFIRM_EVENT", 2)],
        session_context=context,
    )
    assert adjusted["effective_crossing_pts_ms"] == 901_000
    assert interval_index_for_event(adjusted, context) == 1
    human = project_human_added_event(
        _overlay_action(
            "human-add",
            "ADD_MISSED_EVENT",
            1,
            counting_line_id="line-main",
            counting_line_name="Main line",
            direction="A_TO_B",
            crossing_pts_ms=1_200_000,
            engineering_class="UNKNOWN",
            evidence_reference="fixture-frame-12",
            manual_observation_note="operator observed the crossing",
        ),
        session_context={"runtime_configuration_hash": "runtime", "scene_revision": "1", "taxonomy_revision": "taxonomy"},
    )
    assert human["origin"] == "HUMAN_ADDED"
    assert human["source_event_id"] is None
    assert human["human_add_action_id"] == "human-add"
    assert human["evidence"]["evidence_reference"] == "fixture-frame-12"


def test_reconciliation_diagnostics_cover_count_dimensions_and_duplicate_integrity() -> None:
    context = {
        "analysis_start_pts_ms": 0,
        "analysis_end_pts_ms": 60_000,
        "interval_origin_pts_ms": 0,
        "geometry": {"counting_lines": [{"id": "line-main", "active": True}]},
    }
    rows = [
        {
            "reviewed_event_id": "r1",
            "origin": "AUTOMATIC",
            "source_event_id": "event-1",
            "review_status": "CONFIRMED",
            "effective_line_id": "line-main",
            "effective_direction": "WRONG",
            "effective_class": "not-a-class",
            "effective_crossing_pts_ms": 90_000,
            "duplicate_of": "missing-event",
        },
        {
            "reviewed_event_id": "human-1",
            "origin": "HUMAN_ADDED",
            "source_event_id": "should-be-null",
            "human_add_action_id": None,
            "review_status": "UNREVIEWED",
            "effective_line_id": "line-missing",
            "effective_direction": "A_TO_B",
            "effective_class": "PASSENGER_VEHICLE",
            "effective_crossing_pts_ms": 1_000,
            "evidence": {},
        },
    ]
    report = reconcile_reviewed_events(rows, automatic_total=2, context=context, expected_event_ids=["event-1", "event-2"])
    codes = {item["code"] for item in report["diagnostics"]}
    assert report["status"] == "FAILED"
    assert {
        "AUTOMATIC_EVENT_LEDGER_MISMATCH", "MISSING_OR_EXTRA_AUTOMATIC_EVENT", "INVALID_COUNTING_LINE",
        "INVALID_DIRECTION", "UNSUPPORTED_CLASS", "EVENT_OUTSIDE_ANALYSIS_INTERVAL", "DUPLICATE_TARGET_NOT_FOUND",
        "INVALID_HUMAN_ADDED_PROVENANCE", "HUMAN_ADDED_EVIDENCE_MISSING", "INVALID_HUMAN_ADDED_STATUS",
    } <= codes


def test_projection_is_reused_and_queue_is_sql_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client, project, run, session = _client(monkeypatch, tmp_path)
    session_id = session["id"]
    first = client.get(f"/api/v1/review-sessions/{session_id}/queue?limit=10&offset=0&order_by=event_id")
    first.raise_for_status()
    service = client.app.state.foundation_service.review_service
    connection = service.connection
    projection_id = first.json()["reviewed_projection_revision_id"]
    connection.execute("DELETE FROM reviewed_events WHERE reviewed_projection_revision_id = ? AND source_event_id IS NULL", (projection_id,))
    fixture_rows = []
    for index in range(5_000):
        fixture_rows.append(
            (
                f"fixture-row-{index:05d}", f"fixture-{index:05d}", projection_id, session_id, "AUTOMATIC", None, None,
                "line-a" if index % 2 == 0 else "line-b", "Fixture A" if index % 2 == 0 else "Fixture B",
                "A_TO_B" if index % 3 else "B_TO_A", index * 100, "BUS" if index % 5 == 0 else "OTHER",
                "REVIEW_CORRECTED" if index % 7 == 0 else "CONFIRMED" if index % 11 == 0 else "UNREVIEWED",
                "CONFIRMED" if index % 11 == 0 else "UNREVIEWED", f"fixture-{max(index - 1, 0):05d}" if index % 13 == 0 and index else None,
                "[\"class\"]" if index % 7 == 0 else "[]", "[]", json.dumps({"benchmark_error_category": "MISS" if index % 17 == 0 else "OK"}),
                None, "1", "1", "CURRENT", "2026-01-01T00:00:00+00:00", (index % 100) / 100, "MISS" if index % 17 == 0 else "OK",
                1 if index % 7 == 0 else 0, 1 if index % 13 == 0 and index else 0,
            )
        )
    connection.executemany(
        """
        INSERT INTO reviewed_events(
          id, reviewed_event_id, reviewed_projection_revision_id, review_session_id,
          origin, source_event_id, human_add_action_id, effective_line_id, effective_line_name,
          effective_direction, effective_crossing_pts_ms, effective_class, classification_status,
          review_status, duplicate_of, corrected_fields_json, effective_action_ids_json, evidence_json,
          runtime_configuration_hash, scene_revision, taxonomy_revision, stale_status, created_at,
          event_confidence, benchmark_error_category, has_correction, is_duplicate
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        fixture_rows,
    )
    connection.commit()
    service._projection_events = lambda _projection_id: pytest.fail("queue/evidence must not materialize the full projection")

    pages = []
    for offset in range(0, 5_001, 200):
        response = client.get(f"/api/v1/review-sessions/{session_id}/queue?limit=200&offset={offset}&order_by=event_id")
        response.raise_for_status()
        body = response.json()
        assert body["total"] == 5_001
        assert body["reviewed_projection_revision_id"] == projection_id
        pages.append([item["reviewed_event_id"] for item in body["items"]])
    page_ids = [event_id for page in pages for event_id in page]
    assert len(page_ids) == 5_001
    assert len(set(page_ids)) == 5_001
    assert pages[0] == sorted(pages[0])
    filtered = client.get(
        f"/api/v1/review-sessions/{session_id}/queue?limit=25&line=line-a&direction=A_TO_B&engineering_class=BUS&review_status=CONFIRMED&corrected=false&duplicate=false&start_pts_ms=0&end_pts_ms=500000&confidence_min=0.5&benchmark_error_category=OK"
    )
    filtered.raise_for_status()
    assert all(item["effective_line_id"] == "line-a" for item in filtered.json()["items"])
    assert all(item["effective_direction"] == "A_TO_B" for item in filtered.json()["items"])
    assert all(item["effective_class"] == "BUS" for item in filtered.json()["items"])
    assert all(item["review_status"] == "CONFIRMED" for item in filtered.json()["items"])
    evidence = client.get(f"/api/v1/review-sessions/{session_id}/events/fixture-00042/evidence")
    evidence.raise_for_status()
    assert evidence.json()["reviewed_event"]["reviewed_event_id"] == "fixture-00042"
    assert connection.execute("SELECT COUNT(*) FROM reviewed_projection_revisions WHERE review_session_id = ?", (session_id,)).fetchone()[0] == 1


def test_material_action_creates_one_new_projection_and_certification_content_hash_is_stable(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client, project, run, session = _client(monkeypatch, tmp_path)
    session_id = session["id"]
    first_queue = client.get(f"/api/v1/review-sessions/{session_id}/queue")
    first_queue.raise_for_status()
    event_id = first_queue.json()["items"][0]["source_event_id"]
    service = client.app.state.foundation_service.review_service
    def projection_count() -> int:
        return service.connection.execute(
            "SELECT COUNT(*) FROM reviewed_projection_revisions WHERE review_session_id = ?", (session_id,)
        ).fetchone()[0]
    assert projection_count() == 1
    second_queue = client.get(f"/api/v1/review-sessions/{session_id}/queue")
    second_queue.raise_for_status()
    assert projection_count() == 1
    assert client.get(f"/api/v1/review-sessions/{session_id}/events/{event_id}/evidence").status_code == 200
    assert client.get(f"/api/v1/review-sessions/{session_id}/progress").status_code == 200
    assert projection_count() == 1
    action = client.post(
        f"/api/v1/review-sessions/{session_id}/actions",
        json={"action_type": "CONFIRM_EVENT", "target_event_id": event_id, "reason_code": "source_checked", "expected_review_revision": 0},
    )
    action.raise_for_status()
    changed_queue = client.get(f"/api/v1/review-sessions/{session_id}/queue")
    changed_queue.raise_for_status()
    assert projection_count() == 2
    assert client.get(f"/api/v1/review-sessions/{session_id}/queue").json()["reviewed_projection_revision_id"] == changed_queue.json()["reviewed_projection_revision_id"]
    assert projection_count() == 2


def test_export_is_disclosed_stale_after_material_review_action(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client, project, run, session = _client(monkeypatch, tmp_path)
    session_id = session["id"]
    event_id = client.get(f"/api/v1/review-sessions/{session_id}/queue").json()["items"][0]["source_event_id"]
    action = client.post(
        f"/api/v1/review-sessions/{session_id}/actions",
        json={"action_type": "CONFIRM_EVENT", "target_event_id": event_id, "reason_code": "source_checked", "expected_review_revision": 0},
    )
    action.raise_for_status()
    completed = client.post(f"/api/v1/review-sessions/{session_id}/complete", json={"completed_by": "reviewer", "expected_review_revision": 1})
    completed.raise_for_status()
    certification = client.post(f"/api/v1/review-sessions/{session_id}/certification", json={"certified_by": "reviewer"})
    certification.raise_for_status()
    cert_id = certification.json()["id"]
    assert certification.json()["certification_content_hash"] != certification.json()["certification_hash"]
    assert certification.json()["certification_content_hash_status"] == "VERIFIED_CONTENT_HASH"
    export = client.post(f"/api/v1/certifications/{cert_id}/exports", json={"format": "CSV", "language": "en", "client_request_id": "stale-source-export"})
    export.raise_for_status()
    original = export.json()
    original_bytes = client.get(f"/api/v1/export-artifacts/{original['artifact']['artifact_id']}/download").content
    later_action = client.post(
        f"/api/v1/review-sessions/{session_id}/actions",
        json={"action_type": "CHANGE_CLASS", "target_event_id": event_id, "payload": {"engineering_class": "BUS"}, "reason_code": "new_evidence", "expected_review_revision": 1},
    )
    later_action.raise_for_status()
    historical = client.get(f"/api/v1/exports/{original['id']}")
    historical.raise_for_status()
    assert historical.json()["artifact_generation_status"] == "COMPLETED"
    assert historical.json()["source_certification_status"] == "STALE"
    assert historical.json()["effective_export_status"] == "STALE_SOURCE"
    assert client.get(f"/api/v1/export-artifacts/{historical.json()['artifact']['artifact_id']}/download").content == original_bytes
    blocked = client.post(f"/api/v1/certifications/{cert_id}/exports", json={"format": "JSON", "language": "en", "client_request_id": "after-stale"})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "CERTIFICATION_NOT_CURRENT"


def test_exports_include_complete_frozen_action_history_over_public_page_cap(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client, _project, _run, session = _client(monkeypatch, tmp_path)
    session_id = session["id"]
    event = client.get(f"/api/v1/review-sessions/{session_id}/queue").json()["items"][0]
    event_id = event["source_event_id"]

    _seed_fixture_action_history(client, session_id, event_id, start_order=1, count=750)

    public_first_page = client.get(f"/api/v1/review-sessions/{session_id}/actions?limit=500&offset=0")
    public_second_page = client.get(f"/api/v1/review-sessions/{session_id}/actions?limit=500&offset=500")
    public_first_page.raise_for_status()
    public_second_page.raise_for_status()
    assert len(public_first_page.json()) == 500
    assert len(public_second_page.json()) == 250

    completed = client.post(
        f"/api/v1/review-sessions/{session_id}/complete",
        json={"completed_by": "fixture-reviewer", "expected_review_revision": 750},
    )
    completed.raise_for_status()
    certification = client.post(
        f"/api/v1/review-sessions/{session_id}/certification",
        json={"certified_by": "fixture-reviewer"},
    )
    certification.raise_for_status()
    certification_id = certification.json()["id"]
    assert certification.json()["review_revision"] == 750
    assert certification.json()["certification_content_hash_status"] == "VERIFIED_CONTENT_HASH"

    xlsx_export = client.post(
        f"/api/v1/certifications/{certification_id}/exports",
        json={"format": "XLSX", "language": "en", "client_request_id": "large-history-xlsx"},
    )
    xlsx_export.raise_for_status()
    xlsx_payload = xlsx_export.json()
    xlsx_bytes = client.get(f"/api/v1/export-artifacts/{xlsx_payload['artifact']['artifact_id']}/download").content
    with zipfile.ZipFile(io.BytesIO(xlsx_bytes)) as workbook:
        adjustments_xml = workbook.read("xl/worksheets/sheet4.xml")
    assert adjustments_xml.count(b"<row ") == 751
    assert b"fixture-action-0001" in adjustments_xml
    assert b"fixture-action-0500" in adjustments_xml
    assert b"fixture-action-0501" in adjustments_xml
    assert b"fixture-action-0750" in adjustments_xml

    json_export = client.post(
        f"/api/v1/certifications/{certification_id}/exports",
        json={"format": "JSON", "language": "en", "client_request_id": "large-history-json"},
    )
    json_export.raise_for_status()
    json_payload = json_export.json()
    json_bytes = client.get(f"/api/v1/export-artifacts/{json_payload['artifact']['artifact_id']}/download").content
    audit = json.loads(json_bytes)
    references = audit["review_action_references"]
    assert audit["review_action_revision_cutoff"] == 750
    assert len(references) == 750
    assert len({reference["id"] for reference in references}) == 750
    assert [reference["action_order"] for reference in references] == list(range(1, 751))
    assert references[0]["id"] == "fixture-action-0001"
    assert references[499]["id"] == "fixture-action-0500"
    assert references[500]["id"] == "fixture-action-0501"
    assert references[-1]["id"] == "fixture-action-0750"
    reversal = next(reference for reference in references if reference["action_type"] == "REVERSE_ACTION")
    assert reversal["reverses_action_id"] == "fixture-action-0002"

    _seed_fixture_action_history(client, session_id, event_id, start_order=751, count=1)
    historical = client.get(f"/api/v1/exports/{json_payload['id']}")
    historical.raise_for_status()
    assert historical.json()["effective_export_status"] == "STALE_SOURCE"
    assert historical.json()["artifact_sha256"] == json_payload["artifact_sha256"]
    assert client.get(f"/api/v1/export-artifacts/{json_payload['artifact']['artifact_id']}/download").content == json_bytes
    blocked = client.post(
        f"/api/v1/certifications/{certification_id}/exports",
        json={"format": "JSON", "language": "en", "client_request_id": "large-history-after-stale"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "CERTIFICATION_NOT_CURRENT"


def test_export_idempotency_deterministic_bytes_and_failed_generation_cleanup(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client, _project, _run, session = _client(monkeypatch, tmp_path)
    session_id = session["id"]
    event_id = client.get(f"/api/v1/review-sessions/{session_id}/queue").json()["items"][0]["source_event_id"]
    action = client.post(
        f"/api/v1/review-sessions/{session_id}/actions",
        json={"action_type": "CONFIRM_EVENT", "target_event_id": event_id, "reason_code": "source_checked", "expected_review_revision": 0},
    )
    action.raise_for_status()
    completed = client.post(f"/api/v1/review-sessions/{session_id}/complete", json={"completed_by": "reviewer", "expected_review_revision": 1})
    completed.raise_for_status()
    certification = client.post(f"/api/v1/review-sessions/{session_id}/certification", json={"certified_by": "reviewer"})
    certification.raise_for_status()
    cert_id = certification.json()["id"]

    first = client.post(
        f"/api/v1/certifications/{cert_id}/exports",
        json={"format": "JSON", "language": "en", "options": {"include_diagnostics": True}, "client_request_id": "json-idem"},
    )
    first.raise_for_status()
    replay = client.post(
        f"/api/v1/certifications/{cert_id}/exports",
        json={"format": "JSON", "language": "en", "options": {"include_diagnostics": True}, "client_request_id": "json-idem"},
    )
    replay.raise_for_status()
    assert replay.json()["id"] == first.json()["id"]
    assert client.get(f"/api/v1/export-artifacts/{replay.json()['artifact']['artifact_id']}/download").content == client.get(
        f"/api/v1/export-artifacts/{first.json()['artifact']['artifact_id']}/download"
    ).content
    conflict = client.post(
        f"/api/v1/certifications/{cert_id}/exports",
        json={"format": "JSON", "language": "en", "options": {"include_diagnostics": False}, "client_request_id": "json-idem"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"

    service = client.app.state.foundation_service.review_service
    existing_artifacts = set(service.artifacts.root.rglob("*.json"))

    def fail_json(*_args, **_kwargs):
        raise RuntimeError("fixture export failure")

    monkeypatch.setattr(service, "_audit_json_bytes", fail_json)
    with pytest.raises(RuntimeError, match="fixture export failure"):
        client.post(
            f"/api/v1/certifications/{cert_id}/exports",
            json={"format": "JSON", "language": "en", "client_request_id": "failed-export"},
        )
    failed = service.connection.execute(
        "SELECT * FROM export_revisions WHERE client_request_id = ?", ("failed-export",)
    ).fetchone()
    assert failed is not None
    assert failed["status"] == "FAILED"
    assert failed["artifact_generation_status"] == "FAILED"
    assert failed["artifact_sha256"] is None
    assert service.connection.execute("SELECT 1 FROM export_artifacts WHERE export_revision_id = ?", (failed["id"],)).fetchone() is None
    assert set(service.artifacts.root.rglob("*.json")) == existing_artifacts


def test_certification_is_blocked_by_failed_reconciliation_and_partial_scope_is_disclosed(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client, project, run, session = _client(monkeypatch, tmp_path)
    session_id = session["id"]
    event_id = client.get(f"/api/v1/review-sessions/{session_id}/queue").json()["items"][0]["source_event_id"]
    service = client.app.state.foundation_service.review_service
    action = client.post(
        f"/api/v1/review-sessions/{session_id}/actions",
        json={"action_type": "CONFIRM_EVENT", "target_event_id": event_id, "reason_code": "source_checked", "expected_review_revision": 0},
    )
    action.raise_for_status()
    complete = client.post(f"/api/v1/review-sessions/{session_id}/complete", json={"completed_by": "reviewer", "expected_review_revision": 1})
    complete.raise_for_status()
    projection = client.post(f"/api/v1/review-sessions/{session_id}/projections")
    projection.raise_for_status()
    projection_id = projection.json()["id"]
    session_row = service.connection.execute("SELECT * FROM review_sessions WHERE id = ?", (session_id,)).fetchone()
    service.connection.execute(
        """
        INSERT INTO review_reconciliation_runs(
          id, review_session_id, reviewed_projection_revision_id, review_revision,
          status, equation_json, by_line_json, by_direction_json, by_class_json,
          by_interval_json, diagnostics_json, content_hash, created_at
        ) VALUES (?, ?, ?, ?, 'FAILED', '{}', '{}', '{}', '{}', '{}', ?, ?, ?)
        """,
        ("failed-reconciliation", session_id, projection_id, session_row["review_revision"], "[{\"code\":\"fixture\"}]", "failed-reconciliation-hash", "9999-01-01T00:00:00+00:00"),
    )
    service.connection.commit()
    certification = client.post(f"/api/v1/review-sessions/{session_id}/certification", json={"certified_by": "reviewer"})
    assert certification.status_code == 409
    assert certification.json()["detail"]["code"] == "RECONCILIATION_FAILED"

    diagnostic = client.post(
        f"/api/v1/projects/{project['id']}/review-sessions",
        json={"processing_run_id": run["id"], "review_scope_type": "DIAGNOSTIC_SUBSET", "review_scope_filter": {"event_ids": [event_id]}, "created_by": "diagnostic"},
    )
    diagnostic.raise_for_status()
    diagnostic_id = diagnostic.json()["id"]
    diagnostic_action = client.post(
        f"/api/v1/review-sessions/{diagnostic_id}/actions",
        json={"action_type": "CONFIRM_EVENT", "target_event_id": event_id, "reason_code": "diagnostic_source_checked", "expected_review_revision": 0},
    )
    diagnostic_action.raise_for_status()
    diagnostic_complete = client.post(f"/api/v1/review-sessions/{diagnostic_id}/complete", json={"completed_by": "reviewer", "expected_review_revision": 1})
    diagnostic_complete.raise_for_status()
    diagnostic_cert = client.post(f"/api/v1/review-sessions/{diagnostic_id}/certification", json={"certified_by": "reviewer"})
    diagnostic_cert.raise_for_status()
    assert diagnostic_cert.json()["review_scope"]["partial"] is True
    assert "partial" in diagnostic_cert.json()["qualification_disclosure"].lower()


def test_action_idempotency_conflicts_and_optimistic_retry(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client, project, run, session = _client(monkeypatch, tmp_path)
    session_id = session["id"]
    event_id = client.get(f"/api/v1/review-sessions/{session_id}/queue").json()["items"][0]["source_event_id"]
    too_long = client.post(
        f"/api/v1/review-sessions/{session_id}/actions",
        json={
            "action_type": "ADD_NOTE",
            "target_event_id": event_id,
            "payload": {"note": "x"},
            "comment": "x" * 2001,
            "expected_review_revision": 0,
        },
    )
    assert too_long.status_code == 422
    original = {
        "action_type": "CONFIRM_EVENT",
        "target_event_id": event_id,
        "reason_code": "checked_against_source",
        "reviewer_id": "reviewer-a",
        "expected_review_revision": 0,
        "client_request_id": "idem-action-1",
    }
    first = client.post(f"/api/v1/review-sessions/{session_id}/actions", json=original)
    first.raise_for_status()
    replay = client.post(f"/api/v1/review-sessions/{session_id}/actions", json=original)
    replay.raise_for_status()
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["idempotent_replay"] is True

    for changed in (
        {"target_event_id": "different-target"},
        {"action_type": "ADD_NOTE"},
        {"payload": {"note": "different payload"}},
        {"reason_code": "different reason"},
    ):
        conflict = client.post(f"/api/v1/review-sessions/{session_id}/actions", json={**original, **changed})
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"

    stale_client = client.post(
        f"/api/v1/review-sessions/{session_id}/actions",
        json={"action_type": "ADD_NOTE", "target_event_id": event_id, "payload": {"note": "stale reviewer"}, "comment": "stale reviewer", "expected_review_revision": 0, "client_request_id": "stale-client"},
    )
    assert stale_client.status_code == 409
    assert stale_client.json()["detail"]["code"] == "REVIEW_REVISION_CONFLICT"
    assert stale_client.json()["detail"]["actions_since_expected"][0]["action_type"] == "CONFIRM_EVENT"
    retry = client.post(
        f"/api/v1/review-sessions/{session_id}/actions",
        json={"action_type": "ADD_NOTE", "target_event_id": event_id, "payload": {"note": "stale reviewer"}, "comment": "stale reviewer", "expected_review_revision": 1, "client_request_id": "stale-client-retry"},
    )
    retry.raise_for_status()
    assert retry.json()["action_type"] == "ADD_NOTE"


def _apply_migrations_through(connection, version: str) -> None:
    connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    applied = {row["version"] for row in connection.execute("SELECT version FROM schema_migrations")}
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if migration.stem > version:
            break
        if migration.stem in applied:
            continue
        connection.executescript(migration.read_text(encoding="utf-8"))
        connection.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, datetime('now'))", (migration.stem,))
    connection.commit()


def _insert_migration_certification_fixture(connection, certification_id: str, certification_hash: str, *, content_hash: str | None = None) -> None:
    values = {
        "id": certification_id,
        "certification_revision_id": certification_id,
        "project_id": "migration-project",
        "review_session_id": "migration-session",
        "reviewed_projection_revision_id": "migration-projection",
        "review_scope_json": "{}",
        "review_revision": 7,
        "reconciliation_revision_id": "migration-reconciliation",
        "source_id": "migration-source",
        "source_fingerprint_sha256": "migration-source-fingerprint",
        "processing_run_id": "migration-run",
        "processing_configuration_revision_id": None,
        "runtime_configuration_hash": "migration-runtime",
        "scene_revision": "scene-1",
        "taxonomy_revision": "taxonomy-1",
        "mapping_revision": "mapping-1",
        "classification_policy_revision": "classification-1",
        "benchmark_reference_json": "{}",
        "qualification_disclosure": "Fixture qualification disclosure.",
        "rights_disclosure": "Fixture rights disclosure.",
        "review_summary_json": "{}",
        "certified_by": "migration-fixture",
        "certified_at": "2026-01-01T07:00:00+00:00",
        "certification_hash": certification_hash,
        "status": "CERTIFIED",
        "stale_status": "CURRENT",
    }
    if content_hash is not None:
        values["certification_content_hash"] = content_hash
    columns = list(values)
    connection.execute(
        f"INSERT INTO certification_revisions({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        [values[column] for column in columns],
    )


def test_migration_015_and_016_are_additive_and_fail_closed_for_legacy_hashes() -> None:
    connection = connect()
    connection.execute("PRAGMA foreign_keys = OFF")
    _apply_migrations_through(connection, "014_milestone_6e_review_certification_export")
    before = {row["name"] for row in connection.execute("PRAGMA table_info(export_revisions)").fetchall()}
    assert "reviewed_projection_revision_id" not in before
    _insert_migration_certification_fixture(connection, "legacy-014", "legacy-attestation-014")
    connection.commit()

    _apply_migrations_through(connection, "015_milestone_6e_acceptance_remediation")
    _insert_migration_certification_fixture(connection, "copied-015", "legacy-attestation-015", content_hash="legacy-attestation-015")
    _insert_migration_certification_fixture(connection, "verified-015", "attestation-new", content_hash="deterministic-content-new")
    connection.commit()

    migrate(connection)
    after = {row["name"] for row in connection.execute("PRAGMA table_info(export_revisions)").fetchall()}
    assert {
        "reviewed_projection_revision_id", "export_schema_revision", "language", "options_json",
        "artifact_manifest_json", "content_hash", "artifact_generation_status", "artifact_sha256",
        "row_count", "sheet_count",
    } <= after
    event_columns = {row["name"] for row in connection.execute("PRAGMA table_info(reviewed_events)").fetchall()}
    assert {"event_confidence", "benchmark_error_category", "has_correction", "is_duplicate"} <= event_columns
    certification_columns = {row["name"] for row in connection.execute("PRAGMA table_info(certification_revisions)").fetchall()}
    assert {"certification_content_hash", "certification_content_hash_status"} <= certification_columns
    legacy = connection.execute("SELECT * FROM certification_revisions WHERE id = 'legacy-014'").fetchone()
    copied = connection.execute("SELECT * FROM certification_revisions WHERE id = 'copied-015'").fetchone()
    verified = connection.execute("SELECT * FROM certification_revisions WHERE id = 'verified-015'").fetchone()
    assert legacy["certification_hash"] == "legacy-attestation-014"
    assert legacy["certification_content_hash"] == ""
    assert legacy["certification_content_hash_status"] == "LEGACY_UNRESOLVED"
    assert copied["certification_hash"] == "legacy-attestation-015"
    assert copied["certification_content_hash"] == ""
    assert copied["certification_content_hash_status"] == "LEGACY_UNRESOLVED"
    assert verified["certification_content_hash"] == "deterministic-content-new"
    assert verified["certification_content_hash_status"] == "VERIFIED_CONTENT_HASH"
    indexes = {row["name"] for row in connection.execute("PRAGMA index_list(reviewed_events)").fetchall()}
    assert {"idx_reviewed_events_queue_order", "idx_reviewed_events_line_direction_class"} <= indexes
    cert_indexes = {row["name"] for row in connection.execute("PRAGMA index_list(certification_revisions)").fetchall()}
    assert "idx_certifications_content_hash_status" in cert_indexes
    migrate(connection)
