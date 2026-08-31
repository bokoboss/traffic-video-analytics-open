from __future__ import annotations

from datetime import datetime, timezone

from apps.backend.app.domain import TimeContract
from apps.backend.app.synthetic_counting import (
    CrossingDirection,
    ExclusionReason,
    CountingLine,
    CountingScene,
    Observation,
    Point,
    Roi,
    SyntheticTrack,
    SyntheticTrackSet,
    aggregate_events,
    execute_synthetic_counting,
    signed_side,
    validate_track_set,
)


def contract(start: int = 0, end: int = 3_600_000) -> TimeContract:
    return TimeContract(
        source_started_at=datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc),
        timezone_name="Asia/Bangkok",
        analysis_start_pts_ms=start,
        analysis_end_pts_ms=end,
        interval_origin_pts_ms=start,
    )


def line(direction_mode: str = "BIDIRECTIONAL") -> CountingLine:
    return CountingLine(
        line_id="line_main",
        label="Main",
        start=Point(0.2, 0.5),
        end=Point(0.8, 0.5),
        direction_mode=direction_mode,
        movement_name="through",
    )


def scene(*, rois: tuple[Roi, ...] = (), fingerprint: str = "src") -> CountingScene:
    return CountingScene(
        scene_revision="scn_1",
        source_fingerprint=fingerprint,
        counting_lines=(line(),),
        rois=rois,
    )


def track(track_id: str, points: list[tuple[int, float, float]], label: str = "unclassified") -> SyntheticTrack:
    return SyntheticTrack(
        track_id=track_id,
        synthetic_class=label,
        observations=tuple(Observation(ts, Point(x, y), f"s{index}") for index, (ts, x, y) in enumerate(points)),
    )


def tracks(*items: SyntheticTrack, fingerprint: str = "src", scene_revision: str | None = "scn_1") -> SyntheticTrackSet:
    return SyntheticTrackSet(
        schema_version="synthetic-tracks-v1",
        source_fingerprint=fingerprint,
        fixture_id="fixture",
        scene_revision=scene_revision,
        tracks=tuple(items),
    )


def run(*items: SyntheticTrack, scene_value: CountingScene | None = None, time: TimeContract | None = None):
    return execute_synthetic_counting("run_1", tracks(*items), scene_value or scene(), time or contract())


def reasons(result) -> set[ExclusionReason]:
    return {item.reason for item in result.exclusions}


def test_signed_side_convention_and_clean_directions() -> None:
    counting_line = line()
    assert signed_side(counting_line, Point(0.5, 0.6)) > 0
    assert signed_side(counting_line, Point(0.5, 0.4)) < 0

    a_to_b = run(track("t1", [(0, 0.5, 0.6), (1_000, 0.5, 0.4)]))
    b_to_a = run(track("t2", [(0, 0.5, 0.4), (1_000, 0.5, 0.6)]))
    assert a_to_b.events[0].direction == CrossingDirection.A_TO_B
    assert b_to_a.events[0].direction == CrossingDirection.B_TO_A
    assert a_to_b.events[0].readable_direction_label == "Side A -> Side B"


def test_finite_intersection_timestamp_and_bins_are_deterministic() -> None:
    result = run(track("t1", [(0, 0.5, 0.4), (1_800_000, 0.5, 0.6)], "car"))
    event = result.events[0]
    assert event.crossing_timestamp_ms == 900_000
    assert event.interpolation_parameter == 0.5
    assert event.reporting_bin_start_ms == 900_000
    assert result.aggregates["fifteen_minute_counts"] == [0, 1, 0, 0]
    assert aggregate_events(result.events, contract())["grand_total"] == len(result.events)

    repeat = run(track("t1", [(0, 0.5, 0.4), (1_800_000, 0.5, 0.6)], "car"))
    assert [event.event_id for event in result.events] == [event.event_id for event in repeat.events]


def test_no_crossing_parallel_touch_along_line_endpoint_and_outside_finite_line() -> None:
    no_crossing = run(track("n", [(0, 0.3, 0.4), (1_000, 0.7, 0.4)]))
    parallel = run(track("p", [(0, 0.3, 0.6), (1_000, 0.7, 0.6)]))
    touch = run(track("t", [(0, 0.5, 0.4), (1_000, 0.5, 0.5), (2_000, 0.55, 0.4)]))
    endpoint = run(track("e", [(0, 0.2, 0.4), (1_000, 0.2, 0.6)]))
    outside = run(track("o", [(0, 0.9, 0.4), (1_000, 0.9, 0.6)]))

    assert reasons(no_crossing) == {ExclusionReason.NO_CROSSING}
    assert reasons(parallel) == {ExclusionReason.NO_CROSSING}
    assert reasons(touch) == {ExclusionReason.LINE_TOUCH}
    assert ExclusionReason.ENDPOINT_CONTACT in reasons(endpoint)
    assert reasons(outside) == {ExclusionReason.OUTSIDE_FINITE_LINE}


