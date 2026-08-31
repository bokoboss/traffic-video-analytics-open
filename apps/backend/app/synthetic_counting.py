from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import json
import math
from typing import Any, Iterable

from .engineering_outputs import (
    ClassificationPolicy,
    ClassificationStatus,
    compact_track_evidence,
    classify_track_evidence,
)

from .domain import TimeContract, interval_index, reporting_bin_start_ms


ENGINE_VERSION = "synthetic-crossing-engine-v1"
POLICY_VERSION = "synthetic-crossing-policy-v1"
SYNTHETIC_TRACK_SCHEMA_VERSION = "synthetic-tracks-v1"
REAL_TRACK_SCHEMA_VERSION = "real-tracks-v1"
REAL_TRACK_PROVENANCE = "real_inference"
BIDIRECTIONAL = "BIDIRECTIONAL"
LEGACY_DIRECTION_VALUES = {
    "a_to_b": "A_TO_B",
    "b_to_a": "B_TO_A",
    "bidirectional": BIDIRECTIONAL,
}


class CrossingDirection(StrEnum):
    A_TO_B = "A_TO_B"
    B_TO_A = "B_TO_A"


class ExclusionReason(StrEnum):
    INSUFFICIENT_OBSERVATIONS = "insufficient_observations"
    NO_CROSSING = "no_crossing"
    LINE_TOUCH = "line_touch"
    ALONG_LINE = "along_line"
    ENDPOINT_CONTACT = "endpoint_contact"
    OUTSIDE_FINITE_LINE = "outside_finite_line"
    AMBIGUOUS_TOLERANCE = "ambiguous_tolerance"
    OUTSIDE_ROI = "excluded_by_roi"
    OUTSIDE_ANALYSIS_WINDOW = "excluded_by_analysis_window"
    DIRECTION_NOT_ALLOWED = "direction_not_allowed"
    SUPPRESSED_DUPLICATE = "suppressed_duplicate"
    ZERO_DURATION = "zero_duration_pair"


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class BoundingBox:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class Observation:
    timestamp_ms: int
    position: Point
    sample_id: str | None = None
    bbox: BoundingBox | None = None
    source_frame_index: int | None = None
    raw_class_id: int | None = None
    raw_class_name: str | None = None
    detector_confidence: float | None = None


@dataclass(frozen=True)
class SyntheticTrack:
    track_id: str
    observations: tuple[Observation, ...]
    synthetic_class: str = "unclassified"
    confidence: float | None = None
    provenance: str = "synthetic"
    raw_class_id: int | None = None
    raw_class_name: str | None = None
    provisional_class: str | None = None
    classification_review_state: str = "needs_review"
    classification_evidence: tuple[dict[str, Any], ...] = ()
    engineering_class: str | None = None
    classification_status: str = "PROVISIONAL"
    classification_reason: str = "legacy_track_metadata"
    taxonomy_revision: str | None = None
    mapping_revision: str | None = None
    classification_policy_revision: str | None = None


@dataclass(frozen=True)
class SyntheticTrackSet:
    schema_version: str
    source_fingerprint: str
    fixture_id: str
    tracks: tuple[SyntheticTrack, ...]
    scene_revision: str | None = None
    taxonomy_version: str = "synthetic-provisional-v1"


@dataclass(frozen=True)
class CountingLine:
    line_id: str
    label: str
    start: Point
    end: Point
    direction_mode: str
    side_a_label: str = "Side A"
    side_b_label: str = "Side B"
    movement_name: str = ""
    active: bool = True


@dataclass(frozen=True)
class Roi:
    roi_id: str
    label: str
    vertices: tuple[Point, ...]
    active: bool = True


@dataclass(frozen=True)
class CountingScene:
    scene_revision: str
    source_fingerprint: str
    counting_lines: tuple[CountingLine, ...]
    rois: tuple[Roi, ...] = ()


@dataclass(frozen=True)
class CountingTolerances:
    point_on_line: float = 1e-9
    minimum_line_length: float = 0.01
    segment_parallelism: float = 1e-12
    endpoint_proximity: float = 1e-9
    duplicate_crossing_time_ms: int = 250
    spatial_hysteresis: float = 0.002
    minimum_side_distance: float = 0.002
    minimum_side_stability_frames: int = 1
    timestamp_equality_ms: int = 0


