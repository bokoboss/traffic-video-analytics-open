from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.backend.app import services as service_module
from apps.backend.app.db import connect, migrate
from apps.backend.app.main import create_app
from apps.backend.app.real_inference import RealInferenceError, RealInferenceResult, RealProcessingConfig
from apps.backend.app.schemas import SourceCreate
from apps.backend.app.services import FoundationService
from apps.backend.app.synthetic_counting import CountingRunResult


def _empty_result(run_id: str) -> RealInferenceResult:
    return RealInferenceResult(
        counting_result=CountingRunResult(
            run_id=run_id,
            events=(),
            exclusions=(),
            warnings=(),
            aggregates={},
        ),
        config=RealProcessingConfig(resolved_device="cpu"),
        stats={
            "detector_revision": "test-detector",
            "tracker_revision": "test-tracker",
            "weight_identifier": "test-weight",
            "weight_sha256": "test-weight-sha256",
            "configuration_hash": "test-configuration-hash",
            "runtime_configuration_hash": "test-runtime-configuration-hash",
            "actual_runtime_configuration_hash": "test-runtime-configuration-hash",
            "provenance_status": "VERIFIED_RUNTIME_PROVENANCE",
            "provenance_mismatches": [],
        },
        tracks=(),
    )


def _prepared_real_job(
    tmp_path: Path,
) -> tuple[FoundationService, FoundationService, dict[str, object], list[tuple[str, dict[str, object]]]]:
    database_path = tmp_path / "real-production-callbacks.sqlite3"
    media_path = tmp_path / "managed-media.mp4"
    media_path.write_bytes(b"file-backed test media")
    app = create_app(str(database_path))
    api_service = app.state.foundation_service
    project = api_service.create_project("real-callbacks", "test", "intersection", "en")
    source = api_service.register_source(
        SourceCreate(
            project_id=project["id"],
            file_name=media_path.name,
            fingerprint_sha256="real-callback-source",
            source_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            timezone_name="UTC",
            analysis_start_pts_ms=0,
            analysis_end_pts_ms=2_000,
        )
    )
    scene = api_service.create_scene(project["id"], "intersection")
    api_service.connection.execute(
        """
        UPDATE video_sources
        SET source_type = 'local_upload', readiness_state = 'media_ready', managed_media_path = ?,
            width = 2, height = 2, frame_count = 2, duration_ms = 2000,
            first_video_pts = 0, stream_time_base = '1/1000'
        WHERE id = ?
        """,
        (str(media_path), source["id"]),
    )
    api_service.connection.commit()
    worker_connection = connect(database_path, check_same_thread=True)
    migrate(worker_connection)
    events: list[tuple[str, dict[str, object]]] = []

    def callback_connection_factory():
        return connect(database_path, check_same_thread=False)

    worker = FoundationService(
        worker_connection,
        processing_connection_factory=callback_connection_factory,
        processing_event_sink=lambda event, payload: events.append((event, dict(payload))),
    )
    worker.processing_readiness = lambda: {"real_inference": {"ready": True, "state": "real_ready"}}
    configuration = api_service.create_processing_configuration_revision(
        project["id"],
        {"profile_code": "BALANCED", "expected_scene_version": scene["version"], "created_by": "operator"},
    )
    job = worker.submit_processing_job(
        project["id"],
        mode="REAL_VIDEO",
        processing_configuration_revision_id=configuration["id"],
        expected_scene_version=scene["version"],
        auto_start=False,
    )
    assert job["processing_configuration_revision_id"] == configuration["id"]
    assert job["configuration"]["profile_code"] == "BALANCED"
    assert job["configuration"]["profile_revision"] == "balanced-r1"
    assert job["fixture_id"] is None
    assert "claim_token" not in job
    return worker, api_service, job, events


