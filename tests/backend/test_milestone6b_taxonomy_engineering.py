from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from apps.backend.app.db import connect, migrate
from apps.backend.app.engineering_outputs import (
    ClassificationStatus,
    EngineeringClass,
    aggregate_engineering_events,
    assign_interval,
    build_intervals,
    canonical_direction,
    classification_consistency_error,
    classify_track_evidence,
    reconcile_engineering_counts,
    source_time_semantics,
)
from apps.backend.app.services import FoundationService
from apps.backend.app.main import create_app
from apps.backend.app.schemas import SourceCreate


def evidence(raw_class: str, confidence: float | None, pts: int) -> dict:
    return {"native_class": raw_class, "raw_class_id": {"car": 2, "bus": 5, "truck": 7}.get(raw_class), "confidence": confidence, "pts_ms": pts}


def engineering_fixture(
    events: list[dict],
    track_evidence: dict[str, dict] | None = None,
) -> tuple[sqlite3.Connection, FoundationService, str, dict]:
    connection = connect()
    migrate(connection)
    service = FoundationService(connection)
    project = service.create_project("6B fixture", "Bangkok", "intersection", "en")
    service.register_source(
        SourceCreate(
            project_id=project["id"],
            file_name="fixture.mp4",
            fingerprint_sha256="6b-fixture-source",
            source_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            timezone_name="Asia/Bangkok",
            analysis_start_pts_ms=0,
            analysis_end_pts_ms=900_000,
        )
    )
    scene = service.create_scene(project["id"], "intersection")
    run_id = service.submit_processing_job(
        project["id"],
        fixture_id="6b-fixture",
        expected_scene_version=scene["version"],
        auto_start=False,
    )["id"]
    for track_id, compact in (track_evidence or {}).items():
        connection.execute(
            "INSERT INTO track_summaries(id, run_id, track_id, summary_json, evidence_schema_version) VALUES (?, ?, ?, ?, ?)",
            (f"summary-{track_id}", run_id, track_id, json.dumps({"classification_evidence": compact}, sort_keys=True), "compact-track-evidence-v1"),
        )
    for index, item in enumerate(events):
        connection.execute(
            """
            INSERT INTO auto_count_events(
              id, run_id, technical_key, pts_ms, track_id, rule_id, object_domain,
              classification, movement, confidence, qc_state, created_at,
              raw_class_id, raw_class_name, provisional_class
            ) VALUES (?, ?, ?, ?, ?, 'line_main', ?, ?, ?, ?, 'needs_review', 'now', ?, ?, ?)
            """,
            (
                item.get("id", f"event-{index}"),
                run_id,
                item.get("technical_key", f"technical-{index}"),
                item.get("pts_ms", index * 1000),
                item.get("track_id", f"track-{index}"),
                item.get("object_domain", "vehicle"),
                item.get("classification", "unknown"),
                item.get("movement", "A_TO_B"),
                item.get("confidence", 0.9),
                item.get("raw_class_id"),
                item.get("raw_class_name"),
                item.get("provisional_class"),
            ),
        )
    result = service._create_engineering_result(run_id)
    connection.commit()
    return connection, service, run_id, result


def test_taxonomy_seed_is_versioned_and_immutable() -> None:
    connection = connect()
    migrate(connection)
    FoundationService(connection)
    taxonomy = connection.execute("SELECT * FROM taxonomy_revisions WHERE revision = 'pilot-observable-taxonomy-v1'").fetchone()
    assert taxonomy is not None
    classes = {row["code"]: row["capability_state"] for row in connection.execute("SELECT * FROM taxonomy_classes")}
    assert classes["HEAVY_VEHICLE_UNSPECIFIED"] == "PILOT_OBSERVABLE"
    assert "TIMS" not in classes
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE taxonomy_revisions SET status = 'RETIRED' WHERE id = ?", (taxonomy["id"],))