@dataclass(frozen=True)
class SyntheticValidationResult:
    valid: bool
    errors: tuple[dict[str, str], ...]
    warnings: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class CrossingEvent:
    event_id: str
    run_id: str
    source_fingerprint: str
    scene_revision: str
    line_id: str
    line_label: str
    side_a_label: str
    side_b_label: str
    track_id: str
    direction: CrossingDirection
    readable_direction_label: str
    crossing_timestamp_ms: int
    real_world_time: str
    reporting_bin_start_ms: int
    synthetic_class: str
    roi_eligible: bool
    previous_observation_index: int
    next_observation_index: int
    interpolation_parameter: float
    crossing_point: Point
    calculation_method: str
    engine_version: str
    policy_version: str
    status: str = "auto"
    provenance: str = "synthetic"
    raw_class_id: int | None = None
    raw_class_name: str | None = None
    provisional_class: str | None = None
    detector_confidence: float | None = None
    classification_review_state: str = "needs_review"
    source_frame_index: int | None = None
    source_frame_pts_ms: int | None = None
    absolute_event_time: str | None = None
    event_time_status: str = "confirmed"
    track_voted_raw_class_id: int | None = None
    track_voted_raw_class_name: str | None = None
    engineering_class: str | None = None
    classification_status: str = "PROVISIONAL"
    classification_reason: str = "legacy_event_metadata"
    taxonomy_revision: str | None = None
    mapping_revision: str | None = None
    classification_policy_revision: str | None = None
    classification_evidence: dict[str, Any] | None = None


@dataclass(frozen=True)
class CrossingExclusion:
    track_id: str
    line_id: str
    reason: ExclusionReason
    timestamp_ms: int | None = None
    detail: str = ""


@dataclass(frozen=True)
class CountingRunResult:
    run_id: str
    events: tuple[CrossingEvent, ...]
    exclusions: tuple[CrossingExclusion, ...]
    warnings: tuple[dict[str, str], ...]
    aggregates: dict[str, Any]
    engine_version: str = ENGINE_VERSION
    policy_version: str = POLICY_VERSION


def parse_track_set(payload: dict[str, Any]) -> SyntheticTrackSet:
    tracks = []
    for track in payload.get("tracks", []):
        observations = []
        for observation in track.get("observations", []):
            bbox = observation.get("bbox")
            observations.append(
                Observation(
                    timestamp_ms=int(observation["timestamp_ms"]),
                    position=Point(float(observation["x"]), float(observation["y"])),
                    sample_id=observation.get("sample_id"),
                    source_frame_index=(
                        int(observation["source_frame_index"])
                        if observation.get("source_frame_index") is not None
                        else None
                    ),
                    bbox=(
                        BoundingBox(
                            x=float(bbox["x"]),
                            y=float(bbox["y"]),
                            width=float(bbox["width"]),
                            height=float(bbox["height"]),
                        )
                        if isinstance(bbox, dict)
                        else None
                    ),
                    raw_class_id=(
                        int(observation["raw_class_id"])
                        if observation.get("raw_class_id") is not None
                        else None
                    ),
                    raw_class_name=(
                        str(observation["raw_class_name"])
                        if observation.get("raw_class_name") is not None
                        else None
                    ),
                    detector_confidence=observation.get("detector_confidence", observation.get("confidence")),
                )
            )
        tracks.append(
            SyntheticTrack(
                track_id=str(track["track_id"]),
                observations=tuple(observations),
                synthetic_class=str(track.get("synthetic_class", "unclassified")),
                confidence=track.get("confidence"),
                provenance=str(track.get("provenance", "synthetic")),
                raw_class_id=(int(track["raw_class_id"]) if track.get("raw_class_id") is not None else None),
                raw_class_name=(str(track["raw_class_name"]) if track.get("raw_class_name") is not None else None),
                provisional_class=(
                    str(track["provisional_class"]) if track.get("provisional_class") is not None else None
                ),
                classification_review_state=str(track.get("classification_review_state", "needs_review")),
                classification_evidence=tuple(track.get("classification_evidence", ()) or ()),
                engineering_class=(str(track["engineering_class"]) if track.get("engineering_class") is not None else None),
                classification_status=str(track.get("classification_status", "PROVISIONAL")),
                classification_reason=str(track.get("classification_reason", "legacy_track_metadata")),
                taxonomy_revision=(str(track["taxonomy_revision"]) if track.get("taxonomy_revision") is not None else None),
                mapping_revision=(str(track["mapping_revision"]) if track.get("mapping_revision") is not None else None),
                classification_policy_revision=(
                    str(track["classification_policy_revision"])
                    if track.get("classification_policy_revision") is not None
                    else None
                ),
            )
        )
    return SyntheticTrackSet(
        schema_version=str(payload.get("schema_version", "")),
        source_fingerprint=str(payload.get("source_fingerprint", "")),
        fixture_id=str(payload.get("fixture_id", "")),
        scene_revision=payload.get("scene_revision"),
        taxonomy_version=str(payload.get("taxonomy_version", "synthetic-provisional-v1")),
        tracks=tuple(tracks),
    )


