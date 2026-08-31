from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from apps.backend.app.main import create_app


def test_diagnostics_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("TRAFFIC_APP_DIAGNOSTICS", raising=False)
    client = TestClient(create_app())

    response = client.get("/api/v1/health")

    response.raise_for_status()
    assert "X-TVA-Request-ID" not in response.headers
    assert "Server-Timing" not in response.headers


def test_diagnostics_adds_timing_headers_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("TRAFFIC_APP_DIAGNOSTICS", "1")
    client = TestClient(create_app())

    response = client.get("/api/v1/health", headers={"X-Request-ID": "req_test"})

    response.raise_for_status()
    assert response.headers["X-TVA-Request-ID"] == "req_test"
    assert response.headers["Server-Timing"].startswith("app;dur=")


def test_event_endpoints_support_pagination_for_large_review_migration() -> None:
    client = TestClient(create_app())
    project = client.post(
        "/api/v1/projects",
        json={"name": "Pagination", "location": "Bangkok", "study_type": "intersection", "language": "en"},
    ).json()
    client.post(
        "/api/v1/sources",
        json={
            "project_id": project["id"],
            "file_name": "mock.mp4",
            "fingerprint_sha256": "abc123",
            "source_started_at": datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc).isoformat(),
            "timezone_name": "Asia/Bangkok",
            "analysis_start_pts_ms": 0,
            "analysis_end_pts_ms": 3_600_000,
        },
    ).raise_for_status()
    client.post("/api/v1/scenes", json={"project_id": project["id"], "template": "intersection"}).raise_for_status()
    run = client.post(f"/api/v1/projects/{project['id']}/mock-analysis").json()

    first_page = client.get(f"/api/v1/runs/{run['id']}/events?limit=2&offset=0").json()
    second_page = client.get(f"/api/v1/runs/{run['id']}/events?limit=2&offset=2").json()

    assert len(first_page) == 2
    assert len(second_page) == 2
    assert first_page[0]["id"] != second_page[0]["id"]