def test_track_vote_maps_supported_and_unsupported_raw_classes() -> None:
    empty = classify_track_evidence([])
    assert empty.engineering_class == EngineeringClass.UNKNOWN.value
    assert empty.classification_status == ClassificationStatus.UNKNOWN.value

    decision = classify_track_evidence([evidence("truck", 0.9, 0), evidence("truck", 0.8, 1000)])
    assert decision.track_voted_raw_class_name == "truck"
    assert decision.engineering_class == EngineeringClass.HEAVY_VEHICLE_UNSPECIFIED.value
    assert decision.classification_status == ClassificationStatus.PROVISIONAL.value

    unsupported = classify_track_evidence([evidence("train", 0.9, 0), evidence("train", 0.8, 1000)])
    assert unsupported.engineering_class == EngineeringClass.UNKNOWN.value
    assert unsupported.classification_status == ClassificationStatus.UNSUPPORTED_RAW_CLASS.value

    other = classify_track_evidence([evidence("other", 0.9, 0), evidence("other", 0.8, 1000)])
    assert other.engineering_class == EngineeringClass.OTHER.value
    assert other.classification_status == ClassificationStatus.OTHER.value


def test_vote_handles_ties_weak_evidence_and_bad_confidence_conservatively() -> None:
    assert classify_track_evidence([evidence("car", 0.8, 0)]).classification_status == ClassificationStatus.INSUFFICIENT_EVIDENCE.value
    tie = classify_track_evidence([evidence("car", 0.8, 0), evidence("bus", 0.8, 1000)])
    assert tie.classification_status == ClassificationStatus.AMBIGUOUS.value
    assert tie.engineering_class == EngineeringClass.AMBIGUOUS.value

    missing = classify_track_evidence([evidence("car", None, 0), evidence("car", None, 1000), evidence("bus", None, 2000)])
    assert missing.classification_status == ClassificationStatus.CONFIRMED_MAPPING.value
    assert "missing_confidence_excluded_from_weighted_vote" in missing.warnings

    invalid = classify_track_evidence([evidence("car", float("nan"), 0), evidence("car", 0.8, 1000)])
    assert invalid.classification_status == ClassificationStatus.CONFIRMED_MAPPING.value
    assert invalid.confidence_invalid_count == 1
    zero = classify_track_evidence([evidence("car", 0.0, 0), evidence("car", 0.0, 1000)])
    assert zero.classification_status == ClassificationStatus.CONFIRMED_MAPPING.value
    assert "zero_confidence_fallback_to_unweighted_vote" in zero.warnings


def test_vote_is_stable_when_observation_order_changes() -> None:
    ordered = [evidence("car", 0.7, 0), evidence("car", 0.9, 1000), evidence("bus", 0.2, 2000)]
    forward = classify_track_evidence(ordered)
    reverse = classify_track_evidence(list(reversed(ordered)))
    assert forward == reverse


def test_time_semantics_and_half_open_partial_intervals() -> None:
    source = {
        "recording_time_configured": 1,
        "source_started_at": "2026-01-01T23:50:00+00:00",
        "timezone_name": "Asia/Bangkok",
        "source_offset_ms": 0,
        "analysis_start_pts_ms": 0,
        "analysis_end_pts_ms": 1_001_000,
        "interval_origin_pts_ms": 0,
    }
    assert source_time_semantics(source, 0).status == "CONFIRMED"
    assert source_time_semantics(source, 600_000).absolute_event_time == "2026-01-02T07:00:00+07:00"
    intervals = build_intervals(source)
    assert len(intervals) == 2
    assert intervals[-1]["partial"] is True
    assert assign_interval(intervals, 899_999) == 0
    assert assign_interval(intervals, 900_000) == 1
    assert assign_interval(intervals, 1_001_000) is None
    unconfigured = dict(source, recording_time_configured=0)
    assert source_time_semantics(unconfigured, 1000).status == "UNCONFIGURED"
    invalid = dict(source, timezone_name="Not/IANA")
    assert source_time_semantics(invalid, 1000).status == "INVALID_CONFIGURATION"