def scene_from_geometry(scene_row: dict[str, Any]) -> CountingScene:
    geometry = json.loads(str(scene_row["geometry_json"]))
    lines = []
    for line in geometry.get("counting_lines", []):
        if line.get("active", True):
            lines.append(
                CountingLine(
                    line_id=str(line["id"]),
                    label=str(line.get("name") or line["id"]),
                    start=Point(float(line["start"]["x"]), float(line["start"]["y"])),
                    end=Point(float(line["end"]["x"]), float(line["end"]["y"])),
                    direction_mode=canonical_direction_mode(str(line.get("direction_mode", BIDIRECTIONAL))),
                    side_a_label=_side_label(line, "side_a_name", "direction_a_label", "Side A"),
                    side_b_label=_side_label(line, "side_b_name", "direction_b_label", "Side B"),
                    movement_name=str(line.get("movement_name") or line.get("name") or line["id"]),
                    active=True,
                )
            )
    rois = []
    for roi in geometry.get("rois", []):
        if roi.get("active", True):
            rois.append(
                Roi(
                    roi_id=str(roi["id"]),
                    label=str(roi.get("name") or roi["id"]),
                    vertices=tuple(Point(float(point["x"]), float(point["y"])) for point in roi["vertices"]),
                    active=True,
                )
            )
    return CountingScene(
        scene_revision=str(scene_row["id"]),
        source_fingerprint=str(scene_row["source_fingerprint_sha256"]),
        counting_lines=tuple(lines),
        rois=tuple(rois),
    )


def validate_track_set(track_set: SyntheticTrackSet, scene: CountingScene | None = None) -> SyntheticValidationResult:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    is_real = track_set.schema_version == REAL_TRACK_SCHEMA_VERSION
    if track_set.schema_version not in {SYNTHETIC_TRACK_SCHEMA_VERSION, REAL_TRACK_SCHEMA_VERSION}:
        errors.append({"code": "unsupported_schema_version", "field": "schema_version"})
    if not track_set.source_fingerprint:
        errors.append({"code": "missing_source_fingerprint", "field": "source_fingerprint"})
    if scene and track_set.source_fingerprint != scene.source_fingerprint:
        errors.append({"code": "source_fingerprint_mismatch", "field": "source_fingerprint"})
    if scene and track_set.scene_revision and track_set.scene_revision != scene.scene_revision:
        errors.append({"code": "scene_revision_mismatch", "field": "scene_revision"})
    seen_tracks: set[str] = set()
    for track_index, track in enumerate(track_set.tracks):
        prefix = f"tracks[{track_index}]"
        if not track.track_id:
            errors.append({"code": "missing_track_id", "field": f"{prefix}.track_id"})
        if track.track_id in seen_tracks:
            errors.append({"code": "duplicate_track_id", "field": f"{prefix}.track_id"})
        seen_tracks.add(track.track_id)
        expected_provenance = REAL_TRACK_PROVENANCE if is_real else "synthetic"
        if track.provenance != expected_provenance:
            errors.append({"code": "invalid_provenance", "field": f"{prefix}.provenance"})
        if len(track.observations) < 2:
            errors.append({"code": "insufficient_observations", "field": f"{prefix}.observations"})
        previous_ts: int | None = None
        seen_observations: set[tuple[int, float, float]] = set()
        for obs_index, observation in enumerate(track.observations):
            field = f"{prefix}.observations[{obs_index}]"
            if previous_ts is not None and observation.timestamp_ms < previous_ts:
                errors.append({"code": "out_of_order_timestamp", "field": f"{field}.timestamp_ms"})
            if previous_ts is not None and observation.timestamp_ms == previous_ts:
                duplicate_key = (observation.timestamp_ms, observation.position.x, observation.position.y)
                if duplicate_key in seen_observations:
                    warnings.append({"code": "duplicate_observation_suppressed", "field": field})
                else:
                    errors.append({"code": "ambiguous_identical_timestamp", "field": f"{field}.timestamp_ms"})
            previous_ts = observation.timestamp_ms
            seen_observations.add((observation.timestamp_ms, observation.position.x, observation.position.y))
            if not _valid_point(observation.position):
                errors.append({"code": "invalid_normalized_point", "field": f"{field}.position"})
            if observation.bbox and not _valid_bbox(observation.bbox):
                errors.append({"code": "invalid_normalized_bbox", "field": f"{field}.bbox"})
    return SyntheticValidationResult(valid=not errors, errors=tuple(errors), warnings=tuple(warnings))