def test_real_production_callbacks_complete_under_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TVA_LOCAL_DATA_DIR", str(tmp_path))
    worker, api_service, job, events = _prepared_real_job(tmp_path)
    try:
        monkeypatch.setattr(worker, "processing_readiness", lambda: {"real_inference": {"ready": True}})
        monkeypatch.setattr(
            service_module,
            "media_runtime_status",
            lambda: SimpleNamespace(ffmpeg=SimpleNamespace(executable="ffmpeg")),
        )
        assert worker.claim_next_processing_job("callback-worker") is not None

        def fake_inference(**kwargs: object) -> RealInferenceResult:
            status_callback = kwargs["status_callback"]
            cancel_requested = kwargs["cancel_requested"]
            heartbeat_callback = kwargs["heartbeat_callback"]
            assert callable(status_callback)
            assert callable(cancel_requested)
            assert callable(heartbeat_callback)
            status_callback("PROCESSING", {"completed_units": 1, "total_units": 2})
            barrier = threading.Barrier(2)
            failures: list[BaseException] = []

            def cancellation_watcher() -> None:
                barrier.wait()
                try:
                    for _ in range(250):
                        assert cancel_requested() is False
                except BaseException as exc:
                    failures.append(exc)

            watcher = threading.Thread(target=cancellation_watcher, name="test-real-cancel-watcher")
            watcher.start()
            barrier.wait()
            for _ in range(12):
                heartbeat_callback()
            status_callback("PROCESSING", {"completed_units": 2, "total_units": 2})
            watcher.join()
            if failures:
                raise failures[0]
            return _empty_result(str(kwargs["run_id"]))

        monkeypatch.setattr(service_module, "run_real_video_inference", fake_inference)
        completed = worker.execute_claimed_job(str(job["id"]), "callback-worker")

        assert completed["job_state"] == "COMPLETED"
        assert completed["result_ready"] is True
        assert worker.connection.execute("SELECT COUNT(*) FROM real_inference_runs WHERE run_id = ?", (job["id"],)).fetchone()[0] == 1
        emitted = [event for event, _payload in events]
        assert {
            "processing_execute_started",
            "processing_control_verified",
            "processing_inference_started",
            "processing_status",
            "processing_heartbeat",
            "processing_cancel_check",
            "processing_inference_completed",
            "processing_finalize_started",
            "processing_finalize_completed",
        } <= set(emitted)
        assert "processing_callback_failed" not in emitted
        serialized_events = str(events)
        assert "claim_token" not in serialized_events
        execution_connection_ids = {
            payload["connection_identity"]
            for event, payload in events
            if event in {"processing_execute_started", "processing_inference_started", "processing_finalize_started"}
        }
        callback_connection_ids = {
            payload["connection_identity"]
            for event, payload in events
            if event in {"processing_status", "processing_heartbeat", "processing_cancel_check"}
        }
        assert callback_connection_ids
        assert len(callback_connection_ids) >= 2
        assert callback_connection_ids.isdisjoint(execution_connection_ids)
    finally:
        worker.connection.close()
        api_service.connection.close()


