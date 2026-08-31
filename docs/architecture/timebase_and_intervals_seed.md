# Timebase and Intervals Seed

## Authoritative time

Event time must derive from media timestamps or presentation timestamps (PTS) and the confirmed wall-clock start.

Store at least:

- source PTS
- time base
- elapsed time
- confirmed source start datetime
- timezone
- resulting event datetime

Nominal FPS may be displayed as metadata but must not be the authoritative clock.

## Seeking

To analyze from a requested time:

1. seek to an earlier decodable point or keyframe;
2. decode forward;
3. use PTS to include or exclude frames/events;
4. warm up tracking before the count-enabled range where required.

Milestone 2 implements a pure seek-resolution contract that reports:

- requested PTS;
- decoder seek PTS;
- nearest prior keyframe PTS when known;
- first decoded PTS;
- first accepted frame PTS at or after the requested PTS;
- tolerance and warnings.

The decoder keyframe is never treated as the requested analysis start unless its PTS actually satisfies the request.

## Interval semantics

Use half-open intervals:

```text
[start, end)
```

Example for a custom 07:07 start:

```text
[07:07, 07:22)
[07:22, 07:37)
[07:37, 07:52)
[07:52, 08:07)
```

An event exactly at 07:22 belongs only to the second interval.

Reporting bins are anchored at `interval_origin_pts_ms`, use 15-minute durations, and remain half-open `[start, end)`. An event exactly on a boundary belongs to the later bin. The final partial bin is valid for display, but PHF remains defined only for a complete one-hour window with four 15-minute bins.

Sub-second precision is preserved in milliseconds. Nominal FPS is never used as the authoritative clock; it may appear only as inspected metadata or a clearly labelled fallback when PTS is unavailable.

## PHF

For a complete one-hour window divided into four 15-minute intervals:

```text
PHF = hourly volume / (4 × maximum 15-minute volume)
```

Define behavior explicitly for:

- zero volume
- incomplete hour
- missing interval
- manually corrected events
- stale aggregates
- class/direction/movement filters
