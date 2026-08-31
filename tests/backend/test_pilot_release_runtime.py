from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi.testclient import TestClient

from apps.backend.app.db import connect, migrate
from apps.backend.app.main import create_app
from apps.backend.app.services import FoundationService
from apps.backend.app.worker_heartbeat import upsert_worker_heartbeat
from apps.worker.processing_worker import run_worker
from scripts.create_support_bundle import sanitize
from scripts.database_backup import _backup, _restore


def _create_project_with_scene(client: TestClient) -> dict:
    project = client.post(
        "/api/v1/projects",
        json={"name": "Pilot run selection", "location": "Bangkok", "study_type": "intersection", "language": "th"},
    ).json()
    client.post(
        "/api/v1/sources",
        json={
            "project_id": project["id"],
            "file_name": "approved-mock.mp4",
            "fingerprint_sha256": "pilot-selection-source",
            "source_started_at": datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc).isoformat(),
            "timezone_name": "Asia/Bangkok",
            "analysis_start_pts_ms": 0,
            "analysis_end_pts_ms": 3_600_000,
        },
    ).raise_for_status()
    client.post("/api/v1/scenes", json={"project_id": project["id"], "template": "intersection"}).raise_for_status()
    return project


def test_current_run_selection_is_explicit_and_persists_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "pilot.sqlite3"
    client = TestClient(create_app(database_path=database))
    project = _create_project_with_scene(client)
    first = client.post(f"/api/v1/projects/{project['id']}/mock-analysis").json()
    second = client.post(f"/api/v1/projects/{project['id']}/mock-analysis").json()

    snapshot = client.get(f"/api/v1/projects/{project['id']}").json()
    assert snapshot["selected_run_id"] == first["id"]
    assert snapshot["selected_run_explicit"] is True
    assert {item["id"] for item in snapshot["processing_runs"]} >= {first["id"], second["id"]}

    selected = client.post(
        f"/api/v1/projects/{project['id']}/processing-runs/{second['id']}/select",
        json={"selected_by": "pilot-operator"},
    )
    selected.raise_for_status()
    assert selected.json()["selected_run_id"] == second["id"]

    restarted = TestClient(create_app(database_path=database))
    reopened = restarted.get(f"/api/v1/projects/{project['id']}").json()
    assert reopened["selected_run_id"] == second["id"]
    assert reopened["run"]["id"] == second["id"]