def test_real_preview_promotion_reuses_saved_configuration_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker, api_service, job, _events = _prepared_real_job(tmp_path)
    try:
        monkeypatch.setattr(api_service, "processing_readiness", lambda: {"real_inference": {"ready": True}})
        preview = worker.create_preview_run(
            str(job["project_id"]),
            {
                "mode": "REAL_VIDEO",
                "processing_configuration_revision_id": job["processing_configuration_revision_id"],
                "start_pts_ms": 0,
                "end_pts_ms": 2_000,
            },
        )
        claimed = worker.claim_next_preview_run("preview-promotion-worker")
        assert claimed is not None
        raw = worker.connection.execute(
            "SELECT claim_token FROM preview_runs WHERE id = ?", (preview["id"],)
        ).fetchone()
        assert raw is not None
        saved = api_service.processing_profile_store.get_configuration(str(job["processing_configuration_revision_id"]))
        completed_preview = worker.processing_profile_store.set_preview_result(
            str(preview["id"]),
            status="COMPLETED",
            statistics={
                "configuration_hash": saved["configuration_hash"],
                "request_provenance_hash": saved["request_provenance_hash"],
                "runtime_configuration_hash": "preview-runtime-hash",
                "actual_runtime_configuration_hash": "preview-runtime-hash",
                "provenance_status": "VERIFIED_RUNTIME_PROVENANCE",
            },
            worker_id="preview-promotion-worker",
            claim_token=str(raw["claim_token"]),
        )
        assert completed_preview["status"] == "COMPLETED"

        promoted = api_service.promote_preview_run(str(preview["id"]), auto_start=False)
        assert promoted["run_type"] == "PRODUCTION"
        assert promoted["processing_mode"] == "REAL_VIDEO"
        assert promoted["processing_configuration_revision_id"] == job["processing_configuration_revision_id"]
        assert promoted["fixture_id"] is None
        assert promoted["configuration"]["profile_code"] == "BALANCED"
        assert promoted["configuration"]["profile_revision"] == "balanced-r1"
        assert promoted["configuration"]["configuration_hash"] == saved["configuration_hash"]
        assert promoted["configuration"]["request_provenance_hash"] == saved["request_provenance_hash"]
        raw_config = worker.connection.execute(
            "SELECT processing_config_json FROM analysis_runs WHERE id = ?", (promoted["id"],)
        ).fetchone()
        assert raw_config is not None
        assert "fixture_id" not in json.loads(str(raw_config["processing_config_json"]))

        changed = api_service.create_processing_configuration_revision(
            str(job["project_id"]),
            {"profile_code": "FAST_PROCESSING", "expected_scene_version": 1, "created_by": "operator"},
        )
        changed_job = api_service.submit_processing_job(
            str(job["project_id"]),
            mode="REAL_VIDEO",
            processing_configuration_revision_id=changed["id"],
            expected_scene_version=1,
            auto_start=False,
        )
        assert changed_job["id"] != promoted["id"]
        assert changed_job["processing_configuration_revision_id"] == changed["id"]
        assert changed_job["configuration"]["profile_code"] == "FAST_PROCESSING"
    finally:
        worker.connection.close()
        api_service.connection.close()


def test_real_production_operator_cancellation_is_terminal_and_writes_no_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TVA_LOCAL_DATA_DIR", str(tmp_path))
    worker, api_service, job, events = _prepared_real_job(tmp_path)
    try:
        monkeypatch.setattr(worker, "processing_readiness", lambda: {"real_inference": {"ready": True}})
        monkeypatch.setattr(
            service_module,
            "media_runtime_status",
            lambda: SimpleNamespace(ffmpeg=SimpleNamespace(executable="ffmpeg")),
        )
        assert worker.claim_next_processing_job("callback-worker") is not None

        def fake_inference(**kwargs: object) -> RealInferenceResult:
            cancel_requested = kwargs["cancel_requested"]
            assert callable(cancel_requested)
            cancel_thread = threading.Thread(
                target=lambda: api_service.cancel_processing_job(str(job["id"]), "operator_cancelled"),
                name="test-real-operator-cancel",
            )
            cancel_thread.start()
            polls = 0
            requested = False
            for _ in range(250):
                polls += 1
                requested = bool(cancel_requested())
                if requested:
                    break
                time.sleep(0.001)
            cancel_thread.join(timeout=2)
            assert not cancel_thread.is_alive()
            assert polls >= 1
            assert requested is True
            return _empty_result(str(kwargs["run_id"]))

        monkeypatch.setattr(service_module, "run_real_video_inference", fake_inference)
        cancelled = worker.execute_claimed_job(str(job["id"]), "callback-worker")

        assert cancelled["job_state"] == "CANCELLED"
        assert cancelled["result_ready"] is False
        assert worker.connection.execute("SELECT COUNT(*) FROM real_inference_runs WHERE run_id = ?", (job["id"],)).fetchone()[0] == 0
        assert worker.connection.execute("SELECT COUNT(*) FROM crossing_event_ledger WHERE run_id = ?", (job["id"],)).fetchone()[0] == 0
        cancel_events = [payload for event, payload in events if event == "processing_cancel_check"]
        assert any(payload.get("cancellation_requested") is True for payload in cancel_events)
        assert "processing_finalize_started" not in [event for event, _payload in events]
    finally:
        worker.connection.close()
        api_service.connection.close()


