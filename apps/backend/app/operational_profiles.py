from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .processing_profiles import (
    PARAMETER_SCHEMA_REVISION,
    builtin_profiles,
    build_configuration,
    content_hash,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _load(value: Any, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default


class PreviewConflict(ValueError):
    def __init__(self, code: str, detail: str, *, preview_id: str | None = None) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.preview_id = preview_id


class PreviewIdempotencyConflict(PreviewConflict):
    def __init__(self, preview_id: str) -> None:
        super().__init__(
            "IDEMPOTENCY_CONFLICT",
            "The idempotency key is already bound to a different preview request.",
            preview_id=preview_id,
        )


class OperationalProfileStore:
    """Append-only persistence for 6D configuration and preview contracts."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def ensure_builtin_profiles(self) -> None:
        for profile in builtin_profiles():
            profile_hash = content_hash(profile)
            existing = self.connection.execute(
                "SELECT id FROM processing_profile_revisions WHERE content_hash = ?",
                (profile_hash,),
            ).fetchone()
            if existing is not None:
                continue
            self.connection.execute(
                """
                INSERT INTO processing_profile_revisions(
                  id, profile_code, profile_revision, display_name_en, display_name_th,
                  description_en, description_th, intended_use, resolved_parameters_json,
                  supported_domains_json, hardware_expectation, known_tradeoffs_json,
                  created_by, created_at, content_hash, supersedes_revision, status, schema_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_id("profile"),
                    profile["profile_code"],
                    profile["profile_revision"],
                    profile["display_name_en"],
                    profile["display_name_th"],
                    profile["description_en"],
                    profile["description_th"],
                    profile["intended_use"],
                    _json(profile["resolved_parameters"]),
                    _json(profile["supported_domains"]),
                    profile["hardware_expectation"],
                    _json(profile["known_tradeoffs"]),
                    "system",
                    _now(),
                    profile_hash,
                    None,
                    profile["status"],
                    profile["schema_revision"],
                ),
            )
        self.connection.commit()

    def list_profiles(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM processing_profile_revisions ORDER BY profile_code, profile_revision"
        ).fetchall()
        return [self.public_profile(row) for row in rows]

    def get_profile(self, profile_code: str, profile_revision: str | None = None) -> dict[str, Any]:
        code = str(profile_code).upper()
        row = self.connection.execute(
            """
            SELECT * FROM processing_profile_revisions
            WHERE profile_code = ? AND (? IS NULL OR profile_revision = ?)
            ORDER BY profile_revision DESC LIMIT 1
            """,
            (code, profile_revision, profile_revision),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown_processing_profile:{code}")
        return self.public_profile(row)

    @staticmethod
    def public_profile(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["resolved_parameters"] = _load(payload.get("resolved_parameters_json"), {})
        payload["supported_domains"] = _load(payload.get("supported_domains_json"), [])
        payload["known_tradeoffs"] = _load(payload.get("known_tradeoffs_json"), [])
        for field in ("resolved_parameters_json", "supported_domains_json", "known_tradeoffs_json"):
            payload.pop(field, None)
        return payload

    def parameter_schema(self) -> dict[str, Any]:
        from .processing_profiles import EXPERT_PARAMETER_SPECS

        return {
            "revision": PARAMETER_SCHEMA_REVISION,
            "parameters": [item.public() for item in EXPERT_PARAMETER_SPECS],
        }

    def resolve(
        self,
        profile_code: str,
        guided_settings: Mapping[str, Any] | None,
        expert_overrides: Mapping[str, Any] | None,
        *,
        media_duration_ms: int | None = None,
        analysis_start_pts_ms: int | None = None,
        analysis_end_pts_ms: int | None = None,
        cuda_available: bool | None = None,
    ) -> dict[str, Any]:
        result = build_configuration(
            profile_code,
            guided_settings,
            expert_overrides,
            media_duration_ms=media_duration_ms,
            analysis_start_pts_ms=analysis_start_pts_ms,
            analysis_end_pts_ms=analysis_end_pts_ms,
            cuda_available=cuda_available,
        )
        result["profile_id"] = self.get_profile(profile_code, str(result["profile_revision"]))["id"]
        return result

    def create_configuration_revision(
        self,
        *,
        project_id: str | None,
        profile_code: str,
        guided_settings: Mapping[str, Any] | None,
        expert_overrides: Mapping[str, Any] | None,
        created_by: str,
        media_duration_ms: int | None = None,
        analysis_start_pts_ms: int | None = None,
        analysis_end_pts_ms: int | None = None,
        cuda_available: bool | None = None,
        validation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved = dict(
            validation
            or self.resolve(
                profile_code,
                guided_settings,
                expert_overrides,
                media_duration_ms=media_duration_ms,
                analysis_start_pts_ms=analysis_start_pts_ms,
                analysis_end_pts_ms=analysis_end_pts_ms,
                cuda_available=cuda_available,
            )
        )
        if not resolved.get("valid"):
            raise ValueError(json.dumps(resolved.get("errors", []), sort_keys=True))
        profile = self.get_profile(profile_code, str(resolved["profile_revision"]))
        revision_id = _new_id("cfg")
        created_at = _now()
        content = {
            "id": revision_id,
            "project_id": project_id,
            "profile_id": profile["id"],
            "profile_revision": profile["profile_revision"],
            "requested_guided_settings": resolved.get("requested_guided_settings", {}),
            "guided_settings": resolved["guided_settings"],
            "requested_expert_overrides": resolved["requested_expert_overrides"],
            "resolved_parameters": resolved["resolved_parameters"],
            "parameter_schema_revision": resolved["parameter_schema_revision"],
            "crossing_policy_revision": resolved["crossing_policy_revision"],
            "classification_policy_revision": resolved["classification_policy_revision"],
            "device_request": resolved.get("device_request", "AUTO"),
            "runtime_configuration_hash": resolved.get("runtime_configuration_hash"),
            "request_provenance_hash": resolved["request_provenance_hash"],
            "validation_result": resolved,
            "created_by": created_by,
            "created_at": created_at,
        }
        revision_hash = content_hash(content)
        self.connection.execute(
            """
            INSERT INTO processing_configuration_revisions(
              id, project_id, profile_id, profile_revision, guided_settings_json,
              requested_expert_overrides_json, resolved_parameters_json,
              parameter_schema_revision, model_revision, weight_sha256, tracker_revision,
              crossing_policy_revision, classification_policy_revision, device_request,
              created_by, created_at, content_hash, validation_result_json, status,
              runtime_configuration_hash, request_provenance_hash,
              requested_guided_settings_json, provenance_status, runtime_provenance_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                revision_id,
                project_id,
                profile["id"],
                profile["profile_revision"],
                _json(resolved["guided_settings"]),
                _json(resolved["requested_expert_overrides"]),
                _json(resolved["resolved_parameters"]),
                resolved["parameter_schema_revision"],
                resolved.get("model_revision"),
                resolved.get("weight_sha256"),
                resolved.get("tracker_revision"),
                resolved["crossing_policy_revision"],
                resolved["classification_policy_revision"],
                resolved.get("device_request", "AUTO"),
                created_by,
                created_at,
                revision_hash,
                _json(resolved),
                "VALIDATED",
                resolved.get("runtime_configuration_hash"),
                resolved["request_provenance_hash"],
                _json(resolved.get("requested_guided_settings", {})),
                resolved.get("runtime_provenance_status", "UNRESOLVED_OPTIONAL_WEIGHT_SHA"),
                _json(resolved.get("runtime_provenance", {})),
            ),
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM processing_configuration_revisions WHERE id = ?",
            (revision_id,),
        ).fetchone()
        assert row is not None
        return self.public_configuration(row)

    def get_configuration(self, revision_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM processing_configuration_revisions WHERE id = ?",
            (revision_id,),
        ).fetchone()
        if row is None:
            raise ValueError("processing configuration revision not found")
        return self.public_configuration(row)

    def public_configuration(self, row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["guided_settings"] = _load(payload.pop("guided_settings_json", None), {})
        requested_guided = _load(payload.pop("requested_guided_settings_json", None), None)
        payload["requested_guided_settings"] = requested_guided if requested_guided is not None else dict(payload["guided_settings"])
        payload["requested_expert_overrides"] = _load(payload.pop("requested_expert_overrides_json", None), {})
        payload["resolved_parameters"] = _load(payload.pop("resolved_parameters_json", None), {})
        payload["runtime_provenance"] = _load(payload.pop("runtime_provenance_json", None), {})
        validation = _load(payload.pop("validation_result_json", None), {})
        profile = self.connection.execute(
            "SELECT profile_code FROM processing_profile_revisions WHERE id = ?",
            (payload.get("profile_id"),),
        ).fetchone()
        payload["profile_code"] = profile["profile_code"] if profile else payload.get("profile_id")
        payload.update(
            {
                "valid": bool(validation.get("valid", True)),
                "errors": validation.get("errors", []),
                "warnings": validation.get("warnings", []),
                "normalizations": validation.get("normalizations", []),
                "estimated_resource_impact": validation.get("estimated_resource_impact", {}),
                "configuration_hash": payload.get("runtime_configuration_hash") or validation.get("configuration_hash", ""),
                "runtime_configuration_hash": payload.get("runtime_configuration_hash") or validation.get("runtime_configuration_hash"),
                "request_provenance_hash": payload.get("request_provenance_hash") or validation.get("request_provenance_hash"),
                "resolved_device": validation.get("resolved_device", "AUTO"),
                "adapter_support": validation.get("adapter_support", {}),
                "configuration_schema_revision": validation.get("configuration_schema_revision", "processing-configuration-v1"),
                "runtime_provenance_status": payload.get("provenance_status") or validation.get("runtime_provenance_status", "LEGACY_UNRESOLVED"),
            }
        )
        return payload

    def create_preview(
        self,
        *,
        project_id: str,
        source_id: str,
        source_fingerprint: str,
        scene_version_id: str,
        scene_revision: str,
        scene_semantic_hash: str,
        start_pts_ms: int,
        end_pts_ms: int,
        configuration_revision_id: str,
        runtime_configuration_hash: str | None,
        request_fingerprint: str,
        mode: str,
        idempotency_key: str | None,
        created_by: str,
        provenance_status: str = "CONFIGURED",
    ) -> dict[str, Any]:
        if idempotency_key:
            existing = self.connection.execute(
                "SELECT * FROM preview_runs WHERE project_id = ? AND idempotency_key = ?",
                (project_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if str(existing["request_fingerprint"] or "") != request_fingerprint:
                    raise PreviewIdempotencyConflict(str(existing["id"]))
                return self.public_preview(existing)
        preview_id = _new_id("preview")
        created_at = _now()
        try:
            self.connection.execute(
                """
                INSERT INTO preview_runs(
                  id, project_id, source_id, source_fingerprint_sha256, scene_version_id,
                  scene_revision, scene_semantic_hash, start_pts_ms, end_pts_ms,
                  processing_configuration_revision_id, run_type, mode, status,
                  idempotency_key, created_by, created_at, runtime_configuration_hash,
                   request_fingerprint, provenance_status
                 ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PREVIEW_ONLY', ?, 'QUEUED', ?, ?, ?, ?, ?, ?)
                """,
                (
                    preview_id,
                    project_id,
                    source_id,
                    source_fingerprint,
                    scene_version_id,
                    scene_revision,
                    scene_semantic_hash,
                    start_pts_ms,
                    end_pts_ms,
                    configuration_revision_id,
                    mode,
                    idempotency_key,
                    created_by,
                    created_at,
                    runtime_configuration_hash,
                    request_fingerprint,
                    provenance_status,
                ),
            )
        except sqlite3.IntegrityError:
            self.connection.rollback()
            if idempotency_key:
                existing = self.connection.execute(
                    "SELECT * FROM preview_runs WHERE project_id = ? AND idempotency_key = ?",
                    (project_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    if str(existing["request_fingerprint"] or "") != request_fingerprint:
                        raise PreviewIdempotencyConflict(str(existing["id"]))
                    return self.public_preview(existing)
            raise
        self._record_preview_state_event(preview_id, "QUEUED", None, None, {"attempt_number": 0})
        self.connection.commit()
        existing = self.connection.execute("SELECT * FROM preview_runs WHERE id = ?", (preview_id,)).fetchone()
        assert existing is not None
        return self.public_preview(existing)

    def _record_preview_state_event(
        self,
        preview_id: str,
        event_type: str,
        worker_id: str | None,
        attempt_number: int | None,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO preview_run_state_events(
              id, preview_run_id, event_type, worker_id, attempt_number, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (_new_id("preview-state"), preview_id, event_type, worker_id, attempt_number, _json(detail or {}), _now()),
        )

    @staticmethod
    def _lease_expired(value: Any) -> bool:
        if not value:
            return True
        try:
            return datetime.fromisoformat(str(value)) <= datetime.now(timezone.utc)
        except (TypeError, ValueError):
            return True

    def _preview_row_or_raise(self, preview_id: str) -> sqlite3.Row:
        row = self.connection.execute("SELECT * FROM preview_runs WHERE id = ?", (preview_id,)).fetchone()
        if row is None:
            raise ValueError("preview run not found")
        return row

    def _assert_preview_owner(self, row: sqlite3.Row, worker_id: str, claim_token: str) -> None:
        if row["status"] != "RUNNING":
            raise PreviewConflict("PREVIEW_STATE_CONFLICT", "Preview is no longer running.", preview_id=str(row["id"]))
        if row["worker_id"] != worker_id or row["claim_token"] != claim_token:
            raise PreviewConflict("PREVIEW_OWNERSHIP_LOST", "Preview ownership no longer belongs to this worker.", preview_id=str(row["id"]))
        if self._lease_expired(row["lease_expires_at"]):
            raise PreviewConflict("PREVIEW_LEASE_EXPIRED", "Preview lease expired before the worker renewed it.", preview_id=str(row["id"]))

    def heartbeat_preview(self, preview_id: str, worker_id: str, claim_token: str, lease_seconds: int = 120) -> dict[str, Any]:
        self.connection.commit()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._preview_row_or_raise(preview_id)
            self._assert_preview_owner(row, worker_id, claim_token)
            heartbeat_at = _now()
            lease_expires_at = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
            updated = self.connection.execute(
                """
                UPDATE preview_runs
                SET heartbeat_at = ?, lease_expires_at = ?
                WHERE id = ? AND status = 'RUNNING' AND worker_id = ? AND claim_token = ?
                """,
                (heartbeat_at, lease_expires_at, preview_id, worker_id, claim_token),
            )
            if updated.rowcount != 1:
                raise PreviewConflict("PREVIEW_OWNERSHIP_LOST", "Preview ownership changed during heartbeat.", preview_id=preview_id)
            self._record_preview_state_event(preview_id, "HEARTBEAT", worker_id, int(row["attempt_number"] or 0), {"lease_expires_at": lease_expires_at})
            self.connection.commit()
            return self.get_preview(preview_id)
        except Exception:
            self.connection.rollback()
            raise

    def check_preview_control(self, preview_id: str, worker_id: str, claim_token: str) -> bool:
        """Return cancellation state or fail closed when ownership is lost."""
        row = self._preview_row_or_raise(preview_id)
        if row["status"] == "CANCELLED":
            return True
        self._assert_preview_owner(row, worker_id, claim_token)
        return False

    def cancel_preview(self, preview_id: str, reason: str | None = None) -> dict[str, Any]:
        self.connection.commit()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._preview_row_or_raise(preview_id)
            if row["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                self.connection.commit()
                return self.get_preview(preview_id)
            cancelled_at = _now()
            self.connection.execute(
                """
                UPDATE preview_runs
                SET status = 'CANCELLED', completed_at = ?, cancellation_requested_at = ?,
                    cancellation_reason = ?, worker_id = NULL, claim_token = NULL,
                    lease_expires_at = NULL, heartbeat_at = ?
                WHERE id = ? AND status IN ('QUEUED', 'RUNNING')
                """,
                (cancelled_at, cancelled_at, reason or "Cancelled by operator.", cancelled_at, preview_id),
            )
            self.connection.execute(
                """
                INSERT INTO preview_run_statistics(id, preview_run_id, statistics_json, warnings_json, evidence_json, created_at)
                VALUES (?, ?, ?, ?, '[]', ?)
                ON CONFLICT(preview_run_id) DO NOTHING
                """,
                (
                    _new_id("preview-stat"),
                    preview_id,
                    _json({"error_code": "CANCELLED", "run_type": "PREVIEW_ONLY", "cancellation_reason": reason or "Cancelled by operator."}),
                    _json([{"code": "preview_cancelled", "detail": reason or "Cancelled by operator."}]),
                    cancelled_at,
                ),
            )
            self._record_preview_state_event(preview_id, "CANCELLED", None, int(row["attempt_number"] or 0), {"reason": reason or "Cancelled by operator."})
            self.connection.commit()
            return self.get_preview(preview_id)
        except Exception:
            self.connection.rollback()
            raise

    def set_preview_result(
        self,
        preview_id: str,
        *,
        status: str,
        statistics: Mapping[str, Any],
        warnings: list[Mapping[str, Any]] | None = None,
        evidence: list[Mapping[str, Any]] | None = None,
        events: list[Mapping[str, Any]] | None = None,
        worker_id: str | None = None,
        claim_token: str | None = None,
        allow_unclaimed: bool = False,
    ) -> dict[str, Any]:
        if status not in {"COMPLETED", "FAILED", "CANCELLED"}:
            raise ValueError("preview result must be terminal")
        self.connection.commit()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._preview_row_or_raise(preview_id)
            if row["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                if worker_id or claim_token:
                    raise PreviewConflict("PREVIEW_TERMINAL_STATE", "Preview is already terminal; stale finalization was rejected.", preview_id=preview_id)
                self.connection.commit()
                return self.get_preview(preview_id)
            if worker_id is not None or claim_token is not None:
                if not worker_id or not claim_token:
                    raise PreviewConflict("PREVIEW_OWNERSHIP_LOST", "Worker ownership credentials are incomplete.", preview_id=preview_id)
                self._assert_preview_owner(row, worker_id, claim_token)
            elif not allow_unclaimed or row["status"] != "QUEUED":
                raise PreviewConflict("PREVIEW_OWNERSHIP_REQUIRED", "A running preview must be finalized by its owning worker.", preview_id=preview_id)
            finished = _now()
            updated = self.connection.execute(
                """
                UPDATE preview_runs
                SET status = ?, completed_at = ?, worker_id = NULL, claim_token = NULL,
                    lease_expires_at = NULL, heartbeat_at = ?,
                    runtime_configuration_hash = ?, provenance_status = ?
                WHERE id = ? AND status = ?
                """,
                (
                    status,
                    finished,
                    finished,
                    statistics.get("runtime_configuration_hash") or row["runtime_configuration_hash"],
                    statistics.get("provenance_status") or row["provenance_status"],
                    preview_id,
                    row["status"],
                ),
            )
            if updated.rowcount != 1:
                raise PreviewConflict("PREVIEW_STATE_CONFLICT", "Preview state changed before finalization.", preview_id=preview_id)
            self.connection.execute(
                """
                INSERT INTO preview_run_statistics(id, preview_run_id, statistics_json, warnings_json, evidence_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (_new_id("preview-stat"), preview_id, _json(statistics), _json(warnings or []), _json(evidence or []), finished),
            )
            for event in events or []:
                self.connection.execute(
                    """
                    INSERT INTO preview_run_events(
                      id, preview_run_id, track_id, counting_line_id, crossing_pts_ms,
                      classification, movement, event_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _new_id("preview-event"),
                        preview_id,
                        event.get("track_id"),
                        event.get("line_id"),
                        event.get("crossing_timestamp_ms"),
                        event.get("synthetic_class") or event.get("classification"),
                        event.get("direction") or event.get("movement"),
                        _json(event),
                        finished,
                    ),
                )
            self._record_preview_state_event(preview_id, status, worker_id, int(row["attempt_number"] or 0), {})
            self.connection.commit()
            return self.get_preview(preview_id)
        except Exception:
            self.connection.rollback()
            raise

    def list_previews(self, project_id: str, limit: int = 25) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM preview_runs WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
        return [self.public_preview(row) for row in rows]

    def get_preview(self, preview_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM preview_runs WHERE id = ?", (preview_id,)).fetchone()
        if row is None:
            raise ValueError("preview run not found")
        return self.public_preview(row)

    def public_preview(self, row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload.pop("claim_token", None)
        payload.pop("worker_id", None)
        stats = self.connection.execute(
            "SELECT * FROM preview_run_statistics WHERE preview_run_id = ?",
            (payload["id"],),
        ).fetchone()
        payload["statistics"] = _load(stats["statistics_json"], {}) if stats else None
        payload["warnings"] = _load(stats["warnings_json"], []) if stats else []
        payload["evidence"] = _load(stats["evidence_json"], []) if stats else []
        event_rows = self.connection.execute(
            "SELECT event_json FROM preview_run_events WHERE preview_run_id = ? ORDER BY crossing_pts_ms, id",
            (payload["id"],),
        ).fetchall()
        payload["events"] = [_load(item["event_json"], {}) for item in event_rows]
        payload["ownership_state"] = "TERMINAL" if payload.get("status") in {"COMPLETED", "FAILED", "CANCELLED"} else str(payload.get("status", "UNKNOWN"))
        payload["disclosures"] = ["PREVIEW_ONLY", "NOT_PRODUCTION_RESULT"]
        return payload

    def create_comparison(
        self,
        *,
        project_id: str,
        source_fingerprint: str,
        scene_revision: str,
        start_pts_ms: int,
        end_pts_ms: int,
        configuration_revision_ids: list[str],
        comparison: Mapping[str, Any],
        created_by: str,
    ) -> dict[str, Any]:
        comparison_id = _new_id("comparison")
        self.connection.execute(
            """
            INSERT INTO configuration_comparisons(
              id, project_id, source_fingerprint_sha256, scene_revision,
              segment_start_pts_ms, segment_end_pts_ms, taxonomy_revision,
              configuration_revision_ids_json, comparison_json, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                comparison_id,
                project_id,
                source_fingerprint,
                scene_revision,
                start_pts_ms,
                end_pts_ms,
                "pilot-observable-taxonomy-v1",
                _json(configuration_revision_ids),
                _json(comparison),
                created_by,
                _now(),
            ),
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM configuration_comparisons WHERE id = ?", (comparison_id,)
        ).fetchone()
        assert row is not None
        return self.public_comparison(row)

    @staticmethod
    def public_comparison(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["processing_configuration_revision_ids"] = _load(payload.pop("configuration_revision_ids_json", None), [])
        payload["comparison"] = _load(payload.pop("comparison_json", None), {})
        return payload

    def select_candidate(
        self,
        *,
        project_id: str,
        configuration_revision_id: str,
        selection_status: str,
        rationale: str,
        created_by: str,
    ) -> dict[str, Any]:
        candidate_status = "OPERATIONAL_CANDIDATE" if selection_status != "DIAGNOSTIC_ONLY" else "DIAGNOSTIC_ONLY"
        selection_id = _new_id("candidate")
        self.connection.execute(
            """
            INSERT INTO operational_candidate_selections(
              id, project_id, processing_configuration_revision_id, selection_status,
              candidate_status, rationale, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (selection_id, project_id, configuration_revision_id, selection_status, candidate_status, rationale, created_by, _now()),
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM operational_candidate_selections WHERE id = ?", (selection_id,)
        ).fetchone()
        assert row is not None
        return dict(row)

    def create_capability_validation(
        self,
        *,
        project_id: str,
        configuration_revision_id: str,
        preview_run_id: str | None,
        validation_type: str,
        status: str,
        evidence: Mapping[str, Any],
        environment: Mapping[str, Any],
        created_by: str,
    ) -> dict[str, Any]:
        content = {
            "project_id": project_id,
            "configuration_revision_id": configuration_revision_id,
            "preview_run_id": preview_run_id,
            "validation_type": validation_type,
            "status": status,
            "evidence": dict(evidence),
            "environment": dict(environment),
        }
        record_hash = content_hash(content)
        existing = self.connection.execute(
            "SELECT * FROM capability_validation_records WHERE content_hash = ?", (record_hash,)
        ).fetchone()
        if existing is None:
            self.connection.execute(
                """
                INSERT INTO capability_validation_records(
                  id, project_id, processing_configuration_revision_id, preview_run_id,
                  validation_type, status, evidence_json, environment_json,
                  content_hash, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_id("capability"),
                    project_id,
                    configuration_revision_id,
                    preview_run_id,
                    validation_type,
                    status,
                    _json(evidence),
                    _json(environment),
                    record_hash,
                    created_by,
                    _now(),
                ),
            )
            self.connection.commit()
            existing = self.connection.execute(
                "SELECT * FROM capability_validation_records WHERE content_hash = ?", (record_hash,)
            ).fetchone()
        assert existing is not None
        return self.public_capability(existing)

    def list_capability_validations(self, project_id: str, limit: int = 25) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM capability_validation_records WHERE project_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
        return [self.public_capability(row) for row in rows]

    @staticmethod
    def public_capability(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["evidence"] = _load(payload.pop("evidence_json"), {})
        payload["environment"] = _load(payload.pop("environment_json"), {})
        return payload