def execute_synthetic_counting(
    run_id: str,
    track_set: SyntheticTrackSet,
    scene: CountingScene,
    time_contract: TimeContract,
    tolerances: CountingTolerances = CountingTolerances(),
    *,
    time_configured: bool = True,
    classification_policy: ClassificationPolicy | None = None,
) -> CountingRunResult:
    validation = validate_track_set(track_set, scene)
    if not validation.valid:
        return CountingRunResult(
            run_id=run_id,
            events=(),
            exclusions=(),
            warnings=validation.warnings,
            aggregates=aggregate_events((), time_contract),
        )

    classified_tracks = tuple(
        _prepare_track_classification(track, classification_policy) for track in track_set.tracks
    )
    events: list[CrossingEvent] = []
    exclusions: list[CrossingExclusion] = []
    for line in scene.counting_lines:
        if _distance(line.start, line.end) < tolerances.minimum_line_length:
            exclusions.extend(
                CrossingExclusion(track.track_id, line.line_id, ExclusionReason.INSUFFICIENT_OBSERVATIONS, detail="line too short")
                for track in track_set.tracks
            )
            continue
        for track in sorted(classified_tracks, key=lambda item: item.track_id):
            track_events, track_exclusions = _evaluate_track_line(
                run_id,
                track,
                line,
                scene,
                time_contract,
                tolerances,
                time_configured,
            )
            events.extend(track_events)
            exclusions.extend(track_exclusions)
    ordered_events = tuple(sorted(events, key=lambda event: (event.crossing_timestamp_ms, event.line_id, event.track_id, event.event_id)))
    return CountingRunResult(
        run_id=run_id,
        events=ordered_events,
        exclusions=tuple(exclusions),
        warnings=validation.warnings,
        aggregates=aggregate_events(ordered_events, time_contract),
    )


def signed_side(line: CountingLine, point: Point) -> float:
    return (line.end.x - line.start.x) * (point.y - line.start.y) - (line.end.y - line.start.y) * (point.x - line.start.x)


def canonical_direction_mode(value: str) -> str:
    normalized = value.strip()
    return LEGACY_DIRECTION_VALUES.get(normalized, normalized.upper())


def readable_direction_label(line: CountingLine, direction: CrossingDirection) -> str:
    if direction is CrossingDirection.A_TO_B:
        return f"{line.side_a_label or 'Side A'} -> {line.side_b_label or 'Side B'}"
    return f"{line.side_b_label or 'Side B'} -> {line.side_a_label or 'Side A'}"