def test_real_production_ownership_loss_fails_closed_without_partial_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TVA_LOCAL_DATA_DIR", str(tmp_path))
    worker, api_service, job, events = _prepared_real_job(tmp_path)
    try:
        monkeypatch.setattr(worker, "processing_readiness", lambda: {"real_inference": {"ready": True}})
        monkeypatch.setattr(
            service_module,
            "media_runtime_status",
            lambda: SimpleNamespace(ffmpeg=SimpleNamespace(executable="ffmpeg")),
        )
        assert worker.claim_next_processing_job("callback-worker") is not None

        def ownership_loss(**kwargs: object) -> RealInferenceResult:
            api_service.connection.execute(
                "UPDATE analysis_runs SET worker_id = 'replacement-worker' WHERE id = ?", (job["id"],)
            )
            api_service.connection.commit()
            heartbeat_callback = kwargs["heartbeat_callback"]
            assert callable(heartbeat_callback)
            heartbeat_callback()
            return _empty_result(str(kwargs["run_id"]))

        monkeypatch.setattr(service_module, "run_real_video_inference", ownership_loss)
        failed = worker.execute_claimed_job(str(job["id"]), "callback-worker")

        assert failed["job_state"] == "RUNNING"
        assert failed["result_ready"] is False
        assert worker.connection.execute("SELECT COUNT(*) FROM real_inference_runs WHERE run_id = ?", (job["id"],)).fetchone()[0] == 0
        fenced = [payload for event, payload in events if event == "processing_execute_fenced"]
        assert fenced
        assert fenced[0]["error_code"] == "PROCESSING_AUTHORITY_LOST"
        assert "replacement-worker" not in str(events)
    finally:
        worker.connection.close()
        api_service.connection.close()


def test_real_production_stale_input_fails_before_inference_and_is_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TVA_LOCAL_DATA_DIR", str(tmp_path))
    worker, api_service, job, _events = _prepared_real_job(tmp_path)
    try:
        assert worker.claim_next_processing_job("callback-worker") is not None
        api_service.connection.execute(
            "UPDATE video_sources SET fingerprint_sha256 = 'changed-after-claim' WHERE project_id = ?",
            (job["project_id"],),
        )
        api_service.connection.commit()
        stale = worker.execute_claimed_job(str(job["id"]), "callback-worker")
        assert stale["job_state"] == "FAILED"
        assert stale["error_code"] == "STALE_INPUT"
        assert stale["result_ready"] is False
        assert worker.connection.execute("SELECT COUNT(*) FROM real_inference_runs WHERE run_id = ?", (job["id"],)).fetchone()[0] == 0
    finally:
        worker.connection.close()
        api_service.connection.close()


def test_real_production_controlled_inference_failure_is_typed_and_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TVA_LOCAL_DATA_DIR", str(tmp_path))
    worker, api_service, job, events = _prepared_real_job(tmp_path)
    try:
        monkeypatch.setattr(worker, "processing_readiness", lambda: {"real_inference": {"ready": True}})
        monkeypatch.setattr(
            service_module,
            "media_runtime_status",
            lambda: SimpleNamespace(ffmpeg=SimpleNamespace(executable="ffmpeg")),
        )
        assert worker.claim_next_processing_job("callback-worker") is not None

        def controlled_failure(**_kwargs: object) -> RealInferenceResult:
            raise RealInferenceError("controlled_inference_failure", "runtime", "Controlled test failure.")

        monkeypatch.setattr(service_module, "run_real_video_inference", controlled_failure)
        failed = worker.execute_claimed_job(str(job["id"]), "callback-worker")

        assert failed["job_state"] == "FAILED"
        assert failed["error_code"] == "CONTROLLED_INFERENCE_FAILURE"
        assert failed["result_ready"] is False
        assert worker.connection.execute("SELECT COUNT(*) FROM crossing_event_ledger WHERE run_id = ?", (job["id"],)).fetchone()[0] == 0
        assert "processing_finalize_started" not in [event for event, _payload in events]
    finally:
        worker.connection.close()
        api_service.connection.close()