def test_validation_handles_out_of_order_duplicate_zero_duration_and_mismatch() -> None:
    out_of_order = tracks(track("bad", [(1_000, 0.5, 0.4), (500, 0.5, 0.6)]))
    assert validate_track_set(out_of_order, scene()).errors[0]["code"] == "out_of_order_timestamp"

    duplicate = tracks(track("dup", [(0, 0.5, 0.4), (0, 0.5, 0.4), (1_000, 0.5, 0.6)]))
    assert validate_track_set(duplicate, scene()).warnings[0]["code"] == "duplicate_observation_suppressed"

    zero_duration = tracks(track("zero", [(0, 0.5, 0.4), (0, 0.5, 0.6)]))
    assert validate_track_set(zero_duration, scene()).errors[0]["code"] == "ambiguous_identical_timestamp"

    mismatch = tracks(track("m", [(0, 0.5, 0.4), (1_000, 0.5, 0.6)]), fingerprint="other")
    assert validate_track_set(mismatch, scene()).errors[0]["code"] == "source_fingerprint_mismatch"


def test_jitter_suppression_slow_crossing_and_legitimate_u_turn() -> None:
    jitter = run(track("j", [(0, 0.5, 0.499), (100, 0.5, 0.501), (200, 0.5, 0.499)]))
    slow = run(track("s", [(0, 0.5, 0.45), (500, 0.5, 0.4999), (1_000, 0.5, 0.550)]))
    u_turn = run(track("u", [(0, 0.5, 0.6), (1_000, 0.5, 0.4), (3_000, 0.5, 0.6)]))

    assert reasons(jitter) == {ExclusionReason.AMBIGUOUS_TOLERANCE}
    assert len(slow.events) == 1
    assert [event.direction for event in u_turn.events] == [CrossingDirection.A_TO_B, CrossingDirection.B_TO_A]


def test_roi_eligibility_boundary_and_analysis_window_edges() -> None:
    roi = Roi(
        roi_id="roi",
        label="ROI",
        vertices=(Point(0.3, 0.45), Point(0.7, 0.45), Point(0.7, 0.7), Point(0.3, 0.7)),
    )
    eligible = run(track("in", [(0, 0.5, 0.4), (1_000, 0.5, 0.6)]), scene_value=scene(rois=(roi,)))
    boundary = run(track("edge", [(0, 0.3, 0.4), (1_000, 0.3, 0.6)]), scene_value=scene(rois=(roi,)))
    ineligible = run(track("out", [(0, 0.75, 0.4), (1_000, 0.75, 0.6)]), scene_value=scene(rois=(roi,)))
    start_edge = run(track("start", [(0, 0.5, 0.4), (2_000, 0.5, 0.6)]), time=contract(1_000, 3_000))
    end_edge = run(track("end", [(0, 0.5, 0.4), (2_000, 0.5, 0.6)]), time=contract(0, 1_000))

    assert len(eligible.events) == 1
    assert len(boundary.events) == 1
    assert ExclusionReason.OUTSIDE_ROI in reasons(ineligible)
    assert start_edge.events[0].crossing_timestamp_ms == 1_000
    assert ExclusionReason.OUTSIDE_ANALYSIS_WINDOW in reasons(end_edge)


def test_multiple_lines_bidirectional_direction_filter_and_input_ordering() -> None:
    second = CountingLine("line_second", "Second", Point(0.2, 0.7), Point(0.8, 0.7), "BIDIRECTIONAL")
    filtered = CountingScene("scn_1", "src", (line("B_TO_A"), second), ())
    result = run(
        track("b", [(0, 0.5, 0.8), (1_000, 0.5, 0.6)]),
        track("a", [(0, 0.5, 0.4), (1_000, 0.5, 0.8)]),
        track("c", [(0, 0.4, 0.6), (1_000, 0.4, 0.4)]),
        scene_value=filtered,
    )

    assert any(exclusion.reason == ExclusionReason.DIRECTION_NOT_ALLOWED for exclusion in result.exclusions)
    assert [(event.crossing_timestamp_ms, event.line_id, event.track_id) for event in result.events] == sorted(
        (event.crossing_timestamp_ms, event.line_id, event.track_id) for event in result.events
    )
    assert {event.line_id for event in result.events} == {"line_main", "line_second"}

    reversed_input = run(
        track("a", [(0, 0.5, 0.4), (1_000, 0.5, 0.8)]),
        track("b", [(0, 0.5, 0.8), (1_000, 0.5, 0.6)]),
        track("c", [(0, 0.4, 0.6), (1_000, 0.4, 0.4)]),
        scene_value=filtered,
    )
    assert [event.event_id for event in result.events] == [event.event_id for event in reversed_input.events]
