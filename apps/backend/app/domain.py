from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Iterable


class ReviewActionType(StrEnum):
    APPROVE = "approve"
    CHANGE_CLASS = "change_class"
    CHANGE_MOVEMENT = "change_movement"
    EXCLUDE = "exclude"
    ADD_MANUAL = "add_manual"
    REVERSE = "reverse"


class ResultState(StrEnum):
    DRAFT = "draft"
    SOURCE_READY = "source_ready"
    SCENE_CONFIGURED = "scene_configured"
    PROCESSING_COMPLETE = "processing_complete"
    NEEDS_REVIEW = "needs_review"
    REVIEW_COMPLETE = "review_complete"
    CERTIFIED = "certified"
    EXPORTED = "exported"
    STALE = "stale"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class TimeContract:
    source_started_at: datetime
    timezone_name: str
    analysis_start_pts_ms: int
    analysis_end_pts_ms: int
    interval_origin_pts_ms: int

    def __post_init__(self) -> None:
        if self.source_started_at.tzinfo is None:
            raise ValueError("source_started_at must be timezone-aware")
        if self.analysis_end_pts_ms <= self.analysis_start_pts_ms:
            raise ValueError("analysis_end_pts_ms must be greater than analysis_start_pts_ms")

    def event_time(self, pts_ms: int) -> datetime:
        return self.source_started_at + timedelta(milliseconds=pts_ms)

    def analysis_window_datetimes(self) -> tuple[datetime, datetime]:
        return (
            self.event_time(self.analysis_start_pts_ms),
            self.event_time(self.analysis_end_pts_ms),
        )


@dataclass(frozen=True)
class AutoCountEvent:
    id: str
    run_id: str
    technical_key: str
    pts_ms: int
    track_id: str
    rule_id: str
    object_domain: str
    classification: str
    movement: str
    confidence: float
    qc_state: str


@dataclass(frozen=True)
class ReviewAction:
    id: str
    event_id: str
    action_type: ReviewActionType
    reviewer: str
    created_at: datetime
    new_classification: str | None = None
    new_movement: str | None = None
    reason: str | None = None
    reverses_action_id: str | None = None


@dataclass(frozen=True)
class EffectiveEvent:
    original: AutoCountEvent
    effective_classification: str
    effective_movement: str
    included: bool
    review_state: str


def interval_index(contract: TimeContract, pts_ms: int, bucket_minutes: int = 15) -> int | None:
    """Return the half-open bucket index for an event PTS."""
    if pts_ms < contract.analysis_start_pts_ms or pts_ms >= contract.analysis_end_pts_ms:
        return None
    bucket_ms = bucket_minutes * 60 * 1000
    return (pts_ms - contract.interval_origin_pts_ms) // bucket_ms


def reporting_bin_start_ms(contract: TimeContract, pts_ms: int, bucket_minutes: int = 15) -> int | None:
    index = interval_index(contract, pts_ms, bucket_minutes)
    if index is None:
        return None
    return contract.interval_origin_pts_ms + index * bucket_minutes * 60 * 1000


@dataclass(frozen=True)
class SeekResolution:
    requested_pts_ms: int
    decoder_seek_pts_ms: int
    nearest_prior_keyframe_pts_ms: int | None
    first_decoded_pts_ms: int
    first_accepted_pts_ms: int
    tolerance_ms: int
    warnings: tuple[str, ...]


