"""Detector-independent Milestone 6B engineering output contracts.

This module deliberately sits between detector/tracker adapters and persistence.
It owns the pilot taxonomy, deterministic track vote, source-time semantics,
structured aggregation, and reconciliation.  No model result object is part of
the contract.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import StrEnum
import hashlib
import json
import math
import sqlite3
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


TAXONOMY_REVISION = "pilot-observable-taxonomy-v1"
MAPPING_REVISION = "pilot-observable-mapping-v1"
CLASSIFICATION_POLICY_REVISION = "track-vote-policy-v1"
TRACK_EVIDENCE_SCHEMA_VERSION = "compact-track-evidence-v1"
ENGINEERING_SUMMARY_SCHEMA_VERSION = "engineering-summary-v1"
INTERVAL_MINUTES = 15
INTERVAL_MS = INTERVAL_MINUTES * 60 * 1000


class ClassificationStatus(StrEnum):
    CONFIRMED_MAPPING = "CONFIRMED_MAPPING"
    PROVISIONAL = "PROVISIONAL"
    UNKNOWN = "UNKNOWN"
    AMBIGUOUS = "AMBIGUOUS"
    OTHER = "OTHER"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    UNSUPPORTED_RAW_CLASS = "UNSUPPORTED_RAW_CLASS"


class CapabilityState(StrEnum):
    PILOT_OBSERVABLE = "PILOT_OBSERVABLE"
    TARGET_ONLY = "TARGET_ONLY"
    UNSUPPORTED = "UNSUPPORTED"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"


class EngineeringClass(StrEnum):
    PASSENGER_VEHICLE = "PASSENGER_VEHICLE"
    MOTORCYCLE = "MOTORCYCLE"
    BUS = "BUS"
    HEAVY_VEHICLE_UNSPECIFIED = "HEAVY_VEHICLE_UNSPECIFIED"
    BICYCLE = "BICYCLE"
    PEDESTRIAN = "PEDESTRIAN"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"
    AMBIGUOUS = "AMBIGUOUS"


VALID_CANONICAL_DIRECTIONS = frozenset({"A_TO_B", "B_TO_A"})
STATUS_ALLOWED_ENGINEERING_CLASSES: dict[str, frozenset[str]] = {
    ClassificationStatus.CONFIRMED_MAPPING.value: frozenset(
        {
            EngineeringClass.PASSENGER_VEHICLE.value,
            EngineeringClass.MOTORCYCLE.value,
            EngineeringClass.BUS.value,
            EngineeringClass.BICYCLE.value,
            EngineeringClass.PEDESTRIAN.value,
        }
    ),
    ClassificationStatus.PROVISIONAL.value: frozenset({EngineeringClass.HEAVY_VEHICLE_UNSPECIFIED.value}),
    ClassificationStatus.UNKNOWN.value: frozenset({EngineeringClass.UNKNOWN.value}),
    ClassificationStatus.AMBIGUOUS.value: frozenset({EngineeringClass.AMBIGUOUS.value}),
    ClassificationStatus.OTHER.value: frozenset({EngineeringClass.OTHER.value}),
    ClassificationStatus.INSUFFICIENT_EVIDENCE.value: frozenset({EngineeringClass.UNKNOWN.value}),
    ClassificationStatus.UNSUPPORTED_RAW_CLASS.value: frozenset({EngineeringClass.UNKNOWN.value}),
}


def canonical_direction(value: Any) -> str | None:
    """Accept only the two persisted canonical direction values."""

    return value if isinstance(value, str) and value in VALID_CANONICAL_DIRECTIONS else None


def classification_consistency_error(status: Any, engineering_class: Any) -> str | None:
    """Return an explicit error when status and engineering class disagree."""

    status_value = status.value if isinstance(status, StrEnum) else str(status)
    class_value = engineering_class.value if isinstance(engineering_class, StrEnum) else str(engineering_class)
    allowed = STATUS_ALLOWED_ENGINEERING_CLASSES.get(status_value)
    if allowed is None:
        return f"unsupported_classification_status:{status_value}"
    if class_value not in allowed:
        return f"classification_status_class_mismatch:{status_value}:{class_value}"
    return None


def invalid_direction_exclusion(event: Mapping[str, Any], original_direction: Any) -> dict[str, Any]:
    """Describe one source event excluded from engineering dimensions."""

    return {
        "event_id": event.get("source_event_id", event.get("id")),
        "technical_key": event.get("technical_key"),
        "original_direction": original_direction,
        "expected_domain": sorted(VALID_CANONICAL_DIRECTIONS),
        "expected_count": 1,
        "resulting_count": 0,
        "resulting_count_difference": -1,
        "reason": "invalid_canonical_direction",
    }


def classification_exclusion(event: Mapping[str, Any], error: str) -> dict[str, Any]:
    """Describe one source event excluded for inconsistent classification fields."""

    return {
        "event_id": event.get("source_event_id", event.get("id")),
        "technical_key": event.get("technical_key"),
        "classification_status": event.get("classification_status"),
        "engineering_class": event.get("engineering_class"),
        "allowed_classes": sorted(STATUS_ALLOWED_ENGINEERING_CLASSES.get(str(event.get("classification_status")), frozenset())),
        "resulting_count_difference": -1,
        "reason": error,
    }


@dataclass(frozen=True)
class PilotTaxonomyEntry:
    code: str
    display_name_en: str
    display_name_th: str
    object_domain: str
    capability_state: CapabilityState = CapabilityState.PILOT_OBSERVABLE
    target_reference: str | None = None
    display_order: int = 0


@dataclass(frozen=True)
class RawMapping:
    raw_class_name: str
    engineering_class: EngineeringClass
    provisional_class: str
    status: ClassificationStatus
    reason: str


@dataclass(frozen=True)
class ClassificationPolicy:
    revision: str = CLASSIFICATION_POLICY_REVISION
    min_observations: int = 2
    min_winning_vote_share: float = 0.60
    min_winning_weighted_share: float = 0.60
    near_tie_margin: float = 0.10
    confidence_min: float = 0.0
    confidence_max: float = 1.0
    max_evidence_samples: int = 32

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


PILOT_TAXONOMY: tuple[PilotTaxonomyEntry, ...] = (
    PilotTaxonomyEntry("PASSENGER_VEHICLE", "Passenger vehicle", "รถยนต์นั่ง", "vehicle", display_order=10),
    PilotTaxonomyEntry("MOTORCYCLE", "Motorcycle", "รถจักรยานยนต์", "vehicle", display_order=20),
    PilotTaxonomyEntry("BUS", "Bus", "รถโดยสาร", "vehicle", display_order=30),
    PilotTaxonomyEntry("HEAVY_VEHICLE_UNSPECIFIED", "Heavy vehicle (unspecified)", "ยานพาหนะหนัก (ไม่ระบุชนิด)", "vehicle", display_order=40),
    PilotTaxonomyEntry("BICYCLE", "Bicycle", "จักรยาน", "vehicle", display_order=50),
    PilotTaxonomyEntry("PEDESTRIAN", "Pedestrian", "คนเดินเท้า", "pedestrian", display_order=60),
    PilotTaxonomyEntry("OTHER", "Other", "อื่น ๆ", "vehicle", display_order=70),
    PilotTaxonomyEntry("UNKNOWN", "Unknown", "ไม่ทราบ", "unknown", display_order=80),
    PilotTaxonomyEntry("AMBIGUOUS", "Ambiguous", "ไม่ชัดเจน", "unknown", display_order=90),
)

# COCO names are used only as raw detector labels.  They are not TIMS class
# codes and must not be presented as a complete TIMS taxonomy.
RAW_MAPPINGS: tuple[RawMapping, ...] = (
    RawMapping("person", EngineeringClass.PEDESTRIAN, "pedestrian", ClassificationStatus.CONFIRMED_MAPPING, "raw_coco_person"),
    RawMapping("bicycle", EngineeringClass.BICYCLE, "bicycle", ClassificationStatus.CONFIRMED_MAPPING, "raw_coco_bicycle"),
    RawMapping("motorcycle", EngineeringClass.MOTORCYCLE, "motorcycle", ClassificationStatus.CONFIRMED_MAPPING, "raw_coco_motorcycle"),
    RawMapping("car", EngineeringClass.PASSENGER_VEHICLE, "passenger_vehicle", ClassificationStatus.CONFIRMED_MAPPING, "raw_coco_car"),
    RawMapping("bus", EngineeringClass.BUS, "bus", ClassificationStatus.CONFIRMED_MAPPING, "raw_coco_bus"),
    RawMapping("truck", EngineeringClass.HEAVY_VEHICLE_UNSPECIFIED, "heavy_vehicle_unspecified", ClassificationStatus.PROVISIONAL, "raw_coco_truck_coarse_mapping"),
    RawMapping("other", EngineeringClass.OTHER, "other", ClassificationStatus.OTHER, "explicit_other_mapping"),
)
RAW_MAPPING_BY_NAME = {item.raw_class_name: item for item in RAW_MAPPINGS}
TAXONOMY_BY_CODE = {item.code: item for item in PILOT_TAXONOMY}


@dataclass(frozen=True)
class ClassificationDecision:
    raw_class_id: int | None
    raw_class_name: str | None
    track_voted_raw_class_id: int | None
    track_voted_raw_class_name: str | None
    provisional_class: str
    engineering_class: str
    classification_status: str
    classification_reason: str
    raw_vote_counts: dict[str, int]
    weighted_scores: dict[str, float]
    winning_vote_share: float
    winning_weighted_share: float
    confidence_count: int
    confidence_missing_count: int
    confidence_invalid_count: int
    confidence_min: float | None
    confidence_max: float | None
    confidence_mean: float | None
    eligible_observation_count: int
    first_pts_ms: int | None
    last_pts_ms: int | None
    policy_revision: str
    policy_thresholds: dict[str, Any]
    warnings: tuple[str, ...] = ()

    @property
    def status(self) -> ClassificationStatus:
        return ClassificationStatus(self.classification_status)

    def evidence_json(self, samples: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
        evidence = {
            "schema_version": TRACK_EVIDENCE_SCHEMA_VERSION,
            "observation_count": self.eligible_observation_count,
            "eligible_classification_observation_count": self.eligible_observation_count,
            "raw_vote_counts": dict(sorted(self.raw_vote_counts.items())),
            "weighted_scores": {key: round(value, 6) for key, value in sorted(self.weighted_scores.items())},
            "winning_raw_class": self.track_voted_raw_class_name,
            "winning_raw_class_id": self.track_voted_raw_class_id,
            "winning_vote_share": round(self.winning_vote_share, 6),
            "winning_weighted_share": round(self.winning_weighted_share, 6),
            "runner_up_raw_class": self._runner_up(),
            "confidence_stats": {
                "finite_count": self.confidence_count,
                "missing_count": self.confidence_missing_count,
                "invalid_count": self.confidence_invalid_count,
                "min": self.confidence_min,
                "max": self.confidence_max,
                "mean": self.confidence_mean,
            },
            "first_pts_ms": self.first_pts_ms,
            "last_pts_ms": self.last_pts_ms,
            "policy_revision": self.policy_revision,
            "policy_thresholds": dict(self.policy_thresholds),
            "classification_status": self.classification_status,
            "classification_reason": self.classification_reason,
            "provisional_class": self.provisional_class,
            "engineering_class": self.engineering_class,
            "warnings": list(self.warnings),
            "samples": [dict(sample) for sample in samples],
        }
        return evidence

    def _runner_up(self) -> str | None:
        ranked = sorted(self.raw_vote_counts, key=lambda key: (-self.raw_vote_counts[key], key))
        return ranked[1] if len(ranked) > 1 else None


def _finite_confidence(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) and 0.0 <= numeric <= 1.0 else None


def _raw_name(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def _evidence_value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def classify_track_evidence(
    evidence: Iterable[Any],
    *,
    policy: ClassificationPolicy | None = None,
) -> ClassificationDecision:
    """Classify a track from deterministic, bounded observation evidence.

    Raw votes are retained even when confidence is missing or invalid.  Invalid
    confidence values are excluded from weighted sums and cause a conservative
    unweighted decision.  Ties and near ties are explicit AMBIGUOUS outcomes.
    """

    selected_policy = policy or ClassificationPolicy()
    observations = list(evidence)
    raw_vote_counts: dict[str, int] = {}
    weighted_scores: dict[str, float] = {}
    ids_by_name: dict[str, set[int]] = {}
    samples: list[tuple[int, str, int | None, float | None]] = []
    finite_confidences: list[float] = []
    missing_count = 0
    invalid_count = 0
    first_pts: int | None = None
    last_pts: int | None = None

    for ordinal, item in enumerate(observations):
        raw_name = _raw_name(_evidence_value(item, "native_class", _evidence_value(item, "raw_class_name")))
        raw_id_value = _evidence_value(item, "raw_class_id", _evidence_value(item, "class_id"))
        try:
            raw_id = int(raw_id_value) if raw_id_value is not None else None
        except (TypeError, ValueError):
            raw_id = None
        pts_value = _evidence_value(item, "pts_ms", _evidence_value(item, "timestamp_ms"))
        try:
            pts_ms = int(pts_value) if pts_value is not None else ordinal
        except (TypeError, ValueError):
            pts_ms = ordinal
        first_pts = pts_ms if first_pts is None else min(first_pts, pts_ms)
        last_pts = pts_ms if last_pts is None else max(last_pts, pts_ms)
        confidence_raw = _evidence_value(item, "confidence", _evidence_value(item, "detector_confidence"))
        confidence = _finite_confidence(confidence_raw)
        if confidence_raw is None:
            missing_count += 1
        elif confidence is None:
            invalid_count += 1
        else:
            finite_confidences.append(confidence)
        if raw_name is None:
            continue
        raw_vote_counts[raw_name] = raw_vote_counts.get(raw_name, 0) + 1
        if raw_id is not None:
            ids_by_name.setdefault(raw_name, set()).add(raw_id)
        if confidence is not None:
            weighted_scores[raw_name] = weighted_scores.get(raw_name, 0.0) + confidence
        if len(samples) < selected_policy.max_evidence_samples:
            samples.append((pts_ms, raw_name, raw_id, confidence))

    ordered_names = sorted(raw_vote_counts)
    total_votes = sum(raw_vote_counts.values())
    total_weight = sum(weighted_scores.values())
    warnings: list[str] = []
    if missing_count:
        warnings.append("missing_confidence_excluded_from_weighted_vote")
    if invalid_count:
        warnings.append("invalid_confidence_excluded_from_weighted_vote")
    use_unweighted = bool(missing_count or invalid_count or not weighted_scores or total_weight <= 0)
    if use_unweighted and weighted_scores and total_weight <= 0 and not (missing_count or invalid_count):
        warnings.append("zero_confidence_fallback_to_unweighted_vote")

    def rank_key(name: str) -> tuple[float, int, str, int]:
        score = raw_vote_counts[name] if use_unweighted else weighted_scores.get(name, 0.0)
        raw_id = min(ids_by_name.get(name, {2**31 - 1}))
        return (-score, -raw_vote_counts[name], name, raw_id)

    ranked = sorted(ordered_names, key=rank_key)
    winning_name = ranked[0] if ranked else None
    runner_name = ranked[1] if len(ranked) > 1 else None
    winning_votes = raw_vote_counts.get(winning_name or "", 0)
    winning_score = weighted_scores.get(winning_name or "", 0.0)
    winning_vote_share = winning_votes / total_votes if total_votes else 0.0
    winning_weighted_share = winning_score / total_weight if total_weight else 0.0
    near_tie = bool(runner_name and winning_vote_share - (raw_vote_counts[runner_name] / total_votes) < selected_policy.near_tie_margin)
    if not use_unweighted and runner_name and total_weight:
        near_tie = near_tie or winning_weighted_share - (weighted_scores.get(runner_name, 0.0) / total_weight) < selected_policy.near_tie_margin

    mapping = RAW_MAPPING_BY_NAME.get(winning_name or "")
    if total_votes == 0:
        status = ClassificationStatus.UNKNOWN
        reason = "no_raw_class_evidence"
        engineering = EngineeringClass.UNKNOWN.value
        provisional = "unknown"
    elif total_votes < selected_policy.min_observations:
        status = ClassificationStatus.INSUFFICIENT_EVIDENCE
        reason = "below_minimum_observation_count"
        engineering = EngineeringClass.UNKNOWN.value
        provisional = "unknown"
    elif mapping is None:
        status = ClassificationStatus.UNSUPPORTED_RAW_CLASS
        reason = "raw_class_not_in_pilot_mapping"
        engineering = EngineeringClass.UNKNOWN.value
        provisional = "unknown"
    elif near_tie or winning_vote_share < selected_policy.min_winning_vote_share or (
        not use_unweighted and winning_weighted_share < selected_policy.min_winning_weighted_share
    ):
        status = ClassificationStatus.AMBIGUOUS
        reason = "competing_raw_classes_or_vote_share_below_threshold"
        engineering = EngineeringClass.AMBIGUOUS.value
        provisional = "ambiguous"
    else:
        status = mapping.status
        reason = mapping.reason
        engineering = mapping.engineering_class.value
        provisional = mapping.provisional_class
        if status is ClassificationStatus.OTHER:
            reason = "explicit_other_mapping"

    return ClassificationDecision(
        raw_class_id=(min(ids_by_name.get(winning_name, {0})) if winning_name and ids_by_name.get(winning_name) else None),
        raw_class_name=winning_name,
        track_voted_raw_class_id=(min(ids_by_name.get(winning_name, {0})) if winning_name and ids_by_name.get(winning_name) else None),
        track_voted_raw_class_name=winning_name,
        provisional_class=provisional,
        engineering_class=engineering,
        classification_status=status.value,
        classification_reason=reason,
        raw_vote_counts=dict(sorted(raw_vote_counts.items())),
        weighted_scores={key: round(value, 6) for key, value in sorted(weighted_scores.items())},
        winning_vote_share=winning_vote_share,
        winning_weighted_share=winning_weighted_share,
        confidence_count=len(finite_confidences),
        confidence_missing_count=missing_count,
        confidence_invalid_count=invalid_count,
        confidence_min=min(finite_confidences) if finite_confidences else None,
        confidence_max=max(finite_confidences) if finite_confidences else None,
        confidence_mean=(sum(finite_confidences) / len(finite_confidences) if finite_confidences else None),
        eligible_observation_count=total_votes,
        first_pts_ms=first_pts,
        last_pts_ms=last_pts,
        policy_revision=selected_policy.revision,
        policy_thresholds=selected_policy.as_dict(),
        warnings=tuple(sorted(set(warnings))),
    )


def classification_decision_from_compact_evidence(
    evidence: Mapping[str, Any],
) -> tuple[ClassificationDecision, str | None]:
    """Rehydrate one authoritative decision from persisted compact evidence.

    The persisted evidence is an input to the decision, not a second source of
    class semantics.  Missing legacy fields are derived from the raw winner;
    contradictory fields are returned as an explicit consistency error.
    """

    def safe_int(value: Any, default: int | None = None) -> int | None:
        try:
            return int(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    def safe_float(value: Any, default: float = 0.0) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return default
        return numeric if math.isfinite(numeric) else default

    raw_vote_counts: dict[str, int] = {}
    raw_counts = evidence.get("raw_vote_counts")
    if isinstance(raw_counts, Mapping):
        for raw_name, count in raw_counts.items():
            normalized_name = _raw_name(raw_name)
            parsed_count = safe_int(count, 0) or 0
            if normalized_name and parsed_count > 0:
                raw_vote_counts[normalized_name] = parsed_count
    weighted_scores: dict[str, float] = {}
    weighted = evidence.get("weighted_scores")
    if isinstance(weighted, Mapping):
        for raw_name, score in weighted.items():
            normalized_name = _raw_name(raw_name)
            if normalized_name:
                weighted_scores[normalized_name] = round(max(0.0, safe_float(score)), 6)

    policy_thresholds = evidence.get("policy_thresholds")
    policy_thresholds = policy_thresholds if isinstance(policy_thresholds, Mapping) else {}
    observation_count = safe_int(
        evidence.get("eligible_classification_observation_count", evidence.get("observation_count")),
        sum(raw_vote_counts.values()),
    ) or 0
    minimum_observations = safe_int(policy_thresholds.get("min_observations"), 2) or 2
    persisted_status = str(evidence.get("classification_status") or ClassificationStatus.UNKNOWN.value)
    persisted_winner = _raw_name(evidence.get("winning_raw_class"))
    mapping = RAW_MAPPING_BY_NAME.get(persisted_winner or "")
    known_status = persisted_status in STATUS_ALLOWED_ENGINEERING_CLASSES
    error: str | None = None
    if not known_status:
        status_value = ClassificationStatus.UNKNOWN.value
        engineering_value = EngineeringClass.UNKNOWN.value
        provisional_value = "unknown"
        error = f"unsupported_classification_status:{persisted_status}"
    elif persisted_status in {
        ClassificationStatus.UNKNOWN.value,
        ClassificationStatus.AMBIGUOUS.value,
        ClassificationStatus.OTHER.value,
        ClassificationStatus.INSUFFICIENT_EVIDENCE.value,
        ClassificationStatus.UNSUPPORTED_RAW_CLASS.value,
    }:
        status_value = persisted_status
        engineering_value = next(iter(STATUS_ALLOWED_ENGINEERING_CLASSES[persisted_status]))
        provisional_value = {
            ClassificationStatus.AMBIGUOUS.value: "ambiguous",
            ClassificationStatus.OTHER.value: "other",
        }.get(persisted_status, "unknown")
    elif mapping is None or mapping.status.value != persisted_status:
        status_value = ClassificationStatus.UNKNOWN.value
        engineering_value = EngineeringClass.UNKNOWN.value
        provisional_value = "unknown"
        error = f"classification_status_mapping_mismatch:{persisted_status}:{persisted_winner or 'none'}"
    else:
        status_value = persisted_status
        engineering_value = mapping.engineering_class.value
        provisional_value = mapping.provisional_class

    if observation_count < minimum_observations and persisted_status not in {
        ClassificationStatus.UNKNOWN.value,
        ClassificationStatus.INSUFFICIENT_EVIDENCE.value,
    }:
        status_value = ClassificationStatus.INSUFFICIENT_EVIDENCE.value
        engineering_value = EngineeringClass.UNKNOWN.value
        provisional_value = "unknown"
        error = error or f"classification_evidence_below_minimum_observations:{observation_count}:{minimum_observations}"
    elif persisted_status == ClassificationStatus.INSUFFICIENT_EVIDENCE.value and observation_count >= minimum_observations:
        status_value = ClassificationStatus.UNKNOWN.value
        engineering_value = EngineeringClass.UNKNOWN.value
        provisional_value = "unknown"
        error = error or f"classification_status_observation_count_mismatch:{observation_count}:{minimum_observations}"

    persisted_engineering = evidence.get("engineering_class")
    if persisted_engineering is not None and str(persisted_engineering) != engineering_value:
        error = error or f"classification_status_class_mismatch:{status_value}:{persisted_engineering}"
    persisted_provisional = evidence.get("provisional_class")
    if persisted_provisional is not None and str(persisted_provisional) != provisional_value:
        error = error or f"classification_provisional_class_mismatch:{status_value}:{persisted_provisional}"

    confidence_stats = evidence.get("confidence_stats")
    confidence_stats = confidence_stats if isinstance(confidence_stats, Mapping) else {}
    decision = ClassificationDecision(
        raw_class_id=safe_int(evidence.get("winning_raw_class_id")),
        raw_class_name=persisted_winner,
        track_voted_raw_class_id=safe_int(evidence.get("winning_raw_class_id")),
        track_voted_raw_class_name=persisted_winner,
        provisional_class=provisional_value,
        engineering_class=engineering_value,
        classification_status=status_value,
        classification_reason=str(evidence.get("classification_reason") or "persisted_compact_evidence"),
        raw_vote_counts=dict(sorted(raw_vote_counts.items())),
        weighted_scores=dict(sorted(weighted_scores.items())),
        winning_vote_share=max(0.0, min(1.0, safe_float(evidence.get("winning_vote_share")))),
        winning_weighted_share=max(0.0, min(1.0, safe_float(evidence.get("winning_weighted_share")))),
        confidence_count=safe_int(confidence_stats.get("finite_count"), 0) or 0,
        confidence_missing_count=safe_int(confidence_stats.get("missing_count"), 0) or 0,
        confidence_invalid_count=safe_int(confidence_stats.get("invalid_count"), 0) or 0,
        confidence_min=(safe_float(confidence_stats.get("min")) if confidence_stats.get("min") is not None else None),
        confidence_max=(safe_float(confidence_stats.get("max")) if confidence_stats.get("max") is not None else None),
        confidence_mean=(safe_float(confidence_stats.get("mean")) if confidence_stats.get("mean") is not None else None),
        eligible_observation_count=observation_count,
        first_pts_ms=safe_int(evidence.get("first_pts_ms")),
        last_pts_ms=safe_int(evidence.get("last_pts_ms")),
        policy_revision=str(evidence.get("policy_revision") or CLASSIFICATION_POLICY_REVISION),
        policy_thresholds=dict(policy_thresholds),
        warnings=tuple(str(item) for item in evidence.get("warnings", ()) if item is not None),
    )
    error = error or classification_consistency_error(decision.classification_status, decision.engineering_class)
    return decision, error


def compact_track_evidence(
    observations: Iterable[Any],
    decision: ClassificationDecision,
    *,
    anchor_points: Iterable[Mapping[str, Any]] = (),
    line_crossings: Iterable[Mapping[str, Any]] = (),
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a bounded, normalized, detector-independent track evidence object."""

    samples: list[dict[str, Any]] = []
    for item in sorted(
        list(observations),
        key=lambda value: (
            int(_evidence_value(value, "pts_ms", _evidence_value(value, "timestamp_ms", 0))),
            int(_evidence_value(value, "source_frame_index", 0) or 0),
        ),
    ):
        if len(samples) >= 32:
            break
        x = _evidence_value(item, "x", _evidence_value(_evidence_value(item, "position", {}), "x"))
        y = _evidence_value(item, "y", _evidence_value(_evidence_value(item, "position", {}), "y"))
        try:
            x_value = float(x)
            y_value = float(y)
        except (TypeError, ValueError):
            continue
        samples.append(
            {
                "pts_ms": int(_evidence_value(item, "pts_ms", _evidence_value(item, "timestamp_ms", 0))),
                "source_frame_index": _evidence_value(item, "source_frame_index"),
                "x": round(max(0.0, min(1.0, x_value)), 6),
                "y": round(max(0.0, min(1.0, y_value)), 6),
            }
        )
    evidence = decision.evidence_json(samples)
    evidence.update(
        {
            "anchor_samples": samples,
            "line_crossings": [dict(item) for item in list(line_crossings)[:32]],
            "provenance": dict(provenance or {}),
        }
    )
    return evidence


