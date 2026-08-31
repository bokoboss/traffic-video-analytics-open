from __future__ import annotations

import json
import os
import sqlite3
import shutil
from dataclasses import asdict
from threading import Lock, RLock, current_thread, get_ident, local
import uuid
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .domain import (
    AutoCountEvent,
    ReviewAction,
    ReviewActionType,
    ResultState,
    TimeContract,
    bucket_counts,
    can_certify,
    can_export,
    peak_hour_factor,
    project_effective_event,
)
from .diagnostics import emit_diagnostic_event
from .db import database_identity_hash, database_schema_version
from .engineering_outputs import (
    CLASSIFICATION_POLICY_REVISION,
    ENGINEERING_SUMMARY_SCHEMA_VERSION,
    MAPPING_REVISION,
    TAXONOMY_REVISION,
    TRACK_EVIDENCE_SCHEMA_VERSION,
    EngineeringClass,
    aggregate_engineering_events,
    build_intervals,
    canonical_direction,
    classification_consistency_error,
    classification_decision_from_compact_evidence,
    classification_exclusion,
    classify_track_evidence,
    ensure_default_revisions,
    invalid_direction_exclusion,
    reconcile_engineering_counts,
    revision_ids,
    source_time_semantics,
)
from .media import MediaRuntimeError, ReferenceFrameResult, extract_reference_frame, inspect_media, media_runtime_status, sha256_file
from .media import png_dimensions
from .operational_profiles import OperationalProfileStore, PreviewConflict
from .processing_jobs import (
    ACTIVE_STATES,
    DeviceMode,
    JobState,
    ProcessingMode,
    ProgressPhase,
    is_terminal,
    validate_transition,
)
from .real_inference import (
    RealInferenceCancelled,
    RealInferenceError,
    RealProcessingConfig,
    configuration_revision,
    run_real_video_inference,
)
from .scene_geometry import SCENE_SCHEMA_VERSION, SceneValidationError, scene_semantic_hash, validate_scene_geometry
from .schemas import SceneGeometrySave, SourceCreate, TimeConfigurationUpdate
from .synthetic_counting import (
    CountingTolerances,
    REAL_TRACK_PROVENANCE,
    SYNTHETIC_TRACK_SCHEMA_VERSION,
    canonical_fingerprint,
    execute_synthetic_counting,
    parse_track_set,
    scene_from_geometry,
    _prepare_track_classification,
    validate_track_set,
)
from .processing_profiles import preview_request_fingerprint
from .review_service import ReviewService


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _processing_lease_expired(value: Any) -> bool:
    if not value:
        return True
    try:
        return datetime.fromisoformat(str(value)) <= datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return True


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_PILOT_DURATION_MS = 8 * 60 * 60 * 1000
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi"}
VIDEO_SIGNATURES = {
    ".mp4": (b"ftyp",),
    ".mov": (b"ftyp",),
    ".mkv": (b"\x1a\x45\xdf\xa3",),
    ".avi": (b"RIFF",),
}
PRIVATE_SOURCE_FIELDS = {"managed_media_path"}
PRIVATE_FRAME_FIELDS = {"preview_path"}
VIDEO_MIME_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
}


class PreviewLifecycleFailure(RuntimeError):
    def __init__(self, stage: str, cause: BaseException) -> None:
        super().__init__("Preview lifecycle operation failed.")
        self.stage = stage
        self.exception_class = type(cause).__name__


class ProcessingAuthorityLost(ValueError):
    """The execution no longer owns the claimed processing job."""


class ProcessingLifecycleFailure(RuntimeError):
    """A callback could not safely read or renew the production job lifecycle."""

    def __init__(self, stage: str, cause: BaseException) -> None:
        super().__init__("Processing lifecycle callback failed.")
        self.stage = stage
        self.exception_class = type(cause).__name__
        self.authority_lost = isinstance(cause, ProcessingAuthorityLost)


class _ThreadOwnedConnectionPool:
    """Keep one database connection per callback thread until inference ends."""

    def __init__(
        self,
        *,
        factory: Callable[[], sqlite3.Connection] | None,
        fallback_connection: sqlite3.Connection | None,
        ownership_error: str,
    ) -> None:
        self.factory = factory
        self.fallback_connection = fallback_connection
        self.ownership_error = ownership_error
        self.thread_local = local()
        self.connections: list[sqlite3.Connection] = []
        self.lock = Lock()

    def current(self) -> sqlite3.Connection:
        if self.factory is None:
            if self.fallback_connection is None:
                raise RuntimeError("Processing callback connection factory is unavailable.")
            return self.fallback_connection
        context = getattr(self.thread_local, "context", None)
        if context is None:
            connection = self.factory()
            context = (connection, get_ident())
            self.thread_local.context = context
            with self.lock:
                self.connections.append(connection)
        if context[1] != get_ident():
            raise RuntimeError(self.ownership_error)
        return context[0]

    def close(self) -> None:
        with self.lock:
            connections = list(self.connections)
            self.connections.clear()
        for connection in connections:
            connection.close()


class _PreviewCallbackConnections:
    """Give each inference callback thread one non-shared SQLite connection."""

    def __init__(
        self,
        *,
        factory: Callable[[], sqlite3.Connection] | None,
        fallback_store: OperationalProfileStore,
        fallback_connection: sqlite3.Connection,
    ) -> None:
        self.factory = factory
        self.fallback_store = fallback_store
        self.pool = _ThreadOwnedConnectionPool(
            factory=factory,
            fallback_connection=fallback_connection,
            ownership_error="Preview callback connection ownership changed threads.",
        )

    def current(self) -> tuple[OperationalProfileStore, sqlite3.Connection]:
        connection = self.pool.current()
        if self.factory is None:
            return self.fallback_store, connection
        return OperationalProfileStore(connection), connection

    def close(self) -> None:
        self.pool.close()


class _ProcessingCallbackConnections:
    """Give every REAL_VIDEO lifecycle callback thread its own file-backed connection."""

    def __init__(self, factory: Callable[[], sqlite3.Connection] | None) -> None:
        self.pool = _ThreadOwnedConnectionPool(
            factory=factory,
            fallback_connection=None,
            ownership_error="REAL_VIDEO processing callback connection ownership changed threads.",
        )

    def current(self) -> sqlite3.Connection:
        return self.pool.current()

    def close(self) -> None:
        self.pool.close()


