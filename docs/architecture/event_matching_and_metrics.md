# Event matching and metric semantics

## Matching context

Both predicted and ground-truth events must carry the same source fingerprint,
scene revision, and counting line. The event time is source-relative PTS in
milliseconds; nominal frame number/FPS is never substituted for authoritative
time.

The default policy is `pts-one-to-one-v1`: exact canonical direction is the
primary candidate, timestamps must be within the configured tolerance, and a
deterministic Hungarian assignment minimizes timestamp distance with stable
ID tie-breaking. After that exact pass, remaining same-context candidates are
paired diagnostically only when both sides are still unmatched and their
directions are opposite. Each result has a primary category and a list of
secondary flags. A wrong-direction prediction competing with an already
consumed truth event is a `DUPLICATE_AUTOMATIC` primary row with a secondary
`DIRECTION_ERROR` flag; it does not consume the truth a second time.

Primary categories are `TRUE_POSITIVE`, `FALSE_POSITIVE`,
`FALSE_NEGATIVE`, `DUPLICATE_AUTOMATIC`, `DIRECTION_ERROR`, `IGNORED`, and
`UNSCORABLE`. `CLASS_ERROR` and `TIMESTAMP_OUTLIER` are secondary flags on a
matched event. Invalid/stale automatic events and non-valid/non-confirmed
ground truth do not enter the scored denominator.

For eligible scored events, the matcher emits auditable invariants:

```text
TP + DIRECTION_ERROR + FN == eligible_ground_truth
TP + DIRECTION_ERROR + DUPLICATE_AUTOMATIC + FP == eligible_automatic
```

It also asserts that each consuming match references at most one truth and one
automatic event. The primary `DIRECTION_ERROR` category contributes to the
effective precision/recall penalty; the secondary flag on a duplicate is a
diagnostic and is not counted as another consumed direction error.

## Metric families

- Event metrics report precision, recall, F1, false/direction/duplicate
  counts, sample sizes and denominator-zero behavior.
- Count metrics report signed/absolute error and explicit undefined status for
  zero ground-truth denominators by interval, line, direction, class, and PTS
  cumulative snapshots.
- Direction metrics expose canonical-direction confusion and accuracy.
- Class metrics use the pilot-observable engineering taxonomy only. They retain
  `UNKNOWN`, `AMBIGUOUS`, heavy-vehicle diagnostics and an explicit disclosure
  that this is not TIMS 13-class accuracy.
- Timestamp metrics report signed error, absolute error, mean/median/p90/p95/
  maximum, by line/configuration, with `source_relative_pts_ms` authority.
- Duplicate/miss metrics identify their denominators. Event idempotency does
  not claim to solve track fragmentation.
- Fragmentation metrics are `AVAILABLE` only when ground-truth identity and
  matched automatic track evidence exist. Otherwise the report says
  `UNAVAILABLE` or `NO_MATCHED_IDENTITIES` and does not infer fragments from
  event totals.
- Throughput retains decoded/processed counts, wall time, FPS when available,
  processing/video-duration ratio, detector/tracker/device/resource fields,
  and an explicit `NOT_CLAIMED` real-time criterion.
- Qualification threshold evaluation resolves only the documented metric-path
  allowlist. A null metric, invalid metric, or zero-denominator ratio fails
  closed by default; `ALLOW_UNDEFINED` is available only when explicitly
  declared in the approved policy. No threshold is inferred from a fixture or
  a model score.

## Audit trail

Each persisted match references automatic/ground-truth IDs, technical key,
line, directions, timestamp error, duplicate IDs, and category flags. Metric
snapshots, configuration hashes, source checksum, code commit, environment
redaction, taxonomy/mapping/policy revisions, and qualification gates are
stored alongside the report.

When the selected automatic result is stale, structurally invalid, missing
reconciliation, or not engineering-ready, the persistence adapter records an
immutable `INCOMPLETE`/`NOT_SCORABLE` benchmark run and does not persist match
rows or metric snapshots. This prevents an empty or partial result from being
presented as zero error.