@dataclass(frozen=True)
class TimeSemantics:
    absolute_event_time: str | None
    timezone_name: str | None
    status: str
    reason: str | None = None


def _parse_source_started_at(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("source_started_at must be timezone-aware")
    return parsed


def source_time_semantics(source: Mapping[str, Any], event_pts_ms: int | None = None) -> TimeSemantics:
    """Compute recording-local event time without fabricating it."""

    timezone_name = str(source.get("timezone_name") or source.get("user_start_timezone") or "").strip() or None
    if not bool(source.get("recording_time_configured")):
        return TimeSemantics(None, timezone_name, "UNCONFIGURED", "recording_time_not_configured")
    try:
        zone = ZoneInfo(timezone_name or "")
        started = _parse_source_started_at(source.get("source_started_at") or source.get("user_start_instant"))
        if event_pts_ms is None:
            return TimeSemantics(None, timezone_name, "CONFIRMED")
        offset_ms = int(source.get("source_offset_ms") or 0)
        event = (started + timedelta(milliseconds=offset_ms + int(event_pts_ms))).astimezone(zone)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return TimeSemantics(None, timezone_name, "INVALID_CONFIGURATION", "invalid_recording_time_configuration")
    return TimeSemantics(event.isoformat(), timezone_name, "CONFIRMED")


def _absolute_for_pts(source: Mapping[str, Any], pts_ms: int) -> str | None:
    semantics = source_time_semantics(source, pts_ms)
    return semantics.absolute_event_time


def build_intervals(source: Mapping[str, Any], *, bucket_minutes: int = INTERVAL_MINUTES) -> list[dict[str, Any]]:
    start = int(source.get("analysis_start_pts_ms") or source.get("analysis_window_start_ms") or 0)
    end_raw = source.get("analysis_end_pts_ms") or source.get("analysis_window_end_ms")
    if end_raw is None or int(end_raw) <= start:
        return []
    end = int(end_raw)
    bucket_ms = bucket_minutes * 60 * 1000
    origin = int(source.get("interval_origin_pts_ms") or start)
    first_index = math.floor((start - origin) / bucket_ms)
    intervals: list[dict[str, Any]] = []
    cursor = origin + first_index * bucket_ms
    index = 0
    while cursor < end:
        interval_start = max(start, cursor)
        interval_end = min(end, cursor + bucket_ms)
        if interval_start < interval_end:
            start_time = _absolute_for_pts(source, interval_start)
            end_time = _absolute_for_pts(source, interval_end)
            semantics = source_time_semantics(source, interval_start)
            if start_time and end_time:
                label = f"{start_time} – {end_time}"
            else:
                label = f"PTS {interval_start}–{interval_end} ms"
            intervals.append(
                {
                    "index": index,
                    "source_relative_start_pts_ms": interval_start,
                    "source_relative_end_pts_ms": interval_end,
                    "absolute_start": start_time,
                    "absolute_end": end_time,
                    "timezone_name": semantics.timezone_name,
                    "display_label": label,
                    "partial": interval_start != cursor or interval_end != cursor + bucket_ms,
                    "time_status": semantics.status,
                }
            )
            index += 1
        cursor += bucket_ms
    return intervals


def assign_interval(intervals: Sequence[Mapping[str, Any]], event_pts_ms: int) -> int | None:
    for interval in intervals:
        if int(interval["source_relative_start_pts_ms"]) <= event_pts_ms < int(interval["source_relative_end_pts_ms"]):
            return int(interval.get("index", interval.get("interval_index")))
    return None


def _line_order(events: Sequence[Mapping[str, Any]], configured_lines: Sequence[Mapping[str, Any]] = ()) -> list[str]:
    configured = [str(item.get("line_id") or item.get("id")) for item in configured_lines]
    observed = sorted({str(event.get("line_id") or event.get("counting_line_id") or "unknown") for event in events})
    return list(dict.fromkeys([item for item in configured if item] + observed))


def _class_order() -> list[str]:
    return [item.code for item in sorted(PILOT_TAXONOMY, key=lambda item: item.display_order)]


def aggregate_engineering_events(
    events: Iterable[Mapping[str, Any]],
    intervals: Sequence[Mapping[str, Any]],
    *,
    configured_lines: Sequence[Mapping[str, Any]] = (),
    source_event_total: int | None = None,
    invalid_direction_exclusions: Sequence[Mapping[str, Any]] = (),
    classification_exclusions: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Aggregate valid engineering events and retain source exclusions."""

    source_events = [dict(event) for event in events]
    normalized: list[dict[str, Any]] = []
    direction_exclusions = [dict(item) for item in invalid_direction_exclusions]
    class_exclusions = [dict(item) for item in classification_exclusions]
    for event in source_events:
        row = dict(event)
        pts = int(row.get("event_pts_ms", row.get("crossing_timestamp_ms", row.get("pts_ms", 0))))
        row["event_pts_ms"] = pts
        row["line_id"] = str(row.get("line_id") or row.get("counting_line_id") or "unknown")
        if "direction" in row:
            original_direction = row.get("direction")
        elif "canonical_direction" in row:
            original_direction = row.get("canonical_direction")
        else:
            original_direction = row.get("crossing_direction")
        canonical = canonical_direction(original_direction)
        if canonical is None:
            direction_exclusions.append(invalid_direction_exclusion(row, original_direction))
            continue
        row["direction"] = canonical
        row["engineering_class"] = str(row.get("engineering_class") or EngineeringClass.UNKNOWN.value)
        status = row.get("classification_status")
        if status is not None:
            error = classification_consistency_error(status, row["engineering_class"])
            if error:
                class_exclusions.append(classification_exclusion(row, error))
                continue
        row["interval_index"] = assign_interval(intervals, pts)
        normalized.append(row)
    normalized.sort(key=lambda row: (row["line_id"], row["direction"], row["engineering_class"], row["event_pts_ms"], str(row.get("technical_key", row.get("id", "")))))
    lines = _line_order(normalized, configured_lines)
    directions = ["A_TO_B", "B_TO_A"]
    classes = _class_order()
    for row in normalized:
        if row["engineering_class"] not in classes:
            classes.append(row["engineering_class"])

    line_totals = []
    direction_totals = []
    line_class_totals = []
    direction_class_matrix = []
    interval_rows = []
    for line_id in lines:
        line_events = [row for row in normalized if row["line_id"] == line_id]
        label = next((str(item.get("label") or item.get("line_name")) for item in configured_lines if str(item.get("line_id") or item.get("id")) == line_id), line_id)
        a_to_b = sum(row["direction"] == "A_TO_B" for row in line_events)
        b_to_a = sum(row["direction"] == "B_TO_A" for row in line_events)
        line_totals.append({"line_id": line_id, "line_name": label, "total": len(line_events), "a_to_b": a_to_b, "b_to_a": b_to_a})
        direction_totals.extend(
            [
                {"line_id": line_id, "line_name": label, "direction": "A_TO_B", "total": a_to_b},
                {"line_id": line_id, "line_name": label, "direction": "B_TO_A", "total": b_to_a},
            ]
        )
        for code in classes:
            line_class_totals.append({"line_id": line_id, "line_name": label, "engineering_class": code, "total": sum(row["engineering_class"] == code for row in line_events)})
            for direction in directions:
                direction_class_matrix.append({"line_id": line_id, "line_name": label, "direction": direction, "engineering_class": code, "total": sum(row["direction"] == direction and row["engineering_class"] == code for row in line_events)})

    for interval in intervals:
        for line_id in lines:
            label = next((str(item.get("label") or item.get("line_name")) for item in configured_lines if str(item.get("line_id") or item.get("id")) == line_id), line_id)
            for direction in directions:
                for code in classes:
                    interval_index_value = int(interval.get("index", interval.get("interval_index")))
                    count = sum(row["interval_index"] == interval_index_value and row["line_id"] == line_id and row["direction"] == direction and row["engineering_class"] == code for row in normalized)
                    interval_rows.append({"interval_index": interval_index_value, "line_id": line_id, "line_name": label, "direction": direction, "engineering_class": code, "count": count})

    return {
        "schema_version": ENGINEERING_SUMMARY_SCHEMA_VERSION,
        "overall_event_total": len(normalized),
        "source_event_total": len(source_events) if source_event_total is None else int(source_event_total),
        "invalid_direction_exclusions": direction_exclusions,
        "classification_exclusions": class_exclusions,
        "line_totals": line_totals,
        "direction_totals": direction_totals,
        "line_class_totals": line_class_totals,
        "direction_class_matrix": direction_class_matrix,
        "intervals": [dict(interval) for interval in intervals],
        "interval_rows": interval_rows,
        "events": normalized,
        "classes": classes,
        "directions": directions,
        "disclosures": [
            "automatic_classification_is_provisional",
            "overall_multi_line_total_is_event_total_not_unique_vehicle_total",
            "pilot_taxonomy_is_not_a_validated_tims_13_class_result",
        ],
    }


@dataclass(frozen=True)
class ReconciliationInvariant:
    identifier: str
    expected: int
    actual: int
    difference: int
    passed: bool


def reconcile_engineering_counts(
    events: Sequence[Mapping[str, Any]],
    aggregate: Mapping[str, Any],
    *,
    invalid_direction_exclusions: Sequence[Mapping[str, Any]] = (),
    classification_exclusions: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Check structural count invariants and gate engineering readiness."""

    line_totals = aggregate.get("line_totals", [])
    direction_rows = aggregate.get("direction_class_matrix", [])
    interval_rows = aggregate.get("interval_rows", [])
    valid_events = [dict(event) for event in aggregate.get("events", events)]

    def merge_exclusions(
        existing: Sequence[Mapping[str, Any]],
        additional: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in [*existing, *additional]:
            payload = dict(item)
            key = (
                str(payload.get("event_id") or ""),
                str(payload.get("technical_key") or ""),
                str(payload.get("reason") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(payload)
        return merged

    direction_exclusions = merge_exclusions(aggregate.get("invalid_direction_exclusions", []), invalid_direction_exclusions)
    class_exclusions = merge_exclusions(aggregate.get("classification_exclusions", []), classification_exclusions)
    invariants: list[ReconciliationInvariant] = []
    total = len(valid_events)
    line_sum = sum(int(row.get("total", 0)) for row in line_totals)
    invariants.append(ReconciliationInvariant("total_events_equals_sum_by_line", total, line_sum, line_sum - total, line_sum == total))
    for row in line_totals:
        expected = int(row.get("total", 0))
        actual = int(row.get("a_to_b", 0)) + int(row.get("b_to_a", 0))
        invariants.append(ReconciliationInvariant(f"line_total_equals_directions:{row.get('line_id')}", expected, actual, actual - expected, actual == expected))
    for line_id in sorted({str(row.get("line_id")) for row in direction_rows}):
        for direction in ("A_TO_B", "B_TO_A"):
            matrix_total = sum(int(row.get("total", 0)) for row in direction_rows if str(row.get("line_id")) == line_id and row.get("direction") == direction)
            event_total = sum(str(event.get("line_id")) == line_id and str(event.get("direction")) == direction for event in valid_events)
            invariants.append(ReconciliationInvariant(f"line_direction_equals_classes:{line_id}:{direction}", event_total, matrix_total, matrix_total - event_total, matrix_total == event_total))
    for line_id in sorted({str(row.get("line_id")) for row in interval_rows}):
        for direction in ("A_TO_B", "B_TO_A"):
            for code in sorted({str(row.get("engineering_class")) for row in interval_rows if str(row.get("line_id")) == line_id}):
                expected = sum(str(event.get("line_id")) == line_id and str(event.get("direction")) == direction and str(event.get("engineering_class")) == code for event in valid_events)
                actual = sum(int(row.get("count", 0)) for row in interval_rows if str(row.get("line_id")) == line_id and row.get("direction") == direction and str(row.get("engineering_class")) == code)
                invariants.append(ReconciliationInvariant(f"class_total_equals_intervals:{line_id}:{direction}:{code}", expected, actual, actual - expected, actual == expected))
    interval_total = sum(int(row.get("count", 0)) for row in interval_rows)
    invariants.append(ReconciliationInvariant("overall_interval_total_equals_event_total", total, interval_total, interval_total - total, interval_total == total))
    source_event_total = int(aggregate.get("source_event_total", len(events)))
    accounted_keys: set[tuple[str, str]] = set()
    for index, event in enumerate(valid_events):
        accounted_keys.add((str(event.get("source_event_id", event.get("id", f"valid:{index}"))), str(event.get("technical_key", ""))))
    for item in [*direction_exclusions, *class_exclusions]:
        accounted_keys.add((str(item.get("event_id") or f"excluded:{len(accounted_keys)}"), str(item.get("technical_key") or "")))
    accounted = len(accounted_keys)
    invariants.append(ReconciliationInvariant("source_events_accounted_for", source_event_total, accounted, accounted - source_event_total, accounted == source_event_total))
    invariants.append(ReconciliationInvariant("invalid_direction_exclusions_are_empty", 0, len(direction_exclusions), len(direction_exclusions), not direction_exclusions))
    invariants.append(ReconciliationInvariant("classification_consistency_exclusions_are_empty", 0, len(class_exclusions), len(class_exclusions), not class_exclusions))
    passed = all(item.passed for item in invariants)
    return {
        "status": "STRUCTURALLY_VALID" if passed else "STRUCTURALLY_INVALID",
        "engineering_ready": passed,
        "source_event_total": source_event_total,
        "invariants": [asdict(item) for item in invariants],
        "invalid_direction_exclusions": direction_exclusions,
        "classification_exclusions": class_exclusions,
        "result_revision": None,
    }


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def taxonomy_payload() -> dict[str, Any]:
    return {
        "revision": TAXONOMY_REVISION,
        "classes": [asdict(item) | {"capability_state": item.capability_state.value} for item in PILOT_TAXONOMY],
        "target_taxonomy": {
            "name": "DOH TIMS target taxonomy",
            "capability_state": CapabilityState.TARGET_ONLY.value,
            "status": "official_mapping_verification_pending",
            "non_claim": "COCO detector labels do not establish TIMS 13-class observability",
        },
    }


def mapping_payload() -> dict[str, Any]:
    return {"revision": MAPPING_REVISION, "mappings": [asdict(item) | {"engineering_class": item.engineering_class.value, "status": item.status.value} for item in RAW_MAPPINGS]}


def ensure_default_revisions(connection: sqlite3.Connection) -> None:
    """Seed immutable revision rows after migration, idempotently."""

    taxonomy = taxonomy_payload()
    mapping = mapping_payload()
    policy = ClassificationPolicy().as_dict()
    connection.execute(
        """
        INSERT OR IGNORE INTO taxonomy_revisions(id, revision, content_hash, content_json, status, created_at)
        VALUES (?, ?, ?, ?, 'ACTIVE', datetime('now'))
        """,
        (new_revision_id("tax"), TAXONOMY_REVISION, content_hash(taxonomy), canonical_json(taxonomy)),
    )
    taxonomy_row = connection.execute("SELECT id FROM taxonomy_revisions WHERE revision = ?", (TAXONOMY_REVISION,)).fetchone()
    if taxonomy_row:
        for entry in PILOT_TAXONOMY:
            connection.execute(
                """
                INSERT OR IGNORE INTO taxonomy_classes(id, taxonomy_revision_id, code, display_name_en, display_name_th, object_domain, capability_state, target_reference, display_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (new_revision_id("taxc", entry.code), taxonomy_row[0], entry.code, entry.display_name_en, entry.display_name_th, entry.object_domain, entry.capability_state.value, entry.target_reference, entry.display_order),
            )
    connection.execute(
        """
        INSERT OR IGNORE INTO taxonomy_mapping_revisions(id, revision, content_hash, content_json, status, created_at)
        VALUES (?, ?, ?, ?, 'ACTIVE', datetime('now'))
        """,
        (new_revision_id("map"), MAPPING_REVISION, content_hash(mapping), canonical_json(mapping)),
    )
    mapping_row = connection.execute("SELECT id FROM taxonomy_mapping_revisions WHERE revision = ?", (MAPPING_REVISION,)).fetchone()
    if mapping_row:
        for item in RAW_MAPPINGS:
            connection.execute(
                """
                INSERT OR IGNORE INTO taxonomy_mappings(id, mapping_revision_id, raw_class_name, engineering_class, provisional_class, status, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (new_revision_id("mapc", item.raw_class_name), mapping_row[0], item.raw_class_name, item.engineering_class.value, item.provisional_class, item.status.value, item.reason),
            )
    connection.execute(
        """
        INSERT OR IGNORE INTO classification_policy_revisions(id, revision, content_hash, content_json, status, created_at)
        VALUES (?, ?, ?, ?, 'ACTIVE', datetime('now'))
        """,
        (new_revision_id("pol"), CLASSIFICATION_POLICY_REVISION, content_hash(policy), canonical_json(policy)),
    )
    connection.commit()


def new_revision_id(prefix: str, salt: str = "") -> str:
    return f"{prefix}_{hashlib.sha256(f'{prefix}:{salt}:{TAXONOMY_REVISION}:{MAPPING_REVISION}:{CLASSIFICATION_POLICY_REVISION}'.encode()).hexdigest()[:16]}"


def revision_ids(connection: sqlite3.Connection) -> dict[str, str]:
    rows = {
        "taxonomy_revision_id": connection.execute("SELECT id FROM taxonomy_revisions WHERE revision = ?", (TAXONOMY_REVISION,)).fetchone(),
        "mapping_revision_id": connection.execute("SELECT id FROM taxonomy_mapping_revisions WHERE revision = ?", (MAPPING_REVISION,)).fetchone(),
        "classification_policy_revision_id": connection.execute("SELECT id FROM classification_policy_revisions WHERE revision = ?", (CLASSIFICATION_POLICY_REVISION,)).fetchone(),
    }
    return {key: row[0] for key, row in rows.items() if row is not None}


def classify_track_to_dict(evidence: Iterable[Any], *, policy: ClassificationPolicy | None = None) -> tuple[ClassificationDecision, dict[str, Any]]:
    items = list(evidence)
    decision = classify_track_evidence(items, policy=policy)
    ordered = sorted(
        items,
        key=lambda item: (int(_evidence_value(item, "pts_ms", _evidence_value(item, "timestamp_ms", 0))), str(_evidence_value(item, "native_class", ""))),
    )
    return decision, compact_track_evidence(ordered, decision)
