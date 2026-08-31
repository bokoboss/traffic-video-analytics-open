from __future__ import annotations

from datetime import datetime, timezone

from apps.backend.app.domain import (
    AutoCountEvent,
    ReviewAction,
    ReviewActionType,
    ResultState,
    TimeContract,
    bucket_counts,
    can_certify,
    can_export,
    interval_index,
    peak_hour_factor,
    project_effective_event,
    reporting_bin_start_ms,
    resolve_seek,
)


def contract() -> TimeContract:
    return TimeContract(
        source_started_at=datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc),
        timezone_name="Asia/Bangkok",
        analysis_start_pts_ms=0,
        analysis_end_pts_ms=3_600_000,
        interval_origin_pts_ms=0,
    )


def event(event_id: str, pts_ms: int) -> AutoCountEvent:
    return AutoCountEvent(
        id=event_id,
        run_id="run_1",
        technical_key=event_id,
        pts_ms=pts_ms,
        track_id=event_id,
        rule_id="line_1",
        object_domain="vehicle",
        classification="unknown",
        movement="northbound",
        confidence=0.5,
        qc_state="needs_review",
    )


def test_pts_time_mapping_and_half_open_boundaries() -> None:
    c = contract()
    assert c.event_time(1_000).isoformat().startswith("2026-01-01T07:00:01")
    assert interval_index(c, 0) == 0
    assert interval_index(c, 899_999) == 0
    assert interval_index(c, 900_000) == 1
    assert interval_index(c, 3_599_999) == 3
    assert interval_index(c, 3_600_000) is None


def test_four_consecutive_15_minute_buckets_and_hour_validation() -> None:
    counts = bucket_counts(contract(), [event("a", 0), event("b", 900_000), event("c", 1_800_000), event("d", 2_700_000)])
    assert counts == [1, 1, 1, 1]


def test_reporting_bin_origin_and_boundary_contract() -> None:
    c = contract()
    assert reporting_bin_start_ms(c, 0) == 0
    assert reporting_bin_start_ms(c, 899_999) == 0
    assert reporting_bin_start_ms(c, 900_000) == 900_000
    assert reporting_bin_start_ms(c, 3_600_000) is None


def test_seek_resolution_distinguishes_keyframe_decode_and_accepted_frame() -> None:
    resolved = resolve_seek(
        requested_pts_ms=1_100,
        available_pts_ms=[0, 1_000, 1_120, 1_160],
        keyframe_pts_ms=[0, 1_000],
        tolerance_ms=50,
    )
    assert resolved.decoder_seek_pts_ms == 1_000
    assert resolved.first_decoded_pts_ms == 1_000
    assert resolved.first_accepted_pts_ms == 1_120
    assert "decoded_from_prior_frame" in resolved.warnings


def test_phf_and_zero_volume_behavior() -> None:
    assert peak_hour_factor([3, 2, 1, 2]) == 0.667
    assert peak_hour_factor([0, 0, 0, 0]) is None


def test_review_overlay_and_undo_reversal_preserve_original() -> None:
    original = event("evt_1", 1_000)
    first = ReviewAction(
        id="rev_1",
        event_id="evt_1",
        action_type=ReviewActionType.CHANGE_CLASS,
        reviewer="a",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        new_classification="passenger_vehicle",
    )
    undo = ReviewAction(
        id="rev_2",
        event_id="evt_1",
        action_type=ReviewActionType.REVERSE,
        reviewer="a",
        created_at=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
        reverses_action_id="rev_1",
    )
    effective = project_effective_event(original, [first, undo])
    assert effective.original.classification == "unknown"
    assert effective.effective_classification == "unknown"


def test_certification_and_export_gates() -> None:
    assert can_certify(ResultState.STALE, 0, True)[0] is False
    assert can_certify(ResultState.REVIEW_COMPLETE, 1, False)[0] is False
    assert can_certify(ResultState.REVIEW_COMPLETE, 0, False)[0] is True
    assert can_export(ResultState.REVIEW_COMPLETE, certified=False, stale=False)[0] is False
    assert can_export(ResultState.CERTIFIED, certified=True, stale=False)[0] is True
    assert can_export(ResultState.EXPORTED, certified=True, stale=False)[0] is True