class FoundationService:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        preview_connection_factory: Callable[[], sqlite3.Connection] | None = None,
        processing_connection_factory: Callable[[], sqlite3.Connection] | None = None,
        preview_event_sink: Callable[[str, Mapping[str, object]], None] | None = None,
        processing_event_sink: Callable[[str, Mapping[str, object]], None] | None = None,
    ):
        self.connection = connection
        self.lock = RLock()
        self.preview_connection_factory = preview_connection_factory
        self.processing_connection_factory = processing_connection_factory
        self.preview_event_sink = preview_event_sink
        self.processing_event_sink = processing_event_sink
        ensure_default_revisions(self.connection)
        self.processing_profile_store = OperationalProfileStore(connection)
        self.processing_profile_store.ensure_builtin_profiles()
        self.review_service = ReviewService(connection)

    def _emit_preview_event(
        self,
        event: str,
        *,
        preview_id: str,
        worker_id: str,
        stage: str,
        connection: sqlite3.Connection | None = None,
        **payload: object,
    ) -> None:
        active_connection = connection if connection is not None else self.connection
        event_payload: dict[str, object] = {
            "preview_id": preview_id,
            "worker_id": worker_id,
            "stage": stage,
            "thread": current_thread().name,
            "connection_identity": hashlib.sha256(str(id(active_connection)).encode("ascii")).hexdigest()[:16],
            "db_identity_hash": database_identity_hash(active_connection),
            "schema_version": database_schema_version(active_connection),
            **payload,
        }
        if self.preview_event_sink is not None:
            self.preview_event_sink(event, event_payload)
        else:
            emit_diagnostic_event(event, **event_payload)

    def _emit_processing_event(
        self,
        event: str,
        *,
        run_id: str,
        worker_id: str,
        stage: str,
        connection: sqlite3.Connection | None = None,
        **payload: object,
    ) -> None:
        event_payload: dict[str, object] = {
            "run_id": run_id,
            "worker_id": worker_id,
            "stage": stage,
            "thread": current_thread().name,
            **payload,
        }
        # A callback factory can fail before it returns a connection. In that
        # case diagnostics must remain connection-free rather than touching the
        # thread-affine worker connection from the callback thread.
        if connection is not None:
            event_payload.update(
                {
                    "connection_identity": hashlib.sha256(str(id(connection)).encode("ascii")).hexdigest()[:16],
                    "db_identity_hash": database_identity_hash(connection),
                    "schema_version": database_schema_version(connection),
                }
            )
        if self.processing_event_sink is not None:
            self.processing_event_sink(event, event_payload)
        else:
            emit_diagnostic_event(event, **event_payload)

    def list_projects(self) -> list[dict]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM projects ORDER BY COALESCE(updated_at, created_at) DESC"
            )
        ]

    def select_current_processing_run(self, project_id: str, run_id: str, selected_by: str = "operator") -> dict:
        self._project_or_raise(project_id)
        run = self.connection.execute(
            "SELECT * FROM analysis_runs WHERE id = ? AND project_id = ?",
            (run_id, project_id),
        ).fetchone()
        if run is None:
            raise ValueError("processing run not found")
        if not bool(run["result_ready"]):
            raise ValueError("processing run is not ready for selection")
        self.connection.execute(
            "UPDATE projects SET current_analysis_run_id = ?, updated_at = ?, version = version + 1 WHERE id = ?",
            (run_id, now(), project_id),
        )
        self.connection.commit()
        emit_diagnostic_event(
            "current_processing_run_selected",
            project_id=project_id,
            run_id=run_id,
            selected_by=selected_by,
        )
        return self.get_project_snapshot(project_id)

    def get_project_snapshot(self, project_id: str) -> dict:
        project = self._project_or_raise(project_id)
        source = self.connection.execute(
            "SELECT * FROM video_sources WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        scene = self.connection.execute(
            "SELECT * FROM scene_versions WHERE project_id = ? ORDER BY version DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        selected_run_id = str(project["current_analysis_run_id"] or "") if "current_analysis_run_id" in project.keys() else ""
        run = None
        if selected_run_id:
            run = self.connection.execute(
                "SELECT * FROM analysis_runs WHERE id = ? AND project_id = ?",
                (selected_run_id, project_id),
            ).fetchone()
        if run is None:
            run = self.connection.execute(
                "SELECT * FROM analysis_runs WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        processing_runs = []
        for row in self.connection.execute(
            """
            SELECT id, state, job_state, processing_mode, result_version, created_at,
                   completed_at, result_ready, progress_percent, progress_phase,
                   runtime_configuration_hash, processing_configuration_revision_id,
                   source_fingerprint_sha256, scene_revision
            FROM analysis_runs
            WHERE project_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 25
            """,
            (project_id,),
        ):
            item = dict(row)
            item["selected"] = bool(run is not None and row["id"] == run["id"])
            item["result_ready"] = bool(item.get("result_ready"))
            processing_runs.append(item)
        jobs = self.list_processing_jobs(project_id, limit=10)
        if run is not None:
            export = self.connection.execute(
                "SELECT * FROM export_manifests WHERE project_id = ? AND run_id = ? ORDER BY created_at DESC LIMIT 1",
                (project_id, run["id"]),
            ).fetchone()
            current_6e_certification = self.connection.execute(
                "SELECT * FROM certification_revisions WHERE project_id = ? AND processing_run_id = ? ORDER BY certified_at DESC, id DESC LIMIT 1",
                (project_id, run["id"]),
            ).fetchone()
        else:
            export = self.connection.execute(
                "SELECT * FROM export_manifests WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            current_6e_certification = self.connection.execute(
                "SELECT * FROM certification_revisions WHERE project_id = ? ORDER BY certified_at DESC, id DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        current_6e_export = None
        if current_6e_certification is not None:
            current_6e_export = self.connection.execute(
                "SELECT * FROM export_revisions WHERE certification_revision_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (current_6e_certification["id"],),
            ).fetchone()
        stale_warnings: list[str] = []
        if run is not None and source is not None and str(run["source_fingerprint_sha256"] or "") not in {"", str(source["fingerprint_sha256"])}:
            stale_warnings.append("selected_run_source_differs_from_current_source")
        if run is not None and scene is not None and int(run["scene_revision"] or 0) != int(scene["version"] or 0):
            stale_warnings.append("selected_run_scene_differs_from_current_scene")
        processing_configuration: dict[str, Any] | None = None
        if run is not None:
            try:
                processing_configuration = json.loads(str(run["processing_config_json"] or "{}"))
            except json.JSONDecodeError:
                processing_configuration = {"status": "invalid_configuration_json"}
            if processing_configuration is not None:
                processing_configuration = {
                    key: processing_configuration.get(key)
                    for key in (
                        "mode", "fixture_id", "processing_configuration_revision_id",
                        "profile_code", "profile_revision", "runtime_configuration_hash",
                        "request_provenance_hash", "runtime_provenance_status",
                        "resolved_parameters", "validation_only",
                    )
                    if key in processing_configuration
                }
        return {
            "project": dict(project),
            "source": self._public_source(source) if source else None,
            "scene": self._public_scene(scene) if scene else None,
            "reference_frame": self._public_reference_frame(self._latest_reference_frame(project_id)),
            "run": dict(run) if run else None,
            "selected_run_id": run["id"] if run else None,
            "selected_run_explicit": bool(selected_run_id and run is not None and str(run["id"]) == selected_run_id),
            "processing_runs": processing_runs,
            "processing_configuration": processing_configuration,
            "previews": self.processing_profile_store.list_previews(project_id, limit=10),
            "stale_warnings": stale_warnings,
            "active_job": next((job for job in jobs if job["job_state"] in {state.value for state in ACTIVE_STATES}), None),
            "jobs": jobs,
            "review": {"unresolved_mandatory_qc": self.unresolved_qc_count(project_id)},
            "certification": self.review_service._certification_payload(current_6e_certification) if current_6e_certification else self._latest_certification(project_id),
            "export": self.review_service.get_export(str(current_6e_export["id"])) if current_6e_export else (dict(export) if export else None),
            "readiness": self.project_readiness(project_id),
        }

    def create_project(self, name: str, location: str, study_type: str, language: str) -> dict:
        project_id = new_id("prj")
        self.connection.execute(
            """
            INSERT INTO projects(id, name, location, study_type, language, state, stale, created_at, updated_at, version)
            VALUES (?, ?, ?, ?, ?, 'draft', 0, ?, ?, 1)
            """,
            (project_id, name, location, study_type, language, now(), now()),
        )
        self.connection.commit()
        return dict(self.connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())

    def update_project(self, project_id: str, payload: dict) -> dict:
        project = self._project_or_raise(project_id)
        if int(project["version"]) != int(payload["expected_version"]):
            raise StaleWriteError("Project was changed by another request. Reload before saving.")
        self.connection.execute(
            """
            UPDATE projects
            SET name = ?, location = ?, study_type = ?, language = ?, updated_at = ?, version = version + 1
            WHERE id = ?
            """,
            (
                payload["name"],
                payload["location"],
                payload["study_type"],
                payload["language"],
                now(),
                project_id,
            ),
        )
        self.connection.commit()
        return dict(self.connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())

    def register_source(self, source: SourceCreate) -> dict:
        source_id = new_id("src")
        self.connection.execute(
            """
            INSERT INTO video_sources(
              id, project_id, file_name, fingerprint_sha256, source_started_at, timezone_name,
              analysis_start_pts_ms, analysis_end_pts_ms, interval_origin_pts_ms, created_at,
              recording_time_configured, recording_time_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'CONFIRMED')
            """,
            (
                source_id,
                source.project_id,
                source.file_name,
                source.fingerprint_sha256,
                source.source_started_at.isoformat(),
                source.timezone_name,
                source.analysis_start_pts_ms,
                source.analysis_end_pts_ms,
                source.analysis_start_pts_ms,
                now(),
            ),
        )
        downstream_stale = self.invalidate_for_calculation_change(source.project_id, commit=False)
        self._mark_project(source.project_id, "source_ready", stale=downstream_stale)
        self.connection.commit()
        return dict(self.connection.execute("SELECT * FROM video_sources WHERE id = ?", (source_id,)).fetchone())

    def register_uploaded_source(
        self,
        project_id: str,
        uploaded_path: Path,
        original_filename: str,
        local_data_dir: Path,
    ) -> dict:
        self._project_or_raise(project_id)
        local_root = self._safe_local_data_root(local_data_dir)
        self._validate_uploaded_file(uploaded_path, original_filename)
        fingerprint = sha256_file(uploaded_path)
        duplicate = self.connection.execute(
            """
            SELECT id FROM video_sources
            WHERE project_id = ? AND fingerprint_sha256 = ? AND retention_state = 'active'
            ORDER BY created_at DESC LIMIT 1
            """,
            (project_id, fingerprint),
        ).fetchone()
        if duplicate is not None:
            raise ValueError("duplicate_hash")
        source_id = new_id("src")
        media_id = new_id("med")
        suffix = self._safe_extension(original_filename)
        target_dir = (local_root / "media" / project_id / media_id).resolve()
        self._ensure_inside(local_root, target_dir)
        target_dir.mkdir(parents=True, exist_ok=False)
        managed_path = (target_dir / f"source{suffix}").resolve()
        self._ensure_inside(local_root, managed_path)
        shutil.copyfile(uploaded_path, managed_path)
        inspection = inspect_media(managed_path)
        try:
            self._validate_inspection(inspection)
        except ValueError as exc:
            if str(exc) != "media_probe_failed" or not self._allow_e2e_media_fallback(original_filename):
                raise
            inspection = self._e2e_media_fallback_inspection()
        byte_size = managed_path.stat().st_size
        metadata = inspection.metadata
        warnings_json = json.dumps(inspection.warnings)
        display_name = self._display_filename(original_filename)
        duration_ms = metadata.get("duration_ms")
        self.connection.execute(
            """
            INSERT INTO video_sources(
              id, project_id, file_name, fingerprint_sha256, source_started_at, timezone_name,
              analysis_start_pts_ms, analysis_end_pts_ms, interval_origin_pts_ms, created_at,
              source_type, original_filename, managed_media_path, byte_size, fingerprint_strategy,
              container_format, video_codec, duration_ms, width, height, sample_aspect_ratio,
              display_aspect_ratio, nominal_frame_rate, average_frame_rate, stream_time_base,
              start_pts, first_video_pts, frame_count, variable_frame_rate, rotation_degrees,
              metadata_creation_time, inspection_warnings_json, inspection_tool, inspection_tool_version,
              analysis_window_start_ms, analysis_window_end_ms, media_id, stored_internal_filename,
              original_extension, retention_state, media_schema_version, probe_timestamp,
              audio_present, readiness_state
            ) VALUES (
              ?, ?, ?, ?, ?, ?, 0, COALESCE(?, 3600000), 0, ?,
              'local_upload', ?, ?, ?, 'full-file-sha256',
              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ffprobe', ?, 0, COALESCE(?, 3600000)
              , ?, ?, ?, 'active', 'media-metadata-v1', ?, ?, 'media_ready'
            )
            """,
            (
                source_id,
                project_id,
                display_name,
                fingerprint,
                now(),
                "Asia/Bangkok",
                duration_ms,
                now(),
                display_name,
                str(managed_path),
                byte_size,
                metadata.get("container_format"),
                metadata.get("video_codec"),
                metadata.get("duration_ms"),
                metadata.get("width"),
                metadata.get("height"),
                metadata.get("sample_aspect_ratio"),
                metadata.get("display_aspect_ratio"),
                metadata.get("nominal_frame_rate"),
                metadata.get("average_frame_rate"),
                metadata.get("stream_time_base"),
                metadata.get("start_pts"),
                metadata.get("first_video_pts"),
                metadata.get("frame_count"),
                metadata.get("variable_frame_rate", 0),
                metadata.get("rotation_degrees"),
                metadata.get("metadata_creation_time"),
                warnings_json,
                inspection.tool_version,
                duration_ms,
                media_id,
                managed_path.name,
                suffix,
                now(),
                metadata.get("audio_present"),
            ),
        )
        downstream_stale = self.invalidate_for_calculation_change(project_id, commit=False)
        self._mark_project(project_id, "source_ready", stale=downstream_stale)
        self.connection.commit()
        return self._public_source(self.connection.execute("SELECT * FROM video_sources WHERE id = ?", (source_id,)).fetchone())

    def update_time_configuration(self, project_id: str, payload: TimeConfigurationUpdate) -> dict:
        source = self.connection.execute(
            "SELECT * FROM video_sources WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if source is None:
            raise ValueError("source is required before time configuration")
        self._validate_timezone(payload.timezone_name)
        if payload.analysis_end_pts_ms <= payload.analysis_start_pts_ms:
            raise ValueError("analysis range must use a positive half-open [start, end) interval")
        duration = source["duration_ms"]
        if duration is not None and payload.analysis_end_pts_ms > int(duration):
            raise ValueError("analysis range exceeds inspected media duration")
        local_start = payload.source_started_at.astimezone(ZoneInfo(payload.timezone_name))
        self.connection.execute(
            """
            UPDATE video_sources
            SET source_started_at = ?, timezone_name = ?, analysis_start_pts_ms = ?,
                analysis_end_pts_ms = ?, interval_origin_pts_ms = ?, user_start_local = ?,
                 user_start_timezone = ?, user_start_instant = ?, source_offset_ms = ?,
                 analysis_window_start_ms = ?, analysis_window_end_ms = ?, recording_time_configured = 1,
                 recording_time_status = 'CONFIRMED'
            WHERE id = ?
            """,
            (
                payload.source_started_at.isoformat(),
                payload.timezone_name,
                payload.analysis_start_pts_ms,
                payload.analysis_end_pts_ms,
                payload.analysis_start_pts_ms,
                local_start.isoformat(),
                payload.timezone_name,
                payload.source_started_at.astimezone(timezone.utc).isoformat(),
                payload.source_offset_ms,
                payload.analysis_start_pts_ms,
                payload.analysis_end_pts_ms,
                source["id"],
            ),
        )
        downstream_stale = self.invalidate_for_calculation_change(project_id, commit=False)
        self._mark_project(project_id, "source_ready", stale=downstream_stale)
        self.connection.commit()
        return self.get_project_snapshot(project_id)

    def create_scene(self, project_id: str, template: str) -> dict:
        source = self.connection.execute(
            "SELECT * FROM video_sources WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        current = self.connection.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM scene_versions WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        next_version = int(current["version"]) + 1
        scene_id = new_id("scn")
        geometry = {
            "schema_version": SCENE_SCHEMA_VERSION,
            "coordinate_system": {"origin": "top_left", "units": "normalized_source_display"},
            "counting_lines": [
                {
                    "id": "line_main",
                    "name": "Synthetic validation line",
                    "active": True,
                    "start": {"x": 0.25, "y": 0.55},
                    "end": {"x": 0.75, "y": 0.55},
                    "direction_mode": "BIDIRECTIONAL",
                    "side_a_name": "A side",
                    "side_b_name": "B side",
                    "approach_name": "Synthetic approach",
                    "movement_name": "synthetic-through",
                    "analyst_note": "Milestone 4 validation-only scene.",
                }
            ],
            "rois": [
                {
                    "id": "roi_main",
                    "name": "Synthetic validation ROI",
                    "active": True,
                    "vertices": [
                        {"x": 0.1, "y": 0.1},
                        {"x": 0.9, "y": 0.1},
                        {"x": 0.9, "y": 0.9},
                        {"x": 0.1, "y": 0.9},
                    ],
                }
            ],
        }
        semantic_hash = scene_semantic_hash(geometry)
        self.connection.execute(
            """
            INSERT INTO scene_versions(
              id, project_id, version, template, geometry_json, config_hash, created_at,
              source_id, source_fingerprint_sha256, schema_version, status, semantic_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
            """,
            (
                scene_id,
                project_id,
                next_version,
                template,
                json.dumps(geometry),
                semantic_hash,
                now(),
                source["id"] if source else None,
                source["fingerprint_sha256"] if source else None,
                SCENE_SCHEMA_VERSION,
                semantic_hash,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO counting_rule_versions(id, scene_version_id, version, rule_json, created_at)
            VALUES (?, ?, 1, ?, ?)
            """,
            (
                new_id("rule"),
                scene_id,
                json.dumps({"interval": "half-open", "event_time": "pts_ms"}),
                now(),
            ),
        )
        downstream_stale = self.invalidate_for_calculation_change(project_id, commit=False)
        self._mark_project(project_id, "scene_configured", stale=downstream_stale)
        self.connection.commit()
        return dict(self.connection.execute("SELECT * FROM scene_versions WHERE id = ?", (scene_id,)).fetchone())

    def validate_synthetic_input(
        self, project_id: str, synthetic_input: dict, expected_scene_version: int | None = None
    ) -> dict:
        source = self._latest_source_or_raise(project_id)
        scene_row = self._latest_scene_or_raise(project_id, expected_scene_version)
        scene = scene_from_geometry(dict(scene_row))
        if scene.source_fingerprint != source["fingerprint_sha256"]:
            return {
                "valid": False,
                "errors": [{"code": "scene_source_fingerprint_mismatch", "field": "scene"}],
                "warnings": [],
                "engine_version": "synthetic-crossing-engine-v1",
            }
        track_set = parse_track_set(synthetic_input)
        result = validate_track_set(track_set, scene)
        return {
            "valid": result.valid,
            "errors": list(result.errors),
            "warnings": list(result.warnings),
            "engine_version": "synthetic-crossing-engine-v1",
        }

    def run_synthetic_fixture(
        self, project_id: str, fixture_id: str, expected_scene_version: int | None = None
    ) -> dict:
        job = self.submit_processing_job(
            project_id,
            fixture_id=fixture_id,
            expected_scene_version=expected_scene_version,
            run_again=True,
            auto_start=True,
        )
        return {
            **job,
            "id": job["id"],
            "project_id": project_id,
            "state": job["state"],
            "progress_percent": job["progress_percent"],
            "result_version": job["result_version"],
            "aggregate_id": job.get("aggregate_id"),
            "synthetic": True,
            "validation_only": True,
            "event_count": job.get("event_count", 0),
            "exclusion_count": job.get("exclusion_count", 0),
        }

    def submit_processing_job(
        self,
        project_id: str,
        *,
        fixture_id: str | None = None,
        mode: str = ProcessingMode.SYNTHETIC.value,
        configuration: dict[str, Any] | None = None,
        expected_scene_version: int | None = None,
        idempotency_key: str | None = None,
        run_again: bool = False,
        auto_start: bool = True,
        retry_of_run_id: str | None = None,
        processing_configuration_revision_id: str | None = None,
    ) -> dict:
        self._project_or_raise(project_id)
        source = self._latest_source_or_raise(project_id)
        scene_row = self._latest_scene_or_raise(project_id, expected_scene_version)
        scene = scene_from_geometry(dict(scene_row))
        if scene.source_fingerprint != source["fingerprint_sha256"]:
            raise ValueError("scene source fingerprint does not match current source")
        try:
            processing_mode = ProcessingMode(str(mode).upper())
        except ValueError as exc:
            raise ValueError("unsupported_processing_mode") from exc
        if processing_mode is ProcessingMode.REAL_VIDEO and fixture_id is not None:
            raise ValueError("real_video_processing_does_not_accept_fixture_id")
        effective_fixture_id = (
            fixture_id
            if fixture_id is not None
            else "default-crossing-fixture"
            if processing_mode is ProcessingMode.SYNTHETIC
            else None
        )
        real_config: RealProcessingConfig | None = None
        processing_configuration: dict[str, Any] | None = None
        if processing_mode is ProcessingMode.REAL_VIDEO:
            if processing_configuration_revision_id:
                processing_configuration = self.processing_profile_store.get_configuration(
                    processing_configuration_revision_id
                )
                if processing_configuration.get("project_id") not in {None, project_id}:
                    raise ValueError("processing configuration belongs to another project")
                configuration = dict(processing_configuration.get("resolved_parameters", {}))
            try:
                real_config = RealProcessingConfig.from_payload(configuration)
            except RealInferenceError as exc:
                raise ValueError(f"{exc.code}:{exc.detail}") from exc
            readiness = self.processing_readiness()
            if not readiness["real_inference"]["ready"]:
                raise ValueError(f"real_video_not_ready:{readiness['real_inference']['state']}")
            if processing_configuration is None:
                legacy_overrides = dict(configuration or {})
                known_overrides = {
                    key: value
                    for key, value in legacy_overrides.items()
                    if key
                    in {
                        "device_mode",
                        "detector_id",
                        "tracker_id",
                        "confidence_threshold",
                        "iou_threshold",
                        "image_size",
                        "frame_stride",
                        "class_allowlist",
                        "track_activation_threshold",
                        "lost_track_buffer",
                        "minimum_iou_threshold",
                        "minimum_consecutive_frames",
                        "minimum_track_duration_ms",
                        "minimum_track_observations",
                        "crossing_anchor",
                        "crossing_tolerance",
                        "crossing_hysteresis",
                        "minimum_movement_distance",
                        "minimum_side_stability_frames",
                        "duplicate_crossing_cooldown_ms",
                        "classification_min_observations",
                        "classification_min_winning_vote_share",
                        "classification_min_weighted_share",
                        "classification_near_tie_margin",
                    }
                }
                processing_configuration = self.processing_profile_store.create_configuration_revision(
                    project_id=project_id,
                    profile_code="CUSTOM" if known_overrides else "BALANCED",
                    guided_settings={},
                    expert_overrides=known_overrides,
                    created_by="processing-job",
                    media_duration_ms=int(source["duration_ms"]) if source["duration_ms"] is not None else None,
                    analysis_start_pts_ms=int(source["analysis_start_pts_ms"] or 0),
                    analysis_end_pts_ms=int(source["analysis_end_pts_ms"] or 0),
                    validation=self.processing_profile_store.resolve(
                        "CUSTOM" if known_overrides else "BALANCED",
                        {},
                        known_overrides,
                        media_duration_ms=int(source["duration_ms"]) if source["duration_ms"] is not None else None,
                        analysis_start_pts_ms=int(source["analysis_start_pts_ms"] or 0),
                        analysis_end_pts_ms=int(source["analysis_end_pts_ms"] or 0),
                    ),
                )
                processing_configuration_revision_id = processing_configuration["id"]
        request_fingerprint = self._processing_request_fingerprint(
            source,
            scene_row,
            effective_fixture_id,
            processing_mode.value,
            {
                "processing_configuration_revision_id": processing_configuration_revision_id,
                **(real_config.public_payload() if real_config else configuration or {}),
            },
        )
        if idempotency_key and not run_again:
            duplicate = self.connection.execute(
                """
                SELECT * FROM analysis_runs
                WHERE project_id = ? AND idempotency_key = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (project_id, idempotency_key),
            ).fetchone()
            if duplicate is not None:
                job = self._public_processing_job(duplicate)
                job["duplicate_policy"] = "idempotency_key_reused"
                return job
        if not run_again:
            active_states = [state.value for state in ACTIVE_STATES]
            active = self.connection.execute(
                f"""
                SELECT * FROM analysis_runs
                WHERE project_id = ? AND request_fingerprint = ?
                  AND job_state IN ({",".join("?" for _ in active_states)})
                ORDER BY created_at DESC LIMIT 1
                """,
                (project_id, request_fingerprint, *active_states),
            ).fetchone()
            if active is not None:
                job = self._public_processing_job(active)
                job["duplicate_policy"] = "active_equivalent_reused"
                return job
        run_id = new_id("run")
        result_version = f"{processing_mode.value.lower()}-result-{uuid.uuid4().hex[:8]}"
        created = now()
        config = {
            **(real_config.public_payload() if real_config else {}),
            "mode": processing_mode.value,
            "validation_only": processing_mode is ProcessingMode.SYNTHETIC,
            "scene_schema_version": scene_row["schema_version"] if "schema_version" in scene_row.keys() else SCENE_SCHEMA_VERSION,
            "processing_configuration_revision_id": processing_configuration_revision_id,
            "profile_code": processing_configuration.get("profile_code") if processing_configuration else None,
            "profile_revision": processing_configuration.get("profile_revision") if processing_configuration else None,
            "guided_settings": processing_configuration.get("guided_settings", {}) if processing_configuration else {},
            "requested_expert_overrides": processing_configuration.get("requested_expert_overrides", {}) if processing_configuration else {},
            "resolved_parameters": processing_configuration.get("resolved_parameters", {}) if processing_configuration else {},
            "configuration_hash": processing_configuration.get("configuration_hash") if processing_configuration else None,
            "runtime_configuration_hash": processing_configuration.get("runtime_configuration_hash") if processing_configuration else None,
            "request_provenance_hash": processing_configuration.get("request_provenance_hash") if processing_configuration else None,
            "runtime_provenance_status": processing_configuration.get("runtime_provenance_status", "LEGACY_UNRESOLVED") if processing_configuration else "LEGACY_UNRESOLVED",
            "runtime_provenance": processing_configuration.get("runtime_provenance", {}) if processing_configuration else {},
            "model_revision": processing_configuration.get("model_revision") if processing_configuration else None,
            "weight_sha256": processing_configuration.get("weight_sha256") if processing_configuration else None,
            "tracker_revision": processing_configuration.get("tracker_revision") if processing_configuration else None,
            "crossing_policy_revision": processing_configuration.get("crossing_policy_revision") if processing_configuration else None,
            "classification_policy_revision": processing_configuration.get("classification_policy_revision") if processing_configuration else None,
        }
        if effective_fixture_id is not None:
            config["fixture_id"] = effective_fixture_id
        engine_mode = "synthetic_fixture" if processing_mode is ProcessingMode.SYNTHETIC else "real_video"
        self.connection.execute(
            """
            INSERT INTO analysis_runs(
              id, project_id, source_id, scene_version_id, state, progress_percent,
              result_version, created_at, job_state, progress_phase, progress_completed_units,
              progress_total_units, progress_fraction, progress_message_code, queued_at,
              idempotency_key, request_fingerprint, engine_mode, processing_config_json,
              source_fingerprint_sha256, scene_revision, scene_semantic_hash, retry_of_run_id,
              result_ready, processing_mode, detector_id, tracker_id, device_mode,
              configuration_revision, progress_indeterminate, runtime_configuration_hash,
              request_provenance_hash, provenance_status, runtime_provenance_json
            )
            VALUES (?, ?, ?, ?, 'queued', 0, ?, ?, 'CREATED', 'VALIDATING', 0,
              100, 0.0, 'job_created', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
            """,
            (
                run_id,
                project_id,
                source["id"],
                scene_row["id"],
                result_version,
                created,
                created,
                idempotency_key,
                request_fingerprint,
                engine_mode,
                json.dumps(config, sort_keys=True),
                source["fingerprint_sha256"],
                int(scene_row["version"]),
                scene_row["config_hash"],
                retry_of_run_id,
                processing_mode.value,
                real_config.detector_id if real_config else None,
                real_config.tracker_id if real_config else None,
                real_config.device_mode if real_config else DeviceMode.AUTO.value,
                processing_configuration.get("configuration_hash", configuration_revision(config))
                if processing_configuration
                else configuration_revision(config),
                processing_configuration.get("runtime_configuration_hash") if processing_configuration else None,
                processing_configuration.get("request_provenance_hash") if processing_configuration else None,
                processing_configuration.get("runtime_provenance_status", "LEGACY_UNRESOLVED") if processing_configuration else "LEGACY_UNRESOLVED",
                json.dumps(processing_configuration.get("runtime_provenance", {}) if processing_configuration else {}, sort_keys=True),
            ),
        )
        created_event_detail: dict[str, Any] = {"processing_mode": processing_mode.value}
        if effective_fixture_id is not None:
            created_event_detail["fixture_id"] = effective_fixture_id
        self._record_job_event(
            run_id,
            "created",
            None,
            JobState.CREATED.value,
            created_event_detail,
        )
        self.connection.execute(
            "UPDATE analysis_runs SET processing_configuration_revision_id = ?, run_type = 'PRODUCTION' WHERE id = ?",
            (processing_configuration_revision_id, run_id),
        )
        self._transition_processing_job(run_id, JobState.QUEUED, ProgressPhase.QUEUED, 0, "job_queued")
        self.connection.commit()
        if not auto_start or processing_mode is ProcessingMode.REAL_VIDEO:
            return self.get_processing_job(run_id)
        claimed = self.claim_next_processing_job("synthetic-worker")
        if claimed is None or claimed["id"] != run_id:
            return self.get_processing_job(run_id)
        return self.execute_claimed_synthetic_job(run_id, "synthetic-worker")

    def claim_next_processing_job(self, worker_id: str, lease_seconds: int = 30) -> dict | None:
        self.connection.commit()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """
                SELECT * FROM analysis_runs
                WHERE job_state = 'QUEUED'
                ORDER BY queued_at, created_at
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                self.connection.commit()
                return None
            run_id = row["id"]
            claim_token = new_id("claim")
            self._transition_processing_job(run_id, JobState.STARTING, ProgressPhase.STARTING, 5, "worker_claimed")
            current = now()
            lease = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
            self.connection.execute(
                """
                UPDATE analysis_runs
                SET worker_id = ?, claim_token = ?, claimed_at = ?, started_at = ?,
                    last_heartbeat_at = ?, lease_expires_at = ?
                WHERE id = ? AND job_state = 'STARTING'
                """,
                (worker_id, claim_token, current, current, current, lease, run_id),
            )
            self._record_job_event(run_id, "claimed", JobState.QUEUED.value, JobState.STARTING.value, {"worker_id": worker_id})
            self.connection.commit()
            return self.get_processing_job(run_id)
        except Exception:
            self.connection.rollback()
            raise

    def heartbeat_processing_job(
        self,
        run_id: str,
        worker_id: str,
        lease_seconds: int = 30,
        *,
        connection: sqlite3.Connection | None = None,
        claim_token: str | None = None,
    ) -> dict:
        active_connection = connection if connection is not None else self.connection
        row = self._processing_job_or_raise(run_id, connection=active_connection)
        if not self._processing_claim_is_current(row, worker_id, claim_token):
            raise ProcessingAuthorityLost("worker does not own active job")
        lease = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
        if claim_token is None:
            updated = active_connection.execute(
                """
                UPDATE analysis_runs
                SET last_heartbeat_at = ?, lease_expires_at = ?
                WHERE id = ? AND worker_id = ? AND job_state IN ('STARTING', 'RUNNING', 'CANCELLATION_REQUESTED')
                """,
                (now(), lease, run_id, worker_id),
            )
        else:
            updated = active_connection.execute(
                """
                UPDATE analysis_runs
                SET last_heartbeat_at = ?, lease_expires_at = ?
                WHERE id = ? AND worker_id = ? AND claim_token = ?
                  AND job_state IN ('STARTING', 'RUNNING', 'CANCELLATION_REQUESTED')
                """,
                (now(), lease, run_id, worker_id, claim_token),
            )
        if updated.rowcount != 1:
            raise ProcessingAuthorityLost("worker ownership changed during heartbeat")
        active_connection.commit()
        current = active_connection.execute("SELECT * FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()
        if current is None:
            raise ValueError("processing job not found")
        return self._public_processing_job(current)

    def execute_claimed_synthetic_job(self, run_id: str, worker_id: str) -> dict:
        row = self._processing_job_or_raise(run_id)
        if row["worker_id"] != worker_id:
            raise ValueError("worker does not own job")
        if row["job_state"] == JobState.CANCELLATION_REQUESTED.value:
            return self._cancel_claimed_job(run_id)
        self._transition_processing_job(run_id, JobState.RUNNING, ProgressPhase.PROCESSING, 15, "processing_started")
        self.heartbeat_processing_job(run_id, worker_id)
        latest = self._processing_job_or_raise(run_id)
        if latest["job_state"] == JobState.CANCELLATION_REQUESTED.value:
            return self._cancel_claimed_job(run_id)
        return self._complete_synthetic_job(run_id)

    def execute_claimed_job(self, run_id: str, worker_id: str) -> dict:
        row = self._processing_job_or_raise(run_id)
        mode = str(row["processing_mode"] or "SYNTHETIC").upper()
        if mode == ProcessingMode.REAL_VIDEO.value:
            return self.execute_claimed_real_job(run_id, worker_id)
        return self.execute_claimed_synthetic_job(run_id, worker_id)

    def execute_claimed_real_job(self, run_id: str, worker_id: str) -> dict:
        execute_stage = "processing_execute_started"
        self._emit_processing_event(
            execute_stage,
            run_id=run_id,
            worker_id=worker_id,
            stage=execute_stage,
            connection=self.connection,
            ownership_state="UNKNOWN",
            processing_mode=ProcessingMode.REAL_VIDEO.value,
        )
        row = self._processing_job_or_raise(run_id)
        claim_token = str(row["claim_token"] or "")
        if not self._processing_claim_is_current(row, worker_id, claim_token):
            self._emit_processing_event(
                "processing_execute_fenced",
                run_id=run_id,
                worker_id=worker_id,
                stage=execute_stage,
                connection=self.connection,
                error_code="PROCESSING_AUTHORITY_LOST",
                ownership_state="LOST_OR_TERMINAL",
            )
            return self.get_processing_job(run_id)
        if row["job_state"] == JobState.CANCELLATION_REQUESTED.value:
            return self._cancel_claimed_job(run_id, worker_id=worker_id, claim_token=claim_token)
        control_stage = "processing_control_verified"
        self._emit_processing_event(
            control_stage,
            run_id=run_id,
            worker_id=worker_id,
            stage=control_stage,
            connection=self.connection,
            ownership_state=str(row["job_state"]),
        )
        self._transition_processing_job(
            run_id,
            JobState.RUNNING,
            ProgressPhase.INITIALIZING_RUNTIME,
            8,
            "initializing_runtime",
        )
        self.connection.commit()
        source = self.connection.execute("SELECT * FROM video_sources WHERE id = ?", (row["source_id"],)).fetchone()
        scene_row = self.connection.execute("SELECT * FROM scene_versions WHERE id = ?", (row["scene_version_id"],)).fetchone()
        if source is None or scene_row is None:
            return self._fail_processing_job_owned(
                run_id,
                worker_id,
                claim_token,
                "STALE_INPUT",
                "stale_input",
                "Job source or scene snapshot is missing.",
            )
        scene = scene_from_geometry(dict(scene_row))
        if scene.source_fingerprint != source["fingerprint_sha256"]:
            return self._fail_processing_job_owned(
                run_id,
                worker_id,
                claim_token,
                "STALE_INPUT",
                "stale_input",
                "Scene source fingerprint does not match source.",
            )
        self._transition_processing_job(run_id, JobState.RUNNING, ProgressPhase.PREPARING_MEDIA, 10, "preparing_media")
        self.connection.commit()
        managed_path = Path(str(source["managed_media_path"] or ""))
        local_data_dir = Path(os.getenv("TVA_LOCAL_DATA_DIR", str(Path(__file__).resolve().parents[3] / ".local-data"))).expanduser().resolve()
        try:
            managed_path.resolve().relative_to(local_data_dir)
        except ValueError:
            return self._fail_processing_job_owned(
                run_id,
                worker_id,
                claim_token,
                "SOURCE_PATH_BLOCKED",
                "security",
                "Source media is outside the local data root.",
            )
        if not managed_path.is_file() or managed_path.is_symlink():
            return self._fail_processing_job_owned(
                run_id,
                worker_id,
                claim_token,
                "SOURCE_MISSING",
                "media",
                "Source media is unavailable.",
            )
        config_payload = json.loads(str(row["processing_config_json"] or "{}"))
        callback_connections = _ProcessingCallbackConnections(self.processing_connection_factory)
        cancel_check_lock = Lock()
        cancel_check_state: dict[str, bool | None] = {"last": None}
        last_status_diagnostic: tuple[str, int] | None = None

        def callback_failure(
            callback_stage: str,
            connection: sqlite3.Connection | None,
            cause: BaseException,
        ) -> ProcessingLifecycleFailure:
            authority_lost = isinstance(cause, ProcessingAuthorityLost)
            self._emit_processing_event(
                "processing_execute_fenced" if authority_lost else "processing_callback_failed",
                run_id=run_id,
                worker_id=worker_id,
                stage=callback_stage,
                connection=connection,
                error_code="PROCESSING_AUTHORITY_LOST" if authority_lost else "PROCESSING_CALLBACK_FAILED",
                exception_class=type(cause).__name__,
                ownership_state="LOST_OR_TERMINAL" if authority_lost else "UNKNOWN",
            )
            return ProcessingLifecycleFailure(callback_stage, cause)

        try:
            config = RealProcessingConfig.from_payload(config_payload)
            time_contract = TimeContract(
                source_started_at=datetime.fromisoformat(source["source_started_at"]),
                timezone_name=source["timezone_name"],
                analysis_start_pts_ms=source["analysis_start_pts_ms"],
                analysis_end_pts_ms=source["analysis_end_pts_ms"],
                interval_origin_pts_ms=source["interval_origin_pts_ms"],
            )
            runtime_status = media_runtime_status()
            first_pts_ms = self._source_first_pts_ms(source)

            def cancelled() -> bool:
                callback_stage = "processing_cancel_check"
                callback_connection: sqlite3.Connection | None = None
                try:
                    callback_connection = callback_connections.current()
                    current = self._processing_job_or_raise(run_id, connection=callback_connection)
                    if not self._processing_claim_is_current(current, worker_id, claim_token):
                        raise ProcessingAuthorityLost("worker does not own active job")
                except ProcessingLifecycleFailure:
                    raise
                except BaseException as exc:
                    raise callback_failure(callback_stage, callback_connection, exc) from exc
                requested = current["job_state"] == JobState.CANCELLATION_REQUESTED.value
                with cancel_check_lock:
                    should_emit = cancel_check_state["last"] != requested
                    if should_emit:
                        cancel_check_state["last"] = requested
                if should_emit:
                    self._emit_processing_event(
                        callback_stage,
                        run_id=run_id,
                        worker_id=worker_id,
                        stage=callback_stage,
                        connection=callback_connection,
                        ownership_state=str(current["job_state"]),
                        cancellation_requested=requested,
                    )
                return requested

            def heartbeat(connection: sqlite3.Connection | None = None) -> None:
                callback_stage = "processing_heartbeat"
                callback_connection = connection
                try:
                    callback_connection = callback_connection or callback_connections.current()
                    renewed = self.heartbeat_processing_job(
                        run_id,
                        worker_id,
                        lease_seconds=120,
                        connection=callback_connection,
                        claim_token=claim_token,
                    )
                except ProcessingLifecycleFailure:
                    raise
                except BaseException as exc:
                    raise callback_failure(callback_stage, callback_connection, exc) from exc
                self._emit_processing_event(
                    callback_stage,
                    run_id=run_id,
                    worker_id=worker_id,
                    stage=callback_stage,
                    connection=callback_connection,
                    ownership_state=str(renewed["job_state"]),
                )

            def status_callback(phase: str, detail: dict[str, Any]) -> None:
                nonlocal last_status_diagnostic
                callback_stage = "processing_status"
                callback_connection: sqlite3.Connection | None = None
                try:
                    callback_connection = callback_connections.current()
                    current = self._processing_job_or_raise(run_id, connection=callback_connection)
                    if not self._processing_claim_is_current(current, worker_id, claim_token):
                        raise ProcessingAuthorityLost("worker does not own active job")
                    if current["job_state"] == JobState.CANCELLATION_REQUESTED.value:
                        return
                    completed = detail.get("completed_units")
                    total = detail.get("total_units")
                    if completed is not None and total:
                        percent = min(89, max(10, int(round(float(completed) / max(1, int(total)) * 80))))
                    elif source["duration_ms"]:
                        percent = 10
                    else:
                        percent = int(current["progress_percent"] or 10)
                    self._update_processing_progress(
                        run_id,
                        ProgressPhase(phase),
                        completed_units=int(completed) if completed is not None else int(current["progress_completed_units"] or 0),
                        total_units=int(total) if total is not None else None,
                        progress_percent=percent,
                        message_code=phase.lower(),
                        indeterminate=total is None,
                        connection=callback_connection,
                        worker_id=worker_id,
                        claim_token=claim_token,
                    )
                    heartbeat(callback_connection)
                except ProcessingLifecycleFailure:
                    raise
                except BaseException as exc:
                    raise callback_failure(callback_stage, callback_connection, exc) from exc
                status_signature = (phase, percent)
                if last_status_diagnostic is None or status_signature[0] != last_status_diagnostic[0] or abs(status_signature[1] - last_status_diagnostic[1]) >= 5:
                    last_status_diagnostic = status_signature
                    self._emit_processing_event(
                        callback_stage,
                        run_id=run_id,
                        worker_id=worker_id,
                        stage=callback_stage,
                        connection=callback_connection,
                        ownership_state="RUNNING",
                        phase=phase,
                        progress_percent=percent,
                    )

            inference_stage = "processing_inference_started"
            self._emit_processing_event(
                inference_stage,
                run_id=run_id,
                worker_id=worker_id,
                stage=inference_stage,
                connection=self.connection,
                ownership_state="RUNNING",
            )
            final_cancelled = False
            try:
                result = run_real_video_inference(
                    root=Path(__file__).resolve().parents[3],
                    run_id=run_id,
                    source_path=managed_path.resolve(),
                    source_fingerprint=str(source["fingerprint_sha256"]),
                    source_width=int(source["width"] or 0),
                    source_height=int(source["height"] or 0),
                    source_first_pts_ms=first_pts_ms,
                    source_frame_count=int(source["frame_count"]) if source["frame_count"] is not None else None,
                    source_duration_ms=int(source["duration_ms"]) if source["duration_ms"] is not None else None,
                    scene=scene,
                    time_contract=time_contract,
                    source_time_configured=bool(source["recording_time_configured"]),
                    config=config,
                    ffmpeg_executable=runtime_status.ffmpeg.executable,
                    cancel_requested=cancelled,
                    status_callback=status_callback,
                    heartbeat_callback=heartbeat,
                    configured_runtime_configuration_hash=str(row["runtime_configuration_hash"] or "") or None,
                    configured_runtime_provenance=json.loads(str(config_payload.get("runtime_provenance") or "{}")) if isinstance(config_payload.get("runtime_provenance"), str) else config_payload.get("runtime_provenance"),
                    configuration_hash=str(config_payload.get("configuration_hash") or row["configuration_revision"] or "") or None,
                    request_provenance_hash=str(row["request_provenance_hash"] or config_payload.get("request_provenance_hash") or "") or None,
                )
                self._emit_processing_event(
                    "processing_inference_completed",
                    run_id=run_id,
                    worker_id=worker_id,
                    stage="processing_inference_completed",
                    connection=self.connection,
                    ownership_state="RUNNING",
                )
                final_cancelled = cancelled()
            finally:
                try:
                    callback_connections.close()
                except BaseException as exc:
                    raise callback_failure("processing_callback_close", None, exc) from exc
        except ProcessingLifecycleFailure as exc:
            if exc.authority_lost:
                return self.get_processing_job(run_id)
            self._emit_processing_event(
                "processing_execute_failed",
                run_id=run_id,
                worker_id=worker_id,
                stage=exc.stage,
                error_code="PROCESSING_CALLBACK_FAILED",
                exception_class=exc.exception_class,
                ownership_state="RUNNING",
            )
            return self._fail_processing_job_owned(
                run_id,
                worker_id,
                claim_token,
                "PROCESSING_CALLBACK_FAILED",
                "worker",
                "Processing lifecycle callback failed.",
            )
        except RealInferenceCancelled:
            return self._cancel_claimed_job(run_id, worker_id=worker_id, claim_token=claim_token)
        except RealInferenceError as exc:
            self._emit_processing_event(
                "processing_execute_failed",
                run_id=run_id,
                worker_id=worker_id,
                stage="processing_inference",
                connection=self.connection,
                error_code=exc.code.upper(),
                exception_class=type(exc).__name__,
                ownership_state="RUNNING",
            )
            return self._fail_processing_job_owned(
                run_id,
                worker_id,
                claim_token,
                exc.code.upper(),
                exc.category,
                exc.detail,
            )
        except (ValueError, OSError) as exc:
            self._emit_processing_event(
                "processing_execute_failed",
                run_id=run_id,
                worker_id=worker_id,
                stage="processing_execute",
                connection=self.connection,
                error_code="REAL_VIDEO_FAILED",
                exception_class=type(exc).__name__,
                ownership_state="RUNNING",
            )
            return self._fail_processing_job_owned(
                run_id,
                worker_id,
                claim_token,
                "REAL_VIDEO_FAILED",
                "processing",
                str(exc),
            )
        if final_cancelled:
            return self._cancel_claimed_job(run_id, worker_id=worker_id, claim_token=claim_token)
        self.connection.commit()
        self.connection.execute("BEGIN IMMEDIATE")
        final_row = self._processing_job_or_raise(run_id)
        if (
            final_row["job_state"] == JobState.CANCELLATION_REQUESTED.value
            and self._processing_claim_is_current(final_row, worker_id, claim_token)
        ):
            self.connection.rollback()
            return self._cancel_claimed_job(run_id, worker_id=worker_id, claim_token=claim_token)
        if (
            final_row["job_state"] != JobState.RUNNING.value
            or not self._processing_claim_is_current(final_row, worker_id, claim_token)
        ):
            self.connection.rollback()
            self._emit_processing_event(
                "processing_execute_fenced",
                run_id=run_id,
                worker_id=worker_id,
                stage="processing_finalize_started",
                connection=self.connection,
                error_code="PROCESSING_AUTHORITY_LOST",
                ownership_state="LOST_OR_TERMINAL",
            )
            return self.get_processing_job(run_id)
        self._emit_processing_event(
            "processing_finalize_started",
            run_id=run_id,
            worker_id=worker_id,
            stage="processing_finalize_started",
            connection=self.connection,
            ownership_state="RUNNING",
        )
        with self.connection:
            self._transition_processing_job(run_id, JobState.RUNNING, ProgressPhase.FINALIZING_EVENTS, 90, "finalizing_events")
            row = self._processing_job_or_raise(run_id)
            self.connection.execute(
                """
                INSERT INTO real_inference_runs(
                  id, run_id, project_id, source_id, scene_version_id, detector_id, tracker_id,
                  model_revision, tracker_revision, device_mode, resolved_device,
                  configuration_revision, configuration_json, stats_json, provenance_json,
                  created_at, weight_identifier, weight_sha256, runtime_configuration_hash,
                  actual_runtime_configuration_hash, provenance_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("inf"),
                    run_id,
                    row["project_id"],
                    source["id"],
                    scene_row["id"],
                    result.config.detector_id,
                    result.config.tracker_id,
                    result.stats.get("detector_revision"),
                    result.stats.get("tracker_revision"),
                    result.config.device_mode,
                    result.config.resolved_device,
                    result.stats.get("configuration_hash") or row["configuration_revision"] or configuration_revision(result.config.public_payload()),
                    json.dumps(result.config.public_payload(), sort_keys=True),
                    json.dumps(result.stats, sort_keys=True),
                    json.dumps(
                        {
                            "mode": ProcessingMode.REAL_VIDEO.value,
                            "provenance": REAL_TRACK_PROVENANCE,
                            "track_schema": "real-tracks-v1",
                            "source_fingerprint_sha256": source["fingerprint_sha256"],
                            "scene_revision": scene_row["id"],
                            "detector_version": result.stats.get("detector_version"),
                            "tracker_version": result.stats.get("tracker_version"),
                            "weight_identifier": result.stats.get("weight_identifier"),
                            "weight_sha256": result.stats.get("weight_sha256"),
                        },
                        sort_keys=True,
                    ),
                    now(),
                    result.stats.get("weight_identifier"),
                    result.stats.get("weight_sha256"),
                    result.stats.get("runtime_configuration_hash") or row["runtime_configuration_hash"],
                    result.stats.get("actual_runtime_configuration_hash"),
                    result.stats.get("provenance_status", "LEGACY_UNRESOLVED"),
                ),
            )
            self.connection.execute(
                """
                UPDATE analysis_runs
                SET detector_id = ?, tracker_id = ?, model_revision = ?, tracker_revision = ?,
                    weight_identifier = ?, weight_sha256 = ?, resolved_device = ?,
                    configuration_revision = ?, runtime_configuration_hash = ?,
                    actual_runtime_configuration_hash = ?, provenance_status = ?,
                    runtime_provenance_json = ?, processing_stats_json = ?, processing_config_json = ?,
                    progress_indeterminate = 0
                WHERE id = ?
                """,
                (
                    result.config.detector_id,
                    result.config.tracker_id,
                    result.stats.get("detector_revision"),
                    result.stats.get("tracker_revision"),
                    result.stats.get("weight_identifier"),
                    result.stats.get("weight_sha256"),
                    result.config.resolved_device,
                    result.stats.get("configuration_hash") or row["configuration_revision"] or configuration_revision(result.config.public_payload()),
                    result.stats.get("runtime_configuration_hash") or row["runtime_configuration_hash"],
                    result.stats.get("actual_runtime_configuration_hash"),
                    result.stats.get("provenance_status", "LEGACY_UNRESOLVED"),
                    json.dumps(
                        {
                            "configured": result.stats.get("configured_runtime_provenance", {}),
                            "actual": result.stats.get("actual_runtime_provenance", {}),
                            "configured_runtime_payload": result.stats.get("configured_runtime_payload"),
                            "actual_runtime_payload": result.stats.get("actual_runtime_payload"),
                            "mismatches": result.stats.get("provenance_mismatches", []),
                        },
                        sort_keys=True,
                    ),
                    json.dumps(result.stats, sort_keys=True),
                    json.dumps(
                        {
                            **{
                                key: value
                                for key, value in json.loads(str(row["processing_config_json"] or "{}")).items()
                                if key != "fixture_id"
                            },
                            **result.config.public_payload(),
                            "mode": ProcessingMode.REAL_VIDEO.value,
                            "validation_only": False,
                            "scene_schema_version": scene_row["schema_version"],
                        },
                        sort_keys=True,
                    ),
                    run_id,
                ),
            )
            self._transition_processing_job(run_id, JobState.RUNNING, ProgressPhase.AGGREGATING, 94, "aggregating")
            for track in result.tracks:
                self.connection.execute(
                    "INSERT INTO track_summaries(id, run_id, track_id, summary_json, evidence_schema_version, runtime_configuration_hash, runtime_provenance_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_id("track"),
                        run_id,
                        track["track_id"],
                        json.dumps(track, sort_keys=True),
                        TRACK_EVIDENCE_SCHEMA_VERSION,
                        result.stats.get("runtime_configuration_hash"),
                        json.dumps({"status": result.stats.get("provenance_status"), "actual_runtime_configuration_hash": result.stats.get("actual_runtime_configuration_hash")}, sort_keys=True),
                    ),
                )
            for event in result.counting_result.events:
                self._insert_crossing_event(run_id, event)
            for exclusion in result.counting_result.exclusions:
                self.connection.execute(
                    """
                    INSERT INTO synthetic_counting_exclusions(
                      id, run_id, track_id, counting_line_id, reason, timestamp_ms, detail, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (new_id("exc"), run_id, exclusion.track_id, exclusion.line_id, exclusion.reason.value, exclusion.timestamp_ms, exclusion.detail, now()),
                )
            for warning in result.counting_result.warnings:
                self.connection.execute(
                    """
                    INSERT INTO synthetic_counting_warnings(id, run_id, code, field, detail, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (new_id("warn"), run_id, warning.get("code", "warning"), warning.get("field", ""), warning.get("detail", ""), now()),
                )
            self._create_engineering_result(run_id)
            self._transition_processing_job(run_id, JobState.RUNNING, ProgressPhase.PERSISTING_RESULTS, 97, "persisting_results")
            snapshot = self._create_aggregate_snapshot(run_id, stale=False)
            self._transition_processing_job(run_id, JobState.COMPLETED, ProgressPhase.COMPLETED, 100, "job_completed")
            self.connection.execute(
                "UPDATE analysis_runs SET state = 'processing_complete', completed_at = ?, result_ready = 1 WHERE id = ?",
                (now(), run_id),
            )
            self._set_initial_current_processing_run(row["project_id"], run_id)
            self._mark_project(row["project_id"], "needs_review", stale=False)
        job = self.get_processing_job(run_id)
        job["aggregate_id"] = snapshot["id"]
        job["event_count"] = len(result.counting_result.events)
        job["exclusion_count"] = len(result.counting_result.exclusions)
        self._emit_processing_event(
            "processing_finalize_completed",
            run_id=run_id,
            worker_id=worker_id,
            stage="processing_finalize_completed",
            connection=self.connection,
            ownership_state="COMPLETED",
            result_ready=True,
        )
        return job

    def _complete_synthetic_job(self, run_id: str) -> dict:
        row = self._processing_job_or_raise(run_id)
        source = self.connection.execute("SELECT * FROM video_sources WHERE id = ?", (row["source_id"],)).fetchone()
        scene_row = self.connection.execute("SELECT * FROM scene_versions WHERE id = ?", (row["scene_version_id"],)).fetchone()
        if source is None or scene_row is None:
            raise ValueError("job source or scene snapshot missing")
        scene = scene_from_geometry(dict(scene_row))
        if scene.source_fingerprint != source["fingerprint_sha256"]:
            self._fail_processing_job(run_id, "STALE_INPUT", "stale_input", "Scene source fingerprint does not match source.")
            self.connection.commit()
            raise StaleWriteError("Scene source fingerprint no longer matches the job source.")
        config = json.loads(row["processing_config_json"] or "{}")
        fixture_id = config.get("fixture_id", "default-crossing-fixture")
        synthetic_input = self._synthetic_fixture_payload(fixture_id, dict(scene_row), source["fingerprint_sha256"])
        validation = self.validate_synthetic_input(row["project_id"], synthetic_input, int(scene_row["version"]))
        if not validation["valid"]:
            self._fail_processing_job(run_id, "SYNTHETIC_VALIDATION_FAILED", "validation", json.dumps(validation["errors"]))
            self.connection.commit()
            raise ValueError(json.dumps(validation["errors"]))
        self._transition_processing_job(run_id, JobState.RUNNING, ProgressPhase.FINALIZING_EVENTS, 45, "finalizing_events")
        track_set = parse_track_set(synthetic_input)
        contract = TimeContract(
            source_started_at=datetime.fromisoformat(source["source_started_at"]),
            timezone_name=source["timezone_name"],
            analysis_start_pts_ms=source["analysis_start_pts_ms"],
            analysis_end_pts_ms=source["analysis_end_pts_ms"],
            interval_origin_pts_ms=source["interval_origin_pts_ms"],
        )
        with self.connection:
            result = execute_synthetic_counting(
                run_id,
                track_set,
                scene,
                contract,
                time_configured=bool(source["recording_time_configured"]),
            )
            self._transition_processing_job(run_id, JobState.RUNNING, ProgressPhase.AGGREGATING, 70, "aggregating")
            self.connection.execute(
                """
                INSERT INTO synthetic_counting_runs(
                  id, run_id, project_id, source_id, scene_version_id, synthetic_input_json,
                  synthetic_input_fingerprint, engine_version, policy_version, tolerance_json,
                  taxonomy_version, provenance_json, stale, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    new_id("syn"),
                    run_id,
                    row["project_id"],
                    source["id"],
                    scene_row["id"],
                    json.dumps(synthetic_input, sort_keys=True),
                    canonical_fingerprint(synthetic_input),
                    result.engine_version,
                    result.policy_version,
                    json.dumps(CountingTolerances().__dict__, sort_keys=True),
                    track_set.taxonomy_version,
                    json.dumps({"provenance": "synthetic", "validation_only": True}),
                    now(),
                ),
            )
            for track in track_set.tracks:
                prepared = _prepare_track_classification(track)
                self.connection.execute(
                    "INSERT INTO track_summaries(id, run_id, track_id, summary_json, evidence_schema_version, runtime_configuration_hash, runtime_provenance_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_id("track"),
                        run_id,
                        prepared.track_id,
                        json.dumps(asdict(prepared), sort_keys=True),
                        TRACK_EVIDENCE_SCHEMA_VERSION,
                        row["runtime_configuration_hash"],
                        json.dumps({"status": row["provenance_status"], "runtime_configuration_hash": row["runtime_configuration_hash"]}, sort_keys=True),
                    ),
                )
            self._transition_processing_job(run_id, JobState.RUNNING, ProgressPhase.PERSISTING_RESULTS, 90, "persisting_results")
            for event in result.events:
                self._insert_crossing_event(run_id, event)
            for exclusion in result.exclusions:
                self.connection.execute(
                    """
                    INSERT INTO synthetic_counting_exclusions(
                      id, run_id, track_id, counting_line_id, reason, timestamp_ms, detail, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("exc"),
                        run_id,
                        exclusion.track_id,
                        exclusion.line_id,
                        exclusion.reason.value,
                        exclusion.timestamp_ms,
                        exclusion.detail,
                        now(),
                    ),
                )
            for warning in result.warnings:
                self.connection.execute(
                    """
                    INSERT INTO synthetic_counting_warnings(id, run_id, code, field, detail, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("warn"),
                        run_id,
                        warning.get("code", "warning"),
                        warning.get("field", ""),
                        warning.get("detail", ""),
                        now(),
                    ),
                )
            self._create_engineering_result(run_id)
            snapshot = self._create_aggregate_snapshot(run_id, stale=False)
            self._transition_processing_job(run_id, JobState.COMPLETED, ProgressPhase.COMPLETED, 100, "job_completed")
            self.connection.execute(
                """
                UPDATE analysis_runs
                SET state = 'processing_complete', completed_at = ?, result_ready = 1
                WHERE id = ?
                """,
                (now(), run_id),
            )
            self._set_initial_current_processing_run(row["project_id"], run_id)
            self._mark_project(row["project_id"], "needs_review", stale=False)
        job = self.get_processing_job(run_id)
        job["aggregate_id"] = snapshot["id"]
        job["event_count"] = len(result.events)
        job["exclusion_count"] = len(result.exclusions)
        return job

    def cancel_processing_job(self, run_id: str, reason: str | None = None) -> dict:
        row = self._processing_job_or_raise(run_id)
        if is_terminal(row["job_state"]):
            raise ValueError("terminal job cannot be cancelled")
        self._transition_processing_job(
            run_id,
            JobState.CANCELLATION_REQUESTED,
            ProgressPhase.PROCESSING,
            int(row["progress_percent"] or 0),
            "cancellation_requested",
            {"reason": reason},
        )
        self.connection.execute("UPDATE analysis_runs SET cancellation_requested_at = ? WHERE id = ?", (now(), run_id))
        if row["job_state"] in {JobState.CREATED.value, JobState.QUEUED.value}:
            self._transition_processing_job(run_id, JobState.CANCELLED, ProgressPhase.CANCELLED, int(row["progress_percent"] or 0), "job_cancelled")
            self.connection.execute("UPDATE analysis_runs SET state = 'cancelled', cancelled_at = ? WHERE id = ?", (now(), run_id))
        self.connection.commit()
        return self.get_processing_job(run_id)

    def retry_processing_job(self, run_id: str) -> dict:
        row = self._processing_job_or_raise(run_id)
        if row["job_state"] not in {JobState.FAILED.value, JobState.CANCELLED.value}:
            raise ValueError("only failed or cancelled jobs can be retried")
        config = json.loads(row["processing_config_json"] or "{}")
        retry_configuration = None
        if str(config.get("mode", "SYNTHETIC")).upper() == ProcessingMode.REAL_VIDEO.value:
            retry_configuration = dict(config.get("resolved_parameters") or {})
            if not retry_configuration:
                retry_configuration = {
                    key: config[key]
                    for key in {
                        "detector_id",
                        "tracker_id",
                        "device_mode",
                        "confidence_threshold",
                        "iou_threshold",
                        "image_size",
                        "frame_stride",
                        "track_activation_threshold",
                        "lost_track_buffer",
                        "minimum_iou_threshold",
                        "minimum_consecutive_frames",
                        "anchor",
                        "sampling",
                    }
                    if key in config
                }
        retry_mode = str(config.get("mode", "SYNTHETIC"))
        retry_fixture_id = (
            config.get("fixture_id", "default-crossing-fixture")
            if retry_mode.upper() == ProcessingMode.SYNTHETIC.value
            else None
        )
        return self.submit_processing_job(
            row["project_id"],
            fixture_id=retry_fixture_id,
            mode=retry_mode,
            configuration=retry_configuration,
            expected_scene_version=int(row["scene_revision"]) if row["scene_revision"] is not None else None,
            run_again=True,
            auto_start=True,
            retry_of_run_id=run_id,
            processing_configuration_revision_id=config.get("processing_configuration_revision_id"),
        )

    def fail_stale_processing_jobs(self) -> list[dict]:
        current = datetime.now(timezone.utc).isoformat()
        rows = self.connection.execute(
            """
            SELECT * FROM analysis_runs
            WHERE job_state IN ('STARTING', 'RUNNING', 'CANCELLATION_REQUESTED')
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < ?
            """,
            (current,),
        ).fetchall()
        failed: list[dict] = []
        for row in rows:
            self._fail_processing_job(row["id"], "WORKER_LOST", "worker_liveness", "Worker lease expired before terminal result.")
            failed.append(self.get_processing_job(row["id"]))
        self.connection.commit()
        return failed

    def list_processing_jobs(self, project_id: str, limit: int = 25) -> list[dict]:
        return [
            self._public_processing_job(row)
            for row in self.connection.execute(
                """
                SELECT * FROM analysis_runs
                WHERE project_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (project_id, limit),
            )
        ]

    def get_processing_job(self, run_id: str) -> dict:
        return self._public_processing_job(self._processing_job_or_raise(run_id))

    def processing_readiness(self) -> dict:
        active_count = self.connection.execute(
            "SELECT COUNT(*) AS count FROM analysis_runs WHERE job_state IN ('QUEUED', 'STARTING', 'RUNNING', 'CANCELLATION_REQUESTED')"
        ).fetchone()["count"]
        root = Path(__file__).resolve().parents[3]
        registry_path = root / "model_registry.json"
        media = self.media_runtime_status()
        runtime_payload: dict[str, Any] = {"blockers": ["model_registry_missing"], "warnings": []}
        if registry_path.exists():
            try:
                from tools.ai_stack.registry import load_registry

                # Keep the API readiness probe bounded and model-free. Importing
                # torch in a child process is intentionally reserved for the
                # claimed worker; the worker records the resolved device later.
                runtime_payload = self._fast_ai_runtime_payload(root, load_registry(registry_path))
            except Exception as exc:
                runtime_payload = {"blockers": ["ai_runtime_probe_failed"], "warnings": [str(exc)]}
        packages = {item["package"]: item for item in runtime_payload.get("packages", [])}
        weights = {item["model_id"]: item for item in runtime_payload.get("weights", [])}
        detector = packages.get("ultralytics", {})
        tracker = packages.get("trackers", {})
        detector_weight = weights.get("detector.ultralytics-yolo11n-coco", {})
        relevant_blockers = [str(blocker) for blocker in runtime_payload.get("blockers", [])]
        detector_ready = bool(detector.get("matches_required") and detector_weight.get("matches_expected") is True)
        tracker_ready = bool(tracker.get("matches_required"))
        python_ready = bool(runtime_payload.get("python_available"))
        real_ready = bool(
            media["ffmpeg"]["available"]
            and media["ffprobe"]["available"]
            and media["paired_version_consistent"]
            and python_ready
            and detector_ready
            and tracker_ready
            and not relevant_blockers
            and Path(__file__).resolve().parents[2].joinpath("worker", "processing_worker.py").exists()
        )
        torch_payload = runtime_payload.get("torch", {})
        resolved_device = "AUTO"
        readiness_detail = (
            "YOLO11n + ByteTrack + incremental FFmpeg decoder are available."
            if real_ready
            else f"Real-video processing is blocked: {relevant_blockers[0] if relevant_blockers else 'media_runtime_unavailable'}."
        )
        return {
            "state": "synthetic_busy" if active_count else "synthetic_idle",
            "ready": True,
            "detail": "Separate worker boundary is available; processing mode is selected per job.",
            "active_jobs": int(active_count),
            "model_runtime": {
                "state": "available" if python_ready else "unavailable",
                "ready": python_ready,
                "detail": runtime_payload.get("python_executable"),
            },
            # Kept for the original core-readiness contract. The real-mode gate is the
            # explicit real_inference component below, which includes the checksum.
            "model_weights": {
                "state": "optional_real_mode",
                "ready": False,
                "detail": "Use processing.real_inference for detector/tracker readiness.",
            },
            "detector": {
                "id": "detector.ultralytics-yolo11n-coco",
                "state": "ready" if detector_ready else "blocked",
                "ready": detector_ready,
                "package": detector,
                "weight": detector_weight,
            },
            "tracker": {
                "id": "tracker.trackers-bytetrack",
                "state": "ready" if tracker.get("matches_required") else "blocked",
                "ready": bool(tracker.get("matches_required")),
                "package": tracker,
            },
            "device": {
                "requested": DeviceMode.AUTO.value,
                "resolved": resolved_device,
                "cuda_available": torch_payload.get("cuda_available"),
                "ready": bool(python_ready and runtime_payload.get("cpu_provider_available")),
            },
            "real_inference": {
                "mode": ProcessingMode.REAL_VIDEO.value,
                "state": "real_ready" if real_ready else (relevant_blockers[0] if relevant_blockers else "media_runtime_unavailable"),
                "ready": real_ready,
                "detail": readiness_detail,
                "blockers": relevant_blockers or ([] if real_ready else list(media.get("warnings", []))),
                "warnings": list(runtime_payload.get("warnings", [])),
                "detector_id": "detector.ultralytics-yolo11n-coco",
                "tracker_id": "tracker.trackers-bytetrack",
                "device": {"requested": DeviceMode.AUTO.value, "resolved": resolved_device, "resolution": "worker_claim"},
                "runtime": runtime_payload,
            },
        }

    def list_processing_profiles(self) -> list[dict[str, Any]]:
        return self.processing_profile_store.list_profiles()

    def get_processing_profile(self, profile_code: str, profile_revision: str | None = None) -> dict[str, Any]:
        return self.processing_profile_store.get_profile(profile_code, profile_revision)

    def get_processing_parameter_schema(self) -> dict[str, Any]:
        return self.processing_profile_store.parameter_schema()

    def resolve_processing_configuration(self, project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._project_or_raise(project_id)
        source = self._latest_source_or_raise(project_id)
        self._latest_scene_or_raise(project_id, payload.get("expected_scene_version"))
        guided = payload.get("guided_settings") or {}
        overrides = payload.get("expert_overrides") or {}
        readiness = self.processing_readiness()
        cuda_available = readiness.get("device", {}).get("cuda_available")
        return self.processing_profile_store.resolve(
            str(payload.get("profile_code", "BALANCED")),
            guided,
            overrides,
            media_duration_ms=int(source["duration_ms"]) if source["duration_ms"] is not None else None,
            analysis_start_pts_ms=int(source["analysis_start_pts_ms"] or 0),
            analysis_end_pts_ms=int(source["analysis_end_pts_ms"] or 0),
            cuda_available=cuda_available if isinstance(cuda_available, bool) else None,
        )

    def create_processing_configuration_revision(
        self,
        project_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._project_or_raise(project_id)
        source = self._latest_source_or_raise(project_id)
        self._latest_scene_or_raise(project_id, payload.get("expected_scene_version"))
        validation = self.resolve_processing_configuration(project_id, payload)
        if not validation["valid"]:
            raise ValueError(json.dumps(validation["errors"], sort_keys=True))
        return self.processing_profile_store.create_configuration_revision(
            project_id=project_id,
            profile_code=str(payload.get("profile_code", "BALANCED")),
            guided_settings=payload.get("guided_settings") or {},
            expert_overrides=payload.get("expert_overrides") or {},
            created_by=str(payload.get("created_by", "operator")),
            media_duration_ms=int(source["duration_ms"]) if source["duration_ms"] is not None else None,
            analysis_start_pts_ms=int(source["analysis_start_pts_ms"] or 0),
            analysis_end_pts_ms=int(source["analysis_end_pts_ms"] or 0),
            validation=validation,
        )

    def get_processing_configuration_revision(self, revision_id: str) -> dict[str, Any]:
        return self.processing_profile_store.get_configuration(revision_id)

    def create_preview_run(self, project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._project_or_raise(project_id)
        source = self._latest_source_or_raise(project_id)
        scene_row = self._latest_scene_or_raise(project_id, payload.get("expected_scene_version"))
        start_pts_ms = int(payload.get("start_pts_ms", 0))
        end_pts_ms = int(payload.get("end_pts_ms", start_pts_ms + 30_000))
        source_duration = int(source["duration_ms"]) if source["duration_ms"] is not None else None
        source_start_pts_ms = int(source["analysis_start_pts_ms"] or source["analysis_window_start_ms"] or 0)
        source_end_pts_ms = int(source["analysis_end_pts_ms"] or source["analysis_window_end_ms"] or source_duration or 0)
        if end_pts_ms <= start_pts_ms or end_pts_ms - start_pts_ms > 120_000:
            raise ValueError("preview interval must be positive and no longer than 120 seconds")
        if source_duration is not None and end_pts_ms > source_duration:
            raise ValueError("preview interval exceeds media duration")
        if start_pts_ms < source_start_pts_ms or end_pts_ms > source_end_pts_ms:
            raise ValueError("preview interval must stay within the source analysis window")
        mode = str(payload.get("mode", ProcessingMode.REAL_VIDEO.value)).upper()
        fixture_id = payload.get("fixture_id")
        if mode == ProcessingMode.SYNTHETIC.value and str(source["source_type"] or "") == "local_upload":
            raise ValueError("synthetic_preview_requires_approved_fixture_source")
        if mode == ProcessingMode.SYNTHETIC.value and not fixture_id:
            raise ValueError("synthetic_preview_requires_fixture_id")
        if mode == ProcessingMode.REAL_VIDEO.value and fixture_id is not None:
            raise ValueError("real_video_preview_does_not_accept_fixture_id")
        if mode == ProcessingMode.REAL_VIDEO.value and (
            str(source["source_type"] or "") != "local_upload"
            or str(source["readiness_state"] or "") != "media_ready"
            or not source["managed_media_path"]
            or not source["fingerprint_sha256"]
        ):
            raise ValueError("real_video_preview_requires_ready_uploaded_source")
        configuration_id = payload.get("processing_configuration_revision_id")
        idempotency_key = payload.get("idempotency_key")
        if not configuration_id and idempotency_key:
            existing_preview = self.connection.execute(
                "SELECT processing_configuration_revision_id FROM preview_runs WHERE project_id = ? AND idempotency_key = ?",
                (project_id, str(idempotency_key)),
            ).fetchone()
            if existing_preview is not None:
                configuration_id = existing_preview["processing_configuration_revision_id"]
        if configuration_id:
            configuration = self.processing_profile_store.get_configuration(str(configuration_id))
            if configuration.get("project_id") not in {None, project_id}:
                raise ValueError("processing configuration belongs to another project")
        else:
            configuration = self.create_processing_configuration_revision(
                project_id,
                {
                    "profile_code": payload.get("profile_code", "BALANCED"),
                    "guided_settings": payload.get("guided_settings") or {},
                    "expert_overrides": payload.get("expert_overrides") or {},
                    "expected_scene_version": payload.get("expected_scene_version"),
                    "created_by": payload.get("created_by", "operator"),
                },
            )
            configuration_id = configuration["id"]
        runtime_hash = configuration.get("runtime_configuration_hash")
        configuration_hash = str(configuration["configuration_hash"])
        provenance_status = str(configuration.get("runtime_provenance_status") or "LEGACY_UNRESOLVED")
        request_fingerprint = preview_request_fingerprint(
            project_id=project_id,
            source_id=str(source["id"]),
            source_fingerprint=str(source["fingerprint_sha256"]),
            scene_version_id=str(scene_row["id"]),
            scene_semantic_hash=str(scene_row["config_hash"]),
            start_pts_ms=start_pts_ms,
            end_pts_ms=end_pts_ms,
            processing_configuration_revision_id=str(configuration_id),
            configuration_hash=configuration_hash,
            mode=mode,
            fixture_id=fixture_id,
        )
        preview = self.processing_profile_store.create_preview(
            project_id=project_id,
            source_id=str(source["id"]),
            source_fingerprint=str(source["fingerprint_sha256"]),
            scene_version_id=str(scene_row["id"]),
            scene_revision=str(scene_row["version"]),
            scene_semantic_hash=str(scene_row["config_hash"]),
            start_pts_ms=start_pts_ms,
            end_pts_ms=end_pts_ms,
            configuration_revision_id=str(configuration_id),
            runtime_configuration_hash=str(runtime_hash) if runtime_hash else None,
            request_fingerprint=request_fingerprint,
            mode=mode,
            idempotency_key=idempotency_key,
            created_by=str(payload.get("created_by", "operator")),
            provenance_status=provenance_status,
        )
        # A synthetic preview is intentionally evaluated in memory and written to
        # preview tables only. It cannot call _insert_crossing_event or create a
        # 6A/6B result revision.
        if preview["status"] == "QUEUED" and mode == ProcessingMode.SYNTHETIC.value:
            self._complete_synthetic_preview(preview["id"], source, scene_row, str(fixture_id), configuration)
        return self.processing_profile_store.get_preview(preview["id"])

    def get_preview_run(self, preview_id: str) -> dict[str, Any]:
        return self.processing_profile_store.get_preview(preview_id)

    def claim_next_preview_run(self, worker_id: str, lease_seconds: int = 120) -> dict[str, Any] | None:
        self.connection.commit()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            expired_rows = self.connection.execute(
                """
                SELECT id, worker_id, attempt_number
                FROM preview_runs
                WHERE status = 'RUNNING' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?
                ORDER BY lease_expires_at, id
                """,
                (now(),),
            ).fetchall()
            for expired in expired_rows:
                self.connection.execute(
                    """
                    UPDATE preview_runs
                    SET status = 'QUEUED', worker_id = NULL, claim_token = NULL,
                        lease_expires_at = NULL, heartbeat_at = NULL
                    WHERE id = ? AND status = 'RUNNING'
                    """,
                    (expired["id"],),
                )
                self.processing_profile_store._record_preview_state_event(
                    str(expired["id"]),
                    "LEASE_EXPIRED_REQUEUED",
                    expired["worker_id"],
                    int(expired["attempt_number"] or 0),
                    {"reason": "lease_expired"},
                )
            row = self.connection.execute(
                "SELECT * FROM preview_runs WHERE status = 'QUEUED' ORDER BY created_at, id LIMIT 1"
            ).fetchone()
            if row is None:
                self.connection.commit()
                return None
            claim_token = new_id("preview-claim")
            lease = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
            claimed_at = now()
            self.connection.execute(
                """
                UPDATE preview_runs
                SET status = 'RUNNING', worker_id = ?, claim_token = ?,
                    started_at = COALESCE(started_at, ?), heartbeat_at = ?,
                    lease_expires_at = ?, attempt_number = COALESCE(attempt_number, 0) + 1
                WHERE id = ? AND status = 'QUEUED'
                """,
                (worker_id, claim_token, claimed_at, claimed_at, lease, row["id"]),
            )
            if self.connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise PreviewConflict("PREVIEW_CLAIM_CONFLICT", "Preview was claimed by another worker.", preview_id=str(row["id"]))
            self.processing_profile_store._record_preview_state_event(
                str(row["id"]),
                "CLAIMED",
                worker_id,
                int(row["attempt_number"] or 0) + 1,
                {"lease_expires_at": lease},
            )
            self.connection.commit()
            return self.get_preview_run(str(row["id"]))
        except Exception:
            self.connection.rollback()
            raise

    def execute_claimed_preview_run(self, preview_id: str, worker_id: str) -> dict[str, Any]:
        stage = "preview_execute_started"
        self._emit_preview_event(
            stage,
            preview_id=preview_id,
            worker_id=worker_id,
            stage=stage,
            ownership_state="UNKNOWN",
        )
        row = self.connection.execute("SELECT * FROM preview_runs WHERE id = ?", (preview_id,)).fetchone()
        if row is None:
            self._emit_preview_event(
                "preview_execute_failed",
                preview_id=preview_id,
                worker_id=worker_id,
                stage=stage,
                error_code="PREVIEW_NOT_FOUND",
                exception_class="ValueError",
                ownership_state="MISSING",
            )
            raise ValueError("preview run not found")
        claim_token = str(row["claim_token"] or "")
        if not claim_token:
            raise PreviewConflict("PREVIEW_OWNERSHIP_REQUIRED", "Preview has no active worker claim.", preview_id=preview_id)
        self.processing_profile_store.check_preview_control(preview_id, worker_id, claim_token)
        stage = "preview_control_verified"
        self._emit_preview_event(
            stage,
            preview_id=preview_id,
            worker_id=worker_id,
            stage=stage,
            ownership_state=str(row["status"]),
        )
        callback_connections = _PreviewCallbackConnections(
            factory=self.preview_connection_factory,
            fallback_store=self.processing_profile_store,
            fallback_connection=self.connection,
        )

        def heartbeat() -> None:
            callback_stage = "preview_heartbeat"
            store, callback_connection = callback_connections.current()
            try:
                renewed = store.heartbeat_preview(preview_id, worker_id, claim_token)
            except PreviewConflict:
                raise
            except Exception as exc:
                self._emit_preview_event(
                    "preview_callback_failed",
                    preview_id=preview_id,
                    worker_id=worker_id,
                    stage=callback_stage,
                    connection=callback_connection,
                    error_code="PREVIEW_LIFECYCLE_ERROR",
                    exception_class=type(exc).__name__,
                    ownership_state="UNKNOWN",
                )
                raise PreviewLifecycleFailure(callback_stage, exc) from exc
            self._emit_preview_event(
                callback_stage,
                preview_id=preview_id,
                worker_id=worker_id,
                stage=callback_stage,
                connection=callback_connection,
                ownership_state=str(renewed["status"]),
            )

        def cancel_requested() -> bool:
            callback_stage = "preview_control_check"
            store, callback_connection = callback_connections.current()
            try:
                return store.check_preview_control(preview_id, worker_id, claim_token)
            except PreviewConflict:
                raise
            except Exception as exc:
                self._emit_preview_event(
                    "preview_callback_failed",
                    preview_id=preview_id,
                    worker_id=worker_id,
                    stage=callback_stage,
                    connection=callback_connection,
                    error_code="PREVIEW_LIFECYCLE_ERROR",
                    exception_class=type(exc).__name__,
                    ownership_state="UNKNOWN",
                )
                raise PreviewLifecycleFailure(callback_stage, exc) from exc

        def inference_status(phase: str, _detail: dict[str, Any]) -> None:
            nonlocal stage
            next_stage = f"preview_inference_{phase.lower()}"
            if stage == next_stage:
                return
            stage = next_stage
            self._emit_preview_event(
                "preview_inference_stage",
                preview_id=preview_id,
                worker_id=worker_id,
                stage=stage,
                phase=phase,
                ownership_state="RUNNING",
            )

        def finalize(
            status: str,
            statistics: Mapping[str, Any],
            *,
            warnings: list[Mapping[str, Any]] | None = None,
            evidence: list[Mapping[str, Any]] | None = None,
            events: list[Mapping[str, Any]] | None = None,
        ) -> dict[str, Any]:
            nonlocal stage
            stage = "preview_finalize_started"
            self._emit_preview_event(
                stage,
                preview_id=preview_id,
                worker_id=worker_id,
                stage=stage,
                terminal_status=status,
                ownership_state="RUNNING",
            )
            try:
                terminal = self.processing_profile_store.set_preview_result(
                    preview_id,
                    status=status,
                    statistics=statistics,
                    warnings=warnings,
                    evidence=evidence,
                    events=events,
                    worker_id=worker_id,
                    claim_token=claim_token,
                )
            except PreviewConflict as exc:
                self._emit_preview_event(
                    "preview_finalize_rejected",
                    preview_id=preview_id,
                    worker_id=worker_id,
                    stage=stage,
                    error_code=exc.code,
                    ownership_state="LOST_OR_TERMINAL",
                )
                terminal = self.processing_profile_store.get_preview(preview_id)
            except Exception as exc:
                self._emit_preview_event(
                    "preview_finalize_failed",
                    preview_id=preview_id,
                    worker_id=worker_id,
                    stage=stage,
                    error_code="PREVIEW_LIFECYCLE_ERROR",
                    exception_class=type(exc).__name__,
                    ownership_state="UNKNOWN",
                )
                raise PreviewLifecycleFailure(stage, exc) from exc
            stage = "preview_finalize_completed"
            self._emit_preview_event(
                stage,
                preview_id=preview_id,
                worker_id=worker_id,
                stage=stage,
                terminal_status=str(terminal["status"]),
                ownership_state=str(terminal["status"]),
            )
            return terminal

        source = self.connection.execute("SELECT * FROM video_sources WHERE id = ?", (row["source_id"],)).fetchone()
        scene_row = self.connection.execute("SELECT * FROM scene_versions WHERE id = ?", (row["scene_version_id"],)).fetchone()
        configuration = self.processing_profile_store.get_configuration(row["processing_configuration_revision_id"])
        if source is not None:
            stage = "preview_source_loaded"
            self._emit_preview_event(stage, preview_id=preview_id, worker_id=worker_id, stage=stage, ownership_state="RUNNING")
        if scene_row is not None:
            stage = "preview_scene_loaded"
            self._emit_preview_event(stage, preview_id=preview_id, worker_id=worker_id, stage=stage, ownership_state="RUNNING")
        stage = "preview_configuration_loaded"
        self._emit_preview_event(stage, preview_id=preview_id, worker_id=worker_id, stage=stage, ownership_state="RUNNING")
        if source is None or scene_row is None:
            return finalize(
                "FAILED",
                {"error_code": "STALE_INPUT", "run_type": "PREVIEW_ONLY"},
                warnings=[{"code": "preview_input_missing"}],
            )
        managed_path = Path(str(source["managed_media_path"] or ""))
        local_data_dir = Path(
            os.getenv("TVA_LOCAL_DATA_DIR", str(Path(__file__).resolve().parents[3] / ".local-data"))
        ).expanduser().resolve()
        try:
            managed_path.resolve().relative_to(local_data_dir)
        except ValueError:
            return finalize(
                "FAILED",
                {"error_code": "SOURCE_PATH_BLOCKED", "run_type": "PREVIEW_ONLY"},
                warnings=[{"code": "source_path_blocked"}],
            )
        if not managed_path.is_file() or managed_path.is_symlink():
            return finalize(
                "FAILED",
                {"error_code": "SOURCE_MISSING", "run_type": "PREVIEW_ONLY"},
                warnings=[{"code": "source_missing"}],
            )
        try:
            config = RealProcessingConfig.from_payload(configuration["resolved_parameters"])
            time_contract = TimeContract(
                source_started_at=datetime.fromisoformat(source["source_started_at"]),
                timezone_name=source["timezone_name"],
                analysis_start_pts_ms=int(row["start_pts_ms"]),
                analysis_end_pts_ms=int(row["end_pts_ms"]),
                interval_origin_pts_ms=int(source["interval_origin_pts_ms"] or 0),
            )
            runtime_status = media_runtime_status()
            stage = "preview_inference_started"
            self._emit_preview_event(stage, preview_id=preview_id, worker_id=worker_id, stage=stage, ownership_state="RUNNING")
            try:
                result = run_real_video_inference(
                    root=Path(__file__).resolve().parents[3],
                    run_id=preview_id,
                    source_path=managed_path.resolve(),
                    source_fingerprint=str(source["fingerprint_sha256"]),
                    source_width=int(source["width"] or 0),
                    source_height=int(source["height"] or 0),
                    source_first_pts_ms=self._source_first_pts_ms(source),
                    source_frame_count=int(source["frame_count"]) if source["frame_count"] is not None else None,
                    source_duration_ms=int(source["duration_ms"]) if source["duration_ms"] is not None else None,
                    scene=scene_from_geometry(dict(scene_row)),
                    time_contract=time_contract,
                    source_time_configured=bool(source["recording_time_configured"]),
                    config=config,
                    ffmpeg_executable=runtime_status.ffmpeg.executable,
                    cancel_requested=cancel_requested,
                    status_callback=inference_status,
                    heartbeat_callback=heartbeat,
                    configured_runtime_configuration_hash=str(row["runtime_configuration_hash"] or configuration.get("runtime_configuration_hash") or ""),
                    configured_runtime_provenance=configuration.get("runtime_provenance"),
                    configuration_hash=str(configuration.get("configuration_hash") or "") or None,
                    request_provenance_hash=str(configuration.get("request_provenance_hash") or "") or None,
                )
            finally:
                callback_connections.close()
            stage = "preview_inference_completed"
            self._emit_preview_event(stage, preview_id=preview_id, worker_id=worker_id, stage=stage, ownership_state="RUNNING")
        except PreviewConflict as exc:
            self._emit_preview_event(
                "preview_execute_fenced",
                preview_id=preview_id,
                worker_id=worker_id,
                stage=stage,
                error_code=exc.code,
                ownership_state="LOST_OR_TERMINAL",
            )
            return self.processing_profile_store.get_preview(preview_id)
        except RealInferenceCancelled:
            return finalize(
                "CANCELLED",
                {
                    "error_code": "CANCELLED",
                    "run_type": "PREVIEW_ONLY",
                    "runtime_configuration_hash": row["runtime_configuration_hash"],
                    "request_fingerprint": row["request_fingerprint"],
                },
            )
        except RealInferenceError as exc:
            self._emit_preview_event(
                "preview_execute_failed",
                preview_id=preview_id,
                worker_id=worker_id,
                stage=stage,
                error_code=exc.code,
                exception_class=type(exc).__name__,
                ownership_state="RUNNING",
            )
            return finalize(
                "FAILED",
                {
                    "error_code": exc.code,
                    "run_type": "PREVIEW_ONLY",
                    "runtime_configuration_hash": row["runtime_configuration_hash"],
                    "request_fingerprint": row["request_fingerprint"],
                },
                warnings=[{"code": exc.code, "detail": exc.detail}],
            )
        except Exception as exc:
            lifecycle_failure = isinstance(exc, PreviewLifecycleFailure)
            failure_stage = exc.stage if lifecycle_failure else stage
            exception_class = exc.exception_class if lifecycle_failure else type(exc).__name__
            error_code = "PREVIEW_LIFECYCLE_ERROR" if lifecycle_failure or isinstance(exc, sqlite3.Error) else "PREVIEW_WORKER_ERROR"
            safe_detail = "Preview lifecycle operation failed." if error_code == "PREVIEW_LIFECYCLE_ERROR" else "Unexpected preview worker failure."
            self._emit_preview_event(
                "preview_execute_failed",
                preview_id=preview_id,
                worker_id=worker_id,
                stage=failure_stage,
                error_code=error_code,
                exception_class=exception_class,
                ownership_state="RUNNING",
            )
            return finalize(
                "FAILED",
                {
                    "error_code": error_code,
                    "run_type": "PREVIEW_ONLY",
                    "detail": safe_detail,
                    "stage": failure_stage,
                    "exception_class": exception_class,
                    "runtime_configuration_hash": row["runtime_configuration_hash"],
                },
                warnings=[{"code": error_code.lower(), "detail": safe_detail, "stage": failure_stage}],
            )
        events = [
            {
                "event_id": event.event_id,
                "line_id": event.line_id,
                "track_id": event.track_id,
                "crossing_timestamp_ms": event.crossing_timestamp_ms,
                "direction": event.direction.value,
                "synthetic_class": event.synthetic_class,
                "provisional_class": event.provisional_class,
                "classification_status": event.classification_status,
                "event_time_status": event.event_time_status,
            }
            for event in result.counting_result.events
        ]
        by_line: dict[str, int] = {}
        by_direction: dict[str, int] = {}
        by_class: dict[str, int] = {}
        for event in events:
            line_id = str(event["line_id"])
            direction = str(event["direction"])
            event_class = str(event.get("synthetic_class") or event.get("provisional_class") or "unknown")
            by_line[line_id] = by_line.get(line_id, 0) + 1
            by_direction[direction] = by_direction.get(direction, 0) + 1
            by_class[event_class] = by_class.get(event_class, 0) + 1
        statistics = {
            **result.stats,
            "by_line": by_line,
            "by_direction": by_direction,
            "by_class": by_class,
            "run_type": "PREVIEW_ONLY",
            "preview_start_pts_ms": row["start_pts_ms"],
            "preview_end_pts_ms": row["end_pts_ms"],
            "configuration_revision_id": row["processing_configuration_revision_id"],
            "configuration_hash": result.stats.get("configuration_hash") or configuration.get("configuration_hash"),
            "request_provenance_hash": result.stats.get("request_provenance_hash") or configuration.get("request_provenance_hash"),
            "runtime_configuration_hash": result.stats.get("runtime_configuration_hash"),
            "actual_runtime_configuration_hash": result.stats.get("actual_runtime_configuration_hash"),
            "request_fingerprint": row["request_fingerprint"],
            "provenance_status": result.stats.get("provenance_status", row["provenance_status"]),
            "disclosures": ["PREVIEW_ONLY", "NOT_PRODUCTION_RESULT"],
        }
        return finalize(
            "COMPLETED",
            statistics,
            warnings=list(result.counting_result.warnings),
            evidence=list(result.tracks[:32]),
            events=events,
        )

    def list_preview_runs(self, project_id: str, limit: int = 25) -> list[dict[str, Any]]:
        self._project_or_raise(project_id)
        return self.processing_profile_store.list_previews(project_id, limit)

    def fail_claimed_preview_run(self, preview_id: str, worker_id: str, detail: str) -> dict[str, Any]:
        """Durably fail an unexpected worker shutdown/error without bypassing fencing."""
        row = self.connection.execute("SELECT * FROM preview_runs WHERE id = ?", (preview_id,)).fetchone()
        if row is None:
            raise ValueError("preview run not found")
        if row["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return self.processing_profile_store.get_preview(preview_id)
        if row["worker_id"] != worker_id or not row["claim_token"]:
            raise PreviewConflict("PREVIEW_OWNERSHIP_LOST", "Preview ownership no longer belongs to this worker.", preview_id=preview_id)
        try:
            return self.processing_profile_store.set_preview_result(
                preview_id,
                status="FAILED",
                statistics={
                    "error_code": "PREVIEW_WORKER_EXCEPTION",
                    "detail": detail,
                    "run_type": "PREVIEW_ONLY",
                    "runtime_configuration_hash": row["runtime_configuration_hash"],
                },
                warnings=[{"code": "preview_worker_exception", "detail": detail}],
                worker_id=worker_id,
                claim_token=str(row["claim_token"]),
            )
        except PreviewConflict:
            return self.processing_profile_store.get_preview(preview_id)

    def cancel_preview_run(self, preview_id: str, reason: str | None = None) -> dict[str, Any]:
        return self.processing_profile_store.cancel_preview(preview_id, reason)

    def promote_preview_run(self, preview_id: str, *, auto_start: bool = False) -> dict[str, Any]:
        preview = self.processing_profile_store.get_preview(preview_id)
        if preview["status"] != "COMPLETED":
            raise ValueError("only completed previews can be promoted")
        if preview.get("promoted_full_run_id"):
            return self.get_processing_job(str(preview["promoted_full_run_id"]))
        source = self.connection.execute("SELECT * FROM video_sources WHERE id = ?", (preview["source_id"],)).fetchone()
        if source is None:
            raise ValueError("source is required")
        config = self.processing_profile_store.get_configuration(preview["processing_configuration_revision_id"])
        parameters = dict(config["resolved_parameters"])
        job = self.submit_processing_job(
            preview["project_id"],
            fixture_id=None if preview["mode"] == ProcessingMode.REAL_VIDEO.value else "api-acceptance",
            mode=preview["mode"],
            configuration=parameters,
            expected_scene_version=int(preview["scene_revision"]),
            run_again=True,
            auto_start=auto_start,
            processing_configuration_revision_id=preview["processing_configuration_revision_id"],
        )
        updated = self.connection.execute(
            "UPDATE preview_runs SET promoted_full_run_id = ? WHERE id = ? AND status = 'COMPLETED' AND promoted_full_run_id IS NULL",
            (job["id"], preview_id),
        )
        if updated.rowcount != 1:
            current = self.processing_profile_store.get_preview(preview_id)
            if current.get("promoted_full_run_id"):
                return self.get_processing_job(str(current["promoted_full_run_id"]))
            raise PreviewConflict(
                "PREVIEW_PROMOTION_CONFLICT",
                "Preview promotion was claimed by another request.",
                preview_id=preview_id,
            )
        self.connection.commit()
        return job

    def compare_processing_configurations(self, project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._project_or_raise(project_id)
        source = self._latest_source_or_raise(project_id)
        scene = self._latest_scene_or_raise(project_id, payload.get("expected_scene_version"))
        ids = [str(item) for item in payload.get("processing_configuration_revision_ids", [])]
        if len(ids) < 2:
            raise ValueError("at least two processing configurations are required")
        start_pts_ms = int(payload.get("start_pts_ms", 0))
        end_pts_ms = int(payload.get("end_pts_ms", start_pts_ms + 30_000))
        source_duration = int(source["duration_ms"]) if source["duration_ms"] is not None else None
        if end_pts_ms <= start_pts_ms or end_pts_ms - start_pts_ms > 120_000:
            raise ValueError("comparison interval must be positive and no longer than 120 seconds")
        if source_duration is not None and end_pts_ms > source_duration:
            raise ValueError("comparison interval exceeds media duration")
        configurations = [self.processing_profile_store.get_configuration(item) for item in ids]
        if any(item.get("project_id") not in {None, project_id} for item in configurations):
            raise ValueError("processing configuration belongs to another project")
        previews = {
            str(preview_id): self.processing_profile_store.get_preview(str(preview_id))
            for preview_id in payload.get("preview_run_ids", [])
        }
        for preview in previews.values():
            if preview["source_fingerprint_sha256"] != source["fingerprint_sha256"] or preview["scene_revision"] != str(scene["version"]):
                raise ValueError("comparison previews must use the current source and scene")
        comparison_rows: list[dict[str, Any]] = []
        for item in configurations:
            preview = next(
                (value for value in previews.values() if value["processing_configuration_revision_id"] == item["id"]),
                None,
            )
            stats = preview.get("statistics") if preview else None
            preview_runtime_hash = preview.get("runtime_configuration_hash") if preview else None
            comparison_rows.append(
                {
                    "configuration_revision_id": item["id"],
                    "profile_revision": item["profile_revision"],
                    "configuration_hash": item.get("configuration_hash") or item["content_hash"],
                    "runtime_configuration_hash": preview_runtime_hash or item.get("runtime_configuration_hash"),
                    "request_provenance_hash": item.get("request_provenance_hash"),
                    "runtime_provenance_status": (
                        preview.get("provenance_status")
                        if preview
                        else item.get("runtime_provenance_status", "LEGACY_UNRESOLVED")
                    ),
                    "resolved_parameters": item["resolved_parameters"],
                    "preview_id": preview["id"] if preview else None,
                    "preview_status": preview["status"] if preview else "NOT_RUN",
                    "statistics": stats or {},
                    "accuracy_status": "NOT_AVAILABLE_WITHOUT_GROUND_TRUTH",
                }
            )
        comparison = {
            "rows": comparison_rows,
            "diagnostics": [
                "Compare throughput, processed frames, detections, tracks, crossing events, short-track exclusions, and warnings.",
                "No opaque weighted ranking is applied.",
                "Accuracy metrics require 6C ground-truth evidence and are not inferred from operational previews.",
            ],
        }
        return self.processing_profile_store.create_comparison(
            project_id=project_id,
            source_fingerprint=str(source["fingerprint_sha256"]),
            scene_revision=str(scene["version"]),
            start_pts_ms=start_pts_ms,
            end_pts_ms=end_pts_ms,
            configuration_revision_ids=ids,
            comparison=comparison,
            created_by=str(payload.get("created_by", "operator")),
        )

    def diff_processing_configurations(self, project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._project_or_raise(project_id)
        first_id = str(payload["first_processing_configuration_revision_id"])
        second_id = str(payload["second_processing_configuration_revision_id"])
        first = self.processing_profile_store.get_configuration(first_id)
        second = self.processing_profile_store.get_configuration(second_id)
        if first.get("project_id") not in {None, project_id} or second.get("project_id") not in {None, project_id}:
            raise ValueError("processing configuration belongs to another project")
        first_parameters = dict(first.get("resolved_parameters") or {})
        second_parameters = dict(second.get("resolved_parameters") or {})
        changed: dict[str, dict[str, Any]] = {}
        unchanged: list[str] = []
        for key in sorted(set(first_parameters) | set(second_parameters)):
            if first_parameters.get(key) == second_parameters.get(key):
                unchanged.append(key)
            else:
                changed[key] = {"from": first_parameters.get(key), "to": second_parameters.get(key)}
        first_runtime_hash = first.get("runtime_configuration_hash")
        second_runtime_hash = second.get("runtime_configuration_hash")
        runtime_equivalent = bool(first_runtime_hash and second_runtime_hash and first_runtime_hash == second_runtime_hash)
        return {
            "first_processing_configuration_revision_id": first_id,
            "second_processing_configuration_revision_id": second_id,
            "first_configuration_hash": first.get("configuration_hash") or first["content_hash"],
            "second_configuration_hash": second.get("configuration_hash") or second["content_hash"],
            "first_runtime_configuration_hash": first_runtime_hash,
            "second_runtime_configuration_hash": second_runtime_hash,
            "runtime_equivalent": runtime_equivalent,
            "runtime_equivalence_disclosure": (
                "Runtime-equivalent: normalized worker inputs and adapter/policy revisions match; request provenance may still differ."
                if runtime_equivalent
                else "Not runtime-equivalent, or legacy runtime identity is unresolved; compare only the explicitly shown normalized values."
            ),
            "changed_parameters": changed,
            "unchanged_parameters": unchanged,
            "disclosure": "Configuration diff compares resolved values; it does not establish accuracy or select a winner.",
        }

    def select_operational_candidate(self, project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._project_or_raise(project_id)
        configuration = self.processing_profile_store.get_configuration(str(payload["processing_configuration_revision_id"]))
        if configuration.get("project_id") not in {None, project_id}:
            raise ValueError("processing configuration belongs to another project")
        return self.processing_profile_store.select_candidate(
            project_id=project_id,
            configuration_revision_id=str(payload["processing_configuration_revision_id"]),
            selection_status=str(payload["selection_status"]),
            rationale=str(payload["rationale"]),
            created_by=str(payload.get("created_by", "operator")),
        )

    def create_capability_validation(self, project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._project_or_raise(project_id)
        configuration = self.processing_profile_store.get_configuration(str(payload["processing_configuration_revision_id"]))
        if configuration.get("project_id") not in {None, project_id}:
            raise ValueError("processing configuration belongs to another project")
        preview_id = payload.get("preview_run_id")
        preview = self.get_preview_run(str(preview_id)) if preview_id else None
        readiness = self.processing_readiness()
        environment = {
            "device": readiness.get("device", {}),
            "real_inference": readiness.get("real_inference", {}),
            "media_runtime": self.media_runtime_status(),
        }
        validation_type = str(payload["validation_type"])
        if validation_type == "RUNTIME":
            status = "READY" if readiness.get("real_inference", {}).get("ready") else "NOT_READY"
            evidence = {
                "runtime_probe": "completed",
                "model_load_and_first_inference": "not_measured_by_readiness_probe",
                "real_time_status": "NOT_ESTABLISHED",
            }
        elif validation_type in {"REAL_MEDIA", "BENCHMARK"}:
            # An operational preview is useful evidence, but it cannot become
            # real-media/accuracy validation without rights and ground truth.
            status = "PENDING"
            evidence = {
                "preview_status": preview["status"] if preview else "NOT_RUN",
                "rights_cleared_media": False,
                "ground_truth_available": False,
                "accuracy_status": "NOT_AVAILABLE_WITHOUT_GROUND_TRUTH",
            }
        else:
            status = "READY" if preview and preview["status"] == "COMPLETED" else "PENDING"
            evidence = {
                "preview_status": preview["status"] if preview else "NOT_RUN",
                "deterministic_fixture": True,
                "accuracy_status": "ENGINEERING_ONLY",
            }
        return self.processing_profile_store.create_capability_validation(
            project_id=project_id,
            configuration_revision_id=str(payload["processing_configuration_revision_id"]),
            preview_run_id=str(preview_id) if preview_id else None,
            validation_type=validation_type,
            status=status,
            evidence=evidence,
            environment=environment,
            created_by=str(payload.get("created_by", "operator")),
        )

    def list_capability_validations(self, project_id: str, limit: int = 25) -> list[dict[str, Any]]:
        self._project_or_raise(project_id)
        return self.processing_profile_store.list_capability_validations(project_id, limit)

    def _complete_synthetic_preview(
        self,
        preview_id: str,
        source: sqlite3.Row,
        scene_row: sqlite3.Row,
        fixture_id: str,
        configuration: Mapping[str, Any],
    ) -> None:
        preview = self.processing_profile_store.get_preview(preview_id)
        scene = scene_from_geometry(dict(scene_row))
        config = dict(configuration.get("resolved_parameters", {}))
        synthetic_input = self._synthetic_fixture_payload(fixture_id, dict(scene_row), source["fingerprint_sha256"])
        track_set = parse_track_set(synthetic_input)
        contract = TimeContract(
            source_started_at=datetime.fromisoformat(source["source_started_at"]),
            timezone_name=source["timezone_name"],
            analysis_start_pts_ms=preview["start_pts_ms"],
            analysis_end_pts_ms=preview["end_pts_ms"],
            interval_origin_pts_ms=source["interval_origin_pts_ms"],
        )
        result = execute_synthetic_counting(
            preview_id,
            track_set,
            scene,
            contract,
            tolerances=CountingTolerances(
                point_on_line=float(config.get("crossing_tolerance", 1e-9)),
                duplicate_crossing_time_ms=int(config.get("duplicate_crossing_cooldown_ms", 250)),
                spatial_hysteresis=float(config.get("crossing_hysteresis", 0.002)),
                minimum_side_distance=float(config.get("minimum_movement_distance", 0.002)),
                minimum_side_stability_frames=int(config.get("minimum_side_stability_frames", 1)),
            ),
            time_configured=bool(source["recording_time_configured"]),
            classification_policy=self._classification_policy_from_parameters(config),
        )
        statistics = {
            **result.aggregates,
            "run_type": "PREVIEW_ONLY",
            "decoded_frames": sum(len(track.observations) for track in track_set.tracks),
            "processed_frames": sum(len(track.observations) for track in track_set.tracks),
            "track_count": len(track_set.tracks),
            "event_count": len(result.events),
            "exclusion_count": len(result.exclusions),
            "processed_fps": None,
            "processing_ratio": None,
            "model_load_ms": None,
            "first_inference_ms": None,
            "gpu_memory_mb": None,
            "configuration_revision_id": preview["processing_configuration_revision_id"],
            "runtime_configuration_hash": preview.get("runtime_configuration_hash"),
            "request_fingerprint": preview.get("request_fingerprint"),
            "provenance_status": preview.get("provenance_status", "LEGACY_UNRESOLVED"),
            "disclosures": ["PREVIEW_ONLY", "NOT_PRODUCTION_RESULT", "SYNTHETIC_ENGINEERING_ONLY"],
        }
        evidence = [
            {
                "event_id": event.event_id,
                "line_id": event.line_id,
                "track_id": event.track_id,
                "crossing_timestamp_ms": event.crossing_timestamp_ms,
                "direction": event.direction.value,
                "classification": event.synthetic_class,
            }
            for event in result.events[:32]
        ]
        warnings = list(result.warnings)
        self.processing_profile_store.set_preview_result(
            preview_id,
            status="COMPLETED",
            statistics=statistics,
            warnings=warnings,
            evidence=evidence,
            events=[
                {
                    "event_id": event.event_id,
                    "line_id": event.line_id,
                    "track_id": event.track_id,
                    "crossing_timestamp_ms": event.crossing_timestamp_ms,
                    "direction": event.direction.value,
                    "synthetic_class": event.synthetic_class,
                }
                for event in result.events
            ],
            allow_unclaimed=True,
        )

    @staticmethod
    def _classification_policy_from_parameters(parameters: Mapping[str, Any]):
        from .engineering_outputs import ClassificationPolicy

        return ClassificationPolicy(
            min_observations=int(parameters.get("classification_min_observations", 2)),
            min_winning_vote_share=float(parameters.get("classification_min_winning_vote_share", 0.60)),
            min_winning_weighted_share=float(parameters.get("classification_min_weighted_share", 0.60)),
            near_tie_margin=float(parameters.get("classification_near_tie_margin", 0.10)),
        )

    def _fast_ai_runtime_payload(self, root: Path, registry: dict[str, Any]) -> dict[str, Any]:
        ai_python = root / ".venv-ai" / "Scripts" / "python.exe"
        site_packages = root / ".venv-ai" / "Lib" / "site-packages"

        def package_version(package: str) -> str | None:
            if not site_packages.exists():
                return None
            prefix = package.replace("-", "_").lower()
            for metadata_path in site_packages.glob("*.dist-info/METADATA"):
                if not metadata_path.parent.name.lower().startswith(prefix + "-"):
                    continue
                try:
                    for line in metadata_path.read_text(encoding="utf-8", errors="replace").splitlines():
                        if line.lower().startswith("version:"):
                            return line.split(":", 1)[1].strip()
                except OSError:
                    return None
            return None

        packages: list[dict[str, Any]] = []
        package_by_name: dict[str, dict[str, Any]] = {}
        for record in registry.get("models", []):
            package = str(record.get("package", ""))
            if package not in {"ultralytics", "trackers"} or package in package_by_name:
                continue
            installed_version = package_version(package)
            required_version = str(record.get("exact_version"))
            matches = installed_version in {required_version, required_version.split("+", 1)[0]}
            item = {
                "package": package,
                "required_version": required_version,
                "installed": installed_version is not None,
                "installed_version": installed_version,
                "matches_required": bool(installed_version and matches),
            }
            packages.append(item)
            package_by_name[package] = item
        weights: list[dict[str, Any]] = []
        blockers: list[str] = []
        warnings: list[str] = ["cuda_probe_deferred_to_worker"]
        models_dir = root / ".local-tools" / "models"
        for record in registry.get("models", []):
            filename = record.get("model_filename")
            path = models_dir / str(filename) if filename else None
            present = bool(path and path.is_file() and not path.is_symlink())
            actual = sha256_file(path) if present and path is not None else None
            expected = record.get("sha256")
            item = {
                "model_id": str(record.get("model_id")),
                "filename": str(filename) if filename else None,
                "expected_sha256": expected,
                # The readiness API is operator-facing. Keep the model identity
                # and checksum, never the machine-specific absolute path.
                "path": str(filename) if filename else None,
                "present": present,
                "actual_sha256": actual,
                "matches_expected": None if expected is None or actual is None else actual == expected,
            }
            weights.append(item)
            model_id = str(record.get("model_id"))
            if model_id == "detector.ultralytics-yolo11n-coco" and filename:
                if not present:
                    blockers.append(f"weight_missing:{model_id}")
                elif expected and actual != expected:
                    blockers.append(f"weight_hash_mismatch:{model_id}")
            if filename and expected is None:
                warnings.append(f"weight_hash_not_recorded:{model_id}")
        if not ai_python.is_file():
            blockers.append("ai_environment_missing")
        for package in packages:
            if not package["installed"]:
                blockers.append(f"package_missing:{package['package']}")
            elif not package["matches_required"]:
                blockers.append(f"package_version_mismatch:{package['package']}")
        return {
            "schema_version": "ai-runtime-status-fast-v1",
            "python_executable": "<local-ai-venv>/Scripts/python.exe",
            "ai_venv_expected": "<local-ai-venv>",
            "models_dir": "<local-models-dir>",
            "packages": packages,
            "weights": weights,
            "python_available": ai_python.is_file(),
            "torch": {"available": ai_python.is_file(), "cuda_available": None, "probe": "worker_claim"},
            "cpu_provider_available": bool(package_by_name.get("ultralytics", {}).get("matches_required")),
            "gpu_provider_available": False,
            "blockers": list(dict.fromkeys(blockers)),
            "warnings": list(dict.fromkeys(warnings)),
        }

    def list_crossing_events(self, run_id: str, limit: int = 1000, offset: int = 0) -> list[dict]:
        return [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT * FROM crossing_event_ledger
                WHERE run_id = ?
                ORDER BY crossing_timestamp_ms, counting_line_id, track_id
                LIMIT ? OFFSET ?
                """,
                (run_id, limit, offset),
            )
        ]

    def get_aggregate_snapshot(self, run_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM aggregate_snapshots WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if row is None:
            raise ValueError("aggregate snapshot not found")
        payload = dict(row)
        payload["fifteen_minute_counts"] = json.loads(payload.pop("fifteen_minute_counts_json"))
        if payload.get("engineering_result_revision_id"):
            try:
                payload["engineering"] = self.get_engineering_summary(run_id)
            except ValueError:
                payload["engineering"] = None
        else:
            payload["engineering"] = None
        return payload

    def reviewed_totals(self, run_id: str) -> dict:
        events = [
            AutoCountEvent(
                id=row["id"],
                run_id=row["run_id"],
                technical_key=row["technical_key"],
                pts_ms=row["pts_ms"],
                track_id=row["track_id"],
                rule_id=row["rule_id"],
                object_domain=row["object_domain"],
                classification=row["classification"],
                movement=row["movement"],
                confidence=row["confidence"],
                qc_state=row["qc_state"],
            )
            for row in self.connection.execute("SELECT * FROM auto_count_events WHERE run_id = ?", (run_id,))
        ]
        actions = [
            ReviewAction(
                id=row["id"],
                event_id=row["event_id"],
                action_type=ReviewActionType(row["action_type"]),
                reviewer=row["reviewer"],
                created_at=datetime.fromisoformat(row["created_at"]),
                new_classification=row["new_classification"],
                new_movement=row["new_movement"],
                reason=row["reason"],
                reverses_action_id=row["reverses_action_id"],
            )
            for row in self.connection.execute(
                """
                SELECT ra.* FROM review_actions ra
                JOIN auto_count_events e ON e.id = ra.event_id
                WHERE e.run_id = ?
                """,
                (run_id,),
            )
        ]
        raw_total = len(events)
        projected = [project_effective_event(event, actions) for event in events]
        accepted = [event for event in projected if event.included]
        excluded = [event for event in projected if not event.included]
        unresolved = [event for event in projected if event.review_state == "unreviewed"]
        by_class: dict[str, int] = {}
        by_direction: dict[str, int] = {}
        for event in accepted:
            by_class[event.effective_classification] = by_class.get(event.effective_classification, 0) + 1
            by_direction[event.effective_movement] = by_direction.get(event.effective_movement, 0) + 1
        return {
            "raw_total": raw_total,
            "accepted_total": len(accepted),
            "excluded_total": len(excluded),
            "unresolved_total": len(unresolved),
            "correction_count": len([event for event in projected if event.review_state == "corrected"]),
            "by_class": by_class,
            "by_direction": by_direction,
        }

    def media_runtime_status(self) -> dict:
        status = media_runtime_status()
        ffmpeg = status.ffmpeg.__dict__.copy()
        ffprobe = status.ffprobe.__dict__.copy()
        # Executable locations are useful in local logs but must not be returned
        # from the browser-facing API or included in support bundles.
        ffmpeg.pop("executable", None)
        ffprobe.pop("executable", None)
        return {
            "ffmpeg": ffmpeg,
            "ffprobe": ffprobe,
            "readiness_state": status.readiness_state,
            "paired_version_consistent": status.paired_version_consistent,
            "warnings": status.warnings,
            "metadata_inspection_available": status.metadata_inspection_available,
            "reference_frame_extraction_available": status.reference_frame_extraction_available,
        }

    def project_readiness(self, project_id: str) -> dict:
        source = self.connection.execute(
            "SELECT * FROM video_sources WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if source is None:
            return {"state": "no_media", "blockers": ["no_media"]}
        blockers: list[str] = []
        if source["readiness_state"] not in (None, "media_ready"):
            blockers.append(str(source["readiness_state"]))
        if not source["source_started_at"] or not source["timezone_name"]:
            blockers.append("recording_time_incomplete")
        if int(source["analysis_end_pts_ms"]) <= int(source["analysis_start_pts_ms"]):
            blockers.append("invalid_analysis_window")
        reference = self._latest_reference_frame(project_id)
        if reference is None:
            blockers.append("preview_unavailable")
        scene = self.connection.execute(
            "SELECT * FROM scene_versions WHERE project_id = ? ORDER BY version DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if scene is None:
            blockers.append("scene_missing")
        else:
            geometry = json.loads(str(scene["geometry_json"]))
            if not geometry.get("rois"):
                blockers.append("roi_incomplete")
            if not geometry.get("counting_lines"):
                blockers.append("no_counting_line")
            validation = validate_scene_geometry(geometry)
            if validation:
                blockers.append("invalid_geometry")
        project = self._project_or_raise(project_id)
        if bool(project["stale"]):
            blockers.append("stale_scene")
        if blockers:
            return {"state": blockers[0], "blockers": blockers}
        return {"state": "scene_ready_for_ai_trial", "blockers": []}

    def extract_project_reference_frame(
        self,
        project_id: str,
        mode: str,
        requested_pts_ms: int | None,
        local_data_dir: Path,
        force_regenerate: bool = False,
    ) -> dict:
        self._project_or_raise(project_id)
        source = self._latest_source_or_raise(project_id)
        managed_path = source["managed_media_path"]
        if not managed_path:
            raise MediaRuntimeError("source_media_unavailable", "uploaded source media is required")
        if mode == "beginning":
            resolved_request = 0
        elif mode == "analysis_start":
            resolved_request = int(source["analysis_window_start_ms"] or source["analysis_start_pts_ms"] or 0)
        elif mode == "timestamp" and requested_pts_ms is not None:
            resolved_request = requested_pts_ms
        else:
            raise ValueError("requested timestamp is required for timestamp extraction")

        emit_diagnostic_event(
            "reference_frame_request",
            project_id=project_id,
            source_id=str(source["id"]),
            mode=mode,
            requested_pts_ms=resolved_request,
            force_regenerate=force_regenerate,
        )
        existing = self.connection.execute(
            """
            SELECT * FROM reference_frames
            WHERE project_id = ? AND source_id = ? AND source_fingerprint_sha256 = ?
              AND requested_pts_ms = ? AND extraction_mode = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (project_id, source["id"], source["fingerprint_sha256"], resolved_request, mode),
        ).fetchone()
        if existing and not force_regenerate and self._valid_cached_reference_frame(existing):
            emit_diagnostic_event(
                "reference_frame_cache_hit",
                project_id=project_id,
                frame_id=str(existing["id"]),
                requested_pts_ms=resolved_request,
                image_bytes=Path(existing["preview_path"]).stat().st_size,
            )
            return self._public_reference_frame(dict(existing))
        if existing and not force_regenerate:
            emit_diagnostic_event(
                "reference_frame_cache_miss",
                project_id=project_id,
                frame_id=str(existing["id"]),
                requested_pts_ms=resolved_request,
                reason="missing_or_invalid_preview",
            )

        frame_id = str(existing["id"]) if existing else new_id("frm")
        artifact_dir = local_data_dir / "artifacts" / project_id / "reference-frames"
        preview_path = Path(existing["preview_path"]) if existing else artifact_dir / f"{frame_id}.png"
        try:
            frame = extract_reference_frame(Path(managed_path), preview_path, resolved_request)
        except MediaRuntimeError:
            if not self._allow_e2e_media_fallback(str(source["original_filename"] or "")):
                raise
            frame = self._e2e_reference_frame(preview_path, resolved_request)
        emit_diagnostic_event(
            "reference_frame_extracted",
            project_id=project_id,
            frame_id=frame_id,
            requested_pts_ms=resolved_request,
            image_bytes=frame.preview_path.stat().st_size if frame.preview_path.exists() else None,
            preview_width=frame.preview_width,
            preview_height=frame.preview_height,
            warnings=frame.warnings,
        )
        if existing:
            self.connection.execute(
                """
                UPDATE reference_frames
                SET resolved_pts_ms = ?, decoder_seek_pts_ms = ?, first_decoded_pts_ms = ?,
                    selected_pts_ms = ?, preview_path = ?, preview_width = ?, preview_height = ?,
                    source_width = ?, source_height = ?, rotation_degrees = ?, warnings_json = ?,
                    created_at = ?
                WHERE id = ?
                """,
                (
                    frame.resolved_pts_ms,
                    frame.decoder_seek_pts_ms,
                    frame.first_decoded_pts_ms,
                    frame.selected_pts_ms,
                    str(frame.preview_path),
                    frame.preview_width,
                    frame.preview_height,
                    frame.source_width,
                    frame.source_height,
                    frame.rotation_degrees,
                    json.dumps(frame.warnings),
                    now(),
                    frame_id,
                ),
            )
        else:
            self.connection.execute(
                """
                INSERT INTO reference_frames(
                  id, project_id, source_id, source_fingerprint_sha256, requested_pts_ms,
                  resolved_pts_ms, decoder_seek_pts_ms, first_decoded_pts_ms, selected_pts_ms,
                  extraction_mode, preview_path, preview_width, preview_height, source_width,
                  source_height, rotation_degrees, warnings_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    frame_id,
                    project_id,
                    source["id"],
                    source["fingerprint_sha256"],
                    resolved_request,
                    frame.resolved_pts_ms,
                    frame.decoder_seek_pts_ms,
                    frame.first_decoded_pts_ms,
                    frame.selected_pts_ms,
                    mode,
                    str(frame.preview_path),
                    frame.preview_width,
                    frame.preview_height,
                    frame.source_width,
                    frame.source_height,
                    frame.rotation_degrees,
                    json.dumps(frame.warnings),
                    now(),
                ),
            )
        self.connection.commit()
        return self._public_reference_frame(
            dict(self.connection.execute("SELECT * FROM reference_frames WHERE id = ?", (frame_id,)).fetchone())
        )

    def reference_frame_preview_path(self, project_id: str, frame_id: str) -> Path:
        row = self.connection.execute(
            "SELECT * FROM reference_frames WHERE id = ? AND project_id = ?",
            (frame_id, project_id),
        ).fetchone()
        if row is None:
            raise ValueError("reference frame not found")
        path = Path(row["preview_path"])
        if not path.exists():
            raise FileNotFoundError("reference frame preview missing")
        if not self._valid_cached_reference_frame(row):
            raise FileNotFoundError("reference frame preview invalid")
        emit_diagnostic_event(
            "reference_frame_preview_read",
            project_id=project_id,
            frame_id=frame_id,
            image_bytes=path.stat().st_size,
        )
        return path

    def source_video_file(self, project_id: str) -> tuple[Path, str, int]:
        source = self._latest_source_or_raise(project_id)
        managed_path = source["managed_media_path"]
        if not managed_path:
            raise MediaRuntimeError("source_media_unavailable", "uploaded source media is required")
        path = Path(managed_path)
        if not path.exists() or not path.is_file() or path.is_symlink():
            raise MediaRuntimeError("source_missing", "source media is unavailable")
        suffix = str(source["original_extension"] or path.suffix).lower()
        return path, VIDEO_MIME_TYPES.get(suffix, "application/octet-stream"), path.stat().st_size

    def _valid_cached_reference_frame(self, row: sqlite3.Row | dict) -> bool:
        path = Path(row["preview_path"])
        if not path.exists() or not path.is_file() or path.is_symlink() or path.stat().st_size <= 24:
            return False
        width, height = png_dimensions(path)
        if width is None or height is None or width <= 0 or height <= 0:
            return False
        recorded_width = row["preview_width"]
        recorded_height = row["preview_height"]
        return not (
            recorded_width is not None
            and recorded_height is not None
            and (int(recorded_width) != width or int(recorded_height) != height)
        )

    def validate_scene_payload(self, payload: SceneGeometrySave) -> dict:
        errors = validate_scene_geometry(payload.geometry)
        return {"valid": not errors, "errors": errors}

    def save_scene_configuration(self, project_id: str, payload: SceneGeometrySave) -> dict:
        self._project_or_raise(project_id)
        source = self._latest_source_or_raise(project_id)
        reference = self.connection.execute(
            "SELECT * FROM reference_frames WHERE id = ? AND project_id = ?",
            (payload.reference_frame_id, project_id),
        ).fetchone()
        if reference is None:
            raise ValueError("reference frame not found")
        if reference["source_fingerprint_sha256"] != source["fingerprint_sha256"]:
            raise ValueError("reference frame source fingerprint does not match current source")
        errors = validate_scene_geometry(payload.geometry)
        if errors:
            raise SceneValidationError(errors)

        current = self.connection.execute(
            "SELECT * FROM scene_versions WHERE project_id = ? ORDER BY version DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if current is not None and payload.expected_version is not None and int(current["version"]) != payload.expected_version:
            raise StaleWriteError("Scene was changed by another request. Reload before saving.")

        semantic_hash = scene_semantic_hash(payload.geometry)
        material_change = current is None or current["semantic_hash"] != semantic_hash
        next_version = int(current["version"]) + 1 if current else 1
        scene_id = new_id("scn")
        if current:
            self.connection.execute(
                "UPDATE scene_versions SET status = 'superseded', superseded_at = ? WHERE id = ?",
                (now(), current["id"]),
            )
        self.connection.execute(
            """
            INSERT INTO scene_versions(
              id, project_id, version, template, geometry_json, config_hash, created_at,
              source_id, source_fingerprint_sha256, reference_frame_id, reference_frame_pts_ms,
              display_width, display_height, schema_version, status, semantic_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
            """,
            (
                scene_id,
                project_id,
                next_version,
                payload.template,
                json.dumps(payload.geometry),
                semantic_hash,
                now(),
                source["id"],
                source["fingerprint_sha256"],
                reference["id"],
                reference["resolved_pts_ms"],
                reference["preview_width"],
                reference["preview_height"],
                SCENE_SCHEMA_VERSION,
                semantic_hash,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO scene_revision_audit(
              id, project_id, previous_scene_version_id, new_scene_version_id,
              previous_version, new_version, changed_fields_json, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("audit"),
                project_id,
                current["id"] if current else None,
                scene_id,
                int(current["version"]) if current else None,
                next_version,
                json.dumps(self._scene_changed_fields(current, payload.geometry, semantic_hash), sort_keys=True),
                "scene_setup_save",
                now(),
            ),
        )
        downstream_stale = self.invalidate_for_calculation_change(project_id, commit=False) if material_change else False
        self._mark_project(project_id, "scene_configured", stale=downstream_stale)
        self.connection.commit()
        return self._public_scene(self.connection.execute("SELECT * FROM scene_versions WHERE id = ?", (scene_id,)).fetchone())

    def run_mock_analysis(self, project_id: str) -> dict:
        source = self.connection.execute(
            "SELECT * FROM video_sources WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        scene = self.connection.execute(
            "SELECT * FROM scene_versions WHERE project_id = ? ORDER BY version DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if source is None or scene is None:
            raise ValueError("source and scene are required")
        run_id = new_id("run")
        result_version = f"result-{uuid.uuid4().hex[:8]}"
        self.connection.execute(
            """
            INSERT INTO analysis_runs(id, project_id, source_id, scene_version_id, state, progress_percent,
              result_version, created_at, completed_at, job_state, progress_phase, progress_completed_units,
              progress_total_units, progress_fraction, progress_message_code, engine_mode, result_ready)
            VALUES (?, ?, ?, ?, 'processing_complete', 100, ?, ?, ?, 'COMPLETED', 'COMPLETED', 100,
              100, 1.0, 'job_completed', 'mock_analysis', 1)
            """,
            (run_id, project_id, source["id"], scene["id"], result_version, now(), now()),
        )
        for index in range(4):
            self.connection.execute(
                """
                INSERT INTO processing_segments(id, run_id, segment_index, start_pts_ms, end_pts_ms, state)
                VALUES (?, ?, ?, ?, ?, 'committed')
                """,
                (new_id("seg"), run_id, index, index * 900_000, (index + 1) * 900_000),
            )
        self._insert_mock_events(run_id)
        self._create_engineering_result(run_id)
        snapshot = self._create_aggregate_snapshot(run_id, stale=False)
        self._set_initial_current_processing_run(project_id, run_id)
        self._mark_project(project_id, "needs_review", stale=False)
        self.connection.commit()
        return {
            "id": run_id,
            "project_id": project_id,
            "state": "processing_complete",
            "progress_percent": 100,
            "result_version": result_version,
            "aggregate_id": snapshot["id"],
        }

    def retry_segment(self, run_id: str, segment_index: int) -> dict:
        self.connection.execute(
            """
            UPDATE processing_segments
            SET retry_count = retry_count + 1, state = 'committed'
            WHERE run_id = ? AND segment_index = ?
            """,
            (run_id, segment_index),
        )
        self._insert_mock_events(run_id)
        self.connection.commit()
        return dict(
            self.connection.execute(
                "SELECT * FROM processing_segments WHERE run_id = ? AND segment_index = ?",
                (run_id, segment_index),
            ).fetchone()
        )

    def list_events(self, run_id: str, limit: int = 1000, offset: int = 0) -> list[dict]:
        return [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT * FROM auto_count_events
                WHERE run_id = ?
                ORDER BY pts_ms
                LIMIT ? OFFSET ?
                """,
                (run_id, limit, offset),
            )
        ]

    def add_review_action(self, event_id: str, payload: dict) -> dict:
        action_id = new_id("rev")
        self.connection.execute(
            """
            INSERT INTO review_actions(
              id, event_id, action_type, reviewer, new_classification, new_movement,
              reason, reverses_action_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action_id,
                event_id,
                payload.get("action_type"),
                payload.get("reviewer", "mock-reviewer"),
                payload.get("new_classification"),
                payload.get("new_movement"),
                payload.get("reason"),
                payload.get("reverses_action_id"),
                now(),
            ),
        )
        if payload.get("action_type") in {"approve", "change_class", "change_movement", "exclude"}:
            self.connection.execute("UPDATE qc_flags SET resolved_at = ? WHERE event_id = ?", (now(), event_id))
        self.connection.commit()
        return dict(self.connection.execute("SELECT * FROM review_actions WHERE id = ?", (action_id,)).fetchone())

    def mark_review_complete(self, project_id: str) -> dict:
        unresolved = self.unresolved_qc_count(project_id)
        if unresolved:
            raise ValueError("mandatory QC remains unresolved")
        self._mark_project(project_id, "review_complete", stale=False)
        self.connection.commit()
        return dict(self.connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())

    def certify(self, project_id: str, run_id: str, certified_by: str) -> dict:
        project = self.connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        allowed, reason = can_certify(ResultState(project["state"]), self.unresolved_qc_count(project_id), bool(project["stale"]))
        if not allowed:
            raise ValueError(reason)
        run = self.connection.execute("SELECT * FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()
        certification_id = new_id("cert")
        self.connection.execute(
            """
            INSERT INTO certification_records(id, project_id, run_id, result_version, certified_by, certified_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (certification_id, project_id, run_id, run["result_version"], certified_by, now()),
        )
        self._mark_project(project_id, "certified", stale=False)
        self.connection.commit()
        return dict(self.connection.execute("SELECT * FROM certification_records WHERE id = ?", (certification_id,)).fetchone())

    def create_export_manifest(self, project_id: str, run_id: str, export_format: str) -> dict:
        project = self.connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        run = self.connection.execute("SELECT * FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()
        certified = (
            self.connection.execute(
                "SELECT 1 FROM certification_records WHERE run_id = ? AND result_version = ?",
                (run_id, run["result_version"]),
            ).fetchone()
            is not None
        )
        allowed, reason = can_export(ResultState(project["state"]), certified, bool(project["stale"]))
        if not allowed:
            raise ValueError(reason)
        manifest_id = new_id("exp")
        synthetic = (
            self.connection.execute("SELECT 1 FROM synthetic_counting_runs WHERE run_id = ?", (run_id,)).fetchone()
            is not None
        )
        provenance = {
            "source": "mock-source",
            "result_version": run["result_version"],
            "certification": "human-certified",
            "tims_taxonomy": "pending-official-verification",
            "data_provenance": "synthetic" if synthetic else "mock",
            "certification_type": "synthetic-validation" if synthetic else "mock-foundation",
            "validation_only": "true" if synthetic else "false",
        }
        self.connection.execute(
            """
            INSERT INTO export_manifests(id, project_id, run_id, result_version, format, status, provenance_json, created_at)
            VALUES (?, ?, ?, ?, ?, 'manifest_created', ?, ?)
            """,
            (manifest_id, project_id, run_id, run["result_version"], export_format, json.dumps(provenance), now()),
        )
        self._mark_project(project_id, "exported", stale=False)
        self.connection.commit()
        return dict(self.connection.execute("SELECT * FROM export_manifests WHERE id = ?", (manifest_id,)).fetchone())

    def unresolved_qc_count(self, project_id: str) -> int:
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM qc_flags q
            JOIN auto_count_events e ON e.id = q.event_id
            JOIN analysis_runs r ON r.id = e.run_id
            WHERE r.project_id = ? AND q.mandatory = 1 AND q.resolved_at IS NULL
            """,
            (project_id,),
        ).fetchone()
        return int(row["count"])

    def invalidate_for_calculation_change(self, project_id: str, commit: bool = True) -> bool:
        has_dependents = self._has_calculation_dependents(project_id)
        if has_dependents:
            self.connection.execute("UPDATE projects SET stale = 1, state = 'stale' WHERE id = ?", (project_id,))
        self.connection.execute(
            """
            UPDATE aggregate_snapshots SET stale = 1
            WHERE run_id IN (SELECT id FROM analysis_runs WHERE project_id = ?)
            """,
            (project_id,),
        )
        self.connection.execute(
            """
            UPDATE synthetic_counting_runs SET stale = 1
            WHERE project_id = ?
            """,
            (project_id,),
        )
        self.connection.execute(
            """
            UPDATE engineering_result_revisions SET stale = 1, result_status = 'STALE', engineering_ready = 0
            WHERE run_id IN (SELECT id FROM analysis_runs WHERE project_id = ?)
            """,
            (project_id,),
        )
        self.connection.execute(
            """
            UPDATE engineering_event_projections SET stale = 1
            WHERE run_id IN (SELECT id FROM analysis_runs WHERE project_id = ?)
            """,
            (project_id,),
        )
        self.connection.execute(
            """
            UPDATE engineering_track_evidence SET stale = 1
            WHERE run_id IN (SELECT id FROM analysis_runs WHERE project_id = ?)
            """,
            (project_id,),
        )
        self.review_service.mark_project_stale(project_id, "calculation_input_changed")
        if commit:
            self.connection.commit()
        return has_dependents

    def _has_calculation_dependents(self, project_id: str) -> bool:
        return any(
            self.connection.execute(query, (project_id,)).fetchone() is not None
            for query in (
                "SELECT 1 FROM analysis_runs WHERE project_id = ? LIMIT 1",
                "SELECT 1 FROM synthetic_counting_runs WHERE project_id = ? LIMIT 1",
                "SELECT 1 FROM export_manifests WHERE project_id = ? LIMIT 1",
                "SELECT 1 FROM certification_records WHERE project_id = ? LIMIT 1",
            )
        )

    def _set_initial_current_processing_run(self, project_id: str, run_id: str) -> None:
        """Persist the first completed run without silently replacing an operator choice."""

        self.connection.execute(
            "UPDATE projects SET current_analysis_run_id = COALESCE(current_analysis_run_id, ?) WHERE id = ?",
            (run_id, project_id),
        )

    def _mark_project(self, project_id: str, state: str, stale: bool) -> None:
        self.connection.execute(
            "UPDATE projects SET state = ?, stale = ?, updated_at = ?, version = version + 1 WHERE id = ?",
            (state, 1 if stale else 0, now(), project_id),
        )

    def _project_or_raise(self, project_id: str) -> sqlite3.Row:
        project = self.connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if project is None:
            raise ValueError("project not found")
        return project

    def _latest_source_or_raise(self, project_id: str) -> sqlite3.Row:
        source = self.connection.execute(
            "SELECT * FROM video_sources WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if source is None:
            raise ValueError("source is required")
        return source

    def _latest_scene_or_raise(self, project_id: str, expected_scene_version: int | None = None) -> sqlite3.Row:
        scene = self.connection.execute(
            "SELECT * FROM scene_versions WHERE project_id = ? ORDER BY version DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if scene is None:
            raise ValueError("scene is required")
        if expected_scene_version is not None and int(scene["version"]) != expected_scene_version:
            raise StaleWriteError("Scene was changed by another request. Reload before running.")
        return scene

    def _latest_reference_frame(self, project_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM reference_frames WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        return dict(row) if row else None

    def _public_source(self, row: sqlite3.Row | dict | None) -> dict | None:
        if row is None:
            return None
        payload = dict(row)
        for field in PRIVATE_SOURCE_FIELDS:
            payload.pop(field, None)
        if payload.get("fingerprint_sha256"):
            payload["fingerprint_short"] = str(payload["fingerprint_sha256"])[:12]
        return payload

    def _public_reference_frame(self, row: dict | sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        payload = dict(row)
        for field in PRIVATE_FRAME_FIELDS:
            payload.pop(field, None)
        return payload

    def _public_scene(self, row: sqlite3.Row | dict | None) -> dict | None:
        if row is None:
            return None
        return dict(row)

    def _processing_job_or_raise(
        self,
        run_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> sqlite3.Row:
        active_connection = connection if connection is not None else self.connection
        row = active_connection.execute("SELECT * FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("processing job not found")
        if "job_state" not in row.keys() or row["job_state"] is None:
            raise ValueError("run is not a processing job")
        return row

    def _processing_claim_is_current(
        self,
        row: sqlite3.Row,
        worker_id: str,
        claim_token: str | None,
    ) -> bool:
        if row["worker_id"] != worker_id:
            return False
        if claim_token is not None and str(row["claim_token"] or "") != claim_token:
            return False
        if is_terminal(row["job_state"]):
            return False
        return not _processing_lease_expired(row["lease_expires_at"])

    def _public_processing_job(self, row: sqlite3.Row | dict) -> dict:
        payload = dict(row)
        # Claim ownership is an internal worker concern. Keep claim tokens,
        # worker identity, and lease timestamps out of browser/API payloads.
        for field in ("claim_token", "worker_id", "claimed_at", "lease_expires_at"):
            payload.pop(field, None)
        config = json.loads(str(payload.get("processing_config_json") or "{}"))
        payload["fixture_id"] = config.get("fixture_id")
        payload["synthetic"] = payload.get("engine_mode") == "synthetic_fixture"
        payload["processing_mode"] = payload.get("processing_mode") or config.get(
            "mode", ProcessingMode.SYNTHETIC.value
        )
        payload["mode"] = payload["processing_mode"]
        payload["real_video"] = payload["processing_mode"] == ProcessingMode.REAL_VIDEO.value
        if payload.get("resolved_device"):
            config["resolved_device"] = payload["resolved_device"]
        payload["configuration"] = config
        payload["validation_only"] = bool(config.get("validation_only", payload["synthetic"]))
        payload["progress_percent"] = int(payload.get("progress_percent") or 0)
        payload["progress_indeterminate"] = bool(payload.get("progress_indeterminate"))
        payload["progress_fraction"] = (
            None if payload["progress_indeterminate"] else float(payload.get("progress_fraction") or 0.0)
        )
        payload["result_ready"] = bool(payload.get("result_ready"))
        if payload.get("processing_stats_json"):
            try:
                payload["processing_stats"] = json.loads(str(payload["processing_stats_json"]))
            except json.JSONDecodeError:
                payload["processing_stats"] = {}
        return payload

    def _processing_request_fingerprint(
        self,
        source: sqlite3.Row,
        scene: sqlite3.Row,
        fixture_id: str | None,
        mode: str = ProcessingMode.SYNTHETIC.value,
        configuration: dict[str, Any] | None = None,
    ) -> str:
        payload = {
            "source_id": source["id"],
            "source_fingerprint_sha256": source["fingerprint_sha256"],
            "scene_version_id": scene["id"],
            "scene_revision": int(scene["version"]),
            "scene_semantic_hash": scene["config_hash"],
            "mode": mode,
            "configuration": configuration or {},
        }
        if fixture_id is not None:
            payload["fixture_id"] = fixture_id
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def _transition_processing_job(
        self,
        run_id: str,
        next_state: JobState,
        phase: ProgressPhase,
        progress_percent: int,
        message_code: str,
        detail: dict | None = None,
    ) -> None:
        current = self.connection.execute("SELECT job_state FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()
        if current is None:
            raise ValueError("processing job not found")
        validate_transition(current["job_state"], next_state)
        fraction = max(0.0, min(1.0, progress_percent / 100.0))
        self.connection.execute(
            """
            UPDATE analysis_runs
            SET job_state = ?, progress_phase = ?, progress_percent = ?,
                progress_completed_units = ?, progress_total_units = 100,
                progress_fraction = ?, progress_message_code = ?
            WHERE id = ?
            """,
            (next_state.value, phase.value, progress_percent, progress_percent, fraction, message_code, run_id),
        )
        self._record_job_event(
            run_id,
            "state_transition",
            current["job_state"],
            next_state.value,
            {"phase": phase.value, "progress_percent": progress_percent, "message_code": message_code, **(detail or {})},
        )

    def _record_job_event(
        self,
        run_id: str,
        event_type: str,
        from_state: str | None,
        to_state: str | None,
        detail: dict | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO processing_job_events(id, run_id, event_type, from_state, to_state, detail_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (new_id("jobevt"), run_id, event_type, from_state, to_state, json.dumps(detail or {}, sort_keys=True), now()),
        )

    def _update_processing_progress(
        self,
        run_id: str,
        phase: ProgressPhase,
        *,
        completed_units: int,
        total_units: int | None,
        progress_percent: int,
        message_code: str,
        indeterminate: bool,
        connection: sqlite3.Connection | None = None,
        worker_id: str | None = None,
        claim_token: str | None = None,
    ) -> None:
        fraction = None if total_units is None else max(0.0, min(1.0, progress_percent / 100.0))
        active_connection = connection if connection is not None else self.connection
        parameters: tuple[object, ...] = (
            phase.value,
            completed_units,
            total_units,
            fraction,
            progress_percent,
            message_code,
            1 if indeterminate else 0,
            now(),
            run_id,
        )
        if worker_id is None:
            updated = active_connection.execute(
                """
                UPDATE analysis_runs
                SET progress_phase = ?, progress_completed_units = ?, progress_total_units = ?,
                    progress_fraction = ?, progress_percent = ?, progress_message_code = ?,
                    progress_indeterminate = ?, last_heartbeat_at = ?
                WHERE id = ?
                """,
                parameters,
            )
        else:
            if claim_token is None:
                raise ValueError("claim token is required for owned progress updates")
            updated = active_connection.execute(
                """
                UPDATE analysis_runs
                SET progress_phase = ?, progress_completed_units = ?, progress_total_units = ?,
                    progress_fraction = ?, progress_percent = ?, progress_message_code = ?,
                    progress_indeterminate = ?, last_heartbeat_at = ?
                WHERE id = ? AND worker_id = ? AND claim_token = ?
                  AND job_state IN ('STARTING', 'RUNNING', 'CANCELLATION_REQUESTED')
                """,
                (*parameters, worker_id, claim_token),
            )
            if updated.rowcount != 1:
                raise ProcessingAuthorityLost("worker ownership changed during progress update")
        active_connection.commit()

    def _source_first_pts_ms(self, source: sqlite3.Row) -> int | None:
        if source["first_video_pts"] is None:
            return None
        time_base = str(source["stream_time_base"] or "")
        try:
            numerator, denominator = (int(item) for item in time_base.split("/", 1))
            if denominator == 0:
                return None
            return round(int(source["first_video_pts"]) * numerator / denominator * 1000)
        except (TypeError, ValueError):
            return None

    def _fail_processing_job(self, run_id: str, code: str, category: str, detail: str) -> None:
        row = self._processing_job_or_raise(run_id)
        if not is_terminal(row["job_state"]):
            self._transition_processing_job(run_id, JobState.FAILED, ProgressPhase.FAILED, int(row["progress_percent"] or 0), code.lower())
        self.connection.execute(
            """
            UPDATE analysis_runs
            SET state = 'failed', failed_at = ?, error_code = ?, error_category = ?,
                error_detail = ?, result_ready = 0
            WHERE id = ?
            """,
            (now(), code, category, detail, run_id),
        )

    def _fail_processing_job_owned(
        self,
        run_id: str,
        worker_id: str,
        claim_token: str | None,
        code: str,
        category: str,
        detail: str,
    ) -> dict:
        self.connection.commit()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._processing_job_or_raise(run_id)
            if not self._processing_claim_is_current(row, worker_id, claim_token):
                self.connection.rollback()
                return self.get_processing_job(run_id)
            self._fail_processing_job(run_id, code, category, detail)
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return self.get_processing_job(run_id)

    def _cancel_claimed_job(
        self,
        run_id: str,
        *,
        worker_id: str | None = None,
        claim_token: str | None = None,
    ) -> dict:
        if worker_id is not None:
            self.connection.commit()
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                owned = self._processing_job_or_raise(run_id)
                if not self._processing_claim_is_current(owned, worker_id, claim_token):
                    self.connection.rollback()
                    return self.get_processing_job(run_id)
                current = owned
                if current["job_state"] == JobState.CANCELLED.value:
                    self.connection.rollback()
                    return self.get_processing_job(run_id)
                if current["job_state"] != JobState.CANCELLATION_REQUESTED.value:
                    self._transition_processing_job(
                        run_id,
                        JobState.CANCELLATION_REQUESTED,
                        ProgressPhase.PROCESSING,
                        int(current["progress_percent"] or 0),
                        "cancellation_requested",
                    )
                self._transition_processing_job(run_id, JobState.CANCELLED, ProgressPhase.CANCELLED, 0, "job_cancelled")
                self.connection.execute(
                    "UPDATE analysis_runs SET state = 'cancelled', cancelled_at = ?, result_ready = 0 WHERE id = ?",
                    (now(), run_id),
                )
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise
            return self.get_processing_job(run_id)
        current = self._processing_job_or_raise(run_id)
        if current["job_state"] != JobState.CANCELLATION_REQUESTED.value:
            self._transition_processing_job(
                run_id,
                JobState.CANCELLATION_REQUESTED,
                ProgressPhase.PROCESSING,
                int(current["progress_percent"] or 0),
                "cancellation_requested",
            )
        self._transition_processing_job(run_id, JobState.CANCELLED, ProgressPhase.CANCELLED, 0, "job_cancelled")
        self.connection.execute(
            "UPDATE analysis_runs SET state = 'cancelled', cancelled_at = ?, result_ready = 0 WHERE id = ?",
            (now(), run_id),
        )
        self.connection.commit()
        return self.get_processing_job(run_id)

    def _latest_certification(self, project_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM certification_records WHERE project_id = ? ORDER BY certified_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        return dict(row) if row else None

    def _safe_local_data_root(self, local_data_dir: Path) -> Path:
        root = local_data_dir.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink():
            raise ValueError("local_data_symlink_not_allowed")
        return root

    def _ensure_inside(self, root: Path, candidate: Path) -> None:
        try:
            candidate.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError("path_traversal_blocked") from exc

    def _safe_extension(self, filename: str) -> str:
        suffix = Path(filename or "").suffix.lower()
        if suffix not in ALLOWED_VIDEO_EXTENSIONS:
            raise ValueError("unsupported_extension")
        return suffix

    def _display_filename(self, filename: str) -> str:
        name = Path(filename or "source").name.strip().replace("\x00", "")
        return name[:160] if name else "source"

    def _validate_uploaded_file(self, uploaded_path: Path, original_filename: str) -> None:
        suffix = self._safe_extension(original_filename)
        if not uploaded_path.is_file() or uploaded_path.is_symlink():
            raise ValueError("invalid_upload_file")
        size = uploaded_path.stat().st_size
        if size <= 0:
            raise ValueError("empty_upload")
        if size > MAX_UPLOAD_BYTES:
            raise ValueError("oversized_file")
        header = uploaded_path.read_bytes()[:64]
        signatures = VIDEO_SIGNATURES.get(suffix, ())
        if signatures and not any(signature in header for signature in signatures):
            raise ValueError("extension_content_mismatch")

    def _validate_inspection(self, inspection) -> None:
        warnings = set(inspection.warnings)
        if "no_video_stream" in warnings:
            raise ValueError("no_video_stream")
        if any(str(warning).startswith("ffprobe_failed") for warning in warnings):
            raise ValueError("media_probe_failed")
        duration_ms = inspection.metadata.get("duration_ms")
        if duration_ms is not None:
            if int(duration_ms) <= 0:
                raise ValueError("zero_duration")
            if int(duration_ms) > MAX_PILOT_DURATION_MS:
                raise ValueError("duration_limit_exceeded")
        width = inspection.metadata.get("width")
        height = inspection.metadata.get("height")
        if width is not None and height is not None and (int(width) <= 0 or int(height) <= 0):
            raise ValueError("invalid_dimensions")

    def _allow_e2e_media_fallback(self, original_filename: str) -> bool:
        return os.getenv("TVA_E2E_ALLOW_FAKE_MEDIA") == "1" and self._display_filename(original_filename).startswith(
            "e2e-legal-upload"
        )

    def _e2e_media_fallback_inspection(self):
        from .media import InspectionResult

        return InspectionResult(
            metadata={
                "container_format": "test-e2e-mp4",
                "video_codec": "test-e2e-h264",
                "duration_ms": 2000,
                "width": 320,
                "height": 180,
                "sample_aspect_ratio": "1:1",
                "display_aspect_ratio": "16:9",
                "nominal_frame_rate": "10/1",
                "average_frame_rate": "10/1",
                "stream_time_base": "1/1000",
                "start_pts": 0,
                "first_video_pts": 0,
                "frame_count": 20,
                "variable_frame_rate": 0,
                "rotation_degrees": 0,
                "metadata_creation_time": None,
                "audio_present": 0,
            },
            warnings=["e2e_media_probe_fallback"],
            tool_version="e2e-fallback",
        )

    def _e2e_reference_frame(self, preview_path: Path, requested_pts_ms: int) -> ReferenceFrameResult:
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
            b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfeA\xe2\x26\x98"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        return ReferenceFrameResult(
            preview_path=preview_path,
            requested_pts_ms=requested_pts_ms,
            resolved_pts_ms=requested_pts_ms,
            decoder_seek_pts_ms=requested_pts_ms,
            first_decoded_pts_ms=requested_pts_ms,
            selected_pts_ms=requested_pts_ms,
            preview_width=1,
            preview_height=1,
            source_width=320,
            source_height=180,
            rotation_degrees=0,
            warnings=["e2e_reference_frame_fallback"],
        )

    def _scene_changed_fields(self, current: sqlite3.Row | None, geometry: dict, semantic_hash: str) -> list[str]:
        if current is None:
            return ["scene_created"]
        try:
            previous = json.loads(str(current["geometry_json"]))
        except json.JSONDecodeError:
            return ["geometry_json"]
        changed = []
        if current["semantic_hash"] != semantic_hash:
            changed.append("configuration_hash")
        for field in ("counting_lines", "rois", "coordinate_system"):
            if previous.get(field) != geometry.get(field):
                changed.append(field)
        return changed or ["analyst_note"]

    def _synthetic_fixture_payload(self, fixture_id: str, scene_row: dict, source_fingerprint: str) -> dict:
        geometry = json.loads(str(scene_row["geometry_json"]))
        tracks = []
        timestamp = int(self._latest_source_or_raise(scene_row["project_id"])["analysis_start_pts_ms"])
        for index, line in enumerate(geometry.get("counting_lines", [])):
            start = line["start"]
            end = line["end"]
            dx = float(end["x"]) - float(start["x"])
            dy = float(end["y"]) - float(start["y"])
            length = max((dx * dx + dy * dy) ** 0.5, 0.001)
            mid_x = (float(start["x"]) + float(end["x"])) / 2
            mid_y = (float(start["y"]) + float(end["y"])) / 2
            offset = 0.12
            a_point = {
                "x": _clamp(mid_x - dy / length * offset),
                "y": _clamp(mid_y + dx / length * offset),
            }
            b_point = {
                "x": _clamp(mid_x + dy / length * offset),
                "y": _clamp(mid_y - dx / length * offset),
            }
            tracks.append(
                {
                    "track_id": f"synthetic_{index + 1:02d}",
                    "synthetic_class": "passenger_vehicle",
                    "raw_class_id": 2,
                    "raw_class_name": "car",
                    "confidence": 0.90,
                    "provenance": "synthetic",
                    "observations": [
                        {"timestamp_ms": timestamp + index * 120_000, "x": a_point["x"], "y": a_point["y"], "sample_id": "before", "raw_class_id": 2, "raw_class_name": "car", "confidence": 0.90},
                        {"timestamp_ms": timestamp + index * 120_000 + 60_000, "x": b_point["x"], "y": b_point["y"], "sample_id": "after", "raw_class_id": 2, "raw_class_name": "car", "confidence": 0.90},
                    ],
                }
            )
        return {
            "schema_version": SYNTHETIC_TRACK_SCHEMA_VERSION,
            "source_fingerprint": source_fingerprint,
            "fixture_id": fixture_id,
            "scene_revision": scene_row["id"],
            "taxonomy_version": "synthetic-provisional-v1",
            "tracks": tracks,
        }

    def _insert_crossing_event(self, run_id: str, event) -> None:
        run = self.connection.execute(
            "SELECT processing_mode, detector_id, tracker_id, configuration_revision, runtime_configuration_hash, request_provenance_hash FROM analysis_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        processing_mode = str(run["processing_mode"] or ProcessingMode.SYNTHETIC.value) if run else ProcessingMode.SYNTHETIC.value
        is_real = event.provenance == REAL_TRACK_PROVENANCE or processing_mode == ProcessingMode.REAL_VIDEO.value
        provisional_class = event.provisional_class or event.synthetic_class
        confidence = event.detector_confidence if event.detector_confidence is not None else 1.0
        object_domain = (
            "pedestrian"
            if provisional_class == "pedestrian"
            else "unknown"
            if provisional_class in {"unknown", "ambiguous"}
            else "vehicle"
        )
        review_state = event.classification_review_state or "needs_review"
        self.connection.execute(
            """
            INSERT INTO crossing_event_ledger(
              id, run_id, source_fingerprint_sha256, scene_revision, counting_line_id,
              counting_line_label, side_a_label, side_b_label, track_id, crossing_direction,
              readable_direction_label, crossing_timestamp_ms,
              real_world_time, reporting_bin_start_ms, synthetic_class, roi_eligible,
              previous_observation_index, next_observation_index, interpolation_parameter,
              crossing_point_json, calculation_method, engine_version, policy_version,
              event_status, provenance, created_at, raw_class_id, raw_class_name,
              provisional_class, detector_confidence, classification_review_state,
              source_frame_index, source_frame_pts_ms, absolute_event_time, event_time_status,
               processing_mode, detector_id, tracker_id, configuration_revision,
               runtime_configuration_hash, request_provenance_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                run_id,
                event.source_fingerprint,
                event.scene_revision,
                event.line_id,
                event.line_label,
                event.side_a_label,
                event.side_b_label,
                event.track_id,
                event.direction.value,
                event.readable_direction_label,
                event.crossing_timestamp_ms,
                event.real_world_time,
                event.reporting_bin_start_ms,
                provisional_class,
                1 if event.roi_eligible else 0,
                event.previous_observation_index,
                event.next_observation_index,
                event.interpolation_parameter,
                json.dumps({"x": event.crossing_point.x, "y": event.crossing_point.y}, sort_keys=True),
                event.calculation_method,
                event.engine_version,
                event.policy_version,
                event.status,
                event.provenance,
                now(),
                event.raw_class_id,
                event.raw_class_name,
                provisional_class,
                event.detector_confidence,
                review_state,
                event.source_frame_index,
                event.source_frame_pts_ms,
                event.absolute_event_time,
                event.event_time_status,
                ProcessingMode.REAL_VIDEO.value if is_real else ProcessingMode.SYNTHETIC.value,
                run["detector_id"] if run else None,
                run["tracker_id"] if run else None,
                 run["configuration_revision"] if run else None,
                 run["runtime_configuration_hash"] if run else None,
                 run["request_provenance_hash"] if run else None,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO auto_count_events(
              id, run_id, technical_key, pts_ms, track_id, rule_id, object_domain,
              classification, movement, confidence, qc_state, created_at,
              raw_class_id, raw_class_name, provisional_class, detector_confidence,
              classification_review_state, source_frame_index, source_frame_pts_ms,
               absolute_event_time, event_time_status, processing_mode, detector_id,
               tracker_id, configuration_revision, runtime_configuration_hash,
               request_provenance_hash
             ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                run_id,
                f"{event.line_id}:{event.track_id}:{event.crossing_timestamp_ms}:{event.direction.value}",
                event.crossing_timestamp_ms,
                event.track_id,
                event.line_id,
                object_domain,
                provisional_class,
                event.direction.value,
                confidence,
                "needs_review",
                now(),
                event.raw_class_id,
                event.raw_class_name,
                provisional_class,
                event.detector_confidence,
                review_state,
                event.source_frame_index,
                event.source_frame_pts_ms,
                event.absolute_event_time,
                event.event_time_status,
                ProcessingMode.REAL_VIDEO.value if is_real else ProcessingMode.SYNTHETIC.value,
                run["detector_id"] if run else None,
                run["tracker_id"] if run else None,
                 run["configuration_revision"] if run else None,
                 run["runtime_configuration_hash"] if run else None,
                 run["request_provenance_hash"] if run else None,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO qc_flags(id, event_id, severity, reason, mandatory)
            VALUES (?, ?, 'mandatory', ?, 1)
            """,
            (
                f"qc_{event.event_id}",
                event.event_id,
                "real inference event requires human review" if is_real else "synthetic event requires validation review",
            ),
        )

    def _validate_timezone(self, timezone_name: str) -> None:
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("invalid timezone") from exc

    @staticmethod
    def _is_compact_classification_evidence(value: Any) -> bool:
        return isinstance(value, dict) and any(
            key in value
            for key in (
                "schema_version",
                "raw_vote_counts",
                "classification_status",
                "winning_raw_class",
            )
        )

    @classmethod
    def _resolve_engineering_classification(
        cls,
        event: dict[str, Any],
        metadata: dict[str, Any],
    ) -> tuple[Any, dict[str, Any], str | None]:
        """Resolve all classification fields from one shared decision."""

        persisted = metadata.get("classification_evidence")
        decision = None
        evidence: dict[str, Any]
        consistency_error: str | None = None
        if cls._is_compact_classification_evidence(persisted):
            evidence = dict(persisted)
            decision, consistency_error = classification_decision_from_compact_evidence(evidence)
        elif isinstance(persisted, list) and persisted:
            if len(persisted) == 1 and cls._is_compact_classification_evidence(persisted[0]):
                evidence = dict(persisted[0])
                decision, consistency_error = classification_decision_from_compact_evidence(evidence)
            else:
                decision = classify_track_evidence(persisted)
                evidence = decision.evidence_json()
        else:
            raw_name = event.get("raw_class_name") or metadata.get("raw_class_name")
            raw_id = event.get("raw_class_id") if event.get("raw_class_id") is not None else metadata.get("raw_class_id")
            if raw_name:
                decision = classify_track_evidence(
                    [
                        {
                            "pts_ms": event.get("pts_ms"),
                            "raw_class_id": raw_id,
                            "native_class": raw_name,
                            "confidence": event.get("detector_confidence", event.get("confidence")),
                        }
                    ]
                )
            else:
                decision = classify_track_evidence([])
            evidence = decision.evidence_json()
        consistency_error = consistency_error or classification_consistency_error(
            decision.classification_status,
            decision.engineering_class,
        )
        if consistency_error is None and cls._is_compact_classification_evidence(persisted):
            for metadata_key, decision_value in (
                ("classification_status", decision.classification_status),
                ("engineering_class", decision.engineering_class),
            ):
                persisted_value = metadata.get(metadata_key)
                if persisted_value is not None and str(persisted_value) != decision_value:
                    consistency_error = f"metadata_{metadata_key}_mismatch:{persisted_value}:{decision_value}"
                    break
        return decision, evidence, consistency_error

    def _create_engineering_result(self, run_id: str) -> dict[str, Any]:
        """Materialize one atomic engineering projection from immutable events."""

        run_row = self.connection.execute("SELECT * FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()
        if run_row is None:
            raise ValueError("run not found")
        run = dict(run_row)
        source_row = self.connection.execute("SELECT * FROM video_sources WHERE id = ?", (run["source_id"],)).fetchone()
        if source_row is None:
            raise ValueError("source not found")
        source = dict(source_row)
        run_configuration = json.loads(str(run.get("processing_config_json") or "{}"))
        configured_provenance = run_configuration.get("runtime_provenance") if isinstance(run_configuration.get("runtime_provenance"), dict) else {}
        revision = revision_ids(self.connection)
        if len(revision) != 3:
            raise ValueError("6B revision seed is incomplete")
        result_revision_id = new_id("eng")
        source_time = source_time_semantics(source)
        intervals = build_intervals(source)
        scene_row = self.connection.execute("SELECT * FROM scene_versions WHERE id = ?", (run["scene_version_id"],)).fetchone()
        configured_lines: list[dict[str, Any]] = []
        if scene_row is not None:
            configured_lines = [
                {"line_id": line.line_id, "label": line.label}
                for line in scene_from_geometry(dict(scene_row)).counting_lines
            ]

        self.connection.execute(
            """
            INSERT INTO engineering_result_revisions(
              id, run_id, result_version, taxonomy_revision_id, mapping_revision_id,
              classification_policy_revision_id, result_status, engineering_ready,
              disclosure_json, source_time_status, source_timezone_name,
              summary_schema_version, stale, generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'IN_PROGRESS', 0, ?, ?, ?, ?, 0, ?)
            """,
            (
                result_revision_id,
                run_id,
                run["result_version"],
                revision["taxonomy_revision_id"],
                revision["mapping_revision_id"],
                revision["classification_policy_revision_id"],
                json.dumps(
                    [
                        "automatic_classification_is_provisional",
                        "pilot_taxonomy_is_not_a_validated_tims_13_class_result",
                        "overall_multi_line_total_is_event_total_not_unique_vehicle_total",
                    ],
                    sort_keys=True,
                ),
                source_time.status,
                source_time.timezone_name,
                ENGINEERING_SUMMARY_SCHEMA_VERSION,
                now(),
            ),
        )
        self.connection.execute(
            """
            UPDATE analysis_runs
            SET taxonomy_revision = ?, mapping_revision = ?, classification_policy_revision = ?
            WHERE id = ?
            """,
            (TAXONOMY_REVISION, MAPPING_REVISION, CLASSIFICATION_POLICY_REVISION, run_id),
        )

        track_rows = self.connection.execute(
            "SELECT track_id, summary_json, evidence_schema_version FROM track_summaries WHERE run_id = ? ORDER BY track_id",
            (run_id,),
        ).fetchall()
        track_metadata: dict[str, dict[str, Any]] = {}
        for row in track_rows:
            try:
                payload = json.loads(str(row["summary_json"]))
            except json.JSONDecodeError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            track_metadata[str(row["track_id"])] = payload

        event_rows = self.connection.execute(
            """
            SELECT e.*, l.id AS ledger_id, l.source_fingerprint_sha256 AS ledger_source_fingerprint,
                   l.scene_revision AS ledger_scene_revision, l.counting_line_id AS ledger_line_id,
                   l.counting_line_label AS ledger_line_name, l.side_a_label AS ledger_side_a_name,
                   l.side_b_label AS ledger_side_b_name, l.crossing_direction AS ledger_direction,
                   l.readable_direction_label AS ledger_direction_name,
                   l.crossing_timestamp_ms AS ledger_pts_ms,
                   l.source_frame_index AS ledger_source_frame_index,
                   l.source_frame_pts_ms AS ledger_source_frame_pts_ms,
                   l.provenance AS ledger_provenance
            FROM auto_count_events e
            LEFT JOIN crossing_event_ledger l ON l.id = e.id
            WHERE e.run_id = ?
            ORDER BY COALESCE(l.crossing_timestamp_ms, e.pts_ms), COALESCE(l.counting_line_id, e.rule_id), e.track_id, e.technical_key
            """,
            (run_id,),
        ).fetchall()
        projection_inputs: list[dict[str, Any]] = []
        track_evidence_payloads: dict[str, dict[str, Any]] = {}
        invalid_direction_exclusions: list[dict[str, Any]] = []
        classification_exclusions: list[dict[str, Any]] = []
        for row in event_rows:
            event = dict(row)
            track_id = str(event["track_id"])
            metadata = track_metadata.get(track_id, {})
            raw_class_name = event.get("raw_class_name") or metadata.get("raw_class_name")
            raw_class_id = event.get("raw_class_id") if event.get("raw_class_id") is not None else metadata.get("raw_class_id")
            decision, evidence, classification_error = self._resolve_engineering_classification(event, metadata)
            classification_input = dict(event)
            classification_input["classification_status"] = metadata.get("classification_status", evidence.get("classification_status", decision.classification_status))
            classification_input["engineering_class"] = metadata.get("engineering_class", evidence.get("engineering_class", decision.engineering_class))
            if classification_error:
                classification_exclusions.append(classification_exclusion(classification_input, classification_error))
            event_pts_ms = int(event.get("ledger_pts_ms") if event.get("ledger_pts_ms") is not None else event.get("pts_ms") or 0)
            time_semantics = source_time_semantics(source, event_pts_ms)
            line_id = str(event.get("ledger_line_id") or event.get("rule_id") or "unknown")
            line_name = str(event.get("ledger_line_name") or line_id)
            if event.get("ledger_id") is not None:
                original_direction = event.get("ledger_direction")
                direction_name_source = event.get("ledger_direction_name")
            else:
                original_direction = event.get("movement")
                direction_name_source = event.get("movement")
            direction = canonical_direction(original_direction)
            if direction is None:
                invalid_direction_exclusions.append(invalid_direction_exclusion(event, original_direction))
            track_evidence_payloads.setdefault(
                track_id,
                {
                    "track_id": track_id,
                    "schema_version": evidence.get("schema_version", TRACK_EVIDENCE_SCHEMA_VERSION),
                    "evidence": evidence,
                    "provenance": {
                        "processing_mode": run.get("processing_mode") or run.get("engine_mode") or "SYNTHETIC",
                        "detector_id": run.get("detector_id"),
                        "tracker_id": run.get("tracker_id"),
                        "configuration_revision": run.get("configuration_revision"),
                        "runtime_configuration_hash": run.get("runtime_configuration_hash"),
                        "request_provenance_hash": run.get("request_provenance_hash"),
                        "actual_runtime_configuration_hash": run.get("actual_runtime_configuration_hash"),
                        "provenance_status": run.get("provenance_status", "LEGACY_UNRESOLVED"),
                        "model_revision": run.get("model_revision") or configured_provenance.get("model_revision"),
                        "weight_sha256": run.get("weight_sha256") or configured_provenance.get("weight_sha256"),
                        "tracker_revision": run.get("tracker_revision") or configured_provenance.get("tracker_revision"),
                        "crossing_policy_revision": configured_provenance.get("crossing_policy_revision"),
                        "classification_policy_revision": configured_provenance.get("classification_policy_revision"),
                    },
                },
            )
            if classification_error or direction is None:
                continue
            projection_inputs.append(
                {
                    "source_event_id": str(event["id"]),
                    "technical_key": str(event["technical_key"]),
                    "source_fingerprint_sha256": str(event.get("ledger_source_fingerprint") or source["fingerprint_sha256"]),
                    "scene_revision": str(event.get("ledger_scene_revision") or run["scene_version_id"]),
                    "line_id": line_id,
                    "line_name": line_name,
                    "side_a_name": str(event.get("ledger_side_a_name") or "Side A"),
                    "side_b_name": str(event.get("ledger_side_b_name") or "Side B"),
                    "direction": direction,
                    "readable_direction_name": str(direction_name_source or direction),
                    "track_id": track_id,
                    "event_pts_ms": event_pts_ms,
                    "source_frame_index": event.get("ledger_source_frame_index") if event.get("ledger_source_frame_index") is not None else event.get("source_frame_index"),
                    "source_frame_pts_ms": event.get("ledger_source_frame_pts_ms") if event.get("ledger_source_frame_pts_ms") is not None else event.get("source_frame_pts_ms"),
                    "absolute_event_time": time_semantics.absolute_event_time,
                    "event_timezone_name": time_semantics.timezone_name,
                    "event_time_status": time_semantics.status,
                    "raw_detector_class_id": raw_class_id,
                    "raw_detector_class_name": raw_class_name,
                    "track_voted_raw_class_id": decision.track_voted_raw_class_id,
                    "track_voted_raw_class_name": decision.track_voted_raw_class_name,
                    "provisional_class": decision.provisional_class,
                    "engineering_class": decision.engineering_class,
                    "classification_status": decision.classification_status,
                    "classification_reason": decision.classification_reason,
                    "detector_confidence_summary": evidence.get("confidence_stats", {"event_confidence": event.get("detector_confidence", event.get("confidence"))}),
                    "track_evidence_ref": f"{result_revision_id}:{track_id}",
                    "processing_provenance": {
                        "processing_mode": run.get("processing_mode") or run.get("engine_mode") or "SYNTHETIC",
                        "detector_id": run.get("detector_id"),
                        "tracker_id": run.get("tracker_id"),
                        "configuration_revision": run.get("configuration_revision"),
                        "runtime_configuration_hash": run.get("runtime_configuration_hash"),
                        "request_provenance_hash": run.get("request_provenance_hash"),
                        "actual_runtime_configuration_hash": run.get("actual_runtime_configuration_hash"),
                        "provenance_status": run.get("provenance_status", "LEGACY_UNRESOLVED"),
                        "model_revision": run.get("model_revision") or configured_provenance.get("model_revision"),
                        "weight_sha256": run.get("weight_sha256") or configured_provenance.get("weight_sha256"),
                        "tracker_revision": run.get("tracker_revision") or configured_provenance.get("tracker_revision"),
                        "crossing_policy_revision": configured_provenance.get("crossing_policy_revision"),
                        "classification_policy_revision": configured_provenance.get("classification_policy_revision"),
                        "source_event_provenance": event.get("ledger_provenance"),
                    },
                    "qc_state": str(event.get("qc_state") or "needs_review"),
                }
            )

        for track_id in sorted(track_evidence_payloads):
            payload = track_evidence_payloads[track_id]
            self.connection.execute(
                """
                INSERT INTO engineering_track_evidence(
                  id, engineering_result_revision_id, run_id, track_id,
                  evidence_schema_version, evidence_json, provenance_json, stale, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    f"{result_revision_id}:{track_id}",
                    result_revision_id,
                    run_id,
                    track_id,
                    payload["schema_version"],
                    json.dumps(payload["evidence"], sort_keys=True),
                    json.dumps(payload["provenance"], sort_keys=True),
                    now(),
                ),
            )

        for event in projection_inputs:
            self.connection.execute(
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
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    f"{result_revision_id}:event:{event['source_event_id']}",
                    result_revision_id,
                    run_id,
                    event["source_event_id"],
                    event["technical_key"],
                    event["source_fingerprint_sha256"],
                    event["scene_revision"],
                    event["line_id"],
                    event["line_name"],
                    event["side_a_name"],
                    event["side_b_name"],
                    event["direction"],
                    event["readable_direction_name"],
                    event["track_id"],
                    event["event_pts_ms"],
                    event["source_frame_index"],
                    event["source_frame_pts_ms"],
                    event["absolute_event_time"],
                    event["event_timezone_name"],
                    event["event_time_status"],
                    event["raw_detector_class_id"],
                    event["raw_detector_class_name"],
                    event["track_voted_raw_class_id"],
                    event["track_voted_raw_class_name"],
                    event["provisional_class"],
                    event["engineering_class"],
                    event["classification_status"],
                    event["classification_reason"],
                    TAXONOMY_REVISION,
                    MAPPING_REVISION,
                    CLASSIFICATION_POLICY_REVISION,
                    json.dumps(event["detector_confidence_summary"], sort_keys=True),
                    event["track_evidence_ref"],
                    json.dumps(event["processing_provenance"], sort_keys=True),
                    event["qc_state"],
                    now(),
                ),
            )

        aggregate = aggregate_engineering_events(
            projection_inputs,
            intervals,
            configured_lines=configured_lines,
            source_event_total=len(event_rows),
            invalid_direction_exclusions=invalid_direction_exclusions,
            classification_exclusions=classification_exclusions,
        )
        for interval in intervals:
            self.connection.execute(
                """
                INSERT INTO engineering_intervals(
                  id, engineering_result_revision_id, run_id, interval_index,
                  source_relative_start_pts_ms, source_relative_end_pts_ms,
                  absolute_start, absolute_end, timezone_name, display_label,
                  time_status, partial
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{result_revision_id}:interval:{interval['index']}",
                    result_revision_id,
                    run_id,
                    interval["index"],
                    interval["source_relative_start_pts_ms"],
                    interval["source_relative_end_pts_ms"],
                    interval["absolute_start"],
                    interval["absolute_end"],
                    interval["timezone_name"],
                    interval["display_label"],
                    interval["time_status"],
                    1 if interval["partial"] else 0,
                ),
            )
        for row in aggregate["interval_rows"]:
            self.connection.execute(
                """
                INSERT INTO engineering_summary_rows(
                  id, engineering_result_revision_id, run_id, interval_index,
                  counting_line_id, counting_line_name, canonical_direction,
                  engineering_class, count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{result_revision_id}:summary:{row['interval_index']}:{row['line_id']}:{row['direction']}:{row['engineering_class']}",
                    result_revision_id,
                    run_id,
                    row["interval_index"],
                    row["line_id"],
                    row["line_name"],
                    row["direction"],
                    row["engineering_class"],
                    row["count"],
                ),
            )
        reconciliation = reconcile_engineering_counts(projection_inputs, aggregate)
        reconciliation["result_revision"] = result_revision_id
        self.connection.execute(
            """
            INSERT INTO reconciliation_reports(id, engineering_result_revision_id, run_id, status, report_json, generated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (new_id("recon"), result_revision_id, run_id, reconciliation["status"], json.dumps(reconciliation, sort_keys=True), now()),
        )
        self.connection.execute(
            """
            UPDATE engineering_result_revisions
            SET result_status = ?, engineering_ready = ?, stale = 0
            WHERE id = ?
            """,
            (reconciliation["status"], 1 if reconciliation["engineering_ready"] else 0, result_revision_id),
        )
        return {"id": result_revision_id, "run_id": run_id, "engineering_ready": reconciliation["engineering_ready"], "reconciliation": reconciliation, "summary": aggregate}

    @staticmethod
    def _legacy_engineering_class(value: str) -> str:
        """Keep legacy rows conservative until raw evidence is available."""

        return EngineeringClass.UNKNOWN.value

    def _engineering_result_row(self, run_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM engineering_result_revisions WHERE run_id = ? ORDER BY generated_at DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if row is None:
            raise ValueError("engineering result not found")
        return row

    def get_taxonomy(self, revision: str | None = None) -> dict[str, Any]:
        revision_name = revision or TAXONOMY_REVISION
        row = self.connection.execute("SELECT * FROM taxonomy_revisions WHERE revision = ?", (revision_name,)).fetchone()
        if row is None:
            raise ValueError("taxonomy revision not found")
        classes = [dict(item) for item in self.connection.execute("SELECT * FROM taxonomy_classes WHERE taxonomy_revision_id = ? ORDER BY display_order, code", (row["id"],))]
        payload = json.loads(row["content_json"])
        return {"id": row["id"], "revision": row["revision"], "content_hash": row["content_hash"], "status": row["status"], "classes": classes, "target_taxonomy": payload.get("target_taxonomy", {})}

    def get_classification_policy(self, revision: str | None = None) -> dict[str, Any]:
        revision_name = revision or CLASSIFICATION_POLICY_REVISION
        row = self.connection.execute("SELECT * FROM classification_policy_revisions WHERE revision = ?", (revision_name,)).fetchone()
        if row is None:
            raise ValueError("classification policy revision not found")
        return {"id": row["id"], "revision": row["revision"], "content_hash": row["content_hash"], "status": row["status"], "policy": json.loads(row["content_json"])}

    def list_engineering_events(self, run_id: str, limit: int = 1000, offset: int = 0) -> list[dict[str, Any]]:
        result = self._engineering_result_row(run_id)
        payloads = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT * FROM engineering_event_projections
                WHERE engineering_result_revision_id = ?
                ORDER BY event_pts_ms, counting_line_id, track_id, technical_key
                LIMIT ? OFFSET ?
                """,
                (result["id"], limit, offset),
            )
        ]
        for payload in payloads:
            try:
                payload["detector_confidence_summary"] = json.loads(payload.get("detector_confidence_summary_json") or "{}")
            except json.JSONDecodeError:
                payload["detector_confidence_summary"] = {}
            try:
                payload["processing_provenance"] = json.loads(payload.get("processing_provenance_json") or "{}")
            except json.JSONDecodeError:
                payload["processing_provenance"] = {}
        return payloads

    def get_track_evidence(self, run_id: str, track_id: str | None = None) -> list[dict[str, Any]]:
        result = self._engineering_result_row(run_id)
        query = "SELECT * FROM engineering_track_evidence WHERE engineering_result_revision_id = ?"
        params: list[Any] = [result["id"]]
        if track_id:
            query += " AND track_id = ?"
            params.append(track_id)
        query += " ORDER BY track_id"
        rows = []
        for row in self.connection.execute(query, params):
            payload = dict(row)
            payload["evidence"] = json.loads(payload.pop("evidence_json"))
            payload["provenance"] = json.loads(payload.pop("provenance_json"))
            rows.append(payload)
        if track_id and not rows:
            raise ValueError("track evidence not found")
        return rows

    def get_engineering_summary(self, run_id: str) -> dict[str, Any]:
        result = self._engineering_result_row(run_id)
        result_dict = dict(result)
        event_rows = self.list_engineering_events(run_id, limit=5000)
        interval_rows = [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM engineering_intervals WHERE engineering_result_revision_id = ? ORDER BY interval_index",
                (result["id"],),
            )
        ]
        configured_lines: list[dict[str, Any]] = []
        run_row = self.connection.execute("SELECT scene_version_id FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()
        if run_row is not None:
            scene_row = self.connection.execute("SELECT * FROM scene_versions WHERE id = ?", (run_row["scene_version_id"],)).fetchone()
            if scene_row is not None:
                configured_lines = [
                    {"line_id": line.line_id, "label": line.label}
                    for line in scene_from_geometry(dict(scene_row)).counting_lines
                ]
        if not configured_lines:
            configured_lines = [{"line_id": row["counting_line_id"], "label": row["counting_line_name"]} for row in event_rows]
        reconciliation_row = self.connection.execute("SELECT report_json FROM reconciliation_reports WHERE engineering_result_revision_id = ?", (result["id"],)).fetchone()
        stored_reconciliation = json.loads(reconciliation_row["report_json"]) if reconciliation_row else {}
        aggregate = aggregate_engineering_events(
            event_rows,
            interval_rows,
            configured_lines=configured_lines,
            source_event_total=stored_reconciliation.get("source_event_total", len(event_rows)),
            invalid_direction_exclusions=stored_reconciliation.get("invalid_direction_exclusions", []),
            classification_exclusions=stored_reconciliation.get("classification_exclusions", []),
        )
        reconciliation = reconcile_engineering_counts(event_rows, aggregate)
        reconciliation["result_revision"] = result_dict["id"]
        provenance_row = self.connection.execute("SELECT processing_mode, detector_id, tracker_id, configuration_revision FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()
        return {
            "id": result_dict["id"],
            "run_id": run_id,
            "result_version": result_dict["result_version"],
            "result_status": result_dict["result_status"],
            "engineering_ready": bool(result_dict["engineering_ready"]) and not bool(result_dict["stale"]) and reconciliation.get("engineering_ready", False),
            "stale": bool(result_dict["stale"]),
            "taxonomy_revision": TAXONOMY_REVISION,
            "mapping_revision": MAPPING_REVISION,
            "classification_policy_revision": CLASSIFICATION_POLICY_REVISION,
            "source_time_status": result_dict["source_time_status"],
            "source_timezone_name": result_dict["source_timezone_name"],
            "disclosures": json.loads(result_dict["disclosure_json"]),
            "line_totals": aggregate["line_totals"],
            "direction_totals": aggregate["direction_totals"],
            "line_class_totals": aggregate["line_class_totals"],
            "direction_class_matrix": aggregate["direction_class_matrix"],
            "intervals": interval_rows,
            "interval_rows": aggregate["interval_rows"],
            "overall_event_total": aggregate["overall_event_total"],
            "events_count": len(event_rows),
            "reconciliation": reconciliation,
            "provenance": dict(provenance_row) if provenance_row else {},
        }

    def get_engineering_reconciliation(self, run_id: str) -> dict[str, Any]:
        result = self._engineering_result_row(run_id)
        row = self.connection.execute("SELECT * FROM reconciliation_reports WHERE engineering_result_revision_id = ?", (result["id"],)).fetchone()
        if row is None:
            raise ValueError("reconciliation report not found")
        payload = dict(row)
        payload.pop("report_json")
        payload["report"] = self.get_engineering_summary(run_id)["reconciliation"]
        return payload

    def _insert_mock_events(self, run_id: str) -> None:
        rows = [
            ("veh-001-line-1", 120_000, "track_001", "passenger_vehicle", "northbound", 0.94, "ok"),
            ("veh-002-line-1", 900_000, "track_002", "unknown", "northbound", 0.51, "needs_review"),
            ("ped-001-line-1", 1_810_000, "track_003", "pedestrian", "crossing", 0.88, "ok"),
            ("veh-004-line-1", 2_710_000, "track_004", "ambiguous", "right_turn", 0.48, "needs_review"),
        ]
        for technical_key, pts_ms, track_id, classification, movement, confidence, qc_state in rows:
            event_id = f"evt_{run_id}_{technical_key.replace('-', '_')}"
            self.connection.execute(
                """
                INSERT OR IGNORE INTO auto_count_events(
                  id, run_id, technical_key, pts_ms, track_id, rule_id, object_domain,
                  classification, movement, confidence, qc_state, created_at
                ) VALUES (?, ?, ?, ?, ?, 'line_1', ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    run_id,
                    technical_key,
                    pts_ms,
                    track_id,
                    "pedestrian" if classification == "pedestrian" else "vehicle",
                    classification,
                    movement,
                    confidence,
                    qc_state,
                    now(),
                ),
            )
            if qc_state == "needs_review":
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO qc_flags(id, event_id, severity, reason, mandatory)
                    VALUES (?, ?, 'mandatory', 'low confidence or ambiguous class', 1)
                    """,
                    (f"qc_{event_id}", event_id),
                )

    def _create_aggregate_snapshot(self, run_id: str, stale: bool) -> dict:
        run = self.connection.execute("SELECT * FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()
        source = self.connection.execute("SELECT * FROM video_sources WHERE id = ?", (run["source_id"],)).fetchone()
        contract = TimeContract(
            source_started_at=datetime.fromisoformat(source["source_started_at"]),
            timezone_name=source["timezone_name"],
            analysis_start_pts_ms=source["analysis_start_pts_ms"],
            analysis_end_pts_ms=source["analysis_end_pts_ms"],
            interval_origin_pts_ms=source["interval_origin_pts_ms"],
        )
        events = [
            AutoCountEvent(
                id=row["id"],
                run_id=row["run_id"],
                technical_key=row["technical_key"],
                pts_ms=row["pts_ms"],
                track_id=row["track_id"],
                rule_id=row["rule_id"],
                object_domain=row["object_domain"],
                classification=row["classification"],
                movement=row["movement"],
                confidence=row["confidence"],
                qc_state=row["qc_state"],
            )
            for row in self.connection.execute("SELECT * FROM auto_count_events WHERE run_id = ?", (run_id,))
        ]
        counts = bucket_counts(contract, events)
        engineering_row = self.connection.execute(
            "SELECT id, result_status, summary_schema_version FROM engineering_result_revisions WHERE run_id = ? ORDER BY generated_at DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        snapshot_id = new_id("agg")
        self.connection.execute(
            """
            INSERT INTO aggregate_snapshots(
              id, run_id, result_version, fifteen_minute_counts_json, hourly_total, phf, stale,
              engineering_result_revision_id, reconciliation_status, summary_schema_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                run_id,
                run["result_version"],
                json.dumps(counts),
                sum(counts),
                peak_hour_factor(counts),
                1 if stale else 0,
                engineering_row["id"] if engineering_row else None,
                engineering_row["result_status"] if engineering_row else "NOT_GENERATED",
                engineering_row["summary_schema_version"] if engineering_row else "legacy-aggregate-v1",
                now(),
            ),
        )
        return dict(self.connection.execute("SELECT * FROM aggregate_snapshots WHERE id = ?", (snapshot_id,)).fetchone())


class StaleWriteError(ValueError):
    pass