def test_structured_multi_line_aggregation_and_reconciliation() -> None:
    intervals = [
        {"index": 0, "source_relative_start_pts_ms": 0, "source_relative_end_pts_ms": 900_000, "display_label": "0"},
        {"index": 1, "source_relative_start_pts_ms": 900_000, "source_relative_end_pts_ms": 1_800_000, "display_label": "1"},
    ]
    events = [
        {"id": "e1", "technical_key": "one", "line_id": "line-a", "direction": "A_TO_B", "engineering_class": "PASSENGER_VEHICLE", "event_pts_ms": 0, "track_id": "t1"},
        {"id": "e2", "technical_key": "two", "line_id": "line-a", "direction": "B_TO_A", "engineering_class": "AMBIGUOUS", "event_pts_ms": 900_000, "track_id": "t1"},
        {"id": "e3", "technical_key": "three", "line_id": "line-b", "direction": "A_TO_B", "engineering_class": "UNKNOWN", "event_pts_ms": 1_000_000, "track_id": "t1"},
    ]
    summary = aggregate_engineering_events(events, intervals, configured_lines=[{"line_id": "line-a", "label": "A"}, {"line_id": "line-b", "label": "B"}])
    assert summary["overall_event_total"] == 3
    assert {row["line_id"] for row in summary["line_totals"]} == {"line-a", "line-b"}
    assert sum(row["count"] for row in summary["interval_rows"]) == 3
    report = reconcile_engineering_counts(events, summary)
    assert report["status"] == "STRUCTURALLY_VALID"
    corrupted = dict(summary, line_totals=[dict(summary["line_totals"][0], total=99), *summary["line_totals"][1:]])
    assert reconcile_engineering_counts(events, corrupted)["engineering_ready"] is False


def test_canonical_directions_are_strict_and_invalid_events_are_reconciled() -> None:
    assert canonical_direction("A_TO_B") == "A_TO_B"
    assert canonical_direction("B_TO_A") == "B_TO_A"
    assert canonical_direction("northbound") is None
    assert canonical_direction("a_to_b") is None
    assert canonical_direction("") is None
    assert canonical_direction(None) is None

    events = [
        {"id": "valid-a", "technical_key": "valid-a", "line_id": "line-a", "direction": "A_TO_B", "engineering_class": "UNKNOWN", "event_pts_ms": 0},
        {"id": "valid-b", "technical_key": "valid-b", "line_id": "line-a", "direction": "B_TO_A", "engineering_class": "AMBIGUOUS", "event_pts_ms": 1000},
        {"id": "bad-word", "technical_key": "bad-word", "line_id": "line-a", "direction": "northbound", "engineering_class": "UNKNOWN", "event_pts_ms": 2000},
        {"id": "bad-case", "technical_key": "bad-case", "line_id": "line-a", "direction": "a_to_b", "engineering_class": "UNKNOWN", "event_pts_ms": 3000},
        {"id": "bad-empty", "technical_key": "bad-empty", "line_id": "line-a", "direction": "", "engineering_class": "UNKNOWN", "event_pts_ms": 4000},
        {"id": "bad-null", "technical_key": "bad-null", "line_id": "line-a", "direction": None, "engineering_class": "UNKNOWN", "event_pts_ms": 5000},
    ]
    intervals = [{"index": 0, "source_relative_start_pts_ms": 0, "source_relative_end_pts_ms": 900_000, "display_label": "0"}]
    summary = aggregate_engineering_events(events, intervals)
    assert summary["overall_event_total"] == 2
    assert {event["id"] for event in summary["events"]} == {"valid-a", "valid-b"}
    assert {item["event_id"] for item in summary["invalid_direction_exclusions"]} == {"bad-word", "bad-case", "bad-empty", "bad-null"}
    assert sum(row["total"] for row in summary["line_totals"]) == 2
    assert sum(row["total"] for row in summary["direction_totals"]) == 2
    report = reconcile_engineering_counts(events, summary)
    assert report["engineering_ready"] is False
    assert report["status"] == "STRUCTURALLY_INVALID"
    assert report["source_event_total"] == 6
    assert any(item["identifier"] == "invalid_direction_exclusions_are_empty" and not item["passed"] for item in report["invariants"])
    assert all(row["direction"] in {"A_TO_B", "B_TO_A"} for row in summary["direction_class_matrix"])