def aggregate_events(events: Iterable[CrossingEvent], contract: TimeContract) -> dict[str, Any]:
    by_line: dict[str, int] = {}
    by_direction: dict[str, int] = {}
    by_class: dict[str, int] = {}
    by_line_direction: dict[str, int] = {}
    by_line_direction_class: dict[str, int] = {}
    bins = [0, 0, 0, 0]
    interval_counts: dict[int, int] = {}
    for event in events:
        by_line[event.line_id] = by_line.get(event.line_id, 0) + 1
        by_direction[event.direction.value] = by_direction.get(event.direction.value, 0) + 1
        by_class[event.synthetic_class] = by_class.get(event.synthetic_class, 0) + 1
        line_direction = f"{event.line_id}|{event.direction.value}"
        by_line_direction[line_direction] = by_line_direction.get(line_direction, 0) + 1
        line_direction_class = f"{event.line_id}|{event.direction.value}|{event.synthetic_class}"
        by_line_direction_class[line_direction_class] = by_line_direction_class.get(line_direction_class, 0) + 1
        index = interval_index(contract, event.crossing_timestamp_ms)
        if index is not None and 0 <= index < len(bins):
            bins[index] += 1
        if index is not None:
            interval_counts[index] = interval_counts.get(index, 0) + 1
    return {
        "grand_total": sum(bins),
        "fifteen_minute_counts": bins,
        "by_line": by_line,
        "by_direction": by_direction,
        "by_class": by_class,
        "by_line_direction": by_line_direction,
        "by_line_direction_class": by_line_direction_class,
        "interval_counts": [interval_counts[index] for index in sorted(interval_counts)],
    }


def _prepare_track_classification(
    track: SyntheticTrack,
    classification_policy: ClassificationPolicy | None = None,
) -> SyntheticTrack:
    """Apply the shared track policy while retaining a legacy fixture fallback."""

    evidence: list[Any] = list(track.classification_evidence)
    if evidence and not any(
        getattr(item, "native_class", None) or getattr(item, "raw_class_name", None)
        or (isinstance(item, dict) and (item.get("native_class") or item.get("raw_class_name")))
        for item in evidence
    ):
        evidence = []
    if not evidence:
        evidence = [observation for observation in track.observations if observation.raw_class_name]
    if not evidence and track.raw_class_name:
        evidence = [
            {
                "pts_ms": observation.timestamp_ms,
                "raw_class_id": track.raw_class_id,
                "native_class": track.raw_class_name,
                "confidence": track.confidence,
            }
            for observation in track.observations
        ]
    if not evidence and track.synthetic_class not in {"", "unclassified", "unknown", "ambiguous"}:
        legacy_raw_by_class = {
            "passenger_vehicle": (2, "car"),
            "motorcycle": (3, "motorcycle"),
            "bus": (5, "bus"),
            "heavy_vehicle_unspecified": (7, "truck"),
            "bicycle": (1, "bicycle"),
            "pedestrian": (0, "person"),
            "other": (None, "other"),
            "car": (2, "car"),
            "truck": (7, "truck"),
            "person": (0, "person"),
        }
        raw_id, raw_name = legacy_raw_by_class.get(track.synthetic_class, (None, track.synthetic_class))
        evidence = [
            {
                "pts_ms": observation.timestamp_ms,
                "raw_class_id": raw_id,
                "native_class": raw_name,
                "confidence": track.confidence,
            }
            for observation in track.observations
        ]
    if not evidence:
        return replace(
            track,
            synthetic_class="unknown",
            engineering_class="UNKNOWN",
            provisional_class="unknown",
            classification_status=ClassificationStatus.UNKNOWN.value,
            classification_reason="no_raw_class_evidence",
            taxonomy_revision="pilot-observable-taxonomy-v1",
            mapping_revision="pilot-observable-mapping-v1",
            classification_policy_revision="track-vote-policy-v1",
        )

    decision = classify_track_evidence(evidence, policy=classification_policy)
    compact = compact_track_evidence(evidence, decision)
    review_state = track.classification_review_state
    if review_state == "needs_review":
        review_state = (
            "ambiguous"
            if decision.classification_status == ClassificationStatus.AMBIGUOUS.value
            else "unknown"
            if decision.classification_status in {
                ClassificationStatus.UNKNOWN.value,
                ClassificationStatus.UNSUPPORTED_RAW_CLASS.value,
                ClassificationStatus.INSUFFICIENT_EVIDENCE.value,
            }
            else "accepted"
        )
    confidence = track.confidence
    if confidence is None:
        confidence = decision.confidence_mean
    return replace(
        track,
        synthetic_class=decision.provisional_class,
        confidence=confidence,
        raw_class_id=decision.track_voted_raw_class_id,
        raw_class_name=decision.track_voted_raw_class_name,
        provisional_class=decision.provisional_class,
        classification_review_state=review_state,
        classification_evidence=(compact,),
        engineering_class=decision.engineering_class,
        classification_status=decision.classification_status,
        classification_reason=decision.classification_reason,
        taxonomy_revision="pilot-observable-taxonomy-v1",
        mapping_revision="pilot-observable-mapping-v1",
        classification_policy_revision=decision.policy_revision,
    )


