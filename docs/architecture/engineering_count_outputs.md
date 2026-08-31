# Engineering count outputs

Milestone 6B keeps `auto_count_events` and `crossing_event_ledger` immutable.
The engineering layer is a separate projection generated atomically after a
run's events are written. Review corrections remain append-only review actions;
6B does not add the full 6D correction workspace.

## Event projection

`engineering_event_projections` records the source event, source fingerprint,
scene revision, configured line and physical side names, canonical direction,
readable direction label, source-relative crossing PTS, optional source frame
reference, recording-local time, raw and track-voted classes, provisional and
engineering classes, status/reason, three revisions, confidence summary,
track-evidence reference, processing provenance, QC state and staleness.

The canonical direction remains exactly `A_TO_B` or `B_TO_A`. Side labels
describe the physical sides and never redefine the canonical direction. A
source value such as `northbound`, lowercase `a_to_b`, an empty value or
`null` is not coerced. The source event remains in the immutable 6A history,
but it is excluded from every engineering dimension and recorded in
`reconciliation.invalid_direction_exclusions` with the event ID, technical
key, original value, expected domain and count difference. Any such exclusion
makes the result `STRUCTURALLY_INVALID` and `engineering_ready=false`.

## Time semantics

`event_pts_ms` is authoritative in the source timeline. Frame index divided by
nominal FPS is never substituted for FFmpeg PTS. With explicit recording start,
an aware datetime and a valid IANA timezone, `absolute_event_time` is computed
as recording start plus configured source offset plus event PTS. Otherwise the
projection reports `UNCONFIGURED`; invalid values report
`INVALID_CONFIGURATION`; no timestamp is fabricated.

Intervals are half-open `[start, end)`, use the configured analysis window and
support arbitrary duration, hour/date boundaries and a partial final interval.
Each interval stores source-relative start/end PTS, optional absolute start/end,
timezone, display label, status and a partial flag. Thus an event exactly at
08:15:00 belongs to the interval beginning at 08:15:00.

## Structured dimensions

The normalized summary tables support:

- line total with `A_TO_B` and `B_TO_A` subtotals;
- line × engineering class total;
- line × canonical direction × engineering class matrix;
- interval × line × direction × engineering class rows;
- an overall event total.

Unknown and ambiguous events remain included in these dimensions. For multiple
counting lines, the overall number is explicitly an event total, not a
deduplicated vehicle-flow total. One track may legitimately create one event at
each configured line. Turning-movement matrices are out of scope.

Classification dimensions are accepted only when the persisted
`classification_status` and `engineering_class` form one allowed pair. A
contradictory projection is excluded from dimensions and appears in
`reconciliation.classification_exclusions`; the live API recomputes this check
so a later projection mutation cannot make a result appear ready.

## Reconciliation

Every completed projection stores a reconciliation report. It checks total
events against line totals, line totals against direction totals, direction
totals against class totals, class totals against interval rows, and overall
interval total against event total. It also checks that all source events are
accounted for as valid projections or explicit exclusions and that both
exclusion lists are empty. A failed report is
`STRUCTURALLY_INVALID` and sets `engineering_ready=false`; it is not labelled
accurate or certified.

## Persistence and migration

Migration `010_milestone_6b_engineering_outputs.sql` adds immutable taxonomy,
mapping and policy revision tables, result revisions, bounded track evidence,
event projections, intervals, normalized summary rows and reconciliation
reports. It adds safe defaults to existing track summaries and aggregate
snapshots, backfills source time status from the existing configuration flag,
and leaves historical 6A events unchanged. Staleness updates the engineering
projection state without mutating the source event ledger.
