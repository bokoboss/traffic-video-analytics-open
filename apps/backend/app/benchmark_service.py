"""SQLite adapter for the Milestone 6C benchmark contracts."""

from __future__ import annotations

from contextlib import contextmanager
from collections import Counter
import json
import os
import sqlite3
from threading import RLock
import uuid
from typing import Any, Iterator, Mapping

from .benchmarking import (
    BenchmarkValidationError,
    build_experiment_configuration,
    build_metric_bundle,
    build_report_payload,
    canonical_hash,
    environment_snapshot,
    automatic_result_blockers,
    match_events,
    parse_corpus_manifest,
    parse_matching_policy,
    pareto_frontier,
    parse_qualification_policy,
    qualification_gates,
    render_report_markdown,
    sanitize_external_reference,
    throughput_metrics,
    validate_corpus_manifest,
    validate_ground_truth_manifest,
)
from .engineering_outputs import VALID_CANONICAL_DIRECTIONS, build_intervals, classification_consistency_error


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


class BenchmarkService:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.lock = RLock()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def validate_manifest(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return validate_corpus_manifest(payload)

    def import_manifest(self, payload: Mapping[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
        validation = validate_corpus_manifest(payload)
        if not validation["valid"]:
            raise BenchmarkValidationError("invalid benchmark corpus manifest", validation["errors"])
        manifest = parse_corpus_manifest(payload)
        content_hash = validation["content_hash"]
        if dry_run:
            return {"dry_run": True, **validation}
        existing = self.connection.execute(
            "SELECT id, content_hash FROM benchmark_corpus_revisions WHERE revision = ?",
            (manifest.corpus_revision,),
        ).fetchone()
        if existing is not None:
            if existing["content_hash"] != content_hash:
                raise ValueError("corpus revision is immutable; create a new corpus_revision")
            return self.get_corpus(manifest.corpus_revision)
        corpus_id = _id("corpus")
        with self.transaction():
            self.connection.execute(
                "INSERT INTO benchmark_corpus_revisions(id, revision, content_hash, manifest_json, status, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (corpus_id, manifest.corpus_revision, content_hash, _json(manifest.as_dict()), "ACTIVE", manifest.created_at, manifest.created_by),
            )
            for source in manifest.sources:
                source_id = _id("bench-source")
                self.connection.execute(
                    """
                    INSERT INTO benchmark_sources(
                      id, corpus_revision_id, benchmark_source_id, source_fingerprint_sha256,
                      file_name_or_external_reference, media_duration_ms, source_width, source_height,
                      nominal_fps_if_known, recording_start_status, timezone_name, rights_status,
                      rights_basis, permission_reference, redistribution_status, storage_status,
                      checksum_verified, scene_revision, annotation_revision, benchmark_split,
                      condition_tags_json, notes_json, created_at, created_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_id,
                        corpus_id,
                        source.benchmark_source_id,
                        source.source_fingerprint_sha256,
                        source.file_name_or_external_reference,
                        source.media_duration_ms,
                        source.source_width,
                        source.source_height,
                        source.nominal_fps_if_known,
                        source.recording_start_status,
                        source.timezone_name,
                        source.rights_status,
                        source.rights_basis,
                        source.permission_reference,
                        source.redistribution_status,
                        source.storage_status,
                        1 if source.checksum_verified else 0,
                        source.scene_revision,
                        source.annotation_revision,
                        source.benchmark_split,
                        _json(list(source.condition_tags)),
                        _json(source.notes),
                        source.created_at,
                        source.created_by,
                    ),
                )
                self.connection.execute(
                    "INSERT INTO benchmark_rights_records(id, benchmark_source_id, rights_status, rights_basis, permission_reference, redistribution_status, storage_status, notes_json, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (_id("rights"), source_id, source.rights_status, source.rights_basis, source.permission_reference, source.redistribution_status, source.storage_status, _json(source.notes), source.created_at, source.created_by),
                )
        return self.get_corpus(manifest.corpus_revision)

    def list_corpora(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT c.*, COUNT(s.id) AS source_count FROM benchmark_corpus_revisions c LEFT JOIN benchmark_sources s ON s.corpus_revision_id = c.id GROUP BY c.id ORDER BY c.created_at DESC, c.revision"
        ).fetchall()
        return [self._corpus_payload(row) for row in rows]

    def get_corpus(self, revision: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM benchmark_corpus_revisions WHERE revision = ?", (revision,)).fetchone()
        if row is None:
            raise ValueError("benchmark corpus revision not found")
        sources = self.connection.execute(
            "SELECT * FROM benchmark_sources WHERE corpus_revision_id = ? ORDER BY benchmark_source_id",
            (row["id"],),
        ).fetchall()
        payload = self._corpus_payload(row)
        payload["sources"] = [self._source_payload(source) for source in sources]
        payload["source_count"] = len(sources)
        return payload

    def _corpus_payload(self, row: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["manifest"] = _load_json(payload.pop("manifest_json", "{}"), {})
        payload["source_count"] = int(payload.pop("source_count", 0))
        return payload

    def _source_payload(self, row: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["condition_tags"] = _load_json(payload.pop("condition_tags_json", "[]"), [])
        payload["notes"] = _load_json(payload.pop("notes_json", "{}"), {})
        payload["checksum_verified"] = bool(payload.get("checksum_verified"))
        payload["rights_qualifying"] = payload.get("rights_status") in {"CLEARED_FOR_LOCAL_BENCHMARK", "CLEARED_FOR_REPOSITORY_DISTRIBUTION"}
        payload["file_name_or_external_reference"] = sanitize_external_reference(payload.get("file_name_or_external_reference"))
        return payload

    def _source_row(self, benchmark_source_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT s.*, c.revision AS corpus_revision FROM benchmark_sources s JOIN benchmark_corpus_revisions c ON c.id = s.corpus_revision_id WHERE s.benchmark_source_id = ? ORDER BY c.created_at DESC LIMIT 1",
            (benchmark_source_id,),
        ).fetchone()
        if row is None:
            raise ValueError("benchmark source not found")
        return row

    def _source_context(self, source: Mapping[str, Any]) -> dict[str, Any]:
        context = dict(source)
        context["counting_line_ids"] = self._scene_line_ids(str(context.get("scene_revision") or ""))
        context["condition_tags"] = _load_json(context.get("condition_tags_json"), [])
        context["notes"] = _load_json(context.get("notes_json"), {})
        return context

    def _scene_line_ids(self, scene_revision: str) -> set[str]:
        if not scene_revision:
            return set()
        row = self.connection.execute("SELECT geometry_json FROM scene_versions WHERE id = ?", (scene_revision,)).fetchone()
        if row is None:
            return set()
        geometry = _load_json(row["geometry_json"], {})
        return {str(line.get("id")) for line in geometry.get("counting_lines", []) if isinstance(line, Mapping) and line.get("id")}

    def validate_ground_truth(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        source_id = str(payload.get("benchmark_source_id") or "")
        try:
            source = self._source_context(self._source_row(source_id))
        except ValueError:
            return {"valid": False, "errors": [{"field": "benchmark_source_id", "code": "unknown_source", "message": "benchmark source not found"}]}
        return validate_ground_truth_manifest(payload, source=source, counting_line_ids=source.get("counting_line_ids", set()))

    def import_ground_truth(self, payload: Mapping[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
        source_row = self._source_row(str(payload.get("benchmark_source_id") or ""))
        source = self._source_context(source_row)
        validation = validate_ground_truth_manifest(payload, source=source, counting_line_ids=source.get("counting_line_ids", set()))
        if not validation["valid"]:
            raise BenchmarkValidationError("invalid ground truth manifest", validation["errors"])
        normalized = validation["ground_truth"]
        if dry_run:
            return {"dry_run": True, **validation}
        existing = self.connection.execute(
            "SELECT id, content_hash FROM ground_truth_revisions WHERE benchmark_source_id = ? AND revision = ?",
            (source_row["id"], normalized["ground_truth_revision"]),
        ).fetchone()
        if existing is not None:
            if existing["content_hash"] != validation["content_hash"]:
                raise ValueError("ground truth revision is immutable; create a new revision")
            return self.get_ground_truth(str(payload.get("benchmark_source_id")), normalized["ground_truth_revision"])
        gt_revision_id = _id("gt-rev")
        event_row_ids: dict[str, str] = {}
        with self.transaction():
            parent_id = normalized.get("parent_revision_id")
            if parent_id:
                parent = self.connection.execute(
                    "SELECT id FROM ground_truth_revisions WHERE (id = ? OR revision = ?) AND benchmark_source_id = ?",
                    (parent_id, parent_id, source_row["id"]),
                ).fetchone()
                if parent is None:
                    raise ValueError("parent ground truth revision not found")
                parent_id = parent["id"]
            self.connection.execute(
                "INSERT INTO ground_truth_revisions(id, benchmark_source_id, revision, content_hash, status, annotation_schema_version, parent_revision_id, reviewer_agreement_json, notes_json, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (gt_revision_id, source_row["id"], normalized["ground_truth_revision"], validation["content_hash"], normalized.get("status", "IMPORTED"), "ground-truth-v1", parent_id, _json(normalized.get("reviewer_agreement", {})), _json(normalized.get("notes", {})), normalized["created_at"], normalized["created_by"]),
            )
            for event in normalized["events"]:
                row_id = _id("gt-event")
                event_row_ids[event["ground_truth_event_id"]] = row_id
                self.connection.execute(
                    """
                    INSERT INTO ground_truth_events(
                      id, ground_truth_event_id, benchmark_source_id, ground_truth_revision_id,
                      scene_revision, counting_line_id, canonical_direction, crossing_pts_ms,
                      engineering_class, classification_status, timestamp_status, annotation_status,
                      evidence_refs_json, notes, created_by, created_at, source_frame_index,
                      source_frame_pts_ms, frame_start_index, frame_end_index, identity_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row_id,
                        event["ground_truth_event_id"],
                        source_row["id"],
                        gt_revision_id,
                        event["scene_revision"],
                        event["counting_line_id"],
                        event["canonical_direction"],
                        event["crossing_pts_ms"],
                        event["engineering_class"],
                        event["classification_status"],
                        event["timestamp_status"],
                        event["annotation_status"],
                        _json(event["evidence_refs"]),
                        event["notes"],
                        event["created_by"],
                        event["created_at"],
                        event["source_frame_index"],
                        event["source_frame_pts_ms"],
                        event["frame_start_index"],
                        event["frame_end_index"],
                        event["identity_id"],
                    ),
                )
            for annotation in normalized["reviewer_annotations"]:
                event_row_id = event_row_ids[annotation["ground_truth_event_id"]]
                self.connection.execute(
                    "INSERT INTO ground_truth_reviewer_annotations(id, ground_truth_event_id, reviewer_id, annotation_revision, event_decision, class_decision, direction_decision, timestamp_decision_ms, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (_id("gt-review"), event_row_id, annotation["reviewer_id"], annotation["annotation_revision"], annotation["event_decision"], annotation["class_decision"], annotation["direction_decision"], annotation["timestamp_decision"], annotation["notes"], annotation["created_at"]),
                )
        return self.get_ground_truth(str(payload.get("benchmark_source_id")), normalized["ground_truth_revision"])

    def get_ground_truth(self, benchmark_source_id: str, revision: str | None = None) -> dict[str, Any]:
        source = self._source_row(benchmark_source_id)
        query = "SELECT * FROM ground_truth_revisions WHERE benchmark_source_id = ?"
        params: list[Any] = [source["id"]]
        if revision:
            query += " AND revision = ?"
            params.append(revision)
        query += " ORDER BY created_at DESC LIMIT 1"
        gt = self.connection.execute(query, params).fetchone()
        if gt is None:
            raise ValueError("ground truth revision not found")
        events = [
            self._ground_truth_event_payload(row)
            for row in self.connection.execute("SELECT * FROM ground_truth_events WHERE ground_truth_revision_id = ? ORDER BY crossing_pts_ms, counting_line_id, ground_truth_event_id", (gt["id"],))
        ]
        annotations = [
            dict(row)
            for row in self.connection.execute(
                "SELECT a.*, e.ground_truth_event_id FROM ground_truth_reviewer_annotations a JOIN ground_truth_events e ON e.id = a.ground_truth_event_id WHERE e.ground_truth_revision_id = ? ORDER BY a.reviewer_id, e.ground_truth_event_id, a.annotation_revision",
                (gt["id"],),
            )
        ]
        payload = dict(gt)
        payload["reviewer_agreement"] = _load_json(payload.pop("reviewer_agreement_json"), {})
        payload["notes"] = _load_json(payload.pop("notes_json"), {})
        payload["events"] = events
        payload["reviewer_annotations"] = annotations
        payload["benchmark_source_id"] = benchmark_source_id
        payload["source_fingerprint_sha256"] = source["source_fingerprint_sha256"]
        payload["scene_revision"] = source["scene_revision"]
        payload["benchmark_split"] = source["benchmark_split"]
        return payload

    def _ground_truth_event_payload(self, row: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["evidence_refs"] = _load_json(payload.pop("evidence_refs_json"), {})
        return payload

    def _evaluation_config(
        self,
        payload: Mapping[str, Any] | None,
        created_by: str,
        qualification_policy: Mapping[str, Any] | None = None,
        automatic_result_revision_id: str | None = None,
    ) -> tuple[sqlite3.Row, dict[str, Any]]:
        raw = dict(payload or {})
        configuration = build_experiment_configuration(raw) if raw.get("parameters") else None
        policy = parse_matching_policy(raw)
        policy_payload = policy.as_dict()
        policy_payload["configuration"] = configuration
        policy_payload["qualification_policy_hash"] = canonical_hash(qualification_policy) if qualification_policy else None
        policy_payload["automatic_result_revision_id"] = automatic_result_revision_id
        content_hash = canonical_hash(policy_payload)
        revision = str(raw.get("revision") or f"eval-{content_hash[:16]}")
        row = self.connection.execute("SELECT * FROM benchmark_evaluation_configurations WHERE content_hash = ?", (content_hash,)).fetchone()
        if row is None:
            self.connection.execute(
                "INSERT INTO benchmark_evaluation_configurations(id, revision, content_hash, match_tolerance_ms, direction_matching_mode, class_evaluation_mode, ignore_region_policy, timestamp_outlier_ms, interval_bucket_minutes, config_json, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_id("eval-config"), revision, content_hash, policy.match_tolerance_ms, policy.direction_matching_mode, policy.class_evaluation_mode, policy.ignore_region_policy, policy.timestamp_outlier_ms, policy.interval_bucket_minutes, _json(policy_payload), _now(), created_by),
            )
            row = self.connection.execute("SELECT * FROM benchmark_evaluation_configurations WHERE content_hash = ?", (content_hash,)).fetchone()
        assert row is not None
        return row, policy_payload

    def _automatic_context(self, run_id: str) -> tuple[sqlite3.Row, sqlite3.Row, list[dict[str, Any]], dict[str, Any]]:
        run = self.connection.execute("SELECT * FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise ValueError("analysis run not found")
        result = self.connection.execute(
            """
            SELECT er.*, tax.revision AS taxonomy_revision,
                   map.revision AS mapping_revision,
                   policy.revision AS classification_policy_revision
            FROM engineering_result_revisions er
            JOIN taxonomy_revisions tax ON tax.id = er.taxonomy_revision_id
            JOIN taxonomy_mapping_revisions map ON map.id = er.mapping_revision_id
            JOIN classification_policy_revisions policy ON policy.id = er.classification_policy_revision_id
            WHERE er.run_id = ?
            ORDER BY er.generated_at DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if result is None:
            raise ValueError("engineering result revision not found")
        reconciliation_row = self.connection.execute(
            "SELECT status, report_json FROM reconciliation_reports WHERE engineering_result_revision_id = ?",
            (result["id"],),
        ).fetchone()
        reconciliation_status = str(reconciliation_row["status"]) if reconciliation_row is not None else "MISSING"
        reconciliation = _load_json(reconciliation_row["report_json"], {}) if reconciliation_row is not None else {}
        tracks: dict[str, dict[str, Any]] = {}
        for row in self.connection.execute("SELECT track_id, evidence_json FROM engineering_track_evidence WHERE engineering_result_revision_id = ?", (result["id"],)):
            evidence = _load_json(row["evidence_json"], {})
            tracks[str(row["track_id"])] = evidence
        events: list[dict[str, Any]] = []
        projection_blockers: list[str] = []
        for row in self.connection.execute("SELECT * FROM engineering_event_projections WHERE engineering_result_revision_id = ? ORDER BY event_pts_ms, counting_line_id, track_id, technical_key", (result["id"],)):
            event = dict(row)
            event["source_fingerprint_sha256"] = event.get("source_fingerprint_sha256") or run["source_fingerprint_sha256"]
            evidence = tracks.get(str(event.get("track_id")), {})
            event["track_observation_count"] = evidence.get("observation_count")
            first_pts = evidence.get("first_pts_ms", evidence.get("pts_start_ms"))
            last_pts = evidence.get("last_pts_ms", evidence.get("pts_end_ms"))
            if first_pts is not None and last_pts is not None:
                event["track_duration_ms"] = int(last_pts) - int(first_pts)
            row_id = str(event.get("source_event_id") or event.get("id") or "unknown")
            if event.get("stale"):
                projection_blockers.append(f"STALE_PROJECTION:{row_id}")
            if event.get("canonical_direction") not in VALID_CANONICAL_DIRECTIONS:
                projection_blockers.append(f"INVALID_DIRECTION_PROJECTION:{row_id}")
            consistency_error = classification_consistency_error(str(event.get("classification_status") or ""), str(event.get("engineering_class") or ""))
            if consistency_error:
                projection_blockers.append(f"INVALID_CLASSIFICATION_PROJECTION:{row_id}")
            event["stale"] = bool(event.get("stale")) or bool(result["stale"])
            event["structurally_invalid"] = bool(projection_blockers) or reconciliation_status != "STRUCTURALLY_VALID"
            events.append(event)
        context = {
            "engineering_ready": bool(result["engineering_ready"]),
            "stale": bool(result["stale"]) or any(bool(event.get("stale")) for event in events),
            "structurally_invalid": result["result_status"] != "STRUCTURALLY_VALID" or reconciliation_status != "STRUCTURALLY_VALID" or bool(projection_blockers),
            "reconciliation_status": reconciliation_status,
            "reconciliation": reconciliation,
            "projection_blockers": sorted(set(projection_blockers)),
            "result_status": result["result_status"],
            "result_revision_id": result["id"],
        }
        context["blockers"] = automatic_result_blockers(context)
        return run, result, events, context

    def evaluate(
        self,
        *,
        automatic_run_id: str,
        benchmark_source_id: str,
        ground_truth_revision: str | None = None,
        evaluation_configuration: Mapping[str, Any] | None = None,
        code_commit_sha: str | None = None,
        approved_policy: Mapping[str, Any] | None = None,
        created_by: str = "benchmark-cli",
    ) -> dict[str, Any]:
        source_row = self._source_row(benchmark_source_id)
        ground_truth = self.get_ground_truth(benchmark_source_id, ground_truth_revision)
        run, result, predicted, automatic_state = self._automatic_context(automatic_run_id)
        run_source = self.connection.execute("SELECT * FROM video_sources WHERE id = ?", (run["source_id"],)).fetchone()
        if run_source is None:
            raise ValueError("automatic run source not found")
        if str(run_source["fingerprint_sha256"]) != str(source_row["source_fingerprint_sha256"]):
            raise ValueError("benchmark source fingerprint does not match automatic run")
        if str(source_row["scene_revision"]) not in {str(run["scene_version_id"]), str(result["id"]), str(run["scene_semantic_hash"])}:
            # A corpus manifest may carry the scene semantic revision instead of
            # the database row id; event-level scene checks remain authoritative.
            if any(str(event.get("scene_revision")) != str(source_row["scene_revision"]) for event in predicted):
                raise ValueError("benchmark source scene revision does not match automatic result")
        approved_policy_payload = parse_qualification_policy(approved_policy).as_dict() if approved_policy is not None else None
        config_row, policy_payload = self._evaluation_config(evaluation_configuration, created_by, approved_policy_payload, str(result["id"]))
        policy = parse_matching_policy(policy_payload)
        source_context = dict(source_row)
        source_context["analysis_start_pts_ms"] = run_source["analysis_start_pts_ms"]
        source_context["analysis_end_pts_ms"] = run_source["analysis_end_pts_ms"]
        intervals = build_intervals(source_context, bucket_minutes=policy.interval_bucket_minutes)
        truth_events = list(ground_truth["events"])
        stats = _load_json(run["processing_stats_json"], {})
        processing_config = _load_json(run["processing_config_json"], {})
        runtime_hash = str(run["runtime_configuration_hash"] or processing_config.get("runtime_configuration_hash") or "") or None
        provenance_status = str(run["provenance_status"] or processing_config.get("runtime_provenance_status") or "LEGACY_UNRESOLVED")
        auto_context = {
            **automatic_state,
            "taxonomy_revision": result["taxonomy_revision"],
            "mapping_revision": result["mapping_revision"],
            "classification_policy_revision": result["classification_policy_revision"],
            "code_commit_sha": code_commit_sha or os.getenv("GIT_COMMIT_SHA") or "unavailable",
            "configuration_hash": runtime_hash or str(run["configuration_revision"] or canonical_hash(processing_config)),
            "runtime_configuration_hash": runtime_hash,
            "provenance_status": provenance_status,
        }
        if automatic_state["blockers"]:
            matches = {
                "schema_version": "event-match-v1",
                "status": "NOT_SCORABLE",
                "blockers": automatic_state["blockers"],
                "matches": [],
                "predicted_count": 0,
                "ground_truth_count": 0,
                "accounting_invariants": {},
            }
            metrics: dict[str, Any] = {}
        else:
            matches = match_events(predicted, truth_events, policy)
            throughput = throughput_metrics(stats, configuration=processing_config)
            metrics = build_metric_bundle(predicted, truth_events, matches, intervals, throughput=throughput)
        corpus_payload = self.get_corpus(str(source_row["corpus_revision"]))
        source_payload = self._source_payload(source_row)
        source_payload["source_count"] = corpus_payload.get("source_count", 0)
        source_payload["condition_coverage"] = sorted({tag for corpus_source in corpus_payload.get("sources", []) for tag in corpus_source.get("condition_tags", [])})
        qualification = qualification_gates(
            source=source_payload,
            ground_truth=ground_truth,
            automatic_result=auto_context,
            metrics=metrics,
            approved_policy=approved_policy_payload,
        )
        corpus_summary = {
            "corpus_revision": source_row["corpus_revision"],
            "benchmark_source_id": benchmark_source_id,
            "rights_status": source_row["rights_status"],
            "rights_disclosure": "media bytes are external to the repository" if source_row["storage_status"] != "REPOSITORY" else "repository-distributable metadata only",
            "source_count": corpus_payload.get("source_count", 1),
            "benchmark_split": source_row["benchmark_split"],
            "condition_tags": _load_json(source_row["condition_tags_json"], []),
        }
        report = build_report_payload(
            corpus=corpus_summary,
            ground_truth=ground_truth,
            automatic_configuration={**processing_config, "run_id": automatic_run_id, "result_revision_id": result["id"]},
            matching_policy=policy_payload,
            match_result=matches,
            metrics=metrics,
            qualification=qualification,
            reproducibility={
                **auto_context,
                "source_checksum": source_row["source_fingerprint_sha256"],
                "ground_truth_revision": ground_truth["revision"],
                "evaluation_configuration_revision": config_row["revision"],
                "runtime_configuration_hash": auto_context["runtime_configuration_hash"],
                "provenance_status": auto_context["provenance_status"],
                "runtime_equivalence_disclosure": (
                    "Benchmark is tied to the canonical runtime configuration hash."
                    if auto_context["runtime_configuration_hash"]
                    else "Runtime-equivalent comparison is unavailable because this legacy run has no canonical runtime hash."
                ),
                "environment": environment_snapshot(),
            },
        )
        benchmark_run_id = _id("benchmark-run")
        started_at = _now()
        incomplete = bool(automatic_state["blockers"])
        existing = self.connection.execute(
            "SELECT id, automatic_result_revision_id FROM benchmark_runs WHERE automatic_run_id = ? AND ground_truth_revision_id = ? AND evaluation_configuration_id = ?",
            (automatic_run_id, ground_truth["id"], config_row["id"]),
        ).fetchone()
        if existing is not None and str(existing["automatic_result_revision_id"]) == str(result["id"]):
            return self.get_run(str(existing["id"]))
        with self.transaction():
            self.connection.execute(
                """
                INSERT INTO benchmark_runs(
                  id, benchmark_source_id, corpus_revision_id, ground_truth_revision_id,
                  automatic_run_id, automatic_result_revision_id, evaluation_configuration_id,
                  status, qualification_status, source_fingerprint_sha256, scene_revision,
                  code_commit_sha, taxonomy_revision, mapping_revision,
                  classification_policy_revision, model_id, weight_sha256, detector_version,
                  tracker_version, configuration_hash, environment_json, run_configuration_json,
                  started_at, result_summary_json, runtime_configuration_hash, provenance_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    benchmark_run_id,
                    source_row["id"],
                    source_row["corpus_revision_id"],
                    ground_truth["id"],
                    automatic_run_id,
                    result["id"],
                    config_row["id"],
                    "INCOMPLETE" if incomplete else "RUNNING",
                    qualification["status"],
                    source_row["source_fingerprint_sha256"],
                    source_row["scene_revision"],
                    auto_context["code_commit_sha"],
                    result["taxonomy_revision"],
                    result["mapping_revision"],
                    result["classification_policy_revision"],
                    run["model_revision"] or run["detector_id"],
                    run["weight_sha256"],
                    run["model_revision"],
                    run["tracker_revision"],
                    auto_context["configuration_hash"],
                    _json(environment_snapshot()),
                    _json({"processing": processing_config, "evaluation": policy_payload}),
                    started_at,
                    _json({"status": "RUNNING"}),
                    auto_context["runtime_configuration_hash"],
                    auto_context["provenance_status"],
                ),
            )
            if not incomplete:
                for index, match in enumerate(matches["matches"]):
                    self.connection.execute(
                        "INSERT INTO benchmark_event_matches(id, benchmark_run_id, automatic_event_id, ground_truth_event_id, primary_category, secondary_error_flags_json, timestamp_error_ms, absolute_timestamp_error_ms, counting_line_id, automatic_direction, ground_truth_direction, automatic_track_id, technical_key, duplicate_event_ids_json, selected, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (_id(f"match-{index}"), benchmark_run_id, match.get("automatic_event_id"), match.get("ground_truth_event_id"), match["primary_category"], _json(match.get("secondary_error_flags", [])), match.get("timestamp_error_ms"), match.get("absolute_timestamp_error_ms"), match.get("line_id"), match.get("automatic_direction"), match.get("ground_truth_direction"), match.get("automatic_track_id"), match.get("technical_key"), _json(match.get("duplicate_event_ids", [])), 1, started_at),
                    )
                self._insert_metric_snapshots(benchmark_run_id, metrics)
                self.connection.execute(
                    "INSERT INTO benchmark_fragmentation_metrics(id, benchmark_run_id, availability, metrics_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (_id("fragmentation"), benchmark_run_id, metrics["fragmentation_metrics"].get("availability", "UNAVAILABLE"), _json(metrics["fragmentation_metrics"]), started_at),
                )
                self.connection.execute(
                    "INSERT INTO benchmark_throughput_metrics(id, benchmark_run_id, metrics_json, created_at) VALUES (?, ?, ?, ?)",
                    (_id("throughput"), benchmark_run_id, _json(metrics["throughput_metrics"]), started_at),
                )
            self.connection.execute(
                "UPDATE benchmark_runs SET status = ?, qualification_status = ?, completed_at = ?, result_summary_json = ? WHERE id = ?",
                ("INCOMPLETE" if incomplete else "COMPLETED", qualification["status"], _now(), _json({"report": report, "metrics": metrics, "match_result": matches, "qualification": qualification}), benchmark_run_id),
            )
            self.connection.execute(
                "INSERT INTO benchmark_qualification_reports(id, benchmark_run_id, status, gates_json, policy_json, report_json, report_markdown, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (_id("qualification"), benchmark_run_id, qualification["status"], _json(qualification["gates"]), _json(qualification.get("policy", {})), _json(report), render_report_markdown(report), _now()),
            )
        return self.get_run(benchmark_run_id)

    def _insert_metric_snapshots(self, benchmark_run_id: str, metrics: Mapping[str, Any]) -> None:
        scopes: list[tuple[str, Any]] = [
            ("HEADLINE", metrics.get("event_metrics", {})),
            ("COUNT", metrics.get("count_metrics", {}).get("total", {})),
            ("LINE", metrics.get("count_metrics", {}).get("by_line", {})),
            ("DIRECTION", metrics.get("direction_metrics", {})),
            ("CLASS", metrics.get("class_metrics", {})),
            ("TIMESTAMP", metrics.get("timestamp_metrics", {})),
            ("INTERVAL", metrics.get("count_metrics", {}).get("by_interval", [])),
            ("DUPLICATE", metrics.get("duplicate_metrics", {})),
        ]
        for scope, payload in scopes:
            self.connection.execute(
                "INSERT INTO benchmark_metric_snapshots(id, benchmark_run_id, metric_scope, dimension_key, metrics_json, schema_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_id("metric"), benchmark_run_id, scope, "overall", _json(payload), "benchmark-metric-snapshot-v1", _now()),
            )

    def list_runs(self, *, limit: int = 25, offset: int = 0) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT r.*, s.benchmark_source_id, s.benchmark_split, s.rights_status, g.revision AS ground_truth_revision, e.revision AS evaluation_configuration_revision FROM benchmark_runs r JOIN benchmark_sources s ON s.id = r.benchmark_source_id JOIN ground_truth_revisions g ON g.id = r.ground_truth_revision_id JOIN benchmark_evaluation_configurations e ON e.id = r.evaluation_configuration_id ORDER BY r.started_at DESC, r.id LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [self._run_header(row) for row in rows]

    def get_run(self, benchmark_run_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT r.*, s.benchmark_source_id, s.benchmark_split, s.rights_status, g.revision AS ground_truth_revision, e.revision AS evaluation_configuration_revision FROM benchmark_runs r JOIN benchmark_sources s ON s.id = r.benchmark_source_id JOIN ground_truth_revisions g ON g.id = r.ground_truth_revision_id JOIN benchmark_evaluation_configurations e ON e.id = r.evaluation_configuration_id WHERE r.id = ?",
            (benchmark_run_id,),
        ).fetchone()
        if row is None:
            raise ValueError("benchmark run not found")
        payload = self._run_header(row)
        summary = _load_json(row["result_summary_json"], {})
        payload["report"] = summary.get("report", {})
        payload["metrics"] = summary.get("metrics", {})
        payload["match_result"] = summary.get("match_result", {})
        payload["qualification"] = summary.get("qualification", {})
        return payload

    def _run_header(self, row: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["environment"] = _load_json(payload.pop("environment_json", "{}"), {})
        payload["run_configuration"] = _load_json(payload.pop("run_configuration_json", "{}"), {})
        payload.pop("result_summary_json", None)
        return payload

    def list_matches(self, benchmark_run_id: str, *, limit: int = 100, offset: int = 0, category: str | None = None) -> dict[str, Any]:
        self.get_run(benchmark_run_id)
        query = "SELECT * FROM benchmark_event_matches WHERE benchmark_run_id = ?"
        params: list[Any] = [benchmark_run_id]
        if category:
            query += " AND primary_category = ?"
            params.append(category)
        query += " ORDER BY COALESCE(timestamp_error_ms, 9223372036854775807), COALESCE(ground_truth_event_id, ''), COALESCE(automatic_event_id, '') LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = []
        for row in self.connection.execute(query, params):
            payload = dict(row)
            payload["secondary_error_flags"] = _load_json(payload.pop("secondary_error_flags_json"), [])
            payload["duplicate_event_ids"] = _load_json(payload.pop("duplicate_event_ids_json"), [])
            rows.append(payload)
        return {"benchmark_run_id": benchmark_run_id, "items": rows, "limit": limit, "offset": offset, "category": category}

    def metrics(self, benchmark_run_id: str) -> dict[str, Any]:
        run = self.get_run(benchmark_run_id)
        return {"benchmark_run_id": benchmark_run_id, "metrics": run.get("metrics", {}), "qualification": run.get("qualification", {})}

    def qualification(self, benchmark_run_id: str) -> dict[str, Any]:
        run = self.get_run(benchmark_run_id)
        row = self.connection.execute("SELECT * FROM benchmark_qualification_reports WHERE benchmark_run_id = ?", (benchmark_run_id,)).fetchone()
        if row is None:
            raise ValueError("benchmark qualification report not found")
        report_payload = _load_json(row["report_json"], run.get("report", {}))
        qualification_payload = report_payload.get("qualification_gates", {}) if isinstance(report_payload, Mapping) else {}
        return {
            "benchmark_run_id": benchmark_run_id,
            "status": row["status"],
            "gates": _load_json(row["gates_json"], {}),
            "policy": _load_json(row["policy_json"], {}),
            "threshold_evaluation": qualification_payload.get("threshold_evaluation", {}),
            "report": report_payload,
            "report_markdown": row["report_markdown"],
        }

    def list_experiment_comparisons(self, *, limit: int = 25, offset: int = 0) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT s.*, c.revision AS corpus_revision FROM benchmark_experiment_suites s JOIN benchmark_corpus_revisions c ON c.id = s.corpus_revision_id ORDER BY s.created_at DESC, s.id LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        comparisons: list[dict[str, Any]] = []
        for row in rows:
            run_rows = self.connection.execute(
                "SELECT * FROM benchmark_experiment_runs WHERE suite_id = ? ORDER BY benchmark_split, configuration_revision, repetition_index, id",
                (row["id"],),
            ).fetchall()
            results = [_load_json(run["result_json"], {}) for run in run_rows]
            qualification_statuses = sorted({str(result.get("qualification_status")) for result in results if result.get("qualification_status")})
            comparisons.append(
                {
                    "suite_id": row["id"],
                    "suite_revision": row["revision"],
                    "corpus_revision": row["corpus_revision"],
                    "calibration_split": row["calibration_split"],
                    "holdout_split": row["holdout_split"],
                    "baseline_configuration_id": row["baseline_configuration_id"],
                    "status": row["status"],
                    "candidate_configurations": _load_json(row["candidate_configurations_json"], []),
                    "runs": [
                        {
                            "id": run["id"],
                            "benchmark_run_id": run["benchmark_run_id"],
                            "configuration_revision": run["configuration_revision"],
                            "benchmark_split": run["benchmark_split"],
                            "repetition_index": run["repetition_index"],
                            "result": _load_json(run["result_json"], {}),
                        }
                        for run in run_rows
                    ],
                    "pareto": pareto_frontier(results) if results else {"status": "NOT_RUN", "frontier": []},
                    "qualification": {
                        "status": "CALIBRATION_ONLY",
                        "qualification_statuses": qualification_statuses,
                        "no_pilot_qualification_claim": True,
                    },
                }
            )
        return comparisons

    def overview(self) -> dict[str, Any]:
        corpora = self.list_corpora()
        runs = self.list_runs(limit=10)
        rights_counts: Counter[str] = Counter()
        split_counts: Counter[str] = Counter()
        source_count = 0
        for corpus in corpora:
            details = self.get_corpus(str(corpus["revision"]))
            for source in details.get("sources", []):
                source_count += 1
                rights_counts[str(source.get("rights_status"))] += 1
                split_counts[str(source.get("benchmark_split"))] += 1
        latest = runs[0] if runs else None
        return {
            "status": "READY" if corpora else "EMPTY",
            "corpus_revisions": corpora,
            "source_count": source_count,
            "rights_coverage": dict(sorted(rights_counts.items())),
            "split_counts": dict(sorted(split_counts.items())),
            "latest_run": latest,
            "benchmark_runs": runs,
            "limitations": [
                "No accuracy claim is made without reviewed, rights-cleared representative data.",
                "Unknown or ambiguous engineering classes remain explicit.",
                "No owner-approved qualification thresholds are configured.",
                "Milestone 6D correction and production export are not part of this workspace.",
            ],
        }

    def build_experiment_configuration(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return build_experiment_configuration(payload)