def canonical_fingerprint(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _evaluate_track_line(
    run_id: str,
    track: SyntheticTrack,
    line: CountingLine,
    scene: CountingScene,
    contract: TimeContract,
    tolerances: CountingTolerances,
    time_configured: bool,
) -> tuple[list[CrossingEvent], list[CrossingExclusion]]:
    events: list[CrossingEvent] = []
    exclusions: list[CrossingExclusion] = []
    observations = _usable_observations(track.observations)
    if len(observations) < 2:
        return [], [CrossingExclusion(track.track_id, line.line_id, ExclusionReason.INSUFFICIENT_OBSERVATIONS)]

    stable_index: int | None = None
    stable_side: int | None = None
    pending_side: int | None = None
    pending_side_count = 0
    last_event_ms: int | None = None
    saw_touch = False
    saw_along_line = False
    saw_outside_finite = False
    saw_zero_duration = False
    saw_ambiguous = False

    for index, observation in enumerate(observations):
        side_value = signed_side(line, observation.position) / _distance(line.start, line.end)
        current_side = _stable_side(side_value, tolerances)
        if abs(side_value) <= tolerances.point_on_line:
            saw_touch = True
        if current_side is None:
            saw_ambiguous = True
            continue
        if stable_side is None:
            stable_side = current_side
            stable_index = index
            continue
        if current_side == stable_side:
            stable_index = index
            pending_side = None
            pending_side_count = 0
            continue
        if pending_side != current_side:
            pending_side = current_side
            pending_side_count = 1
        else:
            pending_side_count += 1
        if pending_side_count < tolerances.minimum_side_stability_frames:
            continue
        previous = observations[stable_index if stable_index is not None else index - 1]
        if observation.timestamp_ms == previous.timestamp_ms:
            saw_zero_duration = True
            stable_side = current_side
            stable_index = index
            pending_side = None
            pending_side_count = 0
            continue
        intersection = _segment_intersection(previous.position, observation.position, line.start, line.end, tolerances)
        if intersection is None:
            saw_outside_finite = True
            stable_side = current_side
            stable_index = index
            pending_side = None
            pending_side_count = 0
            continue
        t, u, point = intersection
        if u <= tolerances.endpoint_proximity or u >= 1 - tolerances.endpoint_proximity:
            exclusions.append(CrossingExclusion(track.track_id, line.line_id, ExclusionReason.ENDPOINT_CONTACT, observation.timestamp_ms))
            stable_side = current_side
            stable_index = index
            pending_side = None
            pending_side_count = 0
            continue
        direction = CrossingDirection.A_TO_B if stable_side > current_side else CrossingDirection.B_TO_A
        direction_mode = canonical_direction_mode(line.direction_mode)
        if direction_mode != BIDIRECTIONAL and direction.value != direction_mode:
            exclusions.append(CrossingExclusion(track.track_id, line.line_id, ExclusionReason.DIRECTION_NOT_ALLOWED, observation.timestamp_ms))
            stable_side = current_side
            stable_index = index
            pending_side = None
            pending_side_count = 0
            continue
        crossing_ms = round(previous.timestamp_ms + t * (observation.timestamp_ms - previous.timestamp_ms))
        if last_event_ms is not None and crossing_ms - last_event_ms <= tolerances.duplicate_crossing_time_ms:
            exclusions.append(CrossingExclusion(track.track_id, line.line_id, ExclusionReason.SUPPRESSED_DUPLICATE, crossing_ms))
            stable_side = current_side
            stable_index = index
            pending_side = None
            pending_side_count = 0
            continue
        if not _in_analysis_window(contract, crossing_ms):
            exclusions.append(CrossingExclusion(track.track_id, line.line_id, ExclusionReason.OUTSIDE_ANALYSIS_WINDOW, crossing_ms))
            stable_side = current_side
            stable_index = index
            pending_side = None
            pending_side_count = 0
            continue
        if not _roi_eligible(scene.rois, point):
            exclusions.append(CrossingExclusion(track.track_id, line.line_id, ExclusionReason.OUTSIDE_ROI, crossing_ms))
            stable_side = current_side
            stable_index = index
            pending_side = None
            pending_side_count = 0
            continue
        bin_start = reporting_bin_start_ms(contract, crossing_ms)
        assert bin_start is not None
        event = CrossingEvent(
            event_id=_event_id(run_id, line.line_id, track.track_id, crossing_ms, direction.value),
            run_id=run_id,
            source_fingerprint=scene.source_fingerprint,
            scene_revision=scene.scene_revision,
            line_id=line.line_id,
            line_label=line.label,
            side_a_label=line.side_a_label,
            side_b_label=line.side_b_label,
            track_id=track.track_id,
            direction=direction,
            readable_direction_label=readable_direction_label(line, direction),
            crossing_timestamp_ms=crossing_ms,
            real_world_time=contract.event_time(crossing_ms).isoformat() if time_configured else "UNCONFIGURED",
            reporting_bin_start_ms=bin_start,
            synthetic_class=track.synthetic_class,
            roi_eligible=True,
            previous_observation_index=stable_index if stable_index is not None else index - 1,
            next_observation_index=index,
            interpolation_parameter=t,
            crossing_point=point,
            calculation_method="finite_segment_intersection_linear_time_interpolation",
            engine_version=ENGINE_VERSION,
            policy_version=POLICY_VERSION,
            provenance=track.provenance,
            raw_class_id=observation.raw_class_id if observation.raw_class_id is not None else track.raw_class_id,
            raw_class_name=observation.raw_class_name or track.raw_class_name,
            provisional_class=track.provisional_class or track.synthetic_class,
            detector_confidence=observation.detector_confidence if observation.detector_confidence is not None else track.confidence,
            classification_review_state=track.classification_review_state,
            source_frame_index=observation.source_frame_index,
            source_frame_pts_ms=observation.timestamp_ms,
            absolute_event_time=contract.event_time(crossing_ms).isoformat() if time_configured else None,
            event_time_status="confirmed" if time_configured else "unconfigured",
            track_voted_raw_class_id=track.raw_class_id,
            track_voted_raw_class_name=track.raw_class_name,
            engineering_class=track.engineering_class or track.provisional_class or track.synthetic_class,
            classification_status=track.classification_status,
            classification_reason=track.classification_reason,
            taxonomy_revision=track.taxonomy_revision,
            mapping_revision=track.mapping_revision,
            classification_policy_revision=track.classification_policy_revision,
            classification_evidence=(track.classification_evidence[0] if track.classification_evidence else None),
        )
        events.append(event)
        last_event_ms = crossing_ms
        stable_side = current_side
        stable_index = index
        pending_side = None
        pending_side_count = 0

    if not events and not exclusions:
        reason = ExclusionReason.NO_CROSSING
        if saw_zero_duration:
            reason = ExclusionReason.ZERO_DURATION
        elif saw_outside_finite:
            reason = ExclusionReason.OUTSIDE_FINITE_LINE
        elif saw_along_line:
            reason = ExclusionReason.ALONG_LINE
        elif saw_touch:
            reason = ExclusionReason.LINE_TOUCH
        elif saw_ambiguous:
            reason = ExclusionReason.AMBIGUOUS_TOLERANCE
        exclusions.append(CrossingExclusion(track.track_id, line.line_id, reason))
    return events, exclusions


def _segment_intersection(
    first_start: Point,
    first_end: Point,
    second_start: Point,
    second_end: Point,
    tolerances: CountingTolerances,
) -> tuple[float, float, Point] | None:
    rx = first_end.x - first_start.x
    ry = first_end.y - first_start.y
    sx = second_end.x - second_start.x
    sy = second_end.y - second_start.y
    denominator = rx * sy - ry * sx
    if abs(denominator) <= tolerances.segment_parallelism:
        return None
    qpx = second_start.x - first_start.x
    qpy = second_start.y - first_start.y
    t = (qpx * sy - qpy * sx) / denominator
    u = (qpx * ry - qpy * rx) / denominator
    if -tolerances.point_on_line <= t <= 1 + tolerances.point_on_line and -tolerances.point_on_line <= u <= 1 + tolerances.point_on_line:
        clamped_t = min(1.0, max(0.0, t))
        clamped_u = min(1.0, max(0.0, u))
        return (
            clamped_t,
            clamped_u,
            Point(first_start.x + clamped_t * rx, first_start.y + clamped_t * ry),
        )
    return None


def _stable_side(side_value: float, tolerances: CountingTolerances) -> int | None:
    if abs(side_value) < tolerances.minimum_side_distance + tolerances.spatial_hysteresis:
        return None
    return 1 if side_value > 0 else -1


def _side_label(line: dict[str, Any], canonical_key: str, legacy_key: str, fallback: str) -> str:
    value = str(line.get(canonical_key) or line.get(legacy_key) or "").strip()
    return value or fallback


def _usable_observations(observations: tuple[Observation, ...]) -> tuple[Observation, ...]:
    usable: list[Observation] = []
    previous_key: tuple[int, float, float] | None = None
    for observation in observations:
        key = (observation.timestamp_ms, observation.position.x, observation.position.y)
        if key == previous_key:
            continue
        usable.append(observation)
        previous_key = key
    return tuple(usable)


def _roi_eligible(rois: tuple[Roi, ...], point: Point) -> bool:
    active_rois = [roi for roi in rois if roi.active]
    if not active_rois:
        return True
    return any(_point_in_polygon_or_boundary(point, roi.vertices) for roi in active_rois)


def _point_in_polygon_or_boundary(point: Point, polygon: tuple[Point, ...]) -> bool:
    inside = False
    count = len(polygon)
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % count]
        if _point_on_segment(point, start, end):
            return True
        crosses = (start.y > point.y) != (end.y > point.y)
        if crosses:
            x_at_y = (end.x - start.x) * (point.y - start.y) / (end.y - start.y) + start.x
            if point.x <= x_at_y:
                inside = not inside
    return inside