def test_classification_status_and_engineering_class_are_one_consistent_pair() -> None:
    decisions = [
        classify_track_evidence([evidence("car", 0.9, 0), evidence("car", 0.8, 1000)]),
        classify_track_evidence([evidence("truck", 0.9, 0), evidence("truck", 0.8, 1000)]),
        classify_track_evidence([evidence("train", 0.9, 0), evidence("train", 0.8, 1000)]),
        classify_track_evidence([evidence("car", 0.9, 0), evidence("bus", 0.8, 1000)]),
        classify_track_evidence([evidence("car", 0.9, 0)]),
        classify_track_evidence([]),
    ]
    assert all(classification_consistency_error(item.classification_status, item.engineering_class) is None for item in decisions)
    assert decisions[0].engineering_class == EngineeringClass.PASSENGER_VEHICLE.value
    assert decisions[1].engineering_class == EngineeringClass.HEAVY_VEHICLE_UNSPECIFIED.value
    assert decisions[2].engineering_class == EngineeringClass.UNKNOWN.value
    assert decisions[3].engineering_class == EngineeringClass.AMBIGUOUS.value
    assert decisions[4].engineering_class == EngineeringClass.UNKNOWN.value
    assert decisions[5].engineering_class == EngineeringClass.UNKNOWN.value

    invalid = [
        {"id": "class-bad", "technical_key": "class-bad", "line_id": "line-a", "direction": "A_TO_B", "engineering_class": "PASSENGER_VEHICLE", "classification_status": "INSUFFICIENT_EVIDENCE", "event_pts_ms": 0},
        {"id": "class-good", "technical_key": "class-good", "line_id": "line-a", "direction": "A_TO_B", "engineering_class": "UNKNOWN", "classification_status": "INSUFFICIENT_EVIDENCE", "event_pts_ms": 1000},
    ]
    intervals = [{"index": 0, "source_relative_start_pts_ms": 0, "source_relative_end_pts_ms": 900_000, "display_label": "0"}]
    summary = aggregate_engineering_events(invalid, intervals)
    assert summary["overall_event_total"] == 1
    assert summary["classification_exclusions"][0]["event_id"] == "class-bad"
    report = reconcile_engineering_counts(invalid, summary)
    assert report["engineering_ready"] is False
    assert report["classification_exclusions"][0]["allowed_classes"] == ["UNKNOWN"]


def test_service_excludes_invalid_direction_without_mutating_raw_event() -> None:
    connection, service, run_id, result = engineering_fixture(
        [
            {
                "id": "raw-invalid-direction",
                "technical_key": "raw-invalid-direction",
                "track_id": "track-invalid-direction",
                "movement": "northbound",
                "raw_class_id": 2,
                "raw_class_name": "car",
                "provisional_class": "passenger_vehicle",
                "classification": "car",
            }
        ]
    )
    raw_event = dict(connection.execute("SELECT * FROM auto_count_events WHERE id = 'raw-invalid-direction'").fetchone())
    assert result["engineering_ready"] is False
    assert connection.execute("SELECT COUNT(*) AS count FROM engineering_event_projections WHERE run_id = ?", (run_id,)).fetchone()["count"] == 0
    assert dict(connection.execute("SELECT * FROM auto_count_events WHERE id = 'raw-invalid-direction'").fetchone()) == raw_event
    exclusion = result["reconciliation"]["invalid_direction_exclusions"][0]
    assert exclusion["event_id"] == "raw-invalid-direction"
    assert exclusion["technical_key"] == "raw-invalid-direction"
    assert exclusion["original_direction"] == "northbound"
    assert exclusion["expected_domain"] == ["A_TO_B", "B_TO_A"]
    assert exclusion["resulting_count_difference"] == -1
    assert connection.execute("SELECT COUNT(*) AS count FROM engineering_result_revisions WHERE run_id = ?", (run_id,)).fetchone()["count"] == 1


