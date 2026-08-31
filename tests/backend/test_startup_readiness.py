from __future__ import annotations

from fastapi.testclient import TestClient

from apps.backend.app.main import create_app


def test_health_remains_liveness_without_processing_details() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/health")

    response.raise_for_status()
    body = response.json()
    assert body["status"] == "ok"
    assert "processing" not in body


def test_readiness_distinguishes_core_from_unavailable_processing_and_application() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/readiness")

    response.raise_for_status()
    body = response.json()
    assert body["status"] in {"core_ready", "degraded"}
    assert body["liveness"]["backend"]["ready"] is True
    assert body["core"]["project_storage"]["ready"] is True
    assert body["core_status"] in {"READY", "READY_WITH_WARNINGS"}
    assert body["processing_status"] == "BLOCKED"
    assert body["application_status"] == "BLOCKED"
    assert body["application_ready"] is False
    assert body["components"]["worker"]["state"] == "OFFLINE"
    assert body["components"]["worker"]["ready"] is False
    assert body["components"]["frontend"]["ready"] is False
    assert body["processing"]["worker"]["state"] == "OFFLINE"
    assert body["processing"]["worker"]["ready"] is False
    assert body["processing"]["model_weights"]["ready"] is False
    assert {"detector", "tracker", "device", "real_inference"}.issubset(body["processing"])
    assert body["processing"]["real_inference"]["mode"] == "REAL_VIDEO"
    assert "media_runtime" in body["processing"]


def test_startup_phase_diagnostics_are_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("TRAFFIC_APP_DIAGNOSTICS", raising=False)
    disabled = TestClient(create_app()).get("/api/v1/readiness").json()
    assert disabled["startup_phases_ms"] is None

    monkeypatch.setenv("TRAFFIC_APP_DIAGNOSTICS", "1")
    enabled = TestClient(create_app()).get("/api/v1/readiness").json()
    assert enabled["startup_phases_ms"]["database_connect"] >= 0
    assert enabled["startup_phases_ms"]["route_registration"] >= 0