def _point_on_segment(point: Point, start: Point, end: Point, tolerance: float = 1e-9) -> bool:
    cross = (end.x - start.x) * (point.y - start.y) - (end.y - start.y) * (point.x - start.x)
    if abs(cross) > tolerance:
        return False
    return (
        min(start.x, end.x) - tolerance <= point.x <= max(start.x, end.x) + tolerance
        and min(start.y, end.y) - tolerance <= point.y <= max(start.y, end.y) + tolerance
    )


def _in_analysis_window(contract: TimeContract, timestamp_ms: int) -> bool:
    return contract.analysis_start_pts_ms <= timestamp_ms < contract.analysis_end_pts_ms


def _valid_point(point: Point) -> bool:
    return math.isfinite(point.x) and math.isfinite(point.y) and 0 <= point.x <= 1 and 0 <= point.y <= 1


def _valid_bbox(bbox: BoundingBox) -> bool:
    return (
        math.isfinite(bbox.x)
        and math.isfinite(bbox.y)
        and math.isfinite(bbox.width)
        and math.isfinite(bbox.height)
        and bbox.width >= 0
        and bbox.height >= 0
        and 0 <= bbox.x <= 1
        and 0 <= bbox.y <= 1
        and bbox.x + bbox.width <= 1
        and bbox.y + bbox.height <= 1
    )


def _distance(a: Point, b: Point) -> float:
    return math.dist((a.x, a.y), (b.x, b.y))


def _event_id(run_id: str, line_id: str, track_id: str, crossing_ms: int, direction: str) -> str:
    digest = hashlib.sha256(f"{run_id}|{line_id}|{track_id}|{crossing_ms}|{direction}".encode("utf-8")).hexdigest()
    return f"evt_{digest[:24]}"
