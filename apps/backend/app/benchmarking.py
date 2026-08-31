"""Implementation-independent Milestone 6C benchmark contracts.

This module contains the deterministic part of benchmark evaluation.  It does
not import a detector, tracker, media decoder or third-party result object.
The 6C persistence/API layer adapts 6B rows into these bounded dictionaries.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import platform
import re
from statistics import mean, median
from typing import Any, Callable, Iterable, Mapping, Sequence

from .engineering_outputs import (
    EngineeringClass,
    STATUS_ALLOWED_ENGINEERING_CLASSES,
    VALID_CANONICAL_DIRECTIONS,
    classification_consistency_error,
)


BENCHMARK_MANIFEST_VERSION = "benchmark-corpus-v1"
GROUND_TRUTH_MANIFEST_VERSION = "ground-truth-v1"
EVALUATION_CONFIG_VERSION = "benchmark-evaluation-v1"
MATCHING_POLICY_VERSION = "pts-one-to-one-v1"
METRICS_SCHEMA_VERSION = "benchmark-metrics-v1"
QUALIFICATION_POLICY_SCHEMA_VERSION = "qualification-threshold-v1"

RIGHTS_STATUSES = frozenset(
    {
        "CLEARED_FOR_LOCAL_BENCHMARK",
        "CLEARED_FOR_REPOSITORY_DISTRIBUTION",
        "EXTERNAL_REFERENCE_ONLY",
        "NOT_CLEARED",
        "UNKNOWN",
    }
)
BENCHMARK_SPLITS = frozenset({"CALIBRATION", "HOLDOUT", "DIAGNOSTIC_ONLY"})
ANNOTATION_STATUSES = frozenset({"VALID", "IGNORE", "UNSCORABLE", "UNCERTAIN", "DUPLICATE_ANNOTATION"})
TIMESTAMP_STATUSES = frozenset({"CONFIRMED", "UNCERTAIN", "UNAVAILABLE", "UNSCORABLE"})
QUALIFICATION_STATUSES = frozenset(
    {
        "NOT_RUN",
        "INCOMPLETE",
        "BENCHMARK_COMPLETE",
        "CALIBRATION_CANDIDATE",
        "HOLDOUT_INSUFFICIENT",
        "NOT_QUALIFIED",
        "QUALIFIED_FOR_PILOT",
    }
)
MATCH_CATEGORIES = frozenset(
    {
        "TRUE_POSITIVE",
        "FALSE_POSITIVE",
        "FALSE_NEGATIVE",
        "DUPLICATE_AUTOMATIC",
        "DIRECTION_ERROR",
        "CLASS_ERROR",
        "TIMESTAMP_OUTLIER",
        "UNSCORABLE",
        "IGNORED",
    }
)
KNOWN_CONDITION_TAGS = frozenset(
    {
        "daytime",
        "night",
        "rain",
        "glare",
        "shadow",
        "low resolution",
        "high camera angle",
        "oblique camera angle",
        "heavy occlusion",
        "dense motorcycle flow",
        "mixed traffic",
        "stop-and-go",
        "multiple lines",
        "bidirectional flow",
        "pedestrian interaction",
        "short clip",
        "long clip",
    }
)
ENGINEERING_CLASSES = frozenset(item.value for item in EngineeringClass)
GROUND_TRUTH_CLASS_STATUSES = frozenset(
    set(STATUS_ALLOWED_ENGINEERING_CLASSES)
    | {"KNOWN", "CONFIRMED", "ADJUDICATED"}
)
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
SUPPORTED_THRESHOLD_OPERATORS = frozenset({"GTE", "LTE", "GT", "LT", "EQ"})
SUPPORTED_UNDEFINED_BEHAVIORS = frozenset({"FAIL_CLOSED", "ALLOW_UNDEFINED"})

# Qualification policies resolve only these explicitly named, scalar metric
# paths.  This is intentionally an allowlist rather than a general expression
# evaluator so a policy cannot execute arbitrary code or traverse unreviewed
# report payloads.
METRIC_PATH_DEFINITIONS: dict[str, dict[str, str]] = {
    "event_metrics.precision": {"unit": "ratio", "denominator_path": "event_metrics.denominators.precision"},
    "event_metrics.recall": {"unit": "ratio", "denominator_path": "event_metrics.denominators.recall"},
    "event_metrics.f1": {"unit": "ratio"},
    "direction_metrics.direction_accuracy": {"unit": "ratio", "denominator_path": "direction_metrics.denominator"},
    "duplicate_metrics.duplicate_rate": {"unit": "ratio", "denominator_path": "event_metrics.denominators.duplicate_rate"},
    "duplicate_metrics.miss_rate": {"unit": "ratio", "denominator_path": "event_metrics.denominators.miss_rate"},
    "count_metrics.total.absolute_error": {"unit": "count", "denominator_path": "count_metrics.total.ground_truth_count"},
    "count_metrics.total.absolute_percentage_error": {"unit": "ratio", "denominator_path": "count_metrics.total.ground_truth_count"},
    "timestamp_metrics.mean_absolute_error_ms": {"unit": "milliseconds"},
    "timestamp_metrics.p95_absolute_error_ms": {"unit": "milliseconds"},
    "fragmentation_metrics.fragmented_ground_truth_rate": {"unit": "ratio", "denominator_path": "fragmentation_metrics.ground_truth_vehicle_count"},
    "fragmentation_metrics.short_track_rate": {"unit": "ratio"},
    "throughput_metrics.processing_duration_video_duration_ratio": {"unit": "ratio"},
}


class BenchmarkValidationError(ValueError):
    """Raised when a corpus, annotation or evaluation contract is invalid."""

    def __init__(self, message: str, errors: Sequence[Mapping[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.errors = tuple(dict(error) for error in (errors or ()))


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _issue(field_name: str, code: str, message: str) -> dict[str, str]:
    return {"field": field_name, "code": code, "message": message}


def sanitize_external_reference(value: Any) -> str:
    """Keep a filename/URL useful without persisting a private local path."""

    text = str(value or "").strip()
    if not text:
        return ""
    if re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith(("\\\\", "/")):
        name = re.split(r"[\\/]", text)[-1] or "external-media"
        return f"external://{name}"
    return text


def _as_int(value: Any, field_name: str, errors: list[dict[str, str]], *, minimum: int | None = None) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        errors.append(_issue(field_name, "invalid_integer", "must be an integer"))
        return None
    if minimum is not None and result < minimum:
        errors.append(_issue(field_name, "below_minimum", f"must be at least {minimum}"))
    return result


def _bounded_json(value: Any, field_name: str, errors: list[dict[str, str]], limit: int = 16_384) -> Any:
    if isinstance(value, (bytes, bytearray)):
        errors.append(_issue(field_name, "binary_payload_forbidden", "benchmark evidence must use references, not bytes"))
        return {}
    try:
        serialized = canonical_json(value)
    except (TypeError, ValueError):
        errors.append(_issue(field_name, "invalid_json", "must be JSON serializable"))
        return {}
    if len(serialized.encode("utf-8")) > limit:
        errors.append(_issue(field_name, "payload_too_large", f"must be at most {limit} UTF-8 bytes"))
    if re.search(r"(?:image|video|media)_bytes|data:image|data:video", serialized, re.IGNORECASE):
        errors.append(_issue(field_name, "media_payload_forbidden", "media bytes are not part of the SQLite contract"))
    if re.search(r"(?:[A-Za-z]:[\\/]|^|[\"'])/(?:[^\"']+/)+", serialized):
        errors.append(_issue(field_name, "private_path_forbidden", "absolute local paths must not be stored"))
    return value


@dataclass(frozen=True)
class BenchmarkSourceManifest:
    benchmark_source_id: str
    corpus_revision: str
    source_fingerprint_sha256: str
    file_name_or_external_reference: str
    media_duration_ms: int
    source_width: int
    source_height: int
    nominal_fps_if_known: float | None
    recording_start_status: str
    timezone_name: str | None
    rights_status: str
    rights_basis: str
    permission_reference: str | None
    redistribution_status: str
    storage_status: str
    checksum_verified: bool
    scene_revision: str
    annotation_revision: str | None
    benchmark_split: str
    condition_tags: tuple[str, ...]
    created_at: str
    created_by: str
    notes: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "benchmark_source_id": self.benchmark_source_id,
            "corpus_revision": self.corpus_revision,
            "source_fingerprint_sha256": self.source_fingerprint_sha256,
            "file_name_or_external_reference": self.file_name_or_external_reference,
            "media_duration_ms": self.media_duration_ms,
            "source_width": self.source_width,
            "source_height": self.source_height,
            "nominal_fps_if_known": self.nominal_fps_if_known,
            "recording_start_status": self.recording_start_status,
            "timezone_name": self.timezone_name,
            "rights_status": self.rights_status,
            "rights_basis": self.rights_basis,
            "permission_reference": self.permission_reference,
            "redistribution_status": self.redistribution_status,
            "storage_status": self.storage_status,
            "checksum_verified": self.checksum_verified,
            "scene_revision": self.scene_revision,
            "annotation_revision": self.annotation_revision,
            "benchmark_split": self.benchmark_split,
            "condition_tags": list(self.condition_tags),
            "created_at": self.created_at,
            "created_by": self.created_by,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class CorpusManifest:
    corpus_revision: str
    sources: tuple[BenchmarkSourceManifest, ...]
    created_at: str
    created_by: str
    notes: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": BENCHMARK_MANIFEST_VERSION,
            "corpus_revision": self.corpus_revision,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "notes": self.notes,
            "sources": [source.as_dict() for source in self.sources],
        }


def parse_corpus_manifest(payload: Mapping[str, Any]) -> CorpusManifest:
    errors: list[dict[str, str]] = []
    if payload.get("manifest_version") != BENCHMARK_MANIFEST_VERSION:
        errors.append(_issue("manifest_version", "unsupported_version", BENCHMARK_MANIFEST_VERSION))
    revision = str(payload.get("corpus_revision") or "").strip()
    if not revision:
        errors.append(_issue("corpus_revision", "required", "corpus_revision is required"))
    created_by = str(payload.get("created_by") or "").strip()
    if not created_by:
        errors.append(_issue("created_by", "required", "created_by is required"))
    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        errors.append(_issue("sources", "required", "at least one benchmark source is required"))
        raw_sources = []
    sources: list[BenchmarkSourceManifest] = []
    seen_ids: set[str] = set()
    seen_fingerprints: set[str] = set()
    for index, raw in enumerate(raw_sources):
        prefix = f"sources[{index}]"
        if not isinstance(raw, Mapping):
            errors.append(_issue(prefix, "invalid_object", "source must be an object"))
            continue
        source_id = str(raw.get("benchmark_source_id") or "").strip()
        if not source_id:
            errors.append(_issue(f"{prefix}.benchmark_source_id", "required", "source id is required"))
        if source_id in seen_ids:
            errors.append(_issue(f"{prefix}.benchmark_source_id", "duplicate", "source id is duplicated"))
        seen_ids.add(source_id)
        fingerprint = str(raw.get("source_fingerprint_sha256") or "").strip().lower()
        if not SHA256_RE.fullmatch(fingerprint):
            errors.append(_issue(f"{prefix}.source_fingerprint_sha256", "invalid_sha256", "must be a 64-character SHA-256"))
        if fingerprint in seen_fingerprints:
            errors.append(_issue(f"{prefix}.source_fingerprint_sha256", "duplicate", "source fingerprint is duplicated"))
        seen_fingerprints.add(fingerprint)
        duration = _as_int(raw.get("media_duration_ms"), f"{prefix}.media_duration_ms", errors, minimum=1)
        width = _as_int(raw.get("source_width"), f"{prefix}.source_width", errors, minimum=1)
        height = _as_int(raw.get("source_height"), f"{prefix}.source_height", errors, minimum=1)
        fps: float | None = None
        if raw.get("nominal_fps_if_known") is not None:
            try:
                fps = float(raw["nominal_fps_if_known"])
                if fps <= 0 or not math.isfinite(fps):
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(_issue(f"{prefix}.nominal_fps_if_known", "invalid_fps", "must be a positive finite number"))
        rights = str(raw.get("rights_status") or "UNKNOWN")
        if rights not in RIGHTS_STATUSES:
            errors.append(_issue(f"{prefix}.rights_status", "unsupported_value", "rights status is not recognized"))
        split = str(raw.get("benchmark_split") or "")
        if split not in BENCHMARK_SPLITS:
            errors.append(_issue(f"{prefix}.benchmark_split", "unsupported_value", "benchmark split is not recognized"))
        tags_raw = raw.get("condition_tags", [])
        if not isinstance(tags_raw, list) or any(not isinstance(item, str) or not item.strip() for item in tags_raw):
            errors.append(_issue(f"{prefix}.condition_tags", "invalid_tags", "condition_tags must be a list of non-empty strings"))
            tags_raw = []
        tags = tuple(dict.fromkeys(item.strip().lower() for item in tags_raw))
        unknown_tags = sorted(set(tags) - KNOWN_CONDITION_TAGS)
        if unknown_tags:
            errors.append(_issue(f"{prefix}.condition_tags", "unsupported_tag", ", ".join(unknown_tags)))
        for field_name in ("rights_basis", "redistribution_status", "storage_status", "recording_start_status", "scene_revision"):
            if not str(raw.get(field_name) or "").strip():
                errors.append(_issue(f"{prefix}.{field_name}", "required", f"{field_name} is required"))
        notes = _bounded_json(raw.get("notes", {}), f"{prefix}.notes", errors)
        source = BenchmarkSourceManifest(
            benchmark_source_id=source_id,
            corpus_revision=revision,
            source_fingerprint_sha256=fingerprint,
            file_name_or_external_reference=sanitize_external_reference(raw.get("file_name_or_external_reference")),
            media_duration_ms=duration or 0,
            source_width=width or 0,
            source_height=height or 0,
            nominal_fps_if_known=fps,
            recording_start_status=str(raw.get("recording_start_status") or "UNKNOWN"),
            timezone_name=str(raw.get("timezone_name")) if raw.get("timezone_name") else None,
            rights_status=rights,
            rights_basis=str(raw.get("rights_basis") or ""),
            permission_reference=str(raw.get("permission_reference")) if raw.get("permission_reference") else None,
            redistribution_status=str(raw.get("redistribution_status") or ""),
            storage_status=str(raw.get("storage_status") or ""),
            checksum_verified=bool(raw.get("checksum_verified", False)),
            scene_revision=str(raw.get("scene_revision") or ""),
            annotation_revision=str(raw.get("annotation_revision")) if raw.get("annotation_revision") else None,
            benchmark_split=split,
            condition_tags=tags,
            created_at=str(raw.get("created_at") or payload.get("created_at") or now_iso()),
            created_by=str(raw.get("created_by") or created_by),
            notes=notes if isinstance(notes, dict) else {},
        )
        sources.append(source)
    notes = _bounded_json(payload.get("notes", {}), "notes", errors)
    if errors:
        raise BenchmarkValidationError("invalid benchmark corpus manifest", errors)
    return CorpusManifest(
        corpus_revision=revision,
        sources=tuple(sources),
        created_at=str(payload.get("created_at") or now_iso()),
        created_by=created_by,
        notes=notes if isinstance(notes, dict) else {},
    )


def validate_corpus_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        manifest = parse_corpus_manifest(payload)
    except BenchmarkValidationError as exc:
        return {"valid": False, "errors": list(exc.errors) or [{"code": "invalid_manifest", "message": str(exc)}]}
    return {"valid": True, "errors": [], "manifest": manifest.as_dict(), "content_hash": canonical_hash(manifest.as_dict())}


def _source_context(source: Mapping[str, Any] | None) -> tuple[str | None, str | None, int | None, int | None, set[str]]:
    if not source:
        return None, None, None, None, set()
    start = source.get("analysis_start_pts_ms", source.get("analysis_window_start_ms", 0))
    end = source.get("analysis_end_pts_ms", source.get("analysis_window_end_ms", source.get("media_duration_ms")))
    try:
        start_value = int(start) if start is not None else None
    except (TypeError, ValueError):
        start_value = None
    try:
        end_value = int(end) if end is not None else None
    except (TypeError, ValueError):
        end_value = None
    line_ids = set(str(item) for item in source.get("counting_line_ids", []) if item)
    return (
        str(source.get("source_fingerprint_sha256")) if source.get("source_fingerprint_sha256") else None,
        str(source.get("scene_revision")) if source.get("scene_revision") else None,
        start_value,
        end_value,
        line_ids,
    )


def _validate_reviewer_annotations(raw_annotations: Any, event_ids: set[str], errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    if raw_annotations is None:
        return []
    if not isinstance(raw_annotations, list):
        errors.append(_issue("reviewer_annotations", "invalid_object", "reviewer_annotations must be a list"))
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(raw_annotations):
        prefix = f"reviewer_annotations[{index}]"
        if not isinstance(raw, Mapping):
            errors.append(_issue(prefix, "invalid_object", "reviewer annotation must be an object"))
            continue
        reviewer = str(raw.get("reviewer_id") or "").strip()
        event_id = str(raw.get("ground_truth_event_id") or "").strip()
        revision = str(raw.get("annotation_revision") or "").strip()
        if not reviewer or not event_id or not revision:
            errors.append(_issue(prefix, "required", "reviewer_id, ground_truth_event_id and annotation_revision are required"))
        if event_id and event_id not in event_ids:
            errors.append(_issue(f"{prefix}.ground_truth_event_id", "unknown_event", "reviewer annotation references an unknown event"))
        key = (reviewer, event_id, revision)
        if key in seen:
            errors.append(_issue(prefix, "duplicate", "reviewer annotation is duplicated"))
        seen.add(key)
        decision = str(raw.get("event_decision") or "").strip()
        if decision not in ANNOTATION_STATUSES:
            errors.append(_issue(f"{prefix}.event_decision", "unsupported_value", "event decision is not recognized"))
        class_decision = raw.get("class_decision")
        if class_decision is not None and str(class_decision) not in ENGINEERING_CLASSES:
            errors.append(_issue(f"{prefix}.class_decision", "unsupported_class", "class is not in the active engineering taxonomy"))
        direction_decision = raw.get("direction_decision")
        if direction_decision is not None and direction_decision not in VALID_CANONICAL_DIRECTIONS:
            errors.append(_issue(f"{prefix}.direction_decision", "invalid_direction", "direction must be A_TO_B or B_TO_A"))
        timestamp_decision = raw.get("timestamp_decision")
        if timestamp_decision is not None:
            try:
                int(timestamp_decision)
            except (TypeError, ValueError):
                errors.append(_issue(f"{prefix}.timestamp_decision", "invalid_timestamp", "timestamp decision must be milliseconds"))
        normalized.append(
            {
                "reviewer_id": reviewer,
                "ground_truth_event_id": event_id,
                "annotation_revision": revision,
                "event_decision": decision,
                "class_decision": str(class_decision) if class_decision is not None else None,
                "direction_decision": direction_decision,
                "timestamp_decision": int(timestamp_decision) if timestamp_decision is not None and str(timestamp_decision).lstrip("-").isdigit() else None,
                "notes": str(raw.get("notes") or "")[:2000],
                "created_at": str(raw.get("created_at") or now_iso()),
            }
        )
    return normalized


def parse_ground_truth_manifest(
    payload: Mapping[str, Any],
    *,
    source: Mapping[str, Any] | None = None,
    counting_line_ids: Iterable[str] = (),
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    if payload.get("ground_truth_manifest_version") != GROUND_TRUTH_MANIFEST_VERSION:
        errors.append(_issue("ground_truth_manifest_version", "unsupported_version", GROUND_TRUTH_MANIFEST_VERSION))
    source_fingerprint, source_scene, source_start, source_end, source_lines = _source_context(source)
    expected_lines = set(str(item) for item in counting_line_ids) | source_lines
    payload_fingerprint = str(payload.get("source_fingerprint_sha256") or "").lower()
    if source_fingerprint and payload_fingerprint != source_fingerprint:
        errors.append(_issue("source_fingerprint_sha256", "source_fingerprint_mismatch", "annotation source fingerprint does not match the corpus source"))
    if not payload_fingerprint or not SHA256_RE.fullmatch(payload_fingerprint):
        errors.append(_issue("source_fingerprint_sha256", "invalid_sha256", "must be a 64-character SHA-256"))
    revision = str(payload.get("ground_truth_revision") or payload.get("ground_truth_revision_id") or "").strip()
    if not revision:
        errors.append(_issue("ground_truth_revision", "required", "ground truth revision is required"))
    scene_revision = str(payload.get("scene_revision") or source_scene or "").strip()
    if source_scene and scene_revision != source_scene:
        errors.append(_issue("scene_revision", "scene_revision_mismatch", "annotation scene revision does not match the corpus source"))
    raw_events = payload.get("events")
    if not isinstance(raw_events, list):
        errors.append(_issue("events", "required", "events must be a list"))
        raw_events = []
    event_ids: set[str] = set()
    candidate_keys: set[tuple[str, str, int]] = set()
    events: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_events):
        prefix = f"events[{index}]"
        if not isinstance(raw, Mapping):
            errors.append(_issue(prefix, "invalid_object", "event must be an object"))
            continue
        event_id = str(raw.get("ground_truth_event_id") or "").strip()
        if not event_id:
            errors.append(_issue(f"{prefix}.ground_truth_event_id", "required", "event id is required"))
        if event_id in event_ids:
            errors.append(_issue(f"{prefix}.ground_truth_event_id", "duplicate", "event id is duplicated"))
        event_ids.add(event_id)
        line_id = str(raw.get("counting_line_id") or "").strip()
        if expected_lines and line_id not in expected_lines:
            errors.append(_issue(f"{prefix}.counting_line_id", "unknown_line", "line is not present in the scene revision"))
        direction = raw.get("canonical_direction")
        if direction not in VALID_CANONICAL_DIRECTIONS:
            errors.append(_issue(f"{prefix}.canonical_direction", "invalid_direction", "direction must be A_TO_B or B_TO_A"))
        pts = _as_int(raw.get("crossing_pts_ms"), f"{prefix}.crossing_pts_ms", errors, minimum=0)
        if pts is not None and source_start is not None and pts < source_start:
            errors.append(_issue(f"{prefix}.crossing_pts_ms", "outside_analysis_window", "timestamp is before the analysis window"))
        if pts is not None and source_end is not None and pts >= source_end:
            errors.append(_issue(f"{prefix}.crossing_pts_ms", "outside_analysis_window", "timestamp is at or after the half-open analysis window"))
        engineering_class = str(raw.get("engineering_class") or "")
        if engineering_class not in ENGINEERING_CLASSES:
            errors.append(_issue(f"{prefix}.engineering_class", "unsupported_class", "class is not in the active engineering taxonomy"))
        classification_status = str(raw.get("classification_status") or "")
        if classification_status not in GROUND_TRUTH_CLASS_STATUSES:
            errors.append(_issue(f"{prefix}.classification_status", "unsupported_status", "classification status is not recognized"))
        elif classification_status in STATUS_ALLOWED_ENGINEERING_CLASSES:
            consistency_error = classification_consistency_error(classification_status, engineering_class)
            if consistency_error:
                errors.append(_issue(f"{prefix}.engineering_class", "classification_status_mismatch", consistency_error))
        timestamp_status = str(raw.get("timestamp_status") or "")
        if timestamp_status not in TIMESTAMP_STATUSES:
            errors.append(_issue(f"{prefix}.timestamp_status", "unsupported_status", "timestamp status is not recognized"))
        annotation_status = str(raw.get("annotation_status") or "")
        if annotation_status not in ANNOTATION_STATUSES:
            errors.append(_issue(f"{prefix}.annotation_status", "unsupported_status", "annotation status is not recognized"))
        evidence_refs = _bounded_json(raw.get("evidence_refs", {}), f"{prefix}.evidence_refs", errors)
        if pts is not None and direction in VALID_CANONICAL_DIRECTIONS:
            candidate_key = (line_id, str(direction), pts)
            if candidate_key in candidate_keys:
                errors.append(_issue(prefix, "duplicate_event_candidate", "another annotation has the same line, direction and PTS"))
            candidate_keys.add(candidate_key)
        events.append(
            {
                "ground_truth_event_id": event_id,
                "benchmark_source_id": str(payload.get("benchmark_source_id") or source.get("benchmark_source_id") if source else payload.get("benchmark_source_id") or ""),
                "source_fingerprint_sha256": payload_fingerprint,
                "ground_truth_revision": revision,
                "scene_revision": scene_revision,
                "counting_line_id": line_id,
                "canonical_direction": direction,
                "crossing_pts_ms": pts or 0,
                "engineering_class": engineering_class,
                "classification_status": classification_status,
                "timestamp_status": timestamp_status,
                "annotation_status": annotation_status,
                "evidence_refs": evidence_refs if isinstance(evidence_refs, (dict, list)) else {},
                "notes": str(raw.get("notes") or "")[:2000],
                "created_by": str(raw.get("created_by") or payload.get("created_by") or ""),
                "created_at": str(raw.get("created_at") or payload.get("created_at") or now_iso()),
                "source_frame_index": int(raw["source_frame_index"]) if raw.get("source_frame_index") is not None and str(raw["source_frame_index"]).lstrip("-").isdigit() else None,
                "source_frame_pts_ms": int(raw["source_frame_pts_ms"]) if raw.get("source_frame_pts_ms") is not None and str(raw["source_frame_pts_ms"]).lstrip("-").isdigit() else None,
                "frame_start_index": int(raw["frame_start_index"]) if raw.get("frame_start_index") is not None and str(raw["frame_start_index"]).lstrip("-").isdigit() else None,
                "frame_end_index": int(raw["frame_end_index"]) if raw.get("frame_end_index") is not None and str(raw["frame_end_index"]).lstrip("-").isdigit() else None,
                "identity_id": str(raw.get("identity_id")) if raw.get("identity_id") is not None else None,
            }
        )
    reviewer_annotations = _validate_reviewer_annotations(payload.get("reviewer_annotations", []), event_ids, errors)
    if errors:
        raise BenchmarkValidationError("invalid ground truth manifest", errors)
    return {
        "ground_truth_manifest_version": GROUND_TRUTH_MANIFEST_VERSION,
        "benchmark_source_id": str(payload.get("benchmark_source_id") or ""),
        "source_fingerprint_sha256": payload_fingerprint,
        "ground_truth_revision": revision,
        "parent_revision_id": str(payload.get("parent_revision_id")) if payload.get("parent_revision_id") else None,
        "scene_revision": scene_revision,
        "status": str(payload.get("status") or "IMPORTED"),
        "created_by": str(payload.get("created_by") or ""),
        "created_at": str(payload.get("created_at") or now_iso()),
        "analysis_window_start_pts_ms": source_start,
        "analysis_window_end_pts_ms": source_end,
        "events": events,
        "reviewer_annotations": reviewer_annotations,
        "reviewer_agreement": reviewer_agreement(reviewer_annotations),
        "notes": _bounded_json(payload.get("notes", {}), "notes", errors),
    }


def validate_ground_truth_manifest(
    payload: Mapping[str, Any], *, source: Mapping[str, Any] | None = None, counting_line_ids: Iterable[str] = ()
) -> dict[str, Any]:
    try:
        normalized = parse_ground_truth_manifest(payload, source=source, counting_line_ids=counting_line_ids)
    except BenchmarkValidationError as exc:
        return {"valid": False, "errors": list(exc.errors) or [{"code": "invalid_ground_truth", "message": str(exc)}]}
    return {"valid": True, "errors": [], "ground_truth": normalized, "content_hash": canonical_hash(normalized)}


def eligible_ground_truth(event: Mapping[str, Any]) -> bool:
    return event.get("annotation_status") == "VALID" and event.get("timestamp_status") == "CONFIRMED"


def eligible_automatic_event(event: Mapping[str, Any]) -> bool:
    return not bool(event.get("stale")) and not bool(event.get("structurally_invalid")) and not bool(event.get("invalid_direction"))


@dataclass(frozen=True)
class MatchingPolicy:
    match_tolerance_ms: int = 500
    direction_matching_mode: str = "EXACT_CANONICAL"
    class_evaluation_mode: str = "SECONDARY_ON_EVENT_MATCH"
    ignore_region_policy: str = "ANNOTATION_STATUS"
    timestamp_outlier_ms: int = 1_000
    interval_bucket_minutes: int = 15
    revision: str = MATCHING_POLICY_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "evaluation_config_version": EVALUATION_CONFIG_VERSION,
            "revision": self.revision,
            "match_tolerance_ms": self.match_tolerance_ms,
            "direction_matching_mode": self.direction_matching_mode,
            "class_evaluation_mode": self.class_evaluation_mode,
            "ignore_region_policy": self.ignore_region_policy,
            "timestamp_outlier_ms": self.timestamp_outlier_ms,
            "interval_bucket_minutes": self.interval_bucket_minutes,
        }

    @property
    def content_hash(self) -> str:
        return canonical_hash(self.as_dict())


def parse_matching_policy(payload: Mapping[str, Any] | None = None) -> MatchingPolicy:
    raw = dict(payload or {})
    errors: list[dict[str, str]] = []
    values: dict[str, Any] = {}
    for name, default in (
        ("match_tolerance_ms", 500),
        ("timestamp_outlier_ms", 1_000),
        ("interval_bucket_minutes", 15),
    ):
        try:
            values[name] = int(raw.get(name, default))
        except (TypeError, ValueError):
            errors.append(_issue(name, "invalid_integer", "must be an integer"))
            values[name] = default
        if values[name] < 0 or (name == "interval_bucket_minutes" and values[name] < 1):
            errors.append(_issue(name, "invalid_value", "must be non-negative and interval minutes must be positive"))
    for name, default in (
        ("direction_matching_mode", "EXACT_CANONICAL"),
        ("class_evaluation_mode", "SECONDARY_ON_EVENT_MATCH"),
        ("ignore_region_policy", "ANNOTATION_STATUS"),
    ):
        values[name] = str(raw.get(name, default))
    values["revision"] = str(raw.get("revision") or MATCHING_POLICY_VERSION)
    if errors:
        raise BenchmarkValidationError("invalid matching policy", errors)
    return MatchingPolicy(**values)


def _event_id(event: Mapping[str, Any], fallback: str) -> str:
    return str(event.get("source_event_id") or event.get("event_id") or event.get("ground_truth_event_id") or fallback)


def _pts(event: Mapping[str, Any]) -> int:
    value = event.get("event_pts_ms", event.get("crossing_pts_ms", event.get("crossing_timestamp_ms", event.get("pts_ms", 0))))
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _same_context(predicted: Mapping[str, Any], truth: Mapping[str, Any]) -> bool:
    pred_source = str(predicted.get("source_fingerprint_sha256") or predicted.get("media_fingerprint") or "")
    truth_source = str(truth.get("source_fingerprint_sha256") or truth.get("media_fingerprint") or "")
    if not pred_source or not truth_source or pred_source != truth_source:
        return False
    return (
        str(predicted.get("scene_revision") or "") == str(truth.get("scene_revision") or "")
        and str(predicted.get("counting_line_id") or predicted.get("line_id") or "")
        == str(truth.get("counting_line_id") or truth.get("line_id") or "")
    )


def _direction(event: Mapping[str, Any]) -> str | None:
    return event.get("canonical_direction") or event.get("direction") or event.get("crossing_direction")


def _line(event: Mapping[str, Any]) -> str:
    return str(event.get("counting_line_id") or event.get("line_id") or "")


def _class(event: Mapping[str, Any]) -> str:
    return str(event.get("engineering_class") or event.get("class") or event.get("observable_class") or "UNKNOWN")


def _hungarian(cost: Sequence[Sequence[int]]) -> list[int]:
    """Return a minimum-cost column assignment for each row (O(n^3))."""

    rows = len(cost)
    if rows == 0:
        return []
    columns = len(cost[0])
    if columns < rows:
        raise ValueError("assignment matrix must have at least as many columns as rows")
    u = [0] * (rows + 1)
    v = [0] * (columns + 1)
    p = [0] * (columns + 1)
    way = [0] * (columns + 1)
    for i in range(1, rows + 1):
        p[0] = i
        j0 = 0
        minv = [10**40] * (columns + 1)
        used = [False] * (columns + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = 10**40
            j1 = 0
            for j in range(1, columns + 1):
                if used[j]:
                    continue
                current = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if current < minv[j] or (current == minv[j] and j < j1):
                    minv[j] = current
                    way[j] = j0
                if minv[j] < delta or (minv[j] == delta and j < j1):
                    delta = minv[j]
                    j1 = j
            for j in range(columns + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    assignment = [-1] * rows
    for j in range(1, columns + 1):
        if p[j] > 0:
            assignment[p[j] - 1] = j - 1
    return assignment


def _assign_pairs(
    predicted: Sequence[Mapping[str, Any]],
    truth: Sequence[Mapping[str, Any]],
    predicate: Callable[[Mapping[str, Any], Mapping[str, Any]], bool],
) -> list[tuple[int, int]]:
    if not predicted or not truth:
        return []
    ordered_predicted = sorted(enumerate(predicted), key=lambda item: (_event_id(item[1], str(item[0])), str(item[1].get("technical_key", ""))))
    ordered_truth = sorted(enumerate(truth), key=lambda item: (_event_id(item[1], str(item[0])), str(item[1].get("technical_key", ""))))
    valid: dict[tuple[int, int], bool] = {}
    unmatched_cost = 10**30
    matrix: list[list[int]] = []
    for pred_rank, (_pred_index, pred) in enumerate(ordered_predicted):
        row: list[int] = []
        for truth_rank, (_truth_index, target) in enumerate(ordered_truth):
            allowed = predicate(pred, target)
            valid[(pred_rank, truth_rank)] = allowed
            difference = abs(_pts(pred) - _pts(target))
            row.append(difference * 1_000_000 + pred_rank * (len(ordered_truth) + 1) + truth_rank if allowed else unmatched_cost + pred_rank + truth_rank)
        row.extend([unmatched_cost + pred_rank] * len(ordered_predicted))
        matrix.append(row)
    assignment = _hungarian(matrix)
    pairs: list[tuple[int, int]] = []
    for pred_rank, column in enumerate(assignment):
        if 0 <= column < len(ordered_truth) and valid.get((pred_rank, column), False):
            pairs.append((ordered_predicted[pred_rank][0], ordered_truth[column][0]))
    return pairs


def match_events(
    predicted_events: Sequence[Mapping[str, Any]],
    ground_truth_events: Sequence[Mapping[str, Any]],
    policy: MatchingPolicy | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    matching_policy = policy if isinstance(policy, MatchingPolicy) else parse_matching_policy(policy)
    predicted = [dict(event) for event in predicted_events]
    truth = [dict(event) for event in ground_truth_events]
    scored_predicted = [event for event in predicted if eligible_automatic_event(event)]
    scored_truth = [event for event in truth if eligible_ground_truth(event)]
    ignored_truth = [event for event in truth if not eligible_ground_truth(event)]
    tolerance = matching_policy.match_tolerance_ms

    def exact_candidate(pred: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
        return (
            _same_context(pred, target)
            and _direction(pred) in VALID_CANONICAL_DIRECTIONS
            and _direction(pred) == _direction(target)
            and abs(_pts(pred) - _pts(target)) <= tolerance
        )

    exact_pairs = _assign_pairs(scored_predicted, scored_truth, exact_candidate)
    matched_pred = {pred_index for pred_index, _ in exact_pairs}
    matched_truth = {truth_index for _, truth_index in exact_pairs}
    matches: list[dict[str, Any]] = []
    for pred_index, truth_index in exact_pairs:
        pred = scored_predicted[pred_index]
        target = scored_truth[truth_index]
        signed_error = _pts(pred) - _pts(target)
        flags: list[str] = []
        if _class(pred) != _class(target):
            flags.append("CLASS_ERROR")
        if abs(signed_error) > matching_policy.timestamp_outlier_ms:
            flags.append("TIMESTAMP_OUTLIER")
        matches.append(
            {
                "automatic_event_id": _event_id(pred, f"automatic-{pred_index}"),
                "ground_truth_event_id": _event_id(target, f"truth-{truth_index}"),
                "primary_category": "TRUE_POSITIVE",
                "secondary_error_flags": flags,
                "timestamp_error_ms": signed_error,
                "absolute_timestamp_error_ms": abs(signed_error),
                "line_id": _line(pred),
                "automatic_direction": _direction(pred),
                "ground_truth_direction": _direction(target),
                "automatic_class": _class(pred),
                "ground_truth_class": _class(target),
                "automatic_track_id": pred.get("track_id"),
                "technical_key": pred.get("technical_key"),
                "duplicate_event_ids": [],
            }
        )

    remaining_pred_indices = [index for index in range(len(scored_predicted)) if index not in matched_pred]
    remaining_truth_indices = [index for index in range(len(scored_truth)) if index not in matched_truth]
    remaining_pred = [scored_predicted[index] for index in remaining_pred_indices]
    remaining_truth = [scored_truth[index] for index in remaining_truth_indices]

    def direction_error_candidate(pred: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
        return (
            _same_context(pred, target)
            and _direction(pred) in VALID_CANONICAL_DIRECTIONS
            and _direction(target) in VALID_CANONICAL_DIRECTIONS
            and _direction(pred) != _direction(target)
            and abs(_pts(pred) - _pts(target)) <= tolerance
        )

    direction_pairs = _assign_pairs(remaining_pred, remaining_truth, direction_error_candidate)
    diagnostic_pred = {remaining_pred_indices[pred_index] for pred_index, _ in direction_pairs}
    diagnostic_truth = {remaining_truth_indices[truth_index] for _, truth_index in direction_pairs}
    for pred_index, truth_index in direction_pairs:
        pred = scored_predicted[pred_index]
        target = scored_truth[truth_index]
        signed_error = _pts(pred) - _pts(target)
        flags: list[str] = []
        if _class(pred) != _class(target):
            flags.append("CLASS_ERROR")
        if abs(signed_error) > matching_policy.timestamp_outlier_ms:
            flags.append("TIMESTAMP_OUTLIER")
        matches.append(
            {
                "automatic_event_id": _event_id(pred, f"automatic-{pred_index}"),
                "ground_truth_event_id": _event_id(target, f"truth-{truth_index}"),
                "primary_category": "DIRECTION_ERROR",
                "secondary_error_flags": flags,
                "timestamp_error_ms": signed_error,
                "absolute_timestamp_error_ms": abs(signed_error),
                "line_id": _line(pred),
                "automatic_direction": _direction(pred),
                "ground_truth_direction": _direction(target),
                "automatic_class": _class(pred),
                "ground_truth_class": _class(target),
                "automatic_track_id": pred.get("track_id"),
                "technical_key": pred.get("technical_key"),
                "duplicate_event_ids": [],
            }
        )

    consumed_truth_pairs = [*exact_pairs, *direction_pairs]
    matched_truth_by_candidate: dict[int, list[int]] = defaultdict(list)
    for _pred_index, target_index in consumed_truth_pairs:
        target = scored_truth[target_index]
        for other_index, other in enumerate(scored_predicted):
            if other_index in matched_pred or other_index in diagnostic_pred:
                continue
            if _same_context(other, target) and abs(_pts(other) - _pts(target)) <= tolerance:
                matched_truth_by_candidate[other_index].append(target_index)

    false_positive_ids: list[str] = []
    duplicate_ids: list[str] = []
    direction_error_ids = {record["automatic_event_id"] for record in matches if record["primary_category"] == "DIRECTION_ERROR"}
    for index, pred in enumerate(scored_predicted):
        if index in matched_pred or index in diagnostic_pred:
            continue
        candidates = matched_truth_by_candidate.get(index, [])
        if candidates:
            target_index = min(
                candidates,
                key=lambda item: (
                    0 if _direction(pred) == _direction(scored_truth[item]) else 1,
                    abs(_pts(pred) - _pts(scored_truth[item])),
                    _event_id(scored_truth[item], str(item)),
                ),
            )
            target = scored_truth[target_index]
            direction_flag = any(direction_error_candidate(pred, scored_truth[item]) for item in candidates)
            duplicate_ids.append(_event_id(pred, f"automatic-{index}"))
            matches.append(
                {
                    "automatic_event_id": _event_id(pred, f"automatic-{index}"),
                    "ground_truth_event_id": _event_id(target, f"truth-{target_index}"),
                    "primary_category": "DUPLICATE_AUTOMATIC",
                    "secondary_error_flags": ["DIRECTION_ERROR"] if direction_flag else [],
                    "timestamp_error_ms": None,
                    "absolute_timestamp_error_ms": None,
                    "line_id": _line(pred),
                    "automatic_direction": _direction(pred),
                    "ground_truth_direction": _direction(target),
                    "automatic_class": _class(pred),
                    "ground_truth_class": _class(target),
                    "automatic_track_id": pred.get("track_id"),
                    "technical_key": pred.get("technical_key"),
                    "duplicate_event_ids": [
                        _event_id(other, f"automatic-{other_index}")
                        for other_index, other in enumerate(scored_predicted)
                        if other_index != index
                        and (_same_context(other, target) and abs(_pts(other) - _pts(target)) <= tolerance)
                    ],
                }
            )
        else:
            false_positive_ids.append(_event_id(pred, f"automatic-{index}"))
            matches.append(
                {
                    "automatic_event_id": _event_id(pred, f"automatic-{index}"),
                    "ground_truth_event_id": None,
                    "primary_category": "FALSE_POSITIVE",
                    "secondary_error_flags": [],
                    "timestamp_error_ms": None,
                    "absolute_timestamp_error_ms": None,
                    "line_id": _line(pred),
                    "automatic_direction": _direction(pred),
                    "ground_truth_direction": None,
                    "automatic_class": _class(pred),
                    "ground_truth_class": None,
                    "automatic_track_id": pred.get("track_id"),
                    "technical_key": pred.get("technical_key"),
                    "duplicate_event_ids": [],
                }
            )

    false_negative_ids: list[str] = []
    for index, target in enumerate(scored_truth):
        if index in matched_truth or index in diagnostic_truth:
            continue
        event_id = _event_id(target, f"truth-{index}")
        false_negative_ids.append(event_id)
        matches.append(
            {
                "automatic_event_id": None,
                "ground_truth_event_id": event_id,
                "primary_category": "FALSE_NEGATIVE",
                "secondary_error_flags": [],
                "timestamp_error_ms": None,
                "absolute_timestamp_error_ms": None,
                "line_id": _line(target),
                "automatic_direction": None,
                "ground_truth_direction": _direction(target),
                "automatic_class": None,
                "ground_truth_class": _class(target),
                "automatic_track_id": None,
                "technical_key": None,
                "duplicate_event_ids": [],
            }
        )
    for target in ignored_truth:
        matches.append(
            {
                "automatic_event_id": None,
                "ground_truth_event_id": _event_id(target, "ignored-truth"),
                "primary_category": "IGNORED" if target.get("annotation_status") == "IGNORE" else "UNSCORABLE",
                "secondary_error_flags": [],
                "timestamp_error_ms": None,
                "absolute_timestamp_error_ms": None,
                "line_id": _line(target),
                "automatic_direction": None,
                "ground_truth_direction": _direction(target),
                "automatic_class": None,
                "ground_truth_class": _class(target),
                "automatic_track_id": None,
                "technical_key": None,
                "duplicate_event_ids": [],
            }
        )
    for record in matches:
        record["categories"] = [record["primary_category"], *record.get("secondary_error_flags", [])]
        record["consumes_ground_truth"] = record["primary_category"] in {"TRUE_POSITIVE", "DIRECTION_ERROR", "FALSE_NEGATIVE"}
        record["consumes_automatic"] = record["primary_category"] in {"TRUE_POSITIVE", "DIRECTION_ERROR", "DUPLICATE_AUTOMATIC", "FALSE_POSITIVE"}
    matches.sort(key=lambda record: (str(record.get("ground_truth_event_id") or ""), str(record.get("automatic_event_id") or ""), record["primary_category"]))
    category_counts = Counter(record["primary_category"] for record in matches)
    truth_accounted = category_counts["TRUE_POSITIVE"] + category_counts["DIRECTION_ERROR"] + category_counts["FALSE_NEGATIVE"]
    automatic_accounted = category_counts["TRUE_POSITIVE"] + category_counts["DIRECTION_ERROR"] + category_counts["DUPLICATE_AUTOMATIC"] + category_counts["FALSE_POSITIVE"]
    consuming_truth_ids = [record["ground_truth_event_id"] for record in matches if record["consumes_ground_truth"] and record.get("ground_truth_event_id")]
    consuming_automatic_ids = [record["automatic_event_id"] for record in matches if record["consumes_automatic"] and record.get("automatic_event_id")]
    accounting_invariants = {
        "ground_truth_primary_categories_accounted": {
            "expected": len(scored_truth),
            "actual": truth_accounted,
            "passed": truth_accounted == len(scored_truth),
        },
        "automatic_primary_categories_accounted": {
            "expected": len(scored_predicted),
            "actual": automatic_accounted,
            "passed": automatic_accounted == len(scored_predicted),
        },
        "ground_truth_consumed_at_most_once": {
            "expected": len(consuming_truth_ids),
            "actual": len(set(consuming_truth_ids)),
            "passed": len(consuming_truth_ids) == len(set(consuming_truth_ids)),
        },
        "automatic_consumed_at_most_once": {
            "expected": len(consuming_automatic_ids),
            "actual": len(set(consuming_automatic_ids)),
            "passed": len(consuming_automatic_ids) == len(set(consuming_automatic_ids)),
        },
    }
    return {
        "schema_version": "event-match-v1",
        "policy": matching_policy.as_dict(),
        "matches": matches,
        "true_positive": sum(record["primary_category"] == "TRUE_POSITIVE" for record in matches),
        "false_positive_event_ids": sorted(set(false_positive_ids)),
        "false_negative_truth_ids": sorted(set(false_negative_ids)),
        "duplicate_automatic_event_ids": sorted(set(duplicate_ids)),
        "direction_error_event_ids": sorted(direction_error_ids),
        "ignored_event_ids": sorted(_event_id(event, "ignored") for event in ignored_truth if event.get("annotation_status") == "IGNORE"),
        "unscorable_event_ids": sorted(_event_id(event, "unscorable") for event in ignored_truth if event.get("annotation_status") != "IGNORE"),
        "predicted_count": len(scored_predicted),
        "ground_truth_count": len(scored_truth),
        "primary_category_counts": dict(sorted(category_counts.items())),
        "accounting_invariants": accounting_invariants,
    }


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _error_row(automatic: int, truth: int) -> dict[str, Any]:
    signed = automatic - truth
    return {
        "automatic_count": automatic,
        "ground_truth_count": truth,
        "signed_error": signed,
        "absolute_error": abs(signed),
        "absolute_percentage_error": None if truth == 0 else abs(signed) / truth,
        "percentage_error_status": "UNDEFINED_ZERO_GROUND_TRUTH" if truth == 0 else "DEFINED",
    }


def _group_error(predicted: Sequence[Mapping[str, Any]], truth: Sequence[Mapping[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    pred_counts = Counter(str(event.get(key) or event.get("class") if key == "engineering_class" else event.get(key) or "") for event in predicted)
    truth_counts = Counter(str(event.get(key) or "") for event in truth)
    labels = sorted(set(pred_counts) | set(truth_counts))
    return {label: _error_row(pred_counts[label], truth_counts[label]) for label in labels}


def _interval_index(pts: int, intervals: Sequence[Mapping[str, Any]]) -> int | None:
    for interval in intervals:
        start = int(interval.get("source_relative_start_pts_ms", interval.get("start_pts_ms", 0)))
        end = int(interval.get("source_relative_end_pts_ms", interval.get("end_pts_ms", 0)))
        if start <= pts < end:
            return int(interval.get("interval_index", interval.get("index", 0)))
    return None


def count_metrics(
    predicted_events: Sequence[Mapping[str, Any]],
    truth_events: Sequence[Mapping[str, Any]],
    intervals: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    predicted = [event for event in predicted_events if eligible_automatic_event(event)]
    truth = [event for event in truth_events if eligible_ground_truth(event)]
    interval_rows: list[dict[str, Any]] = []
    interval_ids = sorted({int(item.get("interval_index", item.get("index", 0))) for item in intervals})
    for interval_id in interval_ids:
        pred_interval = [event for event in predicted if _interval_index(_pts(event), intervals) == interval_id]
        truth_interval = [event for event in truth if _interval_index(_pts(event), intervals) == interval_id]
        row = _error_row(len(pred_interval), len(truth_interval))
        row["interval_index"] = interval_id
        row["class_breakdown"] = _group_error(pred_interval, truth_interval, "engineering_class")
        row["direction_breakdown"] = _group_error(pred_interval, truth_interval, "canonical_direction")
        row["line_breakdown"] = _group_error(pred_interval, truth_interval, "counting_line_id")
        interval_rows.append(row)
    timeline: list[dict[str, Any]] = []
    for pts_ms in sorted({_pts(event) for event in [*predicted, *truth]}):
        automatic_cumulative = sum(_pts(event) <= pts_ms for event in predicted)
        ground_truth_cumulative = sum(_pts(event) <= pts_ms for event in truth)
        timeline.append(
            {
                "pts_ms": pts_ms,
                "automatic_cumulative": automatic_cumulative,
                "ground_truth_cumulative": ground_truth_cumulative,
                "cumulative_difference": automatic_cumulative - ground_truth_cumulative,
            }
        )
    return {
        "total": _error_row(len(predicted), len(truth)),
        "by_interval": interval_rows,
        "by_line": _group_error(predicted, truth, "counting_line_id"),
        "by_direction": _group_error(predicted, truth, "canonical_direction"),
        "by_class": _group_error(predicted, truth, "engineering_class"),
        "cumulative_count_difference": timeline,
    }


def event_metrics(match_result: Mapping[str, Any]) -> dict[str, Any]:
    records = list(match_result.get("matches", []))
    true_positive = sum(record.get("primary_category") == "TRUE_POSITIVE" for record in records)
    direction_errors = sum(record.get("primary_category") == "DIRECTION_ERROR" for record in records)
    duplicates = sum(record.get("primary_category") == "DUPLICATE_AUTOMATIC" for record in records)
    false_positives = sum(record.get("primary_category") == "FALSE_POSITIVE" for record in records)
    false_negatives = sum(record.get("primary_category") == "FALSE_NEGATIVE" for record in records)
    primary_false_positive = false_positives + duplicates + direction_errors
    primary_false_negative = false_negatives + direction_errors
    precision = _ratio(true_positive, true_positive + primary_false_positive)
    recall = _ratio(true_positive, true_positive + primary_false_negative)
    f1 = None if precision is None or recall is None or precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return {
        "sample_sizes": {
            "predicted": int(match_result.get("predicted_count", 0)),
            "ground_truth": int(match_result.get("ground_truth_count", 0)),
            "matched_same_direction": true_positive,
        },
        "true_positive": true_positive,
        "false_positive": primary_false_positive,
        "false_positive_plain": false_positives,
        "false_negative": primary_false_negative,
        "false_negative_plain": false_negatives,
        "direction_errors": direction_errors,
        "direction_error_secondary_flags": sum("DIRECTION_ERROR" in record.get("secondary_error_flags", []) for record in records),
        "duplicate_automatic": duplicates,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "denominators": {
            "precision": true_positive + primary_false_positive,
            "recall": true_positive + primary_false_negative,
            "duplicate_rate": int(match_result.get("predicted_count", 0)),
            "miss_rate": int(match_result.get("ground_truth_count", 0)),
        },
        "denominator_zero_behavior": "null_metric_with_explicit_sample_count",
        "primary_category_counts": dict(match_result.get("primary_category_counts", {})),
        "accounting_invariants": dict(match_result.get("accounting_invariants", {})),
    }


def direction_metrics(match_result: Mapping[str, Any]) -> dict[str, Any]:
    records = [record for record in match_result.get("matches", []) if record.get("primary_category") in {"TRUE_POSITIVE", "DIRECTION_ERROR"}]
    correct = sum(record.get("primary_category") == "TRUE_POSITIVE" for record in records)
    errors = sum(record.get("primary_category") == "DIRECTION_ERROR" for record in records)
    secondary_errors = sum("DIRECTION_ERROR" in record.get("secondary_error_flags", []) for record in match_result.get("matches", []))
    confusion: dict[str, dict[str, int]] = {direction: {target: 0 for target in sorted(VALID_CANONICAL_DIRECTIONS)} for direction in sorted(VALID_CANONICAL_DIRECTIONS)}
    for record in records:
        automatic = record.get("automatic_direction")
        truth = record.get("ground_truth_direction")
        if automatic in confusion and truth in VALID_CANONICAL_DIRECTIONS:
            confusion[automatic][truth] += 1
    return {
        "correct_direction_matches": correct,
        "direction_errors": errors,
        "direction_error_secondary_flags": secondary_errors,
        "direction_accuracy": _ratio(correct, correct + errors),
        "confusion": confusion,
        "denominator": correct + errors,
    }


def _per_class_metrics(matrix: Mapping[str, Mapping[str, int]], classes: Sequence[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for label in classes:
        tp = int(matrix.get(label, {}).get(label, 0))
        truth_support = sum(int(matrix.get(label, {}).get(predicted_label, 0)) for predicted_label in classes)
        predicted_support = sum(int(matrix.get(truth_label, {}).get(label, 0)) for truth_label in classes)
        precision = _ratio(tp, predicted_support)
        recall = _ratio(tp, truth_support)
        f1 = None if precision is None or recall is None or precision + recall == 0 else 2 * precision * recall / (precision + recall)
        result[label] = {"support": truth_support, "predicted_support": predicted_support, "true_positive": tp, "precision": precision, "recall": recall, "f1": f1}
    return result


def class_metrics(
    predicted_events: Sequence[Mapping[str, Any]],
    truth_events: Sequence[Mapping[str, Any]],
    match_result: Mapping[str, Any],
) -> dict[str, Any]:
    classes = sorted(ENGINEERING_CLASSES | {_class(event) for event in predicted_events} | {_class(event) for event in truth_events})
    matrix: dict[str, dict[str, int]] = {truth_label: {pred_label: 0 for pred_label in classes} for truth_label in classes}
    matched = [record for record in match_result.get("matches", []) if record.get("primary_category") == "TRUE_POSITIVE" and record.get("ground_truth_event_id")]
    for record in matched:
        matrix[str(record.get("ground_truth_class") or "UNKNOWN")][str(record.get("automatic_class") or "UNKNOWN")] += 1
    per_class = _per_class_metrics(matrix, classes)
    supported = [value["f1"] for value in per_class.values() if value["f1"] is not None]
    supports = [value["support"] for value in per_class.values()]
    weighted_denominator = sum(supports)
    weighted = _ratio(sum(value["f1"] * value["support"] for value in per_class.values() if value["f1"] is not None), weighted_denominator)
    matched_count = len(matched)
    unknown_ambiguous = sum(record.get("automatic_class") in {"UNKNOWN", "AMBIGUOUS"} for record in matched)
    heavy_truth = {"HEAVY_VEHICLE_UNSPECIFIED", "BUS"}
    heavy_errors = sum(record.get("ground_truth_class") in heavy_truth and record.get("automatic_class") != record.get("ground_truth_class") for record in matched)
    unsupported = sorted({str(event.get("engineering_class") or event.get("class") or "") for event in predicted_events if str(event.get("engineering_class") or event.get("class") or "") not in ENGINEERING_CLASSES})
    return {
        "active_taxonomy": "pilot-observable-taxonomy-v1",
        "confusion_matrix": matrix,
        "per_class": per_class,
        "macro_f1": mean(supported) if supported else None,
        "weighted_f1": weighted,
        "unknown_ambiguous_rate": _ratio(unknown_ambiguous, matched_count),
        "unknown_ambiguous_count": unknown_ambiguous,
        "matched_support": matched_count,
        "provisional_heavy_vehicle_error": heavy_errors,
        "unsupported_class_diagnostics": unsupported,
        "does_not_claim_tims_13_class_accuracy": True,
    }


def timestamp_metrics(
    predicted_events: Sequence[Mapping[str, Any]],
    truth_events: Sequence[Mapping[str, Any]],
    match_result: Mapping[str, Any],
) -> dict[str, Any]:
    pred_by_id = {_event_id(event, str(index)): event for index, event in enumerate(predicted_events)}
    truth_by_id = {_event_id(event, str(index)): event for index, event in enumerate(truth_events)}
    rows = []
    for record in match_result.get("matches", []):
        if record.get("primary_category") != "TRUE_POSITIVE":
            continue
        if record.get("automatic_event_id") not in pred_by_id or record.get("ground_truth_event_id") not in truth_by_id:
            continue
        signed = _pts(pred_by_id[record["automatic_event_id"]]) - _pts(truth_by_id[record["ground_truth_event_id"]])
        rows.append({"signed_error_ms": signed, "absolute_error_ms": abs(signed), "line_id": _line(pred_by_id[record["automatic_event_id"]]), "configuration": predicted_events[0].get("configuration_revision") if predicted_events else None})
    values = [row["absolute_error_ms"] for row in rows]
    by_line: dict[str, dict[str, Any]] = {}
    for line in sorted({str(row["line_id"]) for row in rows}):
        line_values = [row["absolute_error_ms"] for row in rows if row["line_id"] == line]
        by_line[line] = {"count": len(line_values), "mean_absolute_error_ms": mean(line_values) if line_values else None, "median_absolute_error_ms": median(line_values) if line_values else None, "p95_absolute_error_ms": percentile(line_values, 0.95)}
    return {
        "match_count": len(rows),
        "signed_timestamp_error_ms": [row["signed_error_ms"] for row in rows],
        "absolute_timestamp_error_ms": values,
        "mean_absolute_error_ms": mean(values) if values else None,
        "median_absolute_error_ms": median(values) if values else None,
        "p90_absolute_error_ms": percentile(values, 0.90),
        "p95_absolute_error_ms": percentile(values, 0.95),
        "maximum_absolute_error_ms": max(values) if values else None,
        "by_line_and_configuration": by_line,
        "time_authority": "source_relative_pts_ms",
        "frame_index_fps_substitution": False,
    }


def percentile(values: Sequence[float | int], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def interval_metrics(
    predicted_events: Sequence[Mapping[str, Any]],
    truth_events: Sequence[Mapping[str, Any]],
    intervals: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = count_metrics(predicted_events, truth_events, intervals)["by_interval"]
    return rows


def fragmentation_metrics(
    predicted_events: Sequence[Mapping[str, Any]],
    truth_events: Sequence[Mapping[str, Any]],
    match_result: Mapping[str, Any],
    *,
    short_track_threshold: int = 2,
) -> dict[str, Any]:
    truth_by_id = {_event_id(event, str(index)): event for index, event in enumerate(truth_events)}
    identity_records: dict[str, set[str]] = defaultdict(set)
    matched_event_counts: Counter[str] = Counter()
    for record in match_result.get("matches", []):
        truth_id = record.get("ground_truth_event_id")
        if not truth_id or truth_id not in truth_by_id:
            continue
        identity = truth_by_id[truth_id].get("identity_id")
        track = record.get("automatic_track_id")
        if identity and track:
            identity_records[str(identity)].add(str(track))
            matched_event_counts[str(identity)] += 1
    observation_counts = [int(event["track_observation_count"]) for event in predicted_events if event.get("track_observation_count") is not None]
    durations = [int(event["track_duration_ms"]) for event in predicted_events if event.get("track_duration_ms") is not None]
    if not identity_records:
        identity_available = any(event.get("identity_id") for event in truth_events)
        return {
            "availability": "UNAVAILABLE" if not identity_available else "NO_MATCHED_IDENTITIES",
            "reason": "ground-truth identity_id is required; event totals alone cannot estimate fragmentation",
            "ground_truth_vehicle_count": 0,
            "fragmented_ground_truth_vehicle_count": None,
            "fragmented_ground_truth_rate": None,
            "short_track_count": sum(count < short_track_threshold for count in observation_counts) if observation_counts else None,
            "short_track_rate": _ratio(sum(count < short_track_threshold for count in observation_counts), len(observation_counts)) if observation_counts else None,
            "observation_count_distribution": distribution(observation_counts),
            "track_duration_distribution_ms": distribution(durations),
            "repeated_crossing_risk_count": None,
        }
    fragmented = sum(len(tracks) > 1 for tracks in identity_records.values())
    repeated = sum(count > 1 for count in matched_event_counts.values())
    return {
        "availability": "AVAILABLE",
        "reason": None,
        "ground_truth_vehicle_count": len(identity_records),
        "automatic_track_ids_by_ground_truth_vehicle": {identity: sorted(tracks) for identity, tracks in sorted(identity_records.items())},
        "tracks_per_ground_truth_vehicle": {identity: len(tracks) for identity, tracks in sorted(identity_records.items())},
        "fragmented_ground_truth_vehicle_count": fragmented,
        "fragmentation_count": sum(max(0, len(tracks) - 1) for tracks in identity_records.values()),
        "fragmented_ground_truth_rate": _ratio(fragmented, len(identity_records)),
        "short_track_count": sum(count < short_track_threshold for count in observation_counts),
        "short_track_rate": _ratio(sum(count < short_track_threshold for count in observation_counts), len(observation_counts)),
        "observation_count_distribution": distribution(observation_counts),
        "track_duration_distribution_ms": distribution(durations),
        "repeated_crossing_risk_count": repeated,
    }


def distribution(values: Sequence[int | float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "minimum": None, "median": None, "p90": None, "maximum": None}
    return {"count": len(values), "minimum": min(values), "median": median(values), "p90": percentile(values, 0.90), "maximum": max(values)}


def throughput_metrics(stats: Mapping[str, Any] | None, *, configuration: Mapping[str, Any] | None = None) -> dict[str, Any]:
    raw = dict(stats or {})
    source_duration = numeric_or_none(raw.get("source_duration_ms"))
    elapsed = numeric_or_none(raw.get("elapsed_ms", raw.get("worker_wall_clock_ms")))
    decoded = numeric_or_none(raw.get("decoded_frames"))
    processed = numeric_or_none(raw.get("processed_frames"))
    processed_fps = numeric_or_none(raw.get("processed_fps"))
    decoded_fps = None if elapsed in (None, 0) or decoded is None else decoded / (elapsed / 1000)
    ratio = None if source_duration in (None, 0) or elapsed is None else elapsed / source_duration
    return {
        "source_duration_ms": source_duration,
        "decoded_frames": decoded,
        "processed_frames": processed,
        "detector_detections": numeric_or_none(raw.get("detection_count", raw.get("detector_detections"))),
        "tracks": numeric_or_none(raw.get("track_count")),
        "crossing_events": numeric_or_none(raw.get("event_count")),
        "worker_wall_clock_duration_ms": elapsed,
        "model_load_ms": numeric_or_none(raw.get("model_load_ms")),
        "first_inference_ms": numeric_or_none(raw.get("first_inference_ms")),
        "warm_inference_mean_ms": numeric_or_none(raw.get("warm_inference_mean_ms")),
        "warm_inference_p50_ms": numeric_or_none(raw.get("warm_inference_p50_ms")),
        "warm_inference_p90_ms": numeric_or_none(raw.get("warm_inference_p90_ms")),
        "tracker_update_mean_ms": numeric_or_none(raw.get("tracker_update_mean_ms")),
        "crossing_engine_ms": numeric_or_none(raw.get("crossing_engine_ms")),
        "processed_fps": processed_fps,
        "decoded_fps": decoded_fps,
        "processing_duration_video_duration_ratio": ratio,
        "resolved_device": raw.get("device", raw.get("resolved_device")),
        "gpu_name": raw.get("gpu_name"),
        "cuda_runtime_version": raw.get("cuda_runtime_version", raw.get("cuda_version")),
        "peak_gpu_memory_mib": numeric_or_none(raw.get("peak_gpu_memory_mib")),
        "peak_process_memory_mib": numeric_or_none(raw.get("peak_process_memory_mib")),
        "cpu_information": raw.get("cpu_information", {"processor": platform.processor() or None, "machine": platform.machine()}),
        "image_size": (configuration or {}).get("image_size", raw.get("image_size")),
        "frame_stride": (configuration or {}).get("frame_stride", raw.get("frame_stride")),
        "detector_id": (configuration or {}).get("detector_id", raw.get("detector_id")),
        "tracker_id": (configuration or {}).get("tracker_id", raw.get("tracker_id")),
        "configuration": dict(configuration or {}),
        "gpu_memory_status": "UNAVAILABLE_WITH_REASON" if raw.get("peak_gpu_memory_mib") is None else "MEASURED",
        "gpu_memory_unavailable_reason": raw.get("gpu_memory_unavailable_reason") if raw.get("peak_gpu_memory_mib") is None else None,
        "real_time_criterion": "NOT_CLAIMED",
    }


def numeric_or_none(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


SUPPORTED_EXPERIMENT_PARAMETERS = {
    "detector_confidence_threshold": "adapter:confidence_threshold",
    "inference_image_size": "adapter:image_size",
    "frame_stride": "adapter:frame_stride",
    "tracker_configuration": "adapter:tracker_configuration",
    "track_buffer": "adapter:lost_track_buffer",
    "crossing_anchor": "adapter:anchor_bottom_center_only",
    "crossing_tolerance_ms": "evaluation:matching_policy",
    "classification_min_observations": "policy:minimum_observations",
    "classification_vote_share_threshold": "policy:vote_share_threshold",
    "classification_weighted_share_threshold": "policy:weighted_share_threshold",
    "classification_near_tie_threshold": "policy:near_tie_threshold",
}
UNAVAILABLE_EXPERIMENT_PARAMETERS = {"tracker_match_threshold": "adapter_not_exposed_in_current_runtime"}


def build_experiment_configuration(payload: Mapping[str, Any]) -> dict[str, Any]:
    parameters = dict(payload.get("parameters") or payload)
    ignored = {"configuration_revision", "content_hash", "experiment_config_version", "name", "label"}
    requested = {key: value for key, value in parameters.items() if key not in ignored}
    unsupported = sorted(set(requested) - set(SUPPORTED_EXPERIMENT_PARAMETERS) - set(UNAVAILABLE_EXPERIMENT_PARAMETERS))
    unavailable = {key: UNAVAILABLE_EXPERIMENT_PARAMETERS[key] for key in requested if key in UNAVAILABLE_EXPERIMENT_PARAMETERS}
    errors = [_issue(key, "unsupported_parameter", f"parameter is not supported: {key}") for key in unsupported]
    errors.extend(_issue(key, "unavailable_parameter", reason) for key, reason in sorted(unavailable.items()))
    if errors:
        raise BenchmarkValidationError("experiment configuration contains unavailable parameters", errors)
    configuration = {
        "experiment_config_version": str(payload.get("experiment_config_version") or "experiment-config-v1"),
        "name": str(payload.get("name") or "candidate"),
        "parameters": requested,
        "supported_parameter_adapters": {key: SUPPORTED_EXPERIMENT_PARAMETERS[key] for key in sorted(requested)},
        "seed": int(payload.get("seed", 0)),
    }
    configuration["content_hash"] = canonical_hash(configuration)
    configuration["configuration_revision"] = f"cfg-{configuration['content_hash'][:16]}"
    return configuration


def _metric_value(row: Mapping[str, Any], key: str) -> float | None:
    value = row.get(key)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def pareto_frontier(results: Sequence[Mapping[str, Any]], objectives: Mapping[str, str] | None = None) -> dict[str, Any]:
    objectives = objectives or {
        "recall": "max",
        "precision": "max",
        "duplicate_rate": "min",
        "interval_absolute_error": "min",
        "direction_error_rate": "min",
        "class_macro_f1": "max",
        "timestamp_mae_ms": "min",
        "fragmentation_rate": "min",
        "processing_ratio": "min",
        "peak_gpu_memory_mib": "min",
    }
    normalized = [dict(row) for row in results]

    def dominates(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
        compared = []
        strictly_better = False
        for key, direction in objectives.items():
            left_value = _metric_value(left, key)
            right_value = _metric_value(right, key)
            if left_value is None or right_value is None:
                continue
            compared.append(key)
            if direction == "max" and left_value < right_value:
                return False
            if direction == "min" and left_value > right_value:
                return False
            if left_value != right_value:
                strictly_better = True
        return bool(compared) and strictly_better

    frontier = [row for row in normalized if not any(other is not row and dominates(other, row) for other in normalized)]
    frontier.sort(key=lambda row: str(row.get("configuration_revision") or row.get("id") or ""))
    return {
        "status": "PARETO_CANDIDATES",
        "objectives": objectives,
        "method": "PARETO_NON_DOMINATED_NO_OWNER_APPROVED_WEIGHTS",
        "frontier": frontier,
        "frontier_ids": [str(row.get("configuration_revision") or row.get("id") or "") for row in frontier],
        "candidate_count": len(normalized),
    }


def reviewer_agreement(annotations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_reviewer: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for annotation in annotations:
        by_reviewer[str(annotation.get("reviewer_id") or "")].append(annotation)
    reviewers = sorted(key for key in by_reviewer if key)
    pairwise: list[dict[str, Any]] = []
    for index, left_id in enumerate(reviewers):
        for right_id in reviewers[index + 1 :]:
            left = {str(row.get("ground_truth_event_id")): row for row in by_reviewer[left_id]}
            right = {str(row.get("ground_truth_event_id")): row for row in by_reviewer[right_id]}
            common = sorted(set(left) & set(right))
            direction_left = [left[event].get("direction_decision") for event in common if left[event].get("direction_decision") is not None and right[event].get("direction_decision") is not None]
            direction_right = [right[event].get("direction_decision") for event in common if left[event].get("direction_decision") is not None and right[event].get("direction_decision") is not None]
            class_left = [left[event].get("class_decision") for event in common if left[event].get("class_decision") is not None and right[event].get("class_decision") is not None]
            class_right = [right[event].get("class_decision") for event in common if left[event].get("class_decision") is not None and right[event].get("class_decision") is not None]
            timestamp_diffs = [abs(int(left[event]["timestamp_decision"]) - int(right[event]["timestamp_decision"])) for event in common if left[event].get("timestamp_decision") is not None and right[event].get("timestamp_decision") is not None]
            pairwise.append(
                {
                    "reviewer_a": left_id,
                    "reviewer_b": right_id,
                    "event_count_a": len(left),
                    "event_count_b": len(right),
                    "event_count_agreement": len(left) == len(right),
                    "matched_event_count": len(common),
                    "matched_event_agreement": _ratio(len(common), len(set(left) | set(right))),
                    "direction_agreement": _ratio(sum(a == b for a, b in zip(direction_left, direction_right)), len(direction_left)),
                    "class_agreement": _ratio(sum(a == b for a, b in zip(class_left, class_right)), len(class_left)),
                    "timestamp_difference_ms": {"mean": mean(timestamp_diffs) if timestamp_diffs else None, "maximum": max(timestamp_diffs) if timestamp_diffs else None},
                    "direction_kappa": cohens_kappa(direction_left, direction_right),
                    "class_kappa": cohens_kappa(class_left, class_right),
                    "warnings": ["kappa_insufficient_or_degenerate"] if (direction_left and cohens_kappa(direction_left, direction_right) is None) or (class_left and cohens_kappa(class_left, class_right) is None) else [],
                }
            )
    return {"reviewer_count": len(reviewers), "reviewers": reviewers, "pairwise": pairwise, "minimum_two_reviewers_available": len(reviewers) >= 2}


def cohens_kappa(left: Sequence[Any], right: Sequence[Any]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    labels = sorted(set(left) | set(right), key=str)
    if len(labels) < 2:
        return None
    n = len(left)
    observed = sum(a == b for a, b in zip(left, right)) / n
    left_counts = Counter(left)
    right_counts = Counter(right)
    expected = sum((left_counts[label] / n) * (right_counts[label] / n) for label in labels)
    if expected >= 1.0:
        return None
    return (observed - expected) / (1 - expected)


@dataclass(frozen=True)
class QualificationThreshold:
    metric_path: str
    operator: str
    required_value: int | float
    unit: str
    required: bool
    undefined_behavior: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric_path": self.metric_path,
            "operator": self.operator,
            "required_value": self.required_value,
            "unit": self.unit,
            "required": self.required,
            "undefined_behavior": self.undefined_behavior,
        }


@dataclass(frozen=True)
class QualificationThresholdPolicy:
    policy_revision: str
    approved_by: str
    approved_at: str
    applicable_corpus_revision: str
    required_split: str
    minimum_source_count: int
    minimum_ground_truth_event_count: int
    minimum_condition_coverage: tuple[str, ...]
    thresholds: tuple[QualificationThreshold, ...]
    schema_version: str = QUALIFICATION_POLICY_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_revision": self.policy_revision,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "applicable_corpus_revision": self.applicable_corpus_revision,
            "required_split": self.required_split,
            "minimum_source_count": self.minimum_source_count,
            "minimum_ground_truth_event_count": self.minimum_ground_truth_event_count,
            "minimum_condition_coverage": list(self.minimum_condition_coverage),
            "thresholds": [threshold.as_dict() for threshold in self.thresholds],
        }


def _strict_numeric(value: Any, field_name: str, errors: list[dict[str, str]]) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        errors.append(_issue(field_name, "invalid_numeric", "must be a finite JSON number"))
        return None
    return value


def _strict_positive_integer(value: Any, field_name: str, errors: list[dict[str, str]]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        errors.append(_issue(field_name, "invalid_positive_integer", "must be an integer greater than zero"))
        return 0
    return value


def parse_qualification_policy(payload: Mapping[str, Any] | None) -> QualificationThresholdPolicy:
    """Validate and normalize an owner-approved threshold policy.

    Policy values are deliberately strict: JSON numeric strings, unknown metric
    paths, arbitrary operators, and unit mismatches are rejected before any
    benchmark run can be persisted.
    """

    if not isinstance(payload, Mapping):
        raise BenchmarkValidationError("qualification threshold policy is required", [_issue("policy", "required", "an approved policy object is required")])
    raw = dict(payload)
    errors: list[dict[str, str]] = []
    schema_version = str(raw.get("schema_version") or QUALIFICATION_POLICY_SCHEMA_VERSION)
    if schema_version != QUALIFICATION_POLICY_SCHEMA_VERSION:
        errors.append(_issue("schema_version", "unsupported_version", QUALIFICATION_POLICY_SCHEMA_VERSION))
    text_fields = ("policy_revision", "approved_by", "approved_at", "applicable_corpus_revision")
    text_values: dict[str, str] = {}
    for field_name in text_fields:
        value = str(raw.get(field_name) or "").strip()
        text_values[field_name] = value
        if not value:
            errors.append(_issue(field_name, "required", f"{field_name} is required"))
    approved_at = text_values["approved_at"]
    if approved_at:
        try:
            parsed_at = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
            if parsed_at.tzinfo is None:
                raise ValueError
        except ValueError:
            errors.append(_issue("approved_at", "invalid_timestamp", "approved_at must be an ISO-8601 timestamp with timezone"))
    required_split = str(raw.get("required_split") or "").strip()
    if required_split not in BENCHMARK_SPLITS:
        errors.append(_issue("required_split", "unsupported_split", "required_split must be a recognized benchmark split"))
    minimum_source_count = _strict_positive_integer(raw.get("minimum_source_count"), "minimum_source_count", errors)
    minimum_ground_truth_event_count = _strict_positive_integer(raw.get("minimum_ground_truth_event_count"), "minimum_ground_truth_event_count", errors)
    raw_conditions = raw.get("minimum_condition_coverage")
    conditions: tuple[str, ...] = ()
    if not isinstance(raw_conditions, list) or any(not isinstance(item, str) or not item.strip() for item in raw_conditions):
        errors.append(_issue("minimum_condition_coverage", "invalid_condition_coverage", "must be a list of non-empty condition tags"))
    else:
        conditions = tuple(dict.fromkeys(item.strip().lower() for item in raw_conditions))
        unknown = sorted(set(conditions) - KNOWN_CONDITION_TAGS)
        if unknown:
            errors.append(_issue("minimum_condition_coverage", "unsupported_condition", ", ".join(unknown)))
    raw_thresholds = raw.get("thresholds")
    thresholds: list[QualificationThreshold] = []
    if not isinstance(raw_thresholds, list) or not raw_thresholds:
        errors.append(_issue("thresholds", "required", "thresholds must be a non-empty list"))
        raw_thresholds = []
    for index, raw_threshold in enumerate(raw_thresholds):
        prefix = f"thresholds[{index}]"
        if not isinstance(raw_threshold, Mapping):
            errors.append(_issue(prefix, "invalid_object", "threshold must be an object"))
            continue
        metric_path = str(raw_threshold.get("metric_path") or "").strip()
        operator = str(raw_threshold.get("operator") or "").strip().upper()
        unit = str(raw_threshold.get("unit") or "").strip()
        if metric_path not in METRIC_PATH_DEFINITIONS:
            errors.append(_issue(f"{prefix}.metric_path", "unknown_metric_path", "metric_path is not in the qualification allowlist"))
        if operator not in SUPPORTED_THRESHOLD_OPERATORS:
            errors.append(_issue(f"{prefix}.operator", "unsupported_operator", "operator must be one of GTE, LTE, GT, LT, EQ"))
        required_value = _strict_numeric(raw_threshold.get("required_value"), f"{prefix}.required_value", errors)
        expected_unit = METRIC_PATH_DEFINITIONS.get(metric_path, {}).get("unit")
        if not unit:
            errors.append(_issue(f"{prefix}.unit", "required", "unit is required"))
        elif expected_unit and unit != expected_unit:
            errors.append(_issue(f"{prefix}.unit", "incompatible_unit", f"unit must be {expected_unit} for {metric_path}"))
        required = raw_threshold.get("required")
        if not isinstance(required, bool):
            errors.append(_issue(f"{prefix}.required", "invalid_boolean", "required must be true or false"))
            required = True
        undefined_behavior = str(raw_threshold.get("undefined_behavior") or "").strip().upper()
        if undefined_behavior not in SUPPORTED_UNDEFINED_BEHAVIORS:
            errors.append(_issue(f"{prefix}.undefined_behavior", "unsupported_undefined_behavior", "undefined_behavior must be FAIL_CLOSED or ALLOW_UNDEFINED"))
        if required_value is not None:
            thresholds.append(QualificationThreshold(metric_path, operator, required_value, unit, required, undefined_behavior))
    if errors:
        raise BenchmarkValidationError("invalid qualification threshold policy", errors)
    return QualificationThresholdPolicy(
        policy_revision=text_values["policy_revision"],
        approved_by=text_values["approved_by"],
        approved_at=approved_at,
        applicable_corpus_revision=text_values["applicable_corpus_revision"],
        required_split=required_split,
        minimum_source_count=minimum_source_count,
        minimum_ground_truth_event_count=minimum_ground_truth_event_count,
        minimum_condition_coverage=conditions,
        thresholds=tuple(thresholds),
        schema_version=schema_version,
    )


def _value_at_path(payload: Mapping[str, Any], path: str) -> Any:
    value: Any = payload
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def resolve_metric_path(metrics: Mapping[str, Any], metric_path: str) -> tuple[int | float | None, str]:
    """Resolve an allowlisted scalar metric and classify its availability."""

    definition = METRIC_PATH_DEFINITIONS.get(metric_path)
    if definition is None:
        raise BenchmarkValidationError("unknown benchmark metric path", [_issue("metric_path", "unknown_metric_path", metric_path)])
    value = _value_at_path(metrics, metric_path)
    if value is None:
        denominator_path = definition.get("denominator_path")
        denominator = _value_at_path(metrics, denominator_path) if denominator_path else None
        if denominator == 0:
            return None, "UNDEFINED_ZERO_DENOMINATOR"
        return None, "UNAVAILABLE"
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None, "INVALID_VALUE"
    return value, "AVAILABLE"


def evaluate_thresholds(
    metrics: Mapping[str, Any], policy: QualificationThresholdPolicy | Mapping[str, Any]
) -> dict[str, Any]:
    normalized = policy if isinstance(policy, QualificationThresholdPolicy) else parse_qualification_policy(policy)
    results: list[dict[str, Any]] = []
    for threshold in normalized.thresholds:
        observed_value, availability = resolve_metric_path(metrics, threshold.metric_path)
        passed = False
        failure_reason: str | None = None
        if availability == "AVAILABLE":
            required_value = threshold.required_value
            if threshold.operator == "GTE":
                passed = observed_value >= required_value
            elif threshold.operator == "LTE":
                passed = observed_value <= required_value
            elif threshold.operator == "GT":
                passed = observed_value > required_value
            elif threshold.operator == "LT":
                passed = observed_value < required_value
            elif threshold.operator == "EQ":
                passed = observed_value == required_value
            if not passed:
                failure_reason = "observed value did not satisfy the threshold operator"
        elif threshold.undefined_behavior == "ALLOW_UNDEFINED":
            passed = True
            availability = "UNDEFINED_ALLOWED"
        else:
            failure_reason = (
                "metric is undefined because its denominator is zero"
                if availability == "UNDEFINED_ZERO_DENOMINATOR"
                else "metric is null, unavailable, or non-numeric"
            )
        results.append(
            {
                "metric_path": threshold.metric_path,
                "observed_value": observed_value,
                "required_value": threshold.required_value,
                "operator": threshold.operator,
                "unit": threshold.unit,
                "required": threshold.required,
                "undefined_behavior": threshold.undefined_behavior,
                "passed": bool(passed),
                "availability": availability,
                "failure_reason": failure_reason,
            }
        )
    required_results = [result for result in results if result["required"]]
    return {
        "schema_version": QUALIFICATION_POLICY_SCHEMA_VERSION,
        "policy_revision": normalized.policy_revision,
        "results": results,
        "all_required_passed": all(result["passed"] for result in required_results),
        "all_passed": all(result["passed"] for result in results),
    }


def automatic_result_blockers(automatic_result: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = [str(item) for item in automatic_result.get("blockers", []) if item]
    if bool(automatic_result.get("stale")):
        blockers.append("STALE_AUTOMATIC_RESULT")
    if bool(automatic_result.get("structurally_invalid")):
        blockers.append("STRUCTURALLY_INVALID_AUTOMATIC_RESULT")
    if automatic_result.get("reconciliation_status") and automatic_result.get("reconciliation_status") != "STRUCTURALLY_VALID":
        blockers.append(f"RECONCILIATION_{automatic_result.get('reconciliation_status')}")
    if not bool(automatic_result.get("engineering_ready")):
        blockers.append("ENGINEERING_RESULT_NOT_READY")
    return sorted(set(blockers))


def qualification_gates(
    *,
    source: Mapping[str, Any],
    ground_truth: Mapping[str, Any],
    automatic_result: Mapping[str, Any],
    metrics: Mapping[str, Any],
    split_isolation_valid: bool = True,
    approved_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rights_status = str(source.get("rights_status") or "UNKNOWN")
    split = str(source.get("benchmark_split") or "DIAGNOSTIC_ONLY")
    blockers = automatic_result_blockers(automatic_result)
    policy: QualificationThresholdPolicy | None = None
    if approved_policy is not None:
        policy = parse_qualification_policy(approved_policy)
    threshold_evaluation = (
        evaluate_thresholds(metrics, policy)
        if policy is not None and metrics
        else {
            "schema_version": QUALIFICATION_POLICY_SCHEMA_VERSION,
            "policy_revision": policy.policy_revision if policy else None,
            "results": [],
            "all_required_passed": False if policy else False,
            "all_passed": False if policy else False,
            "status": "NOT_EVALUATED" if policy is None else "NO_METRICS",
        }
    )
    source_count_raw = source.get("source_count", 0)
    try:
        source_count = int(source_count_raw)
    except (TypeError, ValueError):
        source_count = 0
    truth_events = ground_truth.get("events") if isinstance(ground_truth.get("events"), list) else []
    eligible_truth_count = sum(eligible_ground_truth(event) for event in truth_events)
    if ground_truth.get("eligible_ground_truth_event_count") is not None:
        try:
            eligible_truth_count = int(ground_truth["eligible_ground_truth_event_count"])
        except (TypeError, ValueError):
            eligible_truth_count = 0
    actual_conditions = set(str(item).strip().lower() for item in (source.get("condition_coverage") or source.get("condition_tags") or []) if item)
    policy_condition_requirements = set(policy.minimum_condition_coverage) if policy else set()
    missing_conditions = sorted(policy_condition_requirements - actual_conditions)
    result_scorable = not blockers
    policy_revision_matches = bool(policy and policy.applicable_corpus_revision == str(source.get("corpus_revision") or ""))
    required_split_matches = bool(policy and policy.required_split == split)
    gates: dict[str, dict[str, Any]] = {
        "rights_status_acceptable": {"passed": rights_status in {"CLEARED_FOR_LOCAL_BENCHMARK", "CLEARED_FOR_REPOSITORY_DISTRIBUTION"}, "reason": rights_status},
        "source_checksum_verified": {"passed": bool(source.get("checksum_verified")), "reason": "checksum_verified must be true"},
        "scene_revision_present": {"passed": bool(source.get("scene_revision")) and bool(ground_truth.get("scene_revision")), "reason": "source and annotation scene revisions are required"},
        "ground_truth_adjudicated": {"passed": str(ground_truth.get("status")) == "ADJUDICATED" and bool(ground_truth.get("ground_truth_revision") or ground_truth.get("revision")), "reason": "an adjudicated ground-truth revision is required"},
        "engineering_result_structurally_valid": {"passed": result_scorable and bool(automatic_result.get("engineering_ready")), "reason": "6B reconciliation must be STRUCTURALLY_VALID and engineering-ready", "blockers": blockers},
        "automatic_result_current": {"passed": not bool(automatic_result.get("stale")), "reason": "stale automatic results cannot qualify"},
        "revisions_resolved": {"passed": all(bool(automatic_result.get(key)) for key in ("taxonomy_revision", "mapping_revision", "classification_policy_revision")), "reason": "taxonomy/mapping/policy revisions must be retained"},
        "calibration_holdout_isolation": {"passed": split_isolation_valid, "reason": "holdout cannot select calibration parameters"},
        "required_metrics_generated": {"passed": result_scorable and bool(metrics.get("event_metrics")) and bool(metrics.get("count_metrics")) and bool(metrics.get("timestamp_metrics")), "reason": "required metric families must be generated only from a scorable result"},
        "reproducibility_metadata": {
            "passed": (
                str(automatic_result.get("code_commit_sha") or "").lower() not in {"", "unavailable", "unknown"}
                and bool(automatic_result.get("runtime_configuration_hash"))
            ),
            "reason": "code commit and canonical runtime configuration hash are required; legacy configuration hashes are not sufficient",
        },
        "approved_threshold_policy": {"passed": policy is not None, "reason": "a valid owner-approved threshold policy is required"},
        "policy_corpus_revision": {"passed": policy_revision_matches, "reason": "policy applicable_corpus_revision must match the evaluated corpus"},
        "policy_required_split": {"passed": required_split_matches, "reason": "policy required_split must match the evaluated source split"},
        "minimum_source_count": {"passed": policy is not None and source_count >= (policy.minimum_source_count if policy else 1), "observed": source_count, "required": policy.minimum_source_count if policy else None, "reason": "minimum source coverage is not met"},
        "minimum_ground_truth_event_count": {"passed": policy is not None and eligible_truth_count >= (policy.minimum_ground_truth_event_count if policy else 1), "observed": eligible_truth_count, "required": policy.minimum_ground_truth_event_count if policy else None, "reason": "minimum eligible ground-truth event support is not met"},
        "minimum_condition_coverage": {"passed": policy is not None and not missing_conditions, "observed": sorted(actual_conditions), "required": sorted(policy_condition_requirements), "missing": missing_conditions, "reason": "required condition tags are missing"},
        "required_thresholds_pass": {"passed": bool(threshold_evaluation.get("all_required_passed")) if policy else False, "reason": "all mandatory threshold comparisons must pass"},
        "required_holdout_evidence": {"passed": split == "HOLDOUT" and bool(policy and policy.required_split == "HOLDOUT"), "reason": "pilot qualification requires an explicit HOLDOUT policy and source"},
    }
    integrity_gate_names = {name for name in gates if name not in {"required_thresholds_pass"}}
    all_integrity = all(bool(gates[name].get("passed")) for name in integrity_gate_names)
    metrics_present = bool(metrics)
    if blockers:
        status = "INCOMPLETE"
    elif not metrics_present:
        status = "NOT_RUN"
    elif split == "DIAGNOSTIC_ONLY":
        status = "NOT_QUALIFIED"
    elif split == "CALIBRATION":
        status = "CALIBRATION_CANDIDATE"
    elif split != "HOLDOUT" or policy is None or not policy_revision_matches or not required_split_matches:
        status = "HOLDOUT_INSUFFICIENT"
    elif not all_integrity or not bool(threshold_evaluation.get("all_required_passed")):
        status = "NOT_QUALIFIED"
    else:
        status = "QUALIFIED_FOR_PILOT"
    return {
        "status": status,
        "gates": gates,
        "split": split,
        "threshold_policy_present": policy is not None,
        "policy": policy.as_dict() if policy else {},
        "threshold_evaluation": threshold_evaluation,
        "automatic_result_blockers": blockers,
        "no_automatic_pilot_claim": status != "QUALIFIED_FOR_PILOT",
        "calibration_candidate_rationale": "Calibration results are candidate evidence only; they cannot qualify for pilot.",
    }


def build_metric_bundle(
    predicted_events: Sequence[Mapping[str, Any]],
    truth_events: Sequence[Mapping[str, Any]],
    match_result: Mapping[str, Any],
    intervals: Sequence[Mapping[str, Any]],
    *,
    throughput: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        "event_metrics": event_metrics(match_result),
        "count_metrics": count_metrics(predicted_events, truth_events, intervals),
        "direction_metrics": direction_metrics(match_result),
        "class_metrics": class_metrics(predicted_events, truth_events, match_result),
        "timestamp_metrics": timestamp_metrics(predicted_events, truth_events, match_result),
        "fragmentation_metrics": fragmentation_metrics(predicted_events, truth_events, match_result),
        "throughput_metrics": dict(throughput or throughput_metrics(None)),
        "duplicate_metrics": {
            "duplicate_automatic_events": len(match_result.get("duplicate_automatic_event_ids", [])),
            "duplicate_rate": _ratio(len(match_result.get("duplicate_automatic_event_ids", [])), int(match_result.get("predicted_count", 0))),
            "missed_events": len(match_result.get("false_negative_truth_ids", [])),
            "miss_rate": _ratio(len(match_result.get("false_negative_truth_ids", [])), int(match_result.get("ground_truth_count", 0))),
            "false_crossing_rate": _ratio(len(match_result.get("false_positive_event_ids", [])), int(match_result.get("predicted_count", 0))),
            "denominators": {"duplicate_rate": "eligible automatic events", "miss_rate": "eligible ground-truth events", "false_crossing_rate": "eligible automatic events"},
        },
    }


def environment_snapshot() -> dict[str, Any]:
    return {
        "schema_version": "benchmark-environment-v1",
        "python_version": platform.python_version(),
        "platform": platform.system(),
        "platform_release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "python_executable": "<local-runtime>",
        "cwd": "<redacted>",
    }


def build_report_payload(
    *,
    corpus: Mapping[str, Any],
    ground_truth: Mapping[str, Any],
    automatic_configuration: Mapping[str, Any],
    matching_policy: Mapping[str, Any],
    match_result: Mapping[str, Any],
    metrics: Mapping[str, Any],
    qualification: Mapping[str, Any],
    experiment_comparison: Mapping[str, Any] | None = None,
    reproducibility: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "report_schema_version": "benchmark-report-v1",
        "corpus_manifest": dict(corpus),
        "rights_summary": {"status": corpus.get("rights_status"), "source_count": corpus.get("source_count", 1), "disclosure": corpus.get("rights_disclosure")},
        "split": corpus.get("benchmark_split"),
        "condition_coverage": corpus.get("condition_tags", []),
        "ground_truth_protocol": {"revision": ground_truth.get("ground_truth_revision"), "status": ground_truth.get("status"), "reviewer_agreement": ground_truth.get("reviewer_agreement", {})},
        "automatic_configuration": dict(automatic_configuration),
        "matching_policy": dict(matching_policy),
        "event_metrics": metrics.get("event_metrics", {}),
        "count_metrics": metrics.get("count_metrics", {}),
        "interval_metrics": metrics.get("count_metrics", {}).get("by_interval", []),
        "direction_metrics": metrics.get("direction_metrics", {}),
        "class_metrics": metrics.get("class_metrics", {}),
        "timestamp_metrics": metrics.get("timestamp_metrics", {}),
        "duplicates_and_misses": metrics.get("duplicate_metrics", {}),
        "fragmentation": metrics.get("fragmentation_metrics", {}),
        "throughput": metrics.get("throughput_metrics", {}),
        "experiment_comparison": experiment_comparison or {"status": "NOT_RUN"},
        "calibration_candidate_rationale": qualification.get("calibration_candidate_rationale", "Calibration evidence is not pilot qualification."),
        "qualification_gates": qualification,
        "limitations": [
            "No TIMS 13-class accuracy claim.",
            "No human certification or production export is created by Milestone 6C.",
            "No real-time claim is made from FPS alone.",
            "Structurally invalid or stale automatic results are not scored.",
            "Private or unredistributable media remains external to the repository and CI.",
        ],
        "reproducibility": dict(reproducibility or {}),
        "match_diagnostics": {"match_count": len(match_result.get("matches", [])), "categories": Counter(record.get("primary_category") for record in match_result.get("matches", []))},
    }


def render_report_markdown(report: Mapping[str, Any]) -> str:
    event = report.get("event_metrics", {})
    count = report.get("count_metrics", {}).get("total", {})
    qualification = report.get("qualification_gates", {})
    lines = [
        "# Benchmark report",
        "",
        (
            "This report is tied to a canonical runtime configuration hash. It does not certify production traffic counts."
            if report.get("reproducibility", {}).get("runtime_configuration_hash")
            else "This report preserves legacy evaluation evidence without claiming canonical runtime reproducibility. It does not certify production traffic counts."
        ),
        "",
        "## Corpus and rights",
        "",
        f"- Corpus/source: `{report.get('corpus_manifest', {}).get('corpus_revision', 'unavailable')}` / `{report.get('corpus_manifest', {}).get('benchmark_source_id', 'unavailable')}`",
        f"- Rights: `{report.get('rights_summary', {}).get('status', 'UNKNOWN')}`",
        f"- Split: `{report.get('split', 'UNKNOWN')}`",
        f"- Conditions: {', '.join(report.get('condition_coverage', [])) or 'not recorded'}",
        "",
        "## Event metrics",
        "",
        f"- TP / FP / FN: `{event.get('true_positive', 0)} / {event.get('false_positive', 0)} / {event.get('false_negative', 0)}`",
        f"- Precision / recall / F1: `{event.get('precision')}` / `{event.get('recall')}` / `{event.get('f1')}`",
        f"- Automatic / ground truth: `{event.get('sample_sizes', {}).get('predicted', 0)}` / `{event.get('sample_sizes', {}).get('ground_truth', 0)}`",
        "",
        "## Count and timestamp metrics",
        "",
        f"- Signed count error: `{count.get('signed_error')}`; absolute error: `{count.get('absolute_error')}`",
        f"- Timestamp MAE / P95: `{report.get('timestamp_metrics', {}).get('mean_absolute_error_ms')}` / `{report.get('timestamp_metrics', {}).get('p95_absolute_error_ms')}` ms",
        "",
        "## Qualification",
        "",
        f"- Status: `{qualification.get('status', 'NOT_RUN')}`",
        f"- Policy revision: `{qualification.get('policy', {}).get('policy_revision', 'none')}`",
        f"- Automatic-result blockers: `{', '.join(qualification.get('automatic_result_blockers', [])) or 'none'}`",
        "- Threshold evaluation:",
        "",
        "## Limitations and non-claims",
        "",
    ]
    threshold_evaluation = qualification.get("threshold_evaluation", {})
    for threshold in threshold_evaluation.get("results", []):
        lines.append(
            "  - `{metric_path}` `{operator}` `{required_value}` `{unit}` -> observed `{observed_value}`, "
            "`{passed}` ({availability}){failure}".format(
                metric_path=threshold.get("metric_path"),
                operator=threshold.get("operator"),
                required_value=threshold.get("required_value"),
                unit=threshold.get("unit"),
                observed_value=threshold.get("observed_value"),
                passed=threshold.get("passed"),
                availability=threshold.get("availability"),
                failure=f"; {threshold.get('failure_reason')}" if threshold.get("failure_reason") else "",
            )
        )
    lines.extend(f"- {item}" for item in report.get("limitations", []))
    lines.append("")
    return "\n".join(lines)