def test_real_production_callback_failure_is_typed_and_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TVA_LOCAL_DATA_DIR", str(tmp_path))
    worker, api_service, job, events = _prepared_real_job(tmp_path)
    try:
        monkeypatch.setattr(worker, "processing_readiness", lambda: {"real_inference": {"ready": True}})
        monkeypatch.setattr(
            service_module,
            "media_runtime_status",
            lambda: SimpleNamespace(ffmpeg=SimpleNamespace(executable="ffmpeg")),
        )
        assert worker.claim_next_processing_job("callback-worker") is not None

        def invalid_callback_connection():
            return connect(tmp_path / "callback-missing-schema.sqlite3", check_same_thread=False)

        worker.processing_connection_factory = invalid_callback_connection

        def callback_failure(**kwargs: object) -> RealInferenceResult:
            cancel_requested = kwargs["cancel_requested"]
            assert callable(cancel_requested)
            cancel_requested()
            return _empty_result(str(kwargs["run_id"]))

        monkeypatch.setattr(service_module, "run_real_video_inference", callback_failure)
        failed = worker.execute_claimed_job(str(job["id"]), "callback-worker")

        assert failed["job_state"] == "FAILED"
        assert failed["result_ready"] is False
        assert failed["error_code"] == "PROCESSING_CALLBACK_FAILED"
        assert failed["error_detail"] == "Processing lifecycle callback failed."
        assert worker.connection.execute("SELECT COUNT(*) FROM real_inference_runs WHERE run_id = ?", (job["id"],)).fetchone()[0] == 0
        assert any(event == "processing_callback_failed" for event, _payload in events)
        assert "OperationalError" in str(events)
    finally:
        worker.connection.close()
        api_service.connection.close()


def test_real_production_callback_factory_failure_does_not_touch_main_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TVA_LOCAL_DATA_DIR", str(tmp_path))
    worker, api_service, job, events = _prepared_real_job(tmp_path)
    try:
        monkeypatch.setattr(worker, "processing_readiness", lambda: {"real_inference": {"ready": True}})
        monkeypatch.setattr(
            service_module,
            "media_runtime_status",
            lambda: SimpleNamespace(ffmpeg=SimpleNamespace(executable="ffmpeg")),
        )
        assert worker.claim_next_processing_job("callback-worker") is not None

        def failing_callback_connection_factory():
            raise sqlite3.OperationalError("callback database unavailable")

        worker.processing_connection_factory = failing_callback_connection_factory

        def callback_factory_failure(**kwargs: object) -> RealInferenceResult:
            cancel_requested = kwargs["cancel_requested"]
            assert callable(cancel_requested)
            failures: list[BaseException] = []

            def watcher() -> None:
                try:
                    cancel_requested()
                except BaseException as exc:
                    failures.append(exc)

            thread = threading.Thread(target=watcher, name="test-real-callback-factory-failure")
            thread.start()
            thread.join()
            assert failures
            raise failures[0]

        monkeypatch.setattr(service_module, "run_real_video_inference", callback_factory_failure)
        failed = worker.execute_claimed_job(str(job["id"]), "callback-worker")

        assert failed["job_state"] == "FAILED"
        assert failed["result_ready"] is False
        assert failed["error_code"] == "PROCESSING_CALLBACK_FAILED"
        callback_events = [payload for event, payload in events if event == "processing_callback_failed"]
        assert callback_events
        assert callback_events[-1]["exception_class"] == "OperationalError"
        assert "connection_identity" not in callback_events[-1]
        assert "callback database unavailable" not in str(callback_events[-1])
    finally:
        worker.connection.close()
        api_service.connection.close()


def test_real_production_main_connection_remains_thread_affine(tmp_path: Path) -> None:
    worker, api_service, _job, _events = _prepared_real_job(tmp_path)
    try:
        errors: list[BaseException] = []

        def use_worker_connection_from_watcher() -> None:
            try:
                worker.connection.execute("SELECT 1")
            except BaseException as exc:
                errors.append(exc)

        watcher = threading.Thread(target=use_worker_connection_from_watcher, name="test-main-connection-guard")
        watcher.start()
        watcher.join()
        assert errors
        assert type(errors[0]).__name__ == "ProgrammingError"
    finally:
        worker.connection.close()
        api_service.connection.close()