def test_current_run_selection_rejects_incomplete_run(tmp_path: Path) -> None:
    client = TestClient(create_app(database_path=tmp_path / "pilot.sqlite3"))
    project = _create_project_with_scene(client)
    run = client.post(
        f"/api/v1/projects/{project['id']}/processing-jobs",
        json={"fixture_id": "pilot-queued", "mode": "SYNTHETIC", "auto_start": False, "run_again": True},
    ).json()
    response = client.post(
        f"/api/v1/projects/{project['id']}/processing-runs/{run['id']}/select",
        json={},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "processing run is not ready for selection"


def test_readiness_and_runtime_payloads_do_not_expose_private_paths(tmp_path: Path) -> None:
    client = TestClient(create_app(database_path=tmp_path / "pilot.sqlite3"))

    readiness = client.get("/api/v1/readiness")
    readiness.raise_for_status()
    payload = json.dumps(readiness.json())
    assert str(tmp_path) not in payload
    assert '"executable":' not in payload

    release = client.get("/api/v1/release")
    release.raise_for_status()
    assert str(tmp_path) not in json.dumps(release.json())


def _seed_worker_heartbeat(database: Path, *, worker_id: str, token: str, state: str = "READY", at: datetime | None = None) -> None:
    connection = connect(database)
    migrate(connection)
    upsert_worker_heartbeat(
        connection,
        worker_id=worker_id,
        instance_token=token,
        pid=1234,
        started_at=at or datetime.now(timezone.utc),
        runtime_version="0.1.0-pilot",
        state=state,
        now=at,
    )
    connection.close()


def _disable_real_runtime(monkeypatch) -> None:
    original = FoundationService.processing_readiness

    def without_real_runtime(service: FoundationService) -> dict:
        payload = original(service)
        payload["real_inference"] = {
            **payload["real_inference"],
            "ready": False,
            "state": "missing_real_video_runtime",
            "detail": "REAL_VIDEO fixture is intentionally unavailable in this test.",
        }
        return payload

    monkeypatch.setattr(FoundationService, "processing_readiness", without_real_runtime)


def test_readiness_requires_a_fresh_owned_worker_heartbeat(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "pilot.sqlite3"
    monkeypatch.setenv("TVA_WORKER_ID", "test-worker")
    monkeypatch.setenv("TVA_WORKER_INSTANCE_TOKEN", "test-instance-token")
    monkeypatch.setenv("TVA_FRONTEND_URL", "http://127.0.0.1:1")
    _disable_real_runtime(monkeypatch)

    _seed_worker_heartbeat(database, worker_id="test-worker", token="test-instance-token")
    body = TestClient(create_app(database_path=database)).get("/api/v1/readiness").json()

    assert body["components"]["worker"]["state"] == "READY"
    assert body["components"]["worker"]["ready"] is True
    assert body["processing"]["synthetic_ready"] is True
    assert body["components"]["synthetic_capability"]["state"] == "SYNTHETIC_ONLY"
    assert body["processing"]["real_inference"]["ready"] is False
    assert body["processing_status"] == "READY_WITH_WARNINGS"
    assert body["components"]["frontend"]["state"] == "OFFLINE"
    assert body["components"]["frontend"]["ready"] is False
    assert body["application_ready"] is False
    payload = json.dumps(body)
    assert "test-instance-token" not in payload
    assert "1234" not in payload


def test_readiness_marks_a_worker_stale_after_the_five_second_ttl(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "pilot.sqlite3"
    monkeypatch.setenv("TVA_WORKER_ID", "test-worker")
    monkeypatch.setenv("TVA_WORKER_INSTANCE_TOKEN", "test-instance-token")
    monkeypatch.setenv("TVA_FRONTEND_URL", "http://127.0.0.1:1")
    _disable_real_runtime(monkeypatch)
    stale_at = datetime.now(timezone.utc) - timedelta(seconds=6)
    _seed_worker_heartbeat(database, worker_id="test-worker", token="test-instance-token", at=stale_at)

    body = TestClient(create_app(database_path=database)).get("/api/v1/readiness").json()

    assert body["components"]["worker"]["state"] == "STALE"
    assert body["components"]["worker"]["ready"] is False
    assert body["processing_status"] == "BLOCKED"
    assert body["application_ready"] is False


def test_worker_exit_is_observable_as_offline(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "pilot.sqlite3"
    worker_id = "exit-worker"
    token = "exit-instance-token"
    monkeypatch.setenv("TVA_WORKER_ID", worker_id)
    monkeypatch.setenv("TVA_WORKER_INSTANCE_TOKEN", token)
    monkeypatch.setenv("TVA_FRONTEND_URL", "http://127.0.0.1:1")

    assert run_worker(
        database_path=str(database),
        worker_id=worker_id,
        instance_token=token,
        poll_interval=0.01,
        heartbeat_interval=0.25,
        once=True,
    ) == 0
    body = TestClient(create_app(database_path=database)).get("/api/v1/readiness").json()

    assert body["components"]["worker"]["state"] == "OFFLINE"
    assert body["components"]["worker"]["ready"] is False
    assert body["processing_status"] == "BLOCKED"


def test_synthetic_capability_is_not_real_media_readiness(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "pilot.sqlite3"
    monkeypatch.setenv("TVA_WORKER_ID", "test-worker")
    monkeypatch.setenv("TVA_WORKER_INSTANCE_TOKEN", "test-instance-token")
    monkeypatch.setenv("TVA_FRONTEND_URL", "http://127.0.0.1:1")
    _disable_real_runtime(monkeypatch)
    _seed_worker_heartbeat(database, worker_id="test-worker", token="test-instance-token")

    body = TestClient(create_app(database_path=database)).get("/api/v1/readiness").json()

    assert body["components"]["synthetic_capability"]["ready"] is True
    assert body["components"]["synthetic_capability"]["state"] == "SYNTHETIC_ONLY"
    assert body["components"]["real_inference"]["ready"] is False
    assert body["components"]["real_inference"]["state"] != "READY"
    assert body["processing_status"] == "READY_WITH_WARNINGS"


def test_backup_restore_creates_integrity_checked_safety_copy(tmp_path: Path) -> None:
    database = tmp_path / "tva.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE values_table(value TEXT NOT NULL)")
    connection.execute("INSERT INTO values_table(value) VALUES ('before')")
    connection.commit()
    connection.close()
    backup = tmp_path / "backup.sqlite3"
    result = _backup(database, backup)
    assert result["operation"] == "backup"

    connection = sqlite3.connect(database)
    connection.execute("UPDATE values_table SET value = 'changed'")
    connection.commit()
    connection.close()
    restored = _restore(backup, database)
    assert restored["operation"] == "restore"
    assert restored["safety_copy_filename"]
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT value FROM values_table").fetchone()[0] == "before"
    connection.close()


def test_support_bundle_sanitizer_removes_private_paths_and_tokens() -> None:
    payload = sanitize({"path": r"D:\private\source.mp4", "message": r"token=abc123 D:\private\log.txt"})
    assert payload["path"] == "<redacted-local-path>"
    assert "abc123" not in payload["message"]
    assert "D:\\private" not in payload["message"]