def test_service_uses_one_observation_as_unknown_and_multi_observation_as_authoritative() -> None:
    _, _, _, one_observation = engineering_fixture(
        [
            {
                "id": "one-observation",
                "technical_key": "one-observation",
                "track_id": "track-one",
                "raw_class_id": 2,
                "raw_class_name": "car",
                "provisional_class": "passenger_vehicle",
                "classification": "car",
            }
        ]
    )
    assert one_observation["engineering_ready"] is True
    one_row = one_observation["summary"]["events"][0]
    assert one_row["classification_status"] == ClassificationStatus.INSUFFICIENT_EVIDENCE.value
    assert one_row["engineering_class"] == EngineeringClass.UNKNOWN.value
    assert one_row["provisional_class"] == "unknown"

    truck_decision = classify_track_evidence([evidence("truck", 0.9, 0), evidence("truck", 0.8, 1000)])
    _, _, _, multi_observation = engineering_fixture(
        [
            {
                "id": "multi-observation",
                "technical_key": "multi-observation",
                "track_id": "track-multi",
                "raw_class_id": 7,
                "raw_class_name": "truck",
                "provisional_class": "heavy_vehicle_unspecified",
                "classification": "truck",
            }
        ],
        {"track-multi": truck_decision.evidence_json()},
    )
    multi_row = multi_observation["summary"]["events"][0]
    assert multi_row["classification_status"] == ClassificationStatus.PROVISIONAL.value
    assert multi_row["engineering_class"] == EngineeringClass.HEAVY_VEHICLE_UNSPECIFIED.value
    assert multi_row["provisional_class"] == "heavy_vehicle_unspecified"


def test_service_handles_unsupported_ambiguous_no_evidence_and_legacy_conservatively() -> None:
    unsupported = classify_track_evidence([evidence("train", 0.9, 0), evidence("train", 0.8, 1000)])
    ambiguous = classify_track_evidence([evidence("car", 0.9, 0), evidence("bus", 0.8, 1000)])
    _, _, _, result = engineering_fixture(
        [
            {"id": "unsupported", "technical_key": "unsupported", "track_id": "track-unsupported", "raw_class_name": "train", "classification": "train"},
            {"id": "ambiguous", "technical_key": "ambiguous", "track_id": "track-ambiguous", "raw_class_name": "car", "classification": "car"},
            {"id": "no-evidence", "technical_key": "no-evidence", "track_id": "track-no-evidence", "classification": "passenger_vehicle", "provisional_class": "passenger_vehicle"},
            {"id": "legacy", "technical_key": "legacy", "track_id": "track-legacy", "classification": "passenger_vehicle", "provisional_class": "passenger_vehicle"},
        ],
        {"track-unsupported": unsupported.evidence_json(), "track-ambiguous": ambiguous.evidence_json()},
    )
    rows = {row["technical_key"]: row for row in result["summary"]["events"]}
    assert rows["unsupported"]["classification_status"] == ClassificationStatus.UNSUPPORTED_RAW_CLASS.value
    assert rows["unsupported"]["engineering_class"] == EngineeringClass.UNKNOWN.value
    assert rows["ambiguous"]["classification_status"] == ClassificationStatus.AMBIGUOUS.value
    assert rows["ambiguous"]["engineering_class"] == EngineeringClass.AMBIGUOUS.value
    assert rows["no-evidence"]["classification_status"] == ClassificationStatus.UNKNOWN.value
    assert rows["no-evidence"]["engineering_class"] == EngineeringClass.UNKNOWN.value
    assert rows["legacy"]["classification_status"] == ClassificationStatus.UNKNOWN.value
    assert rows["legacy"]["engineering_class"] == EngineeringClass.UNKNOWN.value