def resolve_seek(
    requested_pts_ms: int,
    available_pts_ms: Iterable[int],
    keyframe_pts_ms: Iterable[int],
    tolerance_ms: int = 100,
) -> SeekResolution:
    if requested_pts_ms < 0:
        raise ValueError("requested_pts_ms must be non-negative")
    frames = sorted(set(available_pts_ms))
    if not frames:
        raise ValueError("available_pts_ms must contain at least one frame timestamp")
    keyframes = sorted(pts for pts in set(keyframe_pts_ms) if pts <= requested_pts_ms)
    keyframe = keyframes[-1] if keyframes else None
    decoder_seek = keyframe if keyframe is not None else frames[0]
    decoded_candidates = [pts for pts in frames if pts >= decoder_seek]
    first_decoded = decoded_candidates[0]
    accepted_candidates = [pts for pts in frames if pts >= requested_pts_ms]
    if not accepted_candidates:
        raise ValueError("no frame exists at or after requested_pts_ms")
    first_accepted = accepted_candidates[0]
    warnings: list[str] = []
    if first_decoded != requested_pts_ms:
        warnings.append("decoded_from_prior_frame")
    if first_accepted - requested_pts_ms > tolerance_ms:
        warnings.append("accepted_frame_outside_tolerance")
    return SeekResolution(
        requested_pts_ms=requested_pts_ms,
        decoder_seek_pts_ms=decoder_seek,
        nearest_prior_keyframe_pts_ms=keyframe,
        first_decoded_pts_ms=first_decoded,
        first_accepted_pts_ms=first_accepted,
        tolerance_ms=tolerance_ms,
        warnings=tuple(warnings),
    )


def bucket_counts(contract: TimeContract, events: Iterable[AutoCountEvent]) -> list[int]:
    buckets = [0, 0, 0, 0]
    for event in events:
        index = interval_index(contract, event.pts_ms)
        if index is not None and 0 <= index < len(buckets):
            buckets[index] += 1
    return buckets


def peak_hour_factor(fifteen_minute_counts: list[int]) -> float | None:
    total = sum(fifteen_minute_counts)
    if total == 0:
        return None
    if len(fifteen_minute_counts) != 4:
        raise ValueError("PHF requires exactly four 15-minute buckets")
    peak = max(fifteen_minute_counts)
    if peak == 0:
        return None
    return round(total / (4 * peak), 3)


def project_effective_event(
    event: AutoCountEvent, review_actions: Iterable[ReviewAction]
) -> EffectiveEvent:
    active_actions = {action.id: action for action in review_actions if action.event_id == event.id}
    reversed_ids = {
        action.reverses_action_id
        for action in active_actions.values()
        if action.action_type == ReviewActionType.REVERSE and action.reverses_action_id
    }
    ordered = sorted(
        (action for action in active_actions.values() if action.id not in reversed_ids),
        key=lambda action: action.created_at,
    )

    effective = EffectiveEvent(
        original=event,
        effective_classification=event.classification,
        effective_movement=event.movement,
        included=True,
        review_state="unreviewed",
    )
    for action in ordered:
        if action.action_type == ReviewActionType.APPROVE:
            effective = replace(effective, review_state="approved")
        elif action.action_type == ReviewActionType.CHANGE_CLASS and action.new_classification:
            effective = replace(
                effective,
                effective_classification=action.new_classification,
                review_state="corrected",
            )
        elif action.action_type == ReviewActionType.CHANGE_MOVEMENT and action.new_movement:
            effective = replace(
                effective, effective_movement=action.new_movement, review_state="corrected"
            )
        elif action.action_type == ReviewActionType.EXCLUDE:
            effective = replace(effective, included=False, review_state="excluded")
    return effective


def can_certify(state: ResultState, unresolved_mandatory_qc: int, stale: bool) -> tuple[bool, str]:
    if stale or state == ResultState.STALE:
        return False, "Current results are stale and must be reprocessed."
    if state == ResultState.INCOMPLETE:
        return False, "Processing is incomplete."
    if unresolved_mandatory_qc > 0:
        return False, "Mandatory QC remains unresolved."
    if state not in {ResultState.REVIEW_COMPLETE, ResultState.CERTIFIED}:
        return False, "Review must be complete before certification."
    return True, "Certification is allowed."


def can_export(state: ResultState, certified: bool, stale: bool) -> tuple[bool, str]:
    if stale or state == ResultState.STALE:
        return False, "Stale results cannot be exported as current results."
    if state == ResultState.INCOMPLETE:
        return False, "Incomplete results cannot be exported."
    if not certified:
        return False, "Export requires a certification record for this result version."
    if state not in {ResultState.CERTIFIED, ResultState.EXPORTED}:
        return False, "Only certified current results can be exported."
    return True, "Export manifest can be generated."


UTC = timezone.utc
