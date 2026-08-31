"""SQLite adapter for Milestone 6E review, certification and export contracts."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Mapping
from xml.sax.saxutils import escape as xml_escape

from .engineering_outputs import EngineeringClass, VALID_CANONICAL_DIRECTIONS
from .release import release_identity
from .reviewing import (
    MATERIAL_ACTION_TYPES,
    REVIEW_SCOPE_TYPES,
    ReviewDomainError,
    action_payload,
    canonical_json,
    normalize_action_type,
    project_human_added_event,
    project_review_event,
    reconcile_reviewed_events,
    reversal_state,
    sha256_json,
    validate_reversal_target,
)
from .scene_geometry import scene_semantic_hash


MAX_REVIEW_COMMENT_LENGTH = 2000
MAX_REVIEW_PAYLOAD_BYTES = 16_384
MAX_SCOPE_EVENT_IDS = 5_000
EXPORT_FORMATS = {"CSV", "XLSX", "JSON"}
EXPORT_SCHEMA_REVISIONS = {
    "CSV": "production-export-csv-v2",
    "XLSX": "production-export-xlsx-v2",
    "JSON": "audit-export-v2",
}
_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9_.-]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{hashlib.sha256(f'{prefix}:{_now()}:{os.urandom(8).hex()}'.encode()).hexdigest()[:16]}"


def _load_json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        loaded = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default
    return loaded


def _safe_component(value: str, fallback: str = "item") -> str:
    cleaned = _SAFE_COMPONENT.sub("-", value).strip(".-")
    return (cleaned or fallback)[:80]


def safe_spreadsheet_text(value: Any) -> str:
    """Keep user/imported text in spreadsheet text cells, never formulas."""

    text = "" if value is None else str(value)
    stripped = text.lstrip(" \t\r\n")
    return f"'{text}" if stripped.startswith(("=", "+", "-", "@")) else text


def certification_content_hash(content: Mapping[str, Any]) -> str:
    """Hash frozen certification content without attestation identity fields."""

    return sha256_json(content)


class ReviewConflictError(Exception):
    def __init__(
        self,
        code: str,
        detail: str,
        *,
        session_id: str,
        expected_revision: int | None = None,
        current_revision: int | None = None,
        actions_since: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.session_id = session_id
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        self.actions_since = actions_since or []


class ReviewStaleError(Exception):
    def __init__(self, code: str, detail: str, *, session_id: str | None = None) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.session_id = session_id


class ArtifactPathError(ValueError):
    """Base error for managed-artifact path validation failures."""

    code = "ARTIFACT_PATH_NOT_MANAGED"


class ArtifactSymlinkError(ArtifactPathError):
    """A lexical path component is a symlink and cannot be followed."""

    code = "ARTIFACT_SYMLINK_NOT_ALLOWED"


class ArtifactStore:
    """Managed local artifact root with traversal and symlink protection."""

    def __init__(self, root: str | Path | None = None) -> None:
        configured = root or os.getenv("TVA_EXPORT_ROOT") or ".local-data/exports"
        self.root = Path(configured).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _checked_path(self, relative_path: str) -> Path:
        raw = str(relative_path)
        if not raw or "\x00" in raw:
            raise ArtifactPathError("artifact path is not managed")
        # Validate both path grammars so a path rejected on Windows is also
        # rejected when the same contract is exercised on POSIX (and vice
        # versa).  This catches drive-absolute and UNC forms before joining.
        posix = PurePosixPath(raw.replace("\\", "/"))
        windows = PureWindowsPath(raw)
        if (
            posix.is_absolute()
            or windows.is_absolute()
            or bool(windows.drive)
            or raw.startswith(("/", "\\\\"))
        ):
            raise ArtifactPathError("artifact path is not managed")
        lexical_parts = raw.replace("\\", "/").split("/")
        if any(part in {"", ".", ".."} for part in lexical_parts):
            raise ArtifactPathError("artifact path is not managed")
        relative = Path(*lexical_parts)
        # Check the lexical path first.  Calling resolve() before this loop
        # follows a directory symlink on Windows and loses the reason for the
        # rejection, making a safe symlink escape indistinguishable from an
        # ordinary root-containment failure.
        current = self.root
        for part in lexical_parts:
            current = current / part
            if current.is_symlink():
                raise ArtifactSymlinkError("symlink escape is not allowed")
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ArtifactPathError("artifact path escapes managed root") from exc
        return candidate

    def write_atomic(self, relative_path: str, data: bytes) -> Path:
        path = self._checked_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise ArtifactSymlinkError("artifact target cannot be a symlink")
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix=".partial-", dir=path.parent, delete=False) as handle:
                temp_path = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            return path
        except Exception:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise

    def delete_relative(self, relative_path: str) -> None:
        path = self._checked_path(relative_path)
        if path.exists() and path.is_file() and not path.is_symlink():
            path.unlink()

    def resolve_download(self, relative_path: str) -> Path:
        path = self._checked_path(relative_path)
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError("artifact is unavailable")
        return path


class ReviewService:
    def __init__(self, connection: sqlite3.Connection, artifact_root: str | Path | None = None) -> None:
        self.connection = connection
        self.artifacts = ArtifactStore(artifact_root)

    # ---------- context and scope ----------

    def _session(self, session_id: str) -> sqlite3.Row:
        row = self.connection.execute("SELECT * FROM review_sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise ValueError("review session not found")
        return row

    def _result_context(self, session: sqlite3.Row) -> dict[str, Any]:
        source = self.connection.execute("SELECT * FROM video_sources WHERE id = ?", (session["source_id"],)).fetchone()
        run = self.connection.execute("SELECT * FROM analysis_runs WHERE id = ?", (session["processing_run_id"],)).fetchone()
        scene = self.connection.execute(
            "SELECT * FROM scene_versions WHERE project_id = ? ORDER BY version DESC LIMIT 1",
            (session["project_id"],),
        ).fetchone()
        result = self.connection.execute(
            "SELECT * FROM engineering_result_revisions WHERE id = ?", (session["engineering_result_revision_id"],)
        ).fetchone()
        if source is None or run is None or scene is None or result is None:
            raise ReviewStaleError("REVIEW_CONTEXT_MISSING", "Review provenance is no longer available.", session_id=session["id"])
        geometry = _load_json(scene["geometry_json"], {})
        current_scene_hash = scene_semantic_hash(geometry)
        taxonomy = self.connection.execute(
            "SELECT revision FROM taxonomy_revisions WHERE id = ?", (result["taxonomy_revision_id"],)
        ).fetchone()
        mapping = self.connection.execute(
            "SELECT revision FROM taxonomy_mapping_revisions WHERE id = ?", (result["mapping_revision_id"],)
        ).fetchone()
        policy = self.connection.execute(
            "SELECT revision FROM classification_policy_revisions WHERE id = ?", (result["classification_policy_revision_id"],)
        ).fetchone()
        current_runtime_hash = run["runtime_configuration_hash"] or session["runtime_configuration_hash"]
        return {
            "source": source,
            "run": run,
            "scene": scene,
            "result": result,
            "geometry": geometry,
            "source_fingerprint_sha256": str(source["fingerprint_sha256"]),
            "scene_revision": str(scene["version"]),
            "scene_semantic_hash": current_scene_hash,
            "taxonomy_revision": str(taxonomy["revision"] if taxonomy else result["taxonomy_revision_id"]),
            "mapping_revision": str(mapping["revision"] if mapping else result["mapping_revision_id"]),
            "classification_policy_revision": str(policy["revision"] if policy else result["classification_policy_revision_id"]),
            "runtime_configuration_hash": current_runtime_hash,
            "analysis_start_pts_ms": int(source["analysis_start_pts_ms"]),
            "analysis_end_pts_ms": int(source["analysis_end_pts_ms"]),
            "interval_origin_pts_ms": int(source["interval_origin_pts_ms"]),
            "source_started_at": str(source["source_started_at"]),
            "timezone_name": str(source["timezone_name"]),
        }

    def _scope_filter(self, session: sqlite3.Row, alias: str = "e") -> tuple[str, list[Any]]:
        scope = str(session["review_scope_type"])
        filt = _load_json(session["review_scope_filter_json"], {})
        if scope == "FULL_RESULT":
            return "", []
        if scope == "LINE":
            return f" AND {alias}.counting_line_id = ?", [str(filt.get("line_id") or filt.get("counting_line_id") or "")]
        if scope == "TIME_INTERVAL":
            return f" AND {alias}.event_pts_ms >= ? AND {alias}.event_pts_ms < ?", [int(filt["start_pts_ms"]), int(filt["end_pts_ms"])]
        if scope == "DIAGNOSTIC_SUBSET":
            ids = [str(value) for value in filt.get("event_ids", [])]
            if not ids:
                return " AND 0 = 1", []
            placeholders = ",".join("?" for _ in ids)
            return f" AND {alias}.source_event_id IN ({placeholders})", ids
        raise ReviewDomainError("INVALID_REVIEW_SCOPE", f"Unsupported review scope: {scope}")

    def _scope_event_rows(self, session: sqlite3.Row) -> list[dict[str, Any]]:
        where, params = self._scope_filter(session)
        rows = self.connection.execute(
            f"""
            SELECT e.*
            FROM engineering_event_projections e
            WHERE e.engineering_result_revision_id = ?
              AND e.stale = 0
              {where}
            ORDER BY e.event_pts_ms, e.counting_line_id, e.source_event_id
            """,
            [session["engineering_result_revision_id"], *params],
        ).fetchall()
        return [self._public_engineering_event(row) for row in rows]

    def _public_engineering_event(self, row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["evidence"] = {
            "detector_confidence_summary": _load_json(payload.pop("detector_confidence_summary_json", "{}"), {}),
            "track_evidence_ref": payload.get("track_evidence_ref"),
            "source_frame_index": payload.get("source_frame_index"),
            "source_frame_pts_ms": payload.get("source_frame_pts_ms"),
        }
        payload["processing_provenance"] = _load_json(payload.pop("processing_provenance_json", "{}"), {})
        return payload

    def _actions(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM review_actions WHERE review_session_id = ? ORDER BY action_order, created_at, id",
            (session_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = _load_json(item.get("payload_json"), {})
            item["action_type"] = normalize_action_type(str(item["action_type"]))
            result.append(item)
        return result

    def _validate_scope_filter(self, scope_type: str, scope_filter: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        if scope_type not in REVIEW_SCOPE_TYPES:
            raise ReviewDomainError("INVALID_REVIEW_SCOPE", "Unsupported review scope.", field="review_scope_type")
        result = dict(scope_filter)
        if scope_type == "LINE":
            line_id = str(result.get("line_id") or result.get("counting_line_id") or "")
            line_ids = {str(item.get("id")) for item in context["geometry"].get("counting_lines", []) if item.get("active", True)}
            if not line_id or line_id not in line_ids:
                raise ReviewDomainError("INVALID_REVIEW_LINE", "Review line is not present in the selected scene revision.", field="review_scope_filter")
            result = {"line_id": line_id}
        elif scope_type == "TIME_INTERVAL":
            try:
                start = int(result["start_pts_ms"])
                end = int(result["end_pts_ms"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ReviewDomainError("INVALID_REVIEW_INTERVAL", "Review interval must include integer start/end PTS.", field="review_scope_filter") from exc
            if start < context["analysis_start_pts_ms"] or end > context["analysis_end_pts_ms"] or end <= start:
                raise ReviewDomainError("INVALID_REVIEW_INTERVAL", "Review interval must be inside the analysis window.", field="review_scope_filter")
            result = {"start_pts_ms": start, "end_pts_ms": end}
        elif scope_type == "DIAGNOSTIC_SUBSET":
            ids = [str(value) for value in result.get("event_ids", [])]
            if len(ids) > MAX_SCOPE_EVENT_IDS:
                raise ReviewDomainError("REVIEW_SCOPE_TOO_LARGE", "Diagnostic review scope is too large.", field="review_scope_filter")
            result = {"event_ids": sorted(set(ids))}
        else:
            result = {}
        return result

    def create_session(self, project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        run_id = str(payload.get("processing_run_id") or payload.get("run_id") or "")
        if not run_id:
            raise ReviewDomainError("RUN_REQUIRED", "A completed processing run is required.", field="processing_run_id")
        run = self.connection.execute(
            "SELECT * FROM analysis_runs WHERE id = ? AND project_id = ?", (run_id, project_id)
        ).fetchone()
        if run is None:
            raise ValueError("processing run not found")
        result_id = str(payload.get("engineering_result_revision_id") or "")
        if not result_id:
            latest = self.connection.execute(
                "SELECT id FROM engineering_result_revisions WHERE run_id = ? ORDER BY generated_at DESC, id DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            result_id = str(latest["id"]) if latest else ""
        if not result_id:
            raise ReviewDomainError("ENGINEERING_RESULT_REQUIRED", "A versioned engineering result is required.", field="engineering_result_revision_id")
        result_row = self.connection.execute("SELECT * FROM engineering_result_revisions WHERE id = ?", (result_id,)).fetchone()
        if result_row is None:
            raise ReviewDomainError("ENGINEERING_RESULT_NOT_FOUND", "Engineering result revision was not found.", field="engineering_result_revision_id")
        context = self._context_for_create(project_id, run_id, result_id)
        scope_type = str(payload.get("review_scope_type") or "FULL_RESULT")
        scope_filter = self._validate_scope_filter(scope_type, payload.get("review_scope_filter") or {}, context)
        scope_filter_json = canonical_json(scope_filter)
        provisional = self.connection.execute(
            "SELECT * FROM review_sessions WHERE processing_run_id = ? AND engineering_result_revision_id = ? AND review_scope_type = ? AND review_scope_filter_json = ? AND review_status NOT IN ('SUPERSEDED') ORDER BY created_at DESC LIMIT 1",
            (run_id, result_id, scope_type, scope_filter_json),
        ).fetchone()
        if provisional is not None and not bool(payload.get("new_revision")):
            return self.get_session(str(provisional["id"]))
        source = context["source"]
        session_id = _new_id("review")
        created_at = _now()
        self.connection.execute(
            """
            INSERT INTO review_sessions(
              id, project_id, source_id, source_fingerprint_sha256,
              processing_run_id, processing_configuration_revision_id,
              runtime_configuration_hash, engineering_result_revision_id,
              scene_revision, scene_semantic_hash, taxonomy_revision,
              mapping_revision, classification_policy_revision,
              review_scope_type, review_scope_filter_json, review_status,
              review_revision, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'NOT_STARTED', 0, ?, ?)
            """,
            (
                session_id,
                project_id,
                source["id"],
                context["source_fingerprint_sha256"],
                run_id,
                run["processing_configuration_revision_id"],
                context["runtime_configuration_hash"],
                result_id,
                context["scene_revision"],
                context["scene_semantic_hash"],
                context["taxonomy_revision"],
                context["mapping_revision"],
                context["classification_policy_revision"],
                scope_type,
                scope_filter_json,
                str(payload.get("created_by") or "operator"),
                created_at,
            ),
        )
        self._state_event(session_id, None, "NOT_STARTED", 0, "session_created", str(payload.get("created_by") or "operator"))
        self.connection.commit()
        return self.get_session(session_id)

    def _state_event(self, session_id: str, from_status: str | None, to_status: str, revision: int, reason: str, actor: str) -> None:
        self.connection.execute(
            """
            INSERT INTO review_session_state_events(
              id, review_session_id, from_status, to_status, review_revision,
              reason, actor_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (_new_id("reviewstate"), session_id, from_status, to_status, revision, reason, actor, _now()),
        )

    def _context_for_create(self, project_id: str, run_id: str, result_id: str) -> dict[str, Any]:
        source_row = self.connection.execute(
            "SELECT id FROM video_sources WHERE project_id = ? ORDER BY created_at DESC LIMIT 1", (project_id,)
        ).fetchone()
        run_row = self.connection.execute(
            "SELECT runtime_configuration_hash FROM analysis_runs WHERE id = ? AND project_id = ?", (run_id, project_id)
        ).fetchone()
        if source_row is None or run_row is None:
            raise ReviewStaleError("REVIEW_CONTEXT_MISSING", "Source and run provenance are required.")
        session = _MappingRow({
            "id": "__context__",
            "project_id": project_id,
            "source_id": source_row["id"],
            "processing_run_id": run_id,
            "engineering_result_revision_id": result_id,
            "runtime_configuration_hash": run_row["runtime_configuration_hash"],
        })
        return self._result_context(session)

    # ---------- projection and reconciliation ----------

    def _latest_projection(self, session_id: str, review_revision: int | None = None) -> sqlite3.Row | None:
        query = "SELECT * FROM reviewed_projection_revisions WHERE review_session_id = ?"
        params: list[Any] = [session_id]
        if review_revision is not None:
            query += " AND review_revision = ?"
            params.append(review_revision)
        query += " ORDER BY review_revision DESC, created_at DESC, id DESC LIMIT 1"
        return self.connection.execute(query, params).fetchone()

    def _projection_events(self, projection_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM reviewed_events WHERE reviewed_projection_revision_id = ? ORDER BY effective_crossing_pts_ms, effective_line_id, id",
            (projection_id,),
        ).fetchall()
        return [self._public_reviewed_event(row) for row in rows]

    @staticmethod
    def _public_reviewed_event(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["corrected_fields"] = _load_json(item.pop("corrected_fields_json", "[]"), [])
        item["effective_action_ids"] = _load_json(item.pop("effective_action_ids_json", "[]"), [])
        item["evidence"] = _load_json(item.pop("evidence_json", "{}"), {})
        event_confidence = item.pop("event_confidence", None)
        if event_confidence is not None:
            item["confidence"] = float(event_confidence)
        item.pop("has_correction", None)
        item.pop("is_duplicate", None)
        return item

    def _ensure_projection_row(self, session_id: str) -> sqlite3.Row:
        """Return the immutable projection for the session's current input.

        Queue and evidence requests only need the projection identity.  They
        must not call ``get_projection`` (which materializes every event) just
        to discover whether a revision already exists.
        """

        session = self._session(session_id)
        context = self._result_context(session)
        stale = self._stale_reason(session, context)
        desired_stale_status = "STALE" if stale else "CURRENT"
        existing = self.connection.execute(
            """
            SELECT *
            FROM reviewed_projection_revisions
            WHERE review_session_id = ?
              AND source_engineering_result_revision_id = ?
              AND review_revision = ?
              AND stale_status = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (
                session_id,
                str(session["engineering_result_revision_id"]),
                int(session["review_revision"]),
                desired_stale_status,
            ),
        ).fetchone()
        if existing is not None:
            return existing
        return self._build_projection(session, context, stale)

    @staticmethod
    def _projection_content_hash(
        *,
        review_session_id: str,
        review_revision: int,
        source_engineering_result_revision_id: str,
        events: Iterable[Mapping[str, Any]],
        stale_reason: str | None,
    ) -> str:
        """Hash effective content without random row/action identifiers."""

        normalized_events: list[dict[str, Any]] = []
        for event in events:
            normalized = dict(event)
            normalized.pop("reviewed_event_id", None)
            normalized.pop("human_add_action_id", None)
            normalized.pop("effective_action_ids", None)
            notes = normalized.get("notes") or []
            normalized["notes"] = [
                {"comment": str(note.get("comment") or "")}
                for note in notes
                if isinstance(note, Mapping) and str(note.get("comment") or "")
            ]
            evidence = normalized.get("evidence")
            if isinstance(evidence, Mapping):
                evidence_copy = dict(evidence)
                evidence_notes = evidence_copy.get("notes") or []
                evidence_copy["notes"] = [
                    {"comment": str(note.get("comment") or "")}
                    for note in evidence_notes
                    if isinstance(note, Mapping) and str(note.get("comment") or "")
                ]
                normalized["evidence"] = evidence_copy
            normalized_events.append(normalized)
        return sha256_json(
            {
                "review_session_id": review_session_id,
                "source_engineering_result_revision_id": source_engineering_result_revision_id,
                "review_revision": int(review_revision),
                "stale_reason": stale_reason,
                "events": normalized_events,
            }
        )

    def build_projection(self, session_id: str) -> dict[str, Any]:
        projection = self._ensure_projection_row(session_id)
        return self.get_projection(str(projection["id"]))

    def _build_projection(
        self,
        session: sqlite3.Row,
        context: Mapping[str, Any],
        stale: str | None,
    ) -> sqlite3.Row:
        session_id = str(session["id"])
        actions = self._actions(session_id)
        base_rows = self._scope_event_rows(session)
        projection_id = _new_id("projection")
        projected: list[dict[str, Any]] = []
        for base in base_rows:
            item = project_review_event(base, actions, session_context=context)
            item["reviewed_event_id"] = f"revt_{projection_id}_{base['source_event_id']}"
            if item["corrected_fields"] and item["classification_status"] not in {"UNKNOWN", "AMBIGUOUS"}:
                item["classification_status"] = "REVIEW_CORRECTED"
            projected.append(item)
        reversal = reversal_state(actions)
        for action in actions:
            if action["action_type"] != "ADD_MISSED_EVENT":
                continue
            item = project_human_added_event(action, session_context=context)
            action_id = str(action["id"])
            item["reviewed_event_id"] = f"revt_{projection_id}_{action_id}"
            active = reversal.active_by_id.get(action_id, False)
            item["review_status"] = "CONFIRMED" if active else "REJECTED_FALSE_POSITIVE"
            item["effective_action_ids"] = [action_id] if active else []
            projected.append(item)
        status = "STALE" if stale else "CURRENT"
        if stale:
            for item in projected:
                item["review_status"] = "STALE"
                item["stale_status"] = "STALE"
        content_hash = self._projection_content_hash(
            review_session_id=session_id,
            review_revision=int(session["review_revision"]),
            source_engineering_result_revision_id=str(session["engineering_result_revision_id"]),
            events=projected,
            stale_reason=stale,
        )
        existing = self.connection.execute(
            "SELECT * FROM reviewed_projection_revisions WHERE review_session_id = ? AND review_revision = ? AND content_hash = ?",
            (session_id, session["review_revision"], content_hash),
        ).fetchone()
        if existing is not None:
            return existing
        try:
            self.connection.execute("BEGIN")
            self.connection.execute(
                """
                INSERT INTO reviewed_projection_revisions(
                  id, reviewed_projection_revision_id, review_session_id,
                  source_engineering_result_revision_id, review_revision,
                  created_at, content_hash, status, runtime_configuration_hash,
                  scene_revision, taxonomy_revision, mapping_revision,
                  classification_policy_revision, stale_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    projection_id,
                    projection_id,
                    session_id,
                    session["engineering_result_revision_id"],
                    session["review_revision"],
                    _now(),
                    content_hash,
                    status,
                    context["runtime_configuration_hash"],
                    context["scene_revision"],
                    context["taxonomy_revision"],
                    context["mapping_revision"],
                    context["classification_policy_revision"],
                    status,
                ),
            )
            for item in projected:
                self._insert_reviewed_event(projection_id, session_id, item, context)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        projection = self.connection.execute(
            "SELECT * FROM reviewed_projection_revisions WHERE id = ?", (projection_id,)
        ).fetchone()
        if projection is None:
            raise RuntimeError("reviewed projection was not persisted")
        return projection

    def _insert_reviewed_event(self, projection_id: str, session_id: str, item: Mapping[str, Any], context: Mapping[str, Any]) -> None:
        event_id = str(item["reviewed_event_id"])
        evidence = dict(item.get("evidence") or {})
        evidence["notes"] = item.get("notes") or []
        confidence_summary = evidence.get("detector_confidence_summary")
        confidence_summary = confidence_summary if isinstance(confidence_summary, Mapping) else {}
        confidence_value = item.get("confidence")
        if confidence_value is None:
            for candidate in (
                confidence_summary.get("event_confidence"),
                confidence_summary.get("mean"),
                confidence_summary.get("average"),
                confidence_summary.get("mean_confidence"),
            ):
                if candidate is not None:
                    try:
                        confidence_value = float(candidate)
                    except (TypeError, ValueError):
                        confidence_value = None
                    if confidence_value is not None:
                        break
        benchmark_error_category = item.get("benchmark_error_category") or evidence.get("benchmark_error_category")
        self.connection.execute(
            """
            INSERT INTO reviewed_events(
              id, reviewed_event_id, reviewed_projection_revision_id,
              review_session_id, origin, source_event_id, human_add_action_id,
              effective_line_id, effective_line_name, effective_direction,
              effective_crossing_pts_ms, effective_class, classification_status,
              review_status, duplicate_of, corrected_fields_json,
              effective_action_ids_json, evidence_json, runtime_configuration_hash,
              scene_revision, taxonomy_revision, stale_status, created_at,
              event_confidence, benchmark_error_category, has_correction, is_duplicate
            ) VALUES (
              ?, ?, ?, ?, ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                _new_id("reviewevt"),
                event_id,
                projection_id,
                session_id,
                item["origin"],
                item.get("source_event_id"),
                item.get("human_add_action_id"),
                item["effective_line_id"],
                item["effective_line_name"],
                item["effective_direction"],
                item["effective_crossing_pts_ms"],
                item["effective_class"],
                item["classification_status"],
                item["review_status"],
                item.get("duplicate_of"),
                canonical_json(item.get("corrected_fields") or []),
                canonical_json(item.get("effective_action_ids") or []),
                canonical_json(evidence),
                item.get("runtime_configuration_hash") or context.get("runtime_configuration_hash"),
                item.get("scene_revision") or context["scene_revision"],
                item.get("taxonomy_revision") or context["taxonomy_revision"],
                item.get("stale_status") or "CURRENT",
                _now(),
                confidence_value,
                str(benchmark_error_category) if benchmark_error_category is not None else None,
                1 if item.get("corrected_fields") else 0,
                1 if item.get("duplicate_of") else 0,
            ),
        )

    def get_projection(self, projection_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM reviewed_projection_revisions WHERE id = ? OR reviewed_projection_revision_id = ?",
            (projection_id, projection_id),
        ).fetchone()
        if row is None:
            raise ValueError("reviewed projection revision not found")
        payload = dict(row)
        payload["events"] = self._projection_events(str(row["id"]))
        payload["event_count"] = len(payload["events"])
        return payload

    def get_projection_history(self, session_id: str, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM reviewed_projection_revisions WHERE review_session_id = ? ORDER BY review_revision DESC, created_at DESC, id DESC LIMIT ? OFFSET ?",
            (session_id, min(limit, 100), max(offset, 0)),
        ).fetchall()
        return [dict(row) for row in rows]

    def reconcile(self, session_id: str) -> dict[str, Any]:
        session = self._session(session_id)
        projection = self.build_projection(session_id)
        context = self._result_context(session)
        base_rows = self._scope_event_rows(session)
        report = reconcile_reviewed_events(
            projection["events"],
            automatic_total=len(base_rows),
            context=context,
            expected_event_ids=[row["source_event_id"] for row in base_rows],
        )
        report_hash = sha256_json(
            {
                "projection_content_hash": projection["content_hash"],
                "review_revision": session["review_revision"],
                "report": report,
            }
        )
        existing = self.connection.execute(
            "SELECT * FROM review_reconciliation_runs WHERE review_session_id = ? AND reviewed_projection_revision_id = ? AND content_hash = ?",
            (session_id, projection["id"], report_hash),
        ).fetchone()
        if existing is None:
            self.connection.execute(
                """
                INSERT INTO review_reconciliation_runs(
                  id, review_session_id, reviewed_projection_revision_id,
                  review_revision, status, equation_json, by_line_json,
                  by_direction_json, by_class_json, by_interval_json,
                  diagnostics_json, content_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_id("reconcile"),
                    session_id,
                    projection["id"],
                    session["review_revision"],
                    report["status"],
                    canonical_json(report["equation"]),
                    canonical_json(report["by_line"]),
                    canonical_json(report["by_direction"]),
                    canonical_json(report["by_class"]),
                    canonical_json(report["by_interval"]),
                    canonical_json(report["diagnostics"]),
                    report_hash,
                    _now(),
                ),
            )
            self.connection.commit()
            existing = self.connection.execute(
                "SELECT * FROM review_reconciliation_runs WHERE content_hash = ?", (report_hash,)
            ).fetchone()
        result = dict(existing)
        result.update(report)
        result["id"] = existing["id"]
        result["reconciliation_revision_id"] = existing["id"]
        self._update_progress(session_id, report)
        return result

    # ---------- action persistence and queue ----------

    def _action_hash(self, payload: Mapping[str, Any]) -> str:
        return sha256_json({key: payload[key] for key in sorted(payload) if key not in {"created_at", "id"}})

    def _validate_action(self, session: sqlite3.Row, action: Mapping[str, Any], existing_actions: list[dict[str, Any]]) -> tuple[str, dict[str, Any], str | None]:
        action_type = normalize_action_type(str(action.get("action_type") or ""))
        target_event_id = str(action.get("target_event_id") or action.get("event_id") or "") or None
        payload = action_payload(action)
        if len(canonical_json(payload).encode("utf-8")) > MAX_REVIEW_PAYLOAD_BYTES:
            raise ReviewDomainError("PAYLOAD_TOO_LARGE", "Review action payload is too large.", field="payload")
        comment = str(action.get("comment") or action.get("reason") or payload.get("comment") or "")
        if len(comment) > MAX_REVIEW_COMMENT_LENGTH:
            raise ReviewDomainError("COMMENT_TOO_LARGE", "Review comment is too long.", field="comment")
        if action_type in MATERIAL_ACTION_TYPES and not str(action.get("reason_code") or payload.get("reason") or comment).strip():
            raise ReviewDomainError("REASON_REQUIRED", "Material review corrections require a reason.", field="reason_code")
        base_ids = {str(row["source_event_id"]) for row in self._scope_event_rows(session)}
        if action_type == "ADD_MISSED_EVENT":
            required = {"counting_line_id", "direction", "crossing_pts_ms", "engineering_class"}
            missing = sorted(key for key in required if payload.get(key) in (None, ""))
            if missing:
                raise ReviewDomainError("MISSED_EVENT_FIELDS_REQUIRED", f"Missing missed-event fields: {', '.join(missing)}", field="payload")
            if not payload.get("reason") and not payload.get("manual_observation_note"):
                raise ReviewDomainError("REASON_REQUIRED", "A manual observation reason or note is required.", field="payload")
            if not payload.get("evidence_reference") and not payload.get("manual_observation_note"):
                raise ReviewDomainError("EVIDENCE_REFERENCE_REQUIRED", "Manual additions require evidence or an observation note.", field="payload")
            self._validate_effective_values(session, payload, target_event_id=None, human_add=True)
            return action_type, payload, None
        if action_type == "REVERSE_ACTION":
            target_action_id = str(action.get("reverses_action_id") or payload.get("reverses_action_id") or "")
            validate_reversal_target(existing_actions, target_action_id, str(action.get("id") or "__new__"))
            target_action = next(item for item in existing_actions if str(item.get("id")) == target_action_id)
            if str(target_action.get("review_session_id")) != str(session["id"]):
                raise ReviewDomainError("CROSS_SESSION_REVERSAL", "An action can only be reversed inside its review session.", field="reverses_action_id")
            target_event_id = target_event_id or str(target_action.get("target_event_id") or target_action.get("event_id") or "") or None
            if target_event_id and target_event_id not in base_ids:
                raise ReviewDomainError("TARGET_OUTSIDE_SCOPE", "The target event is not inside this review session scope.", field="target_event_id")
            return action_type, payload, target_event_id
        if not target_event_id:
            raise ReviewDomainError("TARGET_REQUIRED", "This review action must target an automatic event.", field="target_event_id")
        if target_event_id not in base_ids:
            raise ReviewDomainError("TARGET_OUTSIDE_SCOPE", "The target event is not inside this review session scope.", field="target_event_id")
        self._validate_effective_values(session, payload, target_event_id=target_event_id, human_add=False)
        if action_type == "MARK_DUPLICATE":
            duplicate_of = str(payload.get("duplicate_of") or "")
            if not duplicate_of or duplicate_of == target_event_id:
                raise ReviewDomainError("INVALID_DUPLICATE_TARGET", "Duplicate target must be another valid event.", field="payload")
            if duplicate_of not in base_ids:
                raise ReviewDomainError("DUPLICATE_TARGET_OUTSIDE_SCOPE", "Duplicate target is outside the review scope.", field="payload")
            if self._would_duplicate_cycle(existing_actions, target_event_id, duplicate_of):
                raise ReviewDomainError("DUPLICATE_CYCLE", "Duplicate relationship would create a cycle.", field="payload")
        return action_type, payload, target_event_id

    def _validate_effective_values(self, session: sqlite3.Row, payload: Mapping[str, Any], *, target_event_id: str | None, human_add: bool) -> None:
        if payload.get("engineering_class") is not None:
            value = str(payload["engineering_class"])
            if value not in {item.value for item in EngineeringClass}:
                raise ReviewDomainError("UNSUPPORTED_CLASS", "Class is not a supported current engineering taxonomy value.", field="engineering_class")
        if payload.get("direction") is not None and str(payload["direction"]) not in VALID_CANONICAL_DIRECTIONS:
            raise ReviewDomainError("INVALID_DIRECTION", "Direction must be A_TO_B or B_TO_A.", field="direction")
        context = self._result_context(session)
        if payload.get("counting_line_id") is not None:
            line_id = str(payload["counting_line_id"])
            line_ids = {str(item.get("id")) for item in context["geometry"].get("counting_lines", []) if item.get("active", True)}
            if line_id not in line_ids:
                raise ReviewDomainError("INVALID_COUNTING_LINE", "Counting line is not present in the selected scene revision.", field="counting_line_id")
        if payload.get("crossing_pts_ms") is not None:
            pts = int(payload["crossing_pts_ms"])
            if pts < context["analysis_start_pts_ms"] or pts >= context["analysis_end_pts_ms"]:
                raise ReviewDomainError("TIMESTAMP_OUTSIDE_ANALYSIS", "Crossing PTS must be inside the half-open analysis interval.", field="crossing_pts_ms")
        if human_add and (payload.get("counting_line_id") is None or payload.get("direction") is None or payload.get("crossing_pts_ms") is None):
            raise ReviewDomainError("MISSED_EVENT_FIELDS_REQUIRED", "Manual additions require line, direction and crossing PTS.", field="payload")

    def _would_duplicate_cycle(self, actions: Iterable[Mapping[str, Any]], target: str, duplicate_of: str) -> bool:
        edges: dict[str, str] = {}
        for action in actions:
            if normalize_action_type(str(action.get("action_type"))) != "MARK_DUPLICATE":
                continue
            source = str(action.get("target_event_id") or action.get("event_id") or "")
            value = action_payload(action).get("duplicate_of")
            if source and value:
                edges[source] = str(value)
        edges[target] = duplicate_of
        cursor = target
        seen: set[str] = set()
        while cursor:
            if cursor in seen:
                return True
            seen.add(cursor)
            cursor = edges.get(cursor, "")
        return False

    def append_action(self, session_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        session = self._session(session_id)
        current_revision = int(session["review_revision"])
        expected = payload.get("expected_review_revision")
        client_request_id = str(payload.get("client_request_id") or "") or None
        if client_request_id:
            existing_idempotent = self.connection.execute(
                "SELECT * FROM review_actions WHERE review_session_id = ? AND client_request_id = ?",
                (session_id, client_request_id),
            ).fetchone()
            if existing_idempotent is not None:
                incoming_action_type = normalize_action_type(str(payload.get("action_type") or ""))
                incoming_payload = action_payload(payload)
                same_request = (
                    normalize_action_type(str(existing_idempotent["action_type"])) == incoming_action_type
                    and str(existing_idempotent["target_event_id"] or existing_idempotent["event_id"] or "") == str(payload.get("target_event_id") or payload.get("event_id") or "")
                    and canonical_json(_load_json(existing_idempotent["payload_json"], {})) == canonical_json(incoming_payload)
                    and str(existing_idempotent["reason_code"] or "") == str(payload.get("reason_code") or incoming_payload.get("reason") or "")
                    and str(existing_idempotent["comment"] or "") == str(payload.get("comment") or payload.get("reason") or "")
                    and str(existing_idempotent["reverses_action_id"] or "") == str(payload.get("reverses_action_id") or incoming_payload.get("reverses_action_id") or "")
                )
                if not same_request:
                    raise ReviewConflictError("IDEMPOTENCY_CONFLICT", "Client request ID was already used with a different action.", session_id=session_id, current_revision=current_revision)
                return self._public_action(existing_idempotent, idempotent=True)
        if expected is None:
            expected = current_revision
        if int(expected) != current_revision:
            since = [self._public_action(row) for row in self.connection.execute(
                "SELECT * FROM review_actions WHERE review_session_id = ? AND action_order > ? ORDER BY action_order, id",
                (session_id, int(expected)),
            ).fetchall()]
            self.connection.execute(
                "UPDATE review_sessions SET conflict_count = conflict_count + 1 WHERE id = ?", (session_id,)
            )
            self.connection.commit()
            raise ReviewConflictError(
                "REVIEW_REVISION_CONFLICT",
                "Review changed since the client last refreshed. Refresh and retry the action.",
                session_id=session_id,
                expected_revision=int(expected),
                current_revision=current_revision,
                actions_since=since,
            )
        context = self._result_context(session)
        if self._stale_reason(session, context):
            raise ReviewStaleError("REVIEW_STALE", "This review scope is stale and must be reopened from the current result.", session_id=session_id)
        existing_actions = self._actions(session_id)
        action_type, normalized_payload, target_event_id = self._validate_action(session, payload, existing_actions)
        action_id = _new_id("action")
        action_order = current_revision + 1
        action_data = {
            "review_session_id": session_id,
            "target_event_id": target_event_id,
            "target_review_event_id": payload.get("target_review_event_id"),
            "action_type": action_type,
            "payload": normalized_payload,
            "reason_code": str(payload.get("reason_code") or normalized_payload.get("reason") or ""),
            "comment": str(payload.get("comment") or payload.get("reason") or ""),
            "reviewer_id": str(payload.get("reviewer_id") or payload.get("reviewer") or "operator"),
            "expected_review_revision": int(expected),
            "action_order": action_order,
            "client_request_id": client_request_id,
            "reverses_action_id": payload.get("reverses_action_id") or normalized_payload.get("reverses_action_id"),
            "source_event_revision": str(payload.get("source_event_revision") or session["engineering_result_revision_id"]),
        }
        content_hash = self._action_hash(action_data)
        self.connection.execute(
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
            (
                action_id,
                action_id,
                session_id,
                target_event_id,
                "REVIEW_EVENT" if action_type == "ADD_MISSED_EVENT" else "AUTOMATIC_EVENT",
                target_event_id,
                payload.get("target_review_event_id"),
                action_type,
                canonical_json(normalized_payload),
                action_data["reason_code"],
                action_data["comment"],
                action_data["reviewer_id"],
                action_data["reviewer_id"],
                normalized_payload.get("engineering_class"),
                normalized_payload.get("direction"),
                action_data["reason_code"],
                _now(),
                int(expected),
                action_order,
                client_request_id,
                action_data["reverses_action_id"],
                action_data["source_event_revision"],
                content_hash,
            ),
        )
        new_status = "IN_REVIEW"
        self.connection.execute(
            "UPDATE review_sessions SET review_revision = ?, review_status = ?, completed_by = NULL, completed_at = NULL WHERE id = ?",
            (action_order, new_status, session_id),
        )
        self._state_event(session_id, str(session["review_status"]), new_status, action_order, action_type, action_data["reviewer_id"])
        self.connection.commit()
        row = self.connection.execute("SELECT * FROM review_actions WHERE id = ?", (action_id,)).fetchone()
        return self._public_action(row)

    def _public_action(self, row: sqlite3.Row | Mapping[str, Any], *, idempotent: bool = False) -> dict[str, Any]:
        payload = dict(row)
        payload["action_type"] = normalize_action_type(str(payload["action_type"]))
        payload["payload"] = _load_json(payload.pop("payload_json", "{}"), {})
        payload.pop("event_id", None)
        payload["idempotent_replay"] = idempotent
        return payload

    def list_actions(self, session_id: str, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM review_actions WHERE review_session_id = ? ORDER BY action_order, created_at, id LIMIT ? OFFSET ?",
            (session_id, min(limit, 500), max(offset, 0)),
        ).fetchall()
        return [self._public_action(row) for row in rows]

    def _stale_reason(self, session: sqlite3.Row, context: Mapping[str, Any]) -> str | None:
        if str(session["stale_status"]) == "STALE":
            return "session_marked_stale"
        checks = (
            (str(session["source_fingerprint_sha256"]), str(context["source_fingerprint_sha256"]), "source_fingerprint_changed"),
            (str(session["scene_revision"]), str(context["scene_revision"]), "scene_revision_changed"),
            (str(session["scene_semantic_hash"]), str(context["scene_semantic_hash"]), "scene_semantics_changed"),
            (str(session["taxonomy_revision"]), str(context["taxonomy_revision"]), "taxonomy_revision_changed"),
            (str(session["mapping_revision"]), str(context["mapping_revision"]), "mapping_revision_changed"),
            (str(session["classification_policy_revision"]), str(context["classification_policy_revision"]), "classification_policy_changed"),
        )
        for expected, actual, reason in checks:
            if expected != actual:
                return reason
        result = context["result"]
        if bool(result["stale"]) or str(result["result_status"]) != "STRUCTURALLY_VALID" or not bool(result["engineering_ready"]):
            return "engineering_result_not_current"
        latest_result = self.connection.execute(
            "SELECT id FROM engineering_result_revisions WHERE run_id = ? ORDER BY generated_at DESC, id DESC LIMIT 1",
            (session["processing_run_id"],),
        ).fetchone()
        if latest_result is not None and str(latest_result["id"]) != str(session["engineering_result_revision_id"]):
            return "newer_engineering_result_exists"
        return None

    def _update_progress(self, session_id: str, report: Mapping[str, Any]) -> None:
        self.connection.execute(
            """
            UPDATE review_sessions SET
              scope_event_count = ?, reviewed_event_count = ?, confirmed_count = ?,
              corrected_count = ?, rejected_count = ?, duplicate_count = ?,
              human_added_count = ?, unscorable_count = ?, unreviewed_count = ?,
              unresolved_diagnostics_count = ?, review_coverage_percent = ?,
              review_status = CASE WHEN review_status = 'NOT_STARTED' AND ? > 0 THEN 'IN_REVIEW' ELSE review_status END
            WHERE id = ?
            """,
            (
                int(report.get("scope_total", 0)),
                int(report.get("reviewed_event_count", 0)),
                int(report.get("confirmed_count", 0)),
                int(report.get("corrected_count", 0)),
                int(report.get("rejected_count", 0)),
                int(report.get("duplicate_count", 0)),
                int(report.get("human_added_count", 0)),
                int(report.get("unscorable_count", 0)),
                int(report.get("unreviewed_count", 0)),
                len(report.get("diagnostics", [])),
                float(report.get("review_coverage_percent", 0.0)),
                int(report.get("reviewed_event_count", 0)),
                session_id,
            ),
        )
        self.connection.commit()

    def get_progress(self, session_id: str) -> dict[str, Any]:
        reconciliation = self.reconcile(session_id)
        current = dict(self._session(session_id))
        current["review_scope_filter"] = _load_json(current.pop("review_scope_filter_json"), {})
        current["review_coverage_percent"] = float(current["review_coverage_percent"])
        current["stale"] = bool(self._stale_reason(self._session(session_id), self._result_context(self._session(session_id))))
        current["reconciliation"] = reconciliation
        return current

    def queue(
        self,
        session_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        filters: Mapping[str, Any] | None = None,
        order_by: str = "timestamp",
    ) -> dict[str, Any]:
        session = self._session(session_id)
        projection = self._ensure_projection_row(session_id)
        filters = filters or {}
        allowed_order = {
            "timestamp": "effective_crossing_pts_ms ASC, effective_line_id ASC, reviewed_event_id ASC",
            "line": "effective_line_id ASC, effective_crossing_pts_ms ASC, reviewed_event_id ASC",
            "direction": "effective_direction ASC, effective_crossing_pts_ms ASC, reviewed_event_id ASC",
            "event_id": "reviewed_event_id ASC",
            "review_status": "review_status ASC, effective_crossing_pts_ms ASC, reviewed_event_id ASC",
        }
        where = ["reviewed_projection_revision_id = ?"]
        params: list[Any] = [projection["id"]]

        def add_values(column: str, value: Any) -> None:
            values = [str(item) for item in value] if isinstance(value, list) else [str(value)]
            if not values:
                where.append("0 = 1")
                return
            placeholders = ", ".join("?" for _ in values)
            where.append(f"{column} IN ({placeholders})")
            params.extend(values)

        if filters.get("review_status") is not None:
            add_values("review_status", filters["review_status"])
        mappings = {
            "line": "effective_line_id",
            "direction": "effective_direction",
            "class": "effective_class",
            "origin": "origin",
            "classification_status": "classification_status",
        }
        for filter_key, event_key in mappings.items():
            if filters.get(filter_key) is not None:
                add_values(event_key, filters[filter_key])
        if filters.get("corrected") is not None:
            where.append("has_correction = ?")
            params.append(1 if bool(filters["corrected"]) else 0)
        if filters.get("duplicate") is not None:
            where.append("is_duplicate = ?")
            params.append(1 if bool(filters["duplicate"]) else 0)
        if filters.get("automatic_or_human"):
            origin = str(filters["automatic_or_human"]).upper()
            if origin not in {"AUTOMATIC", "HUMAN_ADDED"}:
                where.append("0 = 1")
            else:
                where.append("origin = ?")
                params.append(origin)
        if filters.get("time_interval"):
            interval = filters["time_interval"]
            start, end = int(interval[0]), int(interval[1])
            where.append("effective_crossing_pts_ms >= ? AND effective_crossing_pts_ms < ?")
            params.extend([start, end])
        if filters.get("confidence_min") is not None:
            where.append("event_confidence IS NOT NULL AND event_confidence >= ?")
            params.append(float(filters["confidence_min"]))
        if filters.get("confidence_max") is not None:
            where.append("event_confidence IS NOT NULL AND event_confidence <= ?")
            params.append(float(filters["confidence_max"]))
        if filters.get("benchmark_error_category"):
            where.append("benchmark_error_category = ?")
            params.append(str(filters["benchmark_error_category"]))
        order_expression = allowed_order.get(order_by, allowed_order["timestamp"])
        bounded_limit = max(1, min(int(limit), 200))
        bounded_offset = max(0, int(offset))
        predicate = " AND ".join(where)
        total_row = self.connection.execute(
            f"SELECT COUNT(*) AS total FROM reviewed_events WHERE {predicate}", params
        ).fetchone()
        rows = self.connection.execute(
            f"SELECT * FROM reviewed_events WHERE {predicate} ORDER BY {order_expression} LIMIT ? OFFSET ?",
            [*params, bounded_limit, bounded_offset],
        ).fetchall()
        return {
            "review_session_id": session_id,
            "reviewed_projection_revision_id": projection["id"],
            "review_revision": int(session["review_revision"]),
            "projection_status": projection["status"],
            "items": [self._public_reviewed_event(row) for row in rows],
            "total": int(total_row["total"] if total_row else 0),
            "limit": bounded_limit,
            "offset": bounded_offset,
            "order_by": order_by if order_by in allowed_order else "timestamp",
        }

    def event_evidence(self, session_id: str, event_id: str) -> dict[str, Any]:
        session = self._session(session_id)
        projection = self._ensure_projection_row(session_id)
        event_row = self.connection.execute(
            "SELECT * FROM reviewed_events WHERE reviewed_projection_revision_id = ? AND reviewed_event_id = ? LIMIT 1",
            (projection["id"], event_id),
        ).fetchone()
        if event_row is None:
            event_row = self.connection.execute(
                "SELECT * FROM reviewed_events WHERE reviewed_projection_revision_id = ? AND source_event_id = ? LIMIT 1",
                (projection["id"], event_id),
            ).fetchone()
        if event_row is None:
            raise ValueError("review event not found")
        event = self._public_reviewed_event(event_row)
        base = self.connection.execute(
            "SELECT * FROM engineering_event_projections WHERE source_event_id = ? AND engineering_result_revision_id = ? LIMIT 1",
            (event.get("source_event_id"), session["engineering_result_revision_id"]),
        ).fetchone() if event.get("source_event_id") else None
        actions = self.connection.execute(
            "SELECT * FROM review_actions WHERE review_session_id = ? AND target_event_id = ? ORDER BY action_order, created_at, id LIMIT 200",
            (session_id, event.get("source_event_id")),
        ).fetchall() if event.get("source_event_id") else []
        candidates = self.connection.execute(
            """
            SELECT source_event_id, event_pts_ms, counting_line_id, canonical_direction
            FROM engineering_event_projections
            WHERE engineering_result_revision_id = ?
              AND source_event_id <> ?
              AND ABS(event_pts_ms - ?) <= 5000
            ORDER BY ABS(event_pts_ms - ?), source_event_id LIMIT 20
            """,
            (session["engineering_result_revision_id"], event.get("source_event_id") or "", event["effective_crossing_pts_ms"], event["effective_crossing_pts_ms"]),
        ).fetchall() if event.get("source_event_id") else []
        return {
            "review_session_id": session_id,
            "reviewed_event": event,
            "automatic_engineering_event": self._public_engineering_event(base) if base else None,
            "source_video": {"project_id": session["project_id"], "seek_pts_ms": event["effective_crossing_pts_ms"]},
            "active_review_actions": [self._public_action(row) for row in actions],
            "nearby_events": [dict(row) for row in candidates],
            "duplicate_candidates": [dict(row) for row in candidates if row["counting_line_id"] == event["effective_line_id"]],
            "benchmark_diagnostic": None,
            "disclosures": ["Source video is served through the project-scoped media route; private paths are not exposed."],
        }

    def complete_review(self, session_id: str, *, completed_by: str, expected_review_revision: int | None = None) -> dict[str, Any]:
        session = self._session(session_id)
        current = int(session["review_revision"])
        if expected_review_revision is not None and int(expected_review_revision) != current:
            raise ReviewConflictError(
                "REVIEW_REVISION_CONFLICT",
                "Review changed before completion. Refresh and retry.",
                session_id=session_id,
                expected_revision=int(expected_review_revision),
                current_revision=current,
                actions_since=self.list_actions(session_id),
            )
        context = self._result_context(session)
        if self._stale_reason(session, context):
            self.connection.execute("UPDATE review_sessions SET review_status = 'STALE', stale_status = 'STALE' WHERE id = ?", (session_id,))
            self.connection.commit()
            raise ReviewStaleError("REVIEW_STALE", "Review cannot be completed because its source or result is stale.", session_id=session_id)
        reconciliation = self.reconcile(session_id)
        if not reconciliation["engineering_ready"]:
            self.connection.execute("UPDATE review_sessions SET review_status = 'BLOCKED' WHERE id = ?", (session_id,))
            self.connection.commit()
            raise ReviewDomainError("RECONCILIATION_FAILED", "Reviewed reconciliation failed; resolve diagnostics before completion.")
        if int(reconciliation["unreviewed_count"]) > 0:
            self.connection.execute("UPDATE review_sessions SET review_status = 'BLOCKED' WHERE id = ?", (session_id,))
            self.connection.commit()
            raise ReviewDomainError("REVIEW_INCOMPLETE", "Every event in the explicit review scope must be addressed.")
        # conflict_count is an audit metric, not an unresolved blocker. A
        # stale client can retry successfully after receiving actions_since_expected;
        # requiring a zero historical conflict count would make the session
        # impossible to complete after a legitimate recovery.
        self.connection.execute(
            "UPDATE review_sessions SET review_status = 'REVIEW_COMPLETE', completed_by = ?, completed_at = ?, stale_status = 'CURRENT' WHERE id = ?",
            (completed_by, _now(), session_id),
        )
        self._state_event(session_id, str(session["review_status"]), "REVIEW_COMPLETE", current, "review_completed", completed_by)
        self.connection.commit()
        result = self.get_session(session_id)
        result["reconciliation"] = reconciliation
        result["reviewed_projection_revision_id"] = self._latest_projection(session_id, current)["id"]
        return result

    def supersede(self, session_id: str, *, superseded_by: str, actor_id: str) -> dict[str, Any]:
        session = self._session(session_id)
        self.connection.execute(
            "UPDATE review_sessions SET review_status = 'SUPERSEDED', superseded_by = ? WHERE id = ?",
            (superseded_by, session_id),
        )
        self._state_event(session_id, str(session["review_status"]), "SUPERSEDED", int(session["review_revision"]), "session_superseded", actor_id)
        self.connection.commit()
        return self.get_session(session_id)

    def mark_project_stale(self, project_id: str, reason: str = "upstream_changed") -> None:
        rows = self.connection.execute(
            "SELECT id, review_status, review_revision FROM review_sessions WHERE project_id = ? AND stale_status = 'CURRENT' AND review_status <> 'SUPERSEDED'",
            (project_id,),
        ).fetchall()
        self.connection.execute(
            "UPDATE review_sessions SET stale_status = 'STALE', review_status = CASE WHEN review_status = 'REVIEW_COMPLETE' THEN 'STALE' ELSE review_status END WHERE project_id = ? AND stale_status = 'CURRENT' AND review_status <> 'SUPERSEDED'",
            (project_id,),
        )
        for row in rows:
            self._state_event(str(row["id"]), str(row["review_status"]), "STALE", int(row["review_revision"]), reason, "system")
        self.connection.commit()

    # ---------- session/certification ----------

    def _session_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["review_scope_filter"] = _load_json(payload.pop("review_scope_filter_json"), {})
        payload["stale"] = bool(payload.get("stale_status") == "STALE")
        payload["progress"] = {
            key: payload[key]
            for key in (
                "scope_event_count", "reviewed_event_count", "confirmed_count", "corrected_count",
                "rejected_count", "duplicate_count", "human_added_count", "unscorable_count",
                "unreviewed_count", "unresolved_diagnostics_count", "conflict_count", "review_coverage_percent",
            )
        }
        return payload

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self._session_payload(self._session(session_id))

    def list_sessions(self, project_id: str, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM review_sessions WHERE project_id = ? ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (project_id, min(limit, 100), max(offset, 0)),
        ).fetchall()
        return [self._session_payload(row) for row in rows]

    def _certification_status(self, row: sqlite3.Row) -> str:
        revocation = self.connection.execute(
            "SELECT 1 FROM certification_revocations WHERE certification_revision_id = ? LIMIT 1", (row["id"],)
        ).fetchone()
        if revocation:
            return "REVOKED"
        session = self._session(str(row["review_session_id"]))
        try:
            context = self._result_context(session)
            stale = self._stale_reason(session, context)
        except ReviewStaleError:
            stale = "context_missing"
        latest_projection = self._latest_projection(str(session["id"]))
        if (
            stale
            or int(session["review_revision"]) != int(row["review_revision"])
            or latest_projection is None
            or int(latest_projection["review_revision"]) != int(row["review_revision"])
        ):
            return "STALE"
        return str(row["status"])

    def _certification_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["status"] = self._certification_status(row)
        payload["certification_content_hash_status"] = str(payload.get("certification_content_hash_status") or "LEGACY_UNRESOLVED")
        payload["review_scope"] = _load_json(payload.pop("review_scope_json"), {})
        payload["benchmark_reference"] = _load_json(payload.pop("benchmark_reference_json"), {})
        payload["review_summary"] = _load_json(payload.pop("review_summary_json"), {})
        payload["revocation"] = (
            dict(self.connection.execute("SELECT * FROM certification_revocations WHERE certification_revision_id = ? ORDER BY revoked_at DESC LIMIT 1", (row["id"],)).fetchone())
            if self.connection.execute("SELECT 1 FROM certification_revocations WHERE certification_revision_id = ? LIMIT 1", (row["id"],)).fetchone()
            else None
        )
        return payload

    def create_certification(self, session_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        session = self._session(session_id)
        if str(session["review_status"]) != "REVIEW_COMPLETE":
            raise ReviewDomainError("REVIEW_NOT_COMPLETE", "Certification requires a completed review session.")
        context = self._result_context(session)
        stale = self._stale_reason(session, context)
        if stale:
            self.connection.execute("UPDATE review_sessions SET stale_status = 'STALE', review_status = 'STALE' WHERE id = ?", (session_id,))
            self.connection.commit()
            raise ReviewStaleError("CERTIFICATION_STALE", "Certification requires current source, scene and result provenance.", session_id=session_id)
        run = context["run"]
        if str(run["processing_mode"] or "").upper() == "REAL_VIDEO":
            runtime_hash = str(run["runtime_configuration_hash"] or "")
            actual_runtime_hash = str(run["actual_runtime_configuration_hash"] or "")
            provenance_status = str(run["provenance_status"] or "LEGACY_UNRESOLVED")
            if (
                provenance_status != "VERIFIED_RUNTIME_PROVENANCE"
                or not runtime_hash
                or not actual_runtime_hash
                or runtime_hash != actual_runtime_hash
            ):
                raise ReviewDomainError(
                    "RUNTIME_PROVENANCE_UNVERIFIED",
                    "Certification requires verified configured and actual REAL_VIDEO runtime provenance.",
                )
        projection = self._latest_projection(session_id, int(session["review_revision"])) or self.build_projection(session_id)
        reconciliation = self.connection.execute(
            "SELECT * FROM review_reconciliation_runs WHERE reviewed_projection_revision_id = ? ORDER BY created_at DESC LIMIT 1",
            (projection["id"],),
        ).fetchone()
        if reconciliation is None:
            reconciliation = self.reconcile(session_id)
            reconciliation_id = reconciliation["id"]
        else:
            reconciliation_id = reconciliation["id"]
        if str(reconciliation["status"]) != "PASSED":
            raise ReviewDomainError("RECONCILIATION_FAILED", "Certification is blocked by failed reviewed reconciliation.")
        certified_by = str(payload.get("certified_by") or "")
        if not certified_by.strip():
            raise ReviewDomainError("REVIEWER_REQUIRED", "A reviewer identity is required for certification.", field="certified_by")
        review_scope = {
            "type": session["review_scope_type"],
            "filter": _load_json(session["review_scope_filter_json"], {}),
            "partial": session["review_scope_type"] != "FULL_RESULT",
        }
        qualification = str(payload.get("qualification_disclosure") or "Benchmark diagnostics disclose model evidence; human review does not prove detector accuracy.")
        if review_scope["partial"] and "partial" not in qualification.lower():
            qualification = f"Partial certification scope. {qualification}"
        rights = str(payload.get("rights_disclosure") or "Rights status is operator-supplied and is not inferred by this application.")
        summary = {
            "status": reconciliation["status"],
            "equation": _load_json(reconciliation["equation_json"], {}),
            "by_line": _load_json(reconciliation["by_line_json"], {}),
            "by_direction": _load_json(reconciliation["by_direction_json"], {}),
            "by_class": _load_json(reconciliation["by_class_json"], {}),
            "by_interval": _load_json(reconciliation["by_interval_json"], {}),
            "diagnostics": _load_json(reconciliation["diagnostics_json"], []),
        }
        certification_id = _new_id("cert6e")
        content_hash_payload = {
            "reviewed_projection_content_hash": projection["content_hash"],
            "review_revision": session["review_revision"],
            "reconciliation_content_hash": reconciliation["content_hash"],
            "review_scope": review_scope,
            "source_fingerprint": context["source_fingerprint_sha256"],
            "runtime_configuration_hash": context["runtime_configuration_hash"],
            "source_id": session["source_id"],
            "processing_run_id": session["processing_run_id"],
            "processing_configuration_revision_id": session["processing_configuration_revision_id"],
            "scene_revision": context["scene_revision"],
            "taxonomy_revision": context["taxonomy_revision"],
            "mapping_revision": context["mapping_revision"],
            "classification_policy_revision": context["classification_policy_revision"],
            "benchmark_reference": payload.get("benchmark_reference") or {},
            "qualification_disclosure": qualification,
            "rights_disclosure": rights,
            "review_summary": summary,
        }
        # certification_content_hash is the reproducible identity of the
        # frozen result.  The certification revision ID is not content. The
        # legacy certification_hash remains an attestation hash and may carry
        # reviewer/time semantics without changing content identity.
        certification_content_hash_value = certification_content_hash(content_hash_payload)
        certified_at = _now()
        certification_hash = sha256_json(
            {
                "certification_content_hash": certification_content_hash_value,
                "certified_by": certified_by,
                "certified_at": certified_at,
            }
        )
        self.connection.execute(
            """
            INSERT INTO certification_revisions(
              id, certification_revision_id, project_id, review_session_id,
              reviewed_projection_revision_id, review_scope_json, review_revision,
              reconciliation_revision_id, source_id, source_fingerprint_sha256,
              processing_run_id, processing_configuration_revision_id,
              runtime_configuration_hash, scene_revision, taxonomy_revision,
              mapping_revision, classification_policy_revision,
              benchmark_reference_json, qualification_disclosure, rights_disclosure,
              review_summary_json, certified_by, certified_at, certification_hash,
              certification_content_hash, certification_content_hash_status, status, stale_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'VERIFIED_CONTENT_HASH', 'CERTIFIED', 'CURRENT')
            """,
            (
                certification_id,
                certification_id,
                session["project_id"],
                session_id,
                projection["id"],
                canonical_json(review_scope),
                session["review_revision"],
                reconciliation_id,
                session["source_id"],
                context["source_fingerprint_sha256"],
                session["processing_run_id"],
                session["processing_configuration_revision_id"],
                context["runtime_configuration_hash"],
                context["scene_revision"],
                context["taxonomy_revision"],
                context["mapping_revision"],
                context["classification_policy_revision"],
                canonical_json(payload.get("benchmark_reference") or {}),
                qualification,
                rights,
                canonical_json(summary),
                certified_by,
                certified_at,
                certification_hash,
                certification_content_hash_value,
            ),
        )
        self.connection.commit()
        return self._certification_payload(self.connection.execute("SELECT * FROM certification_revisions WHERE id = ?", (certification_id,)).fetchone())

    def get_certification(self, certification_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM certification_revisions WHERE id = ? OR certification_revision_id = ?",
            (certification_id, certification_id),
        ).fetchone()
        if row is None:
            raise ValueError("certification revision not found")
        return self._certification_payload(row)

    def list_certifications(self, project_id: str, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM certification_revisions WHERE project_id = ? ORDER BY certified_at DESC, id DESC LIMIT ? OFFSET ?",
            (project_id, min(limit, 100), max(offset, 0)),
        ).fetchall()
        return [self._certification_payload(row) for row in rows]

    def revoke_certification(self, certification_id: str, *, reason: str, revoked_by: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM certification_revisions WHERE id = ?", (certification_id,)).fetchone()
        if row is None:
            raise ValueError("certification revision not found")
        if not reason.strip() or not revoked_by.strip():
            raise ReviewDomainError("REVOCATION_FIELDS_REQUIRED", "Revocation reason and reviewer are required.")
        existing = self.connection.execute(
            "SELECT * FROM certification_revocations WHERE certification_revision_id = ? ORDER BY revoked_at DESC LIMIT 1", (certification_id,)
        ).fetchone()
        if existing is None:
            self.connection.execute(
                "INSERT INTO certification_revocations(id, revocation_id, certification_revision_id, reason, revoked_by, revoked_at) VALUES (?, ?, ?, ?, ?, ?)",
                (_new_id("revoke"), _new_id("revocation"), certification_id, reason[:1000], revoked_by[:120], _now()),
            )
            self.connection.commit()
        return self._certification_payload(row)

    def mark_certifications_stale(self, project_id: str) -> None:
        # Certification rows are immutable; status is derived from the current
        # session/projection context. This method exists for explicit callers
        # and keeps the state transition observable through session staleness.
        self.mark_project_stale(project_id, "calculation_input_changed")

    # ---------- deterministic production exports ----------

    def _export_events(self, certification: sqlite3.Row) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        projection = self.get_projection(str(certification["reviewed_projection_revision_id"]))
        session = self._session(str(certification["review_session_id"]))
        context = self._result_context(session)
        project = self.connection.execute("SELECT * FROM projects WHERE id = ?", (session["project_id"],)).fetchone()
        actions = self._all_actions_for_export(str(session["id"]), int(certification["review_revision"]))
        reconciliation = self.connection.execute(
            "SELECT * FROM review_reconciliation_runs WHERE id = ?", (certification["reconciliation_revision_id"],)
        ).fetchone()
        if reconciliation is None:
            raise ReviewStaleError("RECONCILIATION_NOT_FOUND", "Certification reconciliation is unavailable.", session_id=session["id"])
        report = {
            "id": reconciliation["id"],
            "status": reconciliation["status"],
            "equation": _load_json(reconciliation["equation_json"], {}),
            "by_line": _load_json(reconciliation["by_line_json"], {}),
            "by_direction": _load_json(reconciliation["by_direction_json"], {}),
            "by_class": _load_json(reconciliation["by_class_json"], {}),
            "by_interval": _load_json(reconciliation["by_interval_json"], {}),
            "diagnostics": _load_json(reconciliation["diagnostics_json"], []),
        }
        identity = release_identity()
        provenance = {
            "application_release_version": identity["release_version"],
            "git_commit_sha": identity["git_commit_sha"],
            "project_id": certification["project_id"],
            "project_name": project["name"] if project is not None else certification["project_id"],
            "project_location": project["location"] if project is not None else "",
            "source_fingerprint_sha256": certification["source_fingerprint_sha256"],
            "source_id": certification["source_id"],
            "source_file_name": context["source"]["file_name"],
            "recording_time_status": context["source"]["recording_time_status"],
            "source_started_at": context["source"]["source_started_at"],
            "timezone_name": context["source"]["timezone_name"],
            "analysis_start_pts_ms": int(context["source"]["analysis_start_pts_ms"]),
            "analysis_end_pts_ms": int(context["source"]["analysis_end_pts_ms"]),
            "interval_origin_pts_ms": int(context["source"]["interval_origin_pts_ms"]),
            "processing_run_id": certification["processing_run_id"],
            "processing_configuration_revision_id": certification["processing_configuration_revision_id"],
            "runtime_configuration_hash": certification["runtime_configuration_hash"],
            "actual_runtime_configuration_hash": context["run"]["actual_runtime_configuration_hash"],
            "runtime_provenance_status": context["run"]["provenance_status"],
            "scene_revision": certification["scene_revision"],
            "taxonomy_revision": certification["taxonomy_revision"],
            "mapping_revision": certification["mapping_revision"],
            "classification_policy_revision": certification["classification_policy_revision"],
            "review_session_id": certification["review_session_id"],
            "review_revision": certification["review_revision"],
            "reviewed_projection_revision_id": certification["reviewed_projection_revision_id"],
            "certification_revision_id": certification["certification_revision_id"],
            "benchmark_disclosure": certification["qualification_disclosure"],
            "rights_disclosure": certification["rights_disclosure"],
        }
        run = dict(context["run"])
        runtime_provenance = _load_json(run.get("runtime_provenance_json"), {})
        provenance["runtime_provenance"] = runtime_provenance
        processing_provenance = _load_json(run.get("provenance_json"), {})
        for key in ("model_revision", "weight_sha256", "tracker_revision", "crossing_policy_revision"):
            provenance[key] = run.get(key) or runtime_provenance.get(key) or processing_provenance.get(key)
        return projection, actions, report, provenance

    def _all_actions_for_export(self, session_id: str, review_revision: int, *, page_size: int = 500) -> list[dict[str, Any]]:
        """Read the complete immutable action history for a frozen revision.

        The public action endpoint remains capped at 500 rows. Production
        exports use this internal keyset-paginated path so the certification
        revision cutoff is authoritative without silently truncating audit
        history.
        """

        actions: list[dict[str, Any]] = []
        last_key: tuple[int, str, str] | None = None
        while True:
            where = ["review_session_id = ?", "action_order <= ?"]
            params: list[Any] = [session_id, int(review_revision)]
            if last_key is not None:
                last_order, last_created_at, last_id = last_key
                where.append(
                    "(action_order > ? OR (action_order = ? AND created_at > ?) "
                    "OR (action_order = ? AND created_at = ? AND id > ?))"
                )
                params.extend([last_order, last_order, last_created_at, last_order, last_created_at, last_id])
            rows = self.connection.execute(
                f"SELECT * FROM review_actions WHERE {' AND '.join(where)} "
                "ORDER BY action_order, created_at, id LIMIT ?",
                [*params, page_size],
            ).fetchall()
            if not rows:
                break
            actions.extend(self._public_action(row) for row in rows)
            last = rows[-1]
            last_key = (int(last["action_order"]), str(last["created_at"]), str(last["id"]))
            if len(rows) < page_size:
                break
        return actions

    def _current_certification_or_raise(self, certification_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM certification_revisions WHERE id = ? OR certification_revision_id = ?",
            (certification_id, certification_id),
        ).fetchone()
        if row is None:
            raise ValueError("certification revision not found")
        status = self._certification_status(row)
        if status != "CERTIFIED":
            raise ReviewStaleError("CERTIFICATION_NOT_CURRENT", f"Certification is {status} and cannot produce a current export.")
        session = self._session(str(row["review_session_id"]))
        projection = self._latest_projection(str(session["id"]))
        if projection is None or str(projection["id"]) != str(row["reviewed_projection_revision_id"]) or int(projection["review_revision"]) != int(row["review_revision"]):
            raise ReviewStaleError("CERTIFICATION_NOT_CURRENT", "Certification does not reference the current reviewed projection.", session_id=session["id"])
        return row

    def request_export(self, certification_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        certification = self._current_certification_or_raise(certification_id)
        export_format = str(payload.get("format") or "").upper()
        if export_format not in EXPORT_FORMATS:
            raise ReviewDomainError("INVALID_EXPORT_FORMAT", "Export format must be CSV, XLSX or JSON.", field="format")
        client_request_id = str(payload.get("client_request_id") or "") or None
        request_payload = {
            "certification_revision_id": certification["certification_revision_id"],
            "format": export_format,
            "language": str(payload.get("language") or "th"),
            "options": payload.get("options") or {},
        }
        request_hash = sha256_json(request_payload)
        if client_request_id:
            existing = self.connection.execute(
                "SELECT * FROM export_revisions WHERE certification_revision_id = ? AND format = ? AND client_request_id = ?",
                (certification["id"], export_format, client_request_id),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise ReviewConflictError("IDEMPOTENCY_CONFLICT", "Export request ID was already used with different parameters.", session_id=str(certification["review_session_id"]))
                return self.get_export(str(existing["id"]))
        export_id = _new_id("export")
        artifact_id = _new_id("artifact")
        extension = {"CSV": "csv", "XLSX": "xlsx", "JSON": "json"}[export_format]
        safe_filename = f"traffic-review-{_safe_component(str(certification['project_id']))}-{_safe_component(export_id)}.{extension}"
        relative_path = "/".join(
            (
                _safe_component(str(certification["project_id"])),
                _safe_component(str(certification["certification_revision_id"])),
                _safe_component(export_id),
                safe_filename,
            )
        )
        projection: dict[str, Any] | None = None
        row_count: int | None = None
        sheet_count: int | None = None
        try:
            projection, actions, reconciliation, provenance = self._export_events(certification)
            events = projection["events"]
            if export_format == "CSV":
                data, row_count, sheet_count = self._csv_bytes(certification, events, actions, reconciliation, provenance)
            elif export_format == "XLSX":
                data, row_count, sheet_count = self._xlsx_bytes(certification, events, actions, reconciliation, provenance)
            else:
                data, row_count, sheet_count = self._audit_json_bytes(certification, events, actions, reconciliation, provenance)
            self.artifacts.write_atomic(relative_path, data)
            checksum = hashlib.sha256(data).hexdigest()
            schema_revision = EXPORT_SCHEMA_REVISIONS[export_format]
            manifest = {
                "schema_revision": schema_revision,
                "certification_revision_id": certification["certification_revision_id"],
                "reviewed_projection_revision_id": projection["id"],
                "export_revision_id": export_id,
                "format": export_format,
                "language": str(payload.get("language") or "th"),
                "options": payload.get("options") or {},
                "artifact_id": artifact_id,
                "safe_filename": safe_filename,
                "content_length": len(data),
                "sha256": checksum,
                "content_hash": checksum,
                "row_count": row_count,
                "sheet_count": sheet_count,
                "artifact_generation_status": "COMPLETED",
                "application_release_version": provenance.get("application_release_version"),
                "git_commit_sha": provenance.get("git_commit_sha"),
            }
            self.connection.execute(
                """
                INSERT INTO export_revisions(
                  id, export_revision_id, certification_revision_id,
                  format, status, client_request_id, request_hash,
                  manifest_json, created_by, created_at, completed_at,
                  reviewed_projection_revision_id, export_schema_revision,
                  language, options_json, artifact_manifest_json, content_hash,
                  artifact_generation_status, artifact_sha256, row_count, sheet_count
                ) VALUES (?, ?, ?, ?, 'COMPLETED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'COMPLETED', ?, ?, ?)
                """,
                (
                    export_id,
                    export_id,
                    certification["id"],
                    export_format,
                    client_request_id,
                    request_hash,
                    canonical_json(manifest),
                    str(payload.get("created_by") or "operator"),
                    _now(),
                    _now(),
                    projection["id"],
                    schema_revision,
                    str(payload.get("language") or "th"),
                    canonical_json(payload.get("options") or {}),
                    canonical_json(manifest),
                    checksum,
                    checksum,
                    row_count,
                    sheet_count,
                ),
            )
            self.connection.execute(
                "INSERT INTO export_artifacts(id, artifact_id, export_revision_id, safe_filename, relative_path, mime_type, content_length, sha256, row_count, sheet_count, created_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'COMPLETED')",
                (_new_id("artifactrow"), artifact_id, export_id, safe_filename, relative_path, self._mime_for_format(export_format), len(data), checksum, row_count, sheet_count, _now()),
            )
            self.connection.commit()
            result = self.get_export(export_id)
            result["artifact_path"] = None
            return result
        except Exception as exc:
            self.connection.rollback()
            try:
                self.artifacts.delete_relative(relative_path)
            except Exception:
                pass
            # Failed requests remain auditable but never look completed.
            try:
                projection_id = projection["id"] if projection is not None else certification["reviewed_projection_revision_id"]
                failed_manifest = {"status": "FAILED", "error_code": type(exc).__name__}
                self.connection.execute(
                    """
                    INSERT INTO export_revisions(
                      id, export_revision_id, certification_revision_id,
                      format, status, client_request_id, request_hash,
                      manifest_json, created_by, created_at, error_code,
                      reviewed_projection_revision_id, export_schema_revision,
                      language, options_json, artifact_manifest_json,
                      content_hash, artifact_generation_status, artifact_sha256,
                      row_count, sheet_count
                    ) VALUES (?, ?, ?, ?, 'FAILED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'FAILED', NULL, ?, ?)
                    """,
                    (
                        export_id,
                        export_id,
                        certification["id"],
                        export_format,
                        client_request_id,
                        request_hash,
                        canonical_json(failed_manifest),
                        str(payload.get("created_by") or "operator"),
                        _now(),
                        type(exc).__name__,
                        projection_id,
                        EXPORT_SCHEMA_REVISIONS[export_format],
                        str(payload.get("language") or "th"),
                        canonical_json(payload.get("options") or {}),
                        canonical_json(failed_manifest),
                        "",
                        row_count,
                        sheet_count,
                    ),
                )
                self.connection.commit()
            except Exception:
                self.connection.rollback()
            raise

    def get_export(self, export_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM export_revisions WHERE id = ? OR export_revision_id = ?", (export_id, export_id)
        ).fetchone()
        if row is None:
            raise ValueError("export revision not found")
        payload = dict(row)
        payload["manifest"] = _load_json(payload.pop("manifest_json"), {})
        payload["artifact_manifest"] = _load_json(payload.pop("artifact_manifest_json", "{}"), payload["manifest"])
        payload["options"] = _load_json(payload.pop("options_json", "{}"), {})
        payload["artifact_generation_status"] = str(payload.get("artifact_generation_status") or payload.get("status") or "FAILED")
        certification = self.connection.execute(
            "SELECT * FROM certification_revisions WHERE id = ?", (row["certification_revision_id"],)
        ).fetchone()
        source_status = "STALE"
        if certification is not None:
            certification_status = self._certification_status(certification)
            source_status = {
                "CERTIFIED": "CURRENT",
                "REVOKED": "REVOKED",
            }.get(certification_status, "STALE")
            payload["certification_content_hash_status"] = str(certification["certification_content_hash_status"] or "LEGACY_UNRESOLVED")
        payload["source_certification_status"] = source_status
        if payload["artifact_generation_status"] != "COMPLETED":
            payload["effective_export_status"] = payload["artifact_generation_status"]
        else:
            payload["effective_export_status"] = {
                "CURRENT": "COMPLETED",
                "STALE": "STALE_SOURCE",
                "REVOKED": "REVOKED_SOURCE",
            }[source_status]
        artifact = self.connection.execute("SELECT * FROM export_artifacts WHERE export_revision_id = ?", (row["id"],)).fetchone()
        artifact_payload = dict(artifact) if artifact else None
        if artifact_payload is not None:
            artifact_payload.pop("relative_path", None)
        payload["artifact"] = artifact_payload
        payload.pop("client_request_id", None)
        payload.pop("request_hash", None)
        return payload

    def list_exports(self, certification_id: str, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        row = self.connection.execute(
            "SELECT id FROM certification_revisions WHERE id = ? OR certification_revision_id = ?", (certification_id, certification_id)
        ).fetchone()
        if row is None:
            raise ValueError("certification revision not found")
        rows = self.connection.execute(
            "SELECT * FROM export_revisions WHERE certification_revision_id = ? ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (row["id"], min(limit, 100), max(offset, 0)),
        ).fetchall()
        return [self.get_export(str(item["id"])) for item in rows]

    def artifact_path(self, artifact_id: str) -> tuple[Path, dict[str, Any]]:
        row = self.connection.execute("SELECT * FROM export_artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
        if row is None:
            raise ValueError("artifact not found")
        return self.artifacts.resolve_download(str(row["relative_path"])), dict(row)

    @staticmethod
    def _mime_for_format(export_format: str) -> str:
        return {
            "CSV": "text/csv; charset=utf-8",
            "XLSX": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "JSON": "application/json; charset=utf-8",
        }[export_format]

    def _export_event_record(self, event: Mapping[str, Any], provenance: Mapping[str, Any], certification: sqlite3.Row) -> dict[str, Any]:
        pts = int(event.get("effective_crossing_pts_ms") or 0)
        analysis_start = int(provenance.get("analysis_start_pts_ms") or 0)
        analysis_end = int(provenance.get("analysis_end_pts_ms") or 0)
        origin = int(provenance.get("interval_origin_pts_ms") or analysis_start)
        interval_index = (pts - origin) // (15 * 60 * 1000) if analysis_start <= pts < analysis_end else None
        raw_interval_start = origin + (interval_index or 0) * 15 * 60 * 1000 if interval_index is not None else None
        raw_interval_end = raw_interval_start + 15 * 60 * 1000 if raw_interval_start is not None else None
        interval_start = max(raw_interval_start, analysis_start) if raw_interval_start is not None else None
        interval_end = min(raw_interval_end, analysis_end) if raw_interval_end is not None else None
        partial_interval = bool(
            raw_interval_start is not None
            and raw_interval_end is not None
            and (raw_interval_start < analysis_start or raw_interval_end > analysis_end)
        )
        interval_label = (
            f"source-relative [{interval_start}, {interval_end}) ms"
            if interval_start is not None and interval_end is not None
            else "outside-analysis-interval"
        )
        evidence = event.get("evidence") if isinstance(event.get("evidence"), Mapping) else {}
        absolute_event_time = evidence.get("absolute_event_time")
        if provenance.get("recording_time_status") == "CONFIRMED" and provenance.get("source_started_at"):
            try:
                source_started_at = datetime.fromisoformat(str(provenance["source_started_at"]).replace("Z", "+00:00"))
                absolute_event_time = (source_started_at + timedelta(milliseconds=pts)).isoformat()
            except ValueError:
                absolute_event_time = None
        side_a = evidence.get("side_a_name") or evidence.get("side_a_label") or "Side A"
        side_b = evidence.get("side_b_name") or evidence.get("side_b_label") or "Side B"
        return {
            "application_release_version": provenance.get("application_release_version"),
            "git_commit_sha": provenance.get("git_commit_sha"),
            "project_id": provenance.get("project_id"),
            "source_id": provenance.get("source_id"),
            "source_fingerprint": provenance.get("source_fingerprint_sha256"),
            "recording_time_status": provenance.get("recording_time_status") or "UNCONFIGURED",
            "counting_line_id": event.get("effective_line_id"),
            "counting_line_label": event.get("effective_line_name") or event.get("effective_line_id"),
            "side_a_label": side_a,
            "side_b_label": side_b,
            "direction": event.get("effective_direction"),
            "crossing_pts_ms": pts,
            "absolute_event_time": absolute_event_time,
            "interval_start_pts_ms": interval_start,
            "interval_end_pts_ms": interval_end,
            "interval_label": interval_label,
            "partial_interval": "PARTIAL" if partial_interval else "COMPLETE",
            "engineering_class": event.get("effective_class"),
            "classification_status": event.get("classification_status"),
            "origin": event.get("origin"),
            "review_status": event.get("review_status"),
            "corrected": bool(event.get("corrected_fields")),
            "duplicate_of": event.get("duplicate_of"),
            "source_event_id": event.get("source_event_id"),
            "reviewed_event_id": event.get("reviewed_event_id"),
            "human_add_action_id": event.get("human_add_action_id"),
            "corrected_fields": ",".join(str(value) for value in event.get("corrected_fields", [])),
            "effective_action_ids": ",".join(str(value) for value in event.get("effective_action_ids", [])),
            "certification_revision_id": certification["certification_revision_id"],
            "runtime_configuration_hash": provenance.get("runtime_configuration_hash"),
            "actual_runtime_configuration_hash": provenance.get("actual_runtime_configuration_hash"),
            "runtime_provenance_status": provenance.get("runtime_provenance_status"),
            "interval_index": interval_index,
        }

    def _event_rows(self, certification: sqlite3.Row, events: Iterable[Mapping[str, Any]], provenance: Mapping[str, Any]) -> list[list[Any]]:
        headers = [
            "project_id", "source_id", "source_fingerprint", "recording_time_status",
            "counting_line_id", "counting_line_label", "side_a_label", "side_b_label", "direction",
            "crossing_pts_ms", "absolute_event_time", "interval_start_pts_ms", "interval_end_pts_ms",
            "interval_label", "partial_interval", "engineering_class", "classification_status", "origin",
            "review_status", "corrected", "duplicate_of", "source_event_id", "reviewed_event_id",
            "certification_revision_id", "runtime_configuration_hash", "actual_runtime_configuration_hash",
            "runtime_provenance_status", "application_release_version", "git_commit_sha",
        ]
        rows: list[list[Any]] = [headers]
        records = [self._export_event_record(event, provenance, certification) for event in events]
        records.sort(key=lambda row: (int(row["crossing_pts_ms"] or 0), str(row["counting_line_id"] or ""), str(row["reviewed_event_id"] or "")))
        for record in records:
            rows.append([record[header] for header in headers])
        return rows

    def _csv_bytes(self, certification: sqlite3.Row, events: list[dict[str, Any]], actions: list[dict[str, Any]], reconciliation: Mapping[str, Any], provenance: Mapping[str, Any]) -> tuple[bytes, int, int]:
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        rows = self._event_rows(certification, events, provenance)
        for row in rows:
            writer.writerow([value if isinstance(value, (int, float)) else safe_spreadsheet_text(value) for value in row])
        return output.getvalue().encode("utf-8"), max(len(rows) - 1, 0), 1

    def _audit_json_bytes(self, certification: sqlite3.Row, events: list[dict[str, Any]], actions: list[dict[str, Any]], reconciliation: Mapping[str, Any], provenance: Mapping[str, Any]) -> tuple[bytes, int, int]:
        runtime_provenance = dict(provenance.get("runtime_provenance") or {})
        runtime_provenance.update(
            {
                "runtime_configuration_hash": provenance.get("runtime_configuration_hash"),
                "actual_runtime_configuration_hash": provenance.get("actual_runtime_configuration_hash"),
                "status": provenance.get("runtime_provenance_status"),
            }
        )
        audit = {
            "schema_revision": "audit-export-v2",
            "certification_revision": self._certification_payload(certification),
            "review_scope": _load_json(certification["review_scope_json"], {}),
            "reviewed_projection": {
                "id": certification["reviewed_projection_revision_id"],
                "review_revision": certification["review_revision"],
                "events": events,
            },
            "effective_reviewed_events": events,
            "review_action_revision_cutoff": int(certification["review_revision"]),
            "review_action_references": [
                {
                    "id": action.get("id"),
                    "action_order": int(action.get("action_order") or 0),
                    "action_type": action.get("action_type"),
                    "target_event_id": action.get("target_event_id"),
                    "reverses_action_id": action.get("reverses_action_id"),
                    "payload": action.get("payload") or {},
                    "reason_code": action.get("reason_code"),
                    "comment": action.get("comment"),
                    "content_hash": action.get("content_hash"),
                    "reviewer_id": action.get("reviewer_id"),
                    "created_at": action.get("created_at"),
                }
                for action in actions
            ],
            "reconciliation": reconciliation,
            "source_provenance": provenance,
            "runtime_provenance": runtime_provenance,
            "benchmark_disclosure": certification["qualification_disclosure"],
            "rights_disclosure": certification["rights_disclosure"],
            "artifact_hashes": {"content_sha256": "computed-after-serialization"},
        }
        return canonical_json(audit).encode("utf-8"), len(events), 1

    def _xlsx_bytes(self, certification: sqlite3.Row, events: list[dict[str, Any]], actions: list[dict[str, Any]], reconciliation: Mapping[str, Any], provenance: Mapping[str, Any]) -> tuple[bytes, int, int]:
        sheets = self._workbook_sheets(certification, events, actions, reconciliation, provenance)
        data = _build_xlsx(sheets)
        return data, len(events), len(sheets)

    def _workbook_sheets(self, certification: sqlite3.Row, events: list[dict[str, Any]], actions: list[dict[str, Any]], reconciliation: Mapping[str, Any], provenance: Mapping[str, Any]) -> dict[str, list[list[Any]]]:
        event_records = [self._export_event_record(event, provenance, certification) for event in events]
        event_records.sort(key=lambda row: (int(row["crossing_pts_ms"] or 0), str(row["counting_line_id"] or ""), str(row["reviewed_event_id"] or "")))
        event_rows = self._event_rows(certification, events, provenance)
        active_statuses = {"UNREVIEWED", "CONFIRMED", "CORRECTED"}
        count_map: dict[tuple[Any, ...], int] = {}
        for record in event_records:
            if record["review_status"] not in active_statuses:
                continue
            key = (
                record["counting_line_label"], record["direction"], record["engineering_class"],
                record["interval_start_pts_ms"], record["interval_end_pts_ms"],
                record["interval_label"], record["partial_interval"],
            )
            count_map[key] = count_map.get(key, 0) + 1
        counts: list[list[Any]] = [[
            "counting line", "direction", "engineering class", "interval start PTS", "interval end PTS",
            "15-minute interval", "partial interval", "count",
        ]]
        for key, count in sorted(count_map.items(), key=lambda item: (int(item[0][3] or 0), str(item[0][0]), str(item[0][1]), str(item[0][2]))):
            counts.append([*key, count])
        summary = [
            ["field", "value"],
            ["project", provenance.get("project_name")],
            ["project location", provenance.get("project_location")],
            ["source id", provenance.get("source_id")],
            ["source fingerprint", provenance.get("source_fingerprint_sha256")],
            ["recording time status", provenance.get("recording_time_status")],
            ["review scope", canonical_json(_load_json(certification["review_scope_json"], {}))],
            ["review revision", certification["review_revision"]],
            ["review action revision cutoff", certification["review_revision"]],
            ["certification revision", certification["certification_revision_id"]],
            ["final active reviewed events", reconciliation.get("equation", {}).get("final_active_reviewed_events")],
            ["automatic source events", reconciliation.get("equation", {}).get("automatic_events")],
            ["active human-added events", reconciliation.get("equation", {}).get("active_human_added_events")],
            ["false positives", reconciliation.get("equation", {}).get("rejected_false_positives")],
            ["duplicates", reconciliation.get("equation", {}).get("duplicate_suppressed_events")],
            ["unscorable", reconciliation.get("equation", {}).get("unscorable_excluded_events")],
            ["reconciliation status", reconciliation.get("status")],
            ["qualification disclosure", certification["qualification_disclosure"]],
            ["rights disclosure", certification["rights_disclosure"]],
        ]
        adjustment_rows: list[list[Any]] = [["action id", "action order", "action time", "reviewer", "action type", "target", "old effective value", "new value", "reason", "comment", "reversal relationship"]]
        for action in sorted(actions, key=lambda row: (int(row.get("action_order") or 0), str(row.get("created_at")), str(row.get("id")))):
            payload = action.get("payload") or {}
            adjustment_rows.append([
                action.get("id"), action.get("action_order"), action.get("created_at"), action.get("reviewer_id"), action.get("action_type"), action.get("target_event_id") or action.get("human_add_action_id"),
                "", canonical_json(payload), action.get("reason_code"), action.get("comment"), action.get("reverses_action_id") or "",
            ])
        equation = reconciliation.get("equation", {})
        qc_rows = [["metric", "value"], *[[key, value] for key, value in sorted(equation.items())], ["status", reconciliation.get("status")], ["diagnostics", canonical_json(reconciliation.get("diagnostics", []))]]
        for dimension, values in (("by_line", reconciliation.get("by_line", {})), ("by_direction", reconciliation.get("by_direction", {})), ("by_class", reconciliation.get("by_class", {})), ("by_interval", reconciliation.get("by_interval", {}))):
            qc_rows.append([dimension, canonical_json(values)])
        methodology = [
            ["methodology", "value"],
            ["event semantics", "Crossing events, not unique vehicles across multiple independent lines."],
            ["time authority", "Source-relative media PTS; intervals are half-open [start, end)."],
            ["direction", "A_TO_B and B_TO_A derive from stable Side A/Side B transitions."],
            ["review overlay", "Automatic evidence is immutable; append-only actions create a deterministic reviewed projection."],
            ["human additions", "HUMAN_ADDED events are disclosed separately and never enter the automatic ledger."],
            ["classification", "Unknown and ambiguous classes remain explicit; human review does not claim detector accuracy."],
        ]
        provenance_rows = [
            ["field", "value"],
            *[
                [key, canonical_json(value) if isinstance(value, (dict, list)) else value]
                for key, value in sorted(provenance.items())
            ],
            ["certification_hash", certification["certification_hash"]],
            ["certification_content_hash", certification["certification_content_hash"]],
            ["certification_content_hash_status", certification["certification_content_hash_status"]],
        ]
        return {
            "Summary": summary,
            "15-min Counts": counts,
            "Event Ledger": event_rows,
            "Review Adjustments": adjustment_rows,
            "QC and Reconciliation": qc_rows,
            "Methodology": methodology,
            "Provenance": provenance_rows,
        }


class _MappingRow(dict[str, Any]):
    """Small mapping with sqlite.Row's string-key access for context helpers."""

    def __getitem__(self, key: str) -> Any:
        return dict.__getitem__(self, key)


def _column_name(index: int) -> str:
    value = index + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _xlsx_cell(reference: str, value: Any) -> str:
    if value is None:
        return f'<c r="{reference}"/>'
    if isinstance(value, bool):
        return f'<c r="{reference}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{reference}"><v>{value}</v></c>'
    text = xml_escape(safe_spreadsheet_text(value))
    return f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _worksheet_xml(rows: list[list[Any]]) -> str:
    max_columns = max((len(row) for row in rows), default=1)
    max_rows = max(len(rows), 1)
    last_ref = f"{_column_name(max_columns - 1)}{max_rows}"
    body: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        cells = "".join(_xlsx_cell(f"{_column_name(column_index)}{row_index}", value) for column_index, value in enumerate(row))
        body.append(f'<row r="{row_index}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f'<sheetData>{"".join(body)}</sheetData><autoFilter ref="A1:{last_ref}"/>'
        '</worksheet>'
    )


def _zip_xml(zf: zipfile.ZipFile, name: str, content: str) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    zf.writestr(info, content.encode("utf-8"))


def _build_xlsx(sheets: Mapping[str, list[list[Any]]]) -> bytes:
    sheet_items = list(sheets.items())
    workbook_sheets = "".join(
        f'<sheet name="{xml_escape(name[:31])}" sheetId="{index}" r:id="rId{index + 1}"/>'
        for index, (name, _) in enumerate(sheet_items, start=1)
    )
    workbook_rels = "".join(
        f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, len(sheet_items) + 1)
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + "".join(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for index in range(1, len(sheet_items) + 1)
        )
        + '</Types>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<fileVersion appName="Traffic Video Analytics"/><workbookPr date1904="0"/>'
        f'<sheets>{workbook_sheets}</sheets></workbook>'
    )
    workbook_relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{workbook_rels}</Relationships>'
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        _zip_xml(zf, "[Content_Types].xml", content_types)
        _zip_xml(zf, "_rels/.rels", root_rels)
        _zip_xml(zf, "xl/workbook.xml", workbook)
        _zip_xml(zf, "xl/_rels/workbook.xml.rels", workbook_relationships)
        for index, (_, rows) in enumerate(sheet_items, start=1):
            _zip_xml(zf, f"xl/worksheets/sheet{index}.xml", _worksheet_xml(rows))
    return output.getvalue()