def test_live_summary_and_reconciliation_block_contradictory_persisted_projection() -> None:
    decision = classify_track_evidence([evidence("car", 0.9, 0), evidence("car", 0.8, 1000)])
    connection, service, run_id, result = engineering_fixture(
        [{"id": "projection", "technical_key": "projection", "track_id": "track-projection", "raw_class_name": "car", "classification": "car"}],
        {"track-projection": decision.evidence_json()},
    )
    assert result["engineering_ready"] is True
    connection.execute(
        "UPDATE engineering_event_projections SET classification_status = 'INSUFFICIENT_EVIDENCE' WHERE run_id = ?",
        (run_id,),
    )
    connection.commit()
    summary = service.get_engineering_summary(run_id)
    reconciliation = service.get_engineering_reconciliation(run_id)["report"]
    assert summary["engineering_ready"] is False
    assert reconciliation["engineering_ready"] is False
    assert reconciliation["classification_exclusions"][0]["event_id"] == "projection"
    assert reconciliation["classification_exclusions"][0]["engineering_class"] == EngineeringClass.PASSENGER_VEHICLE.value


def test_api_exposes_typed_engineering_summary_and_evidence() -> None:
    client = TestClient(create_app())
    project = client.post("/api/v1/projects", json={"name": "6B", "location": "Bangkok", "study_type": "intersection", "language": "en"}).json()
    client.post(
        "/api/v1/sources",
        json={
            "project_id": project["id"],
            "file_name": "synthetic.mp4",
            "fingerprint_sha256": "6b-api-source",
            "source_started_at": datetime(2026, 1, 1, 0, tzinfo=timezone.utc).isoformat(),
            "timezone_name": "Asia/Bangkok",
            "analysis_start_pts_ms": 0,
            "analysis_end_pts_ms": 900_000,
        },
    ).raise_for_status()
    scene = client.post("/api/v1/scenes", json={"project_id": project["id"], "template": "intersection"}).json()
    run = client.post(
        f"/api/v1/projects/{project['id']}/synthetic-runs",
        json={"fixture_id": "6b-api", "expected_scene_version": scene["version"]},
    ).json()
    run_id = run["id"]
    summary_response = client.get(f"/api/v1/runs/{run_id}/engineering-summary")
    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary["engineering_ready"] is True
    assert summary["reconciliation"]["status"] == "STRUCTURALLY_VALID"
    assert summary["reconciliation"]["invalid_direction_exclusions"] == []
    assert summary["reconciliation"]["classification_exclusions"] == []
    assert summary["direction_totals"]
    assert client.get("/api/v1/taxonomy").json()["revision"] == "pilot-observable-taxonomy-v1"
    assert client.get(f"/api/v1/runs/{run_id}/engineering/disclosures").json()["engineering_ready"] is True
    event_response = client.get(f"/api/v1/runs/{run_id}/engineering-events")
    assert event_response.status_code == 200
    event = event_response.json()[0]
    evidence_response = client.get(f"/api/v1/runs/{run_id}/tracks/{event['track_id']}/evidence")
    assert evidence_response.status_code == 200
    assert evidence_response.json()[0]["evidence"]["schema_version"] == "compact-track-evidence-v1"
    reconciliation_response = client.get(f"/api/v1/runs/{run_id}/engineering/reconciliation")
    assert reconciliation_response.status_code == 200
    assert reconciliation_response.json()["invalid_direction_exclusions"] == []
    assert reconciliation_response.json()["classification_exclusions"] == []
