# Traffic taxonomy and classification contract

## Scope

Milestone 6B introduces a versioned, pilot-observable classification layer
between raw detector evidence and engineering count outputs. It does not
qualify accuracy and it does not claim complete observability of the DOH TIMS
target taxonomy.

The layers are deliberately separate:

1. `raw_detector_class_id` and `raw_detector_class_name` are the detector's
   observation labels.
2. `track_voted_raw_class_id` and `track_voted_raw_class_name` are the result of
   the deterministic track vote.
3. `provisional_class` is the legacy-compatible lower-snake-case mapped label.
4. `engineering_class` is the stable uppercase pilot output dimension.
5. `classification_status` and `classification_reason` explain support or
   uncertainty.
6. taxonomy, mapping and policy revisions identify the exact interpretation.

## Pilot-observable classes

| Raw COCO label | Engineering class | Capability | Meaning |
|---|---|---|---|
| `car` | `PASSENGER_VEHICLE` | `PILOT_OBSERVABLE` | A coarse passenger-vehicle mapping |
| `motorcycle` | `MOTORCYCLE` | `PILOT_OBSERVABLE` | A motorcycle mapping |
| `bus` | `BUS` | `PILOT_OBSERVABLE` | A bus mapping |
| `truck` | `HEAVY_VEHICLE_UNSPECIFIED` | `PILOT_OBSERVABLE` | Heavy vehicle only; no body-type claim |
| `bicycle` | `BICYCLE` | `PILOT_OBSERVABLE` | A bicycle mapping |
| `person` | `PEDESTRIAN` | `PILOT_OBSERVABLE` | A separate pedestrian object domain |
| `other` | `OTHER` | `PILOT_OBSERVABLE` | Explicit catch-all mapping |
| missing/unsupported | `UNKNOWN` | `UNSUPPORTED` | Evidence is retained, but no pilot mapping is claimed |
| competing classes | `AMBIGUOUS` | `REQUIRES_REVIEW` | Evidence is retained and remains countable |

The target TIMS taxonomy is represented separately as `TARGET_ONLY` reference
information pending official source verification. COCO labels do not establish
TIMS codes or TIMS 13-class capability. This contract never infers pickup, van,
taxi, rigid truck, articulated truck, trailer, axle count or truck body type.

`CONFIRMED_MAPPING` means only that a deterministic pilot mapping was applied
with sufficient internal evidence. It does not mean accurate, survey-certified
or human-certified classification. `PROVISIONAL` is used for the intentionally
coarse truck mapping. Legacy rows without raw evidence are backfilled as
`UNKNOWN`; a legacy provisional label is never used to manufacture a specific
engineering class. All automatic
classification is disclosed as provisional in the API and UI.

## Classification invariant

`ClassificationDecision` is the single authority for `provisional_class`,
`engineering_class`, `classification_status` and `classification_reason`.
Persisted compact evidence includes the decision fields so older projections
can be rehydrated and checked without inventing a second mapping path.

| Status | Allowed engineering class | Conservative fallback |
|---|---|---|
| `CONFIRMED_MAPPING` | `PASSENGER_VEHICLE`, `MOTORCYCLE`, `BUS`, `BICYCLE`, `PEDESTRIAN` | `UNKNOWN` if the raw winner is missing or mapping is inconsistent |
| `PROVISIONAL` | `HEAVY_VEHICLE_UNSPECIFIED` | `UNKNOWN` |
| `OTHER` | `OTHER` | `UNKNOWN` |
| `AMBIGUOUS` | `AMBIGUOUS` | `AMBIGUOUS` |
| `UNKNOWN`, `INSUFFICIENT_EVIDENCE`, `UNSUPPORTED_RAW_CLASS` | `UNKNOWN` | `UNKNOWN` |

The invariant is checked when a result is generated and again when the API
reads a persisted projection. A contradiction is an explicit reconciliation
exclusion and blocks engineering readiness. Insufficient observations,
unsupported raw labels, missing evidence and legacy-only labels never receive
a specific class.

## Revisions

The seeded immutable revisions are:

- `pilot-observable-taxonomy-v1`
- `pilot-observable-mapping-v1`
- `track-vote-policy-v1`

The revision rows store canonical JSON and SHA-256 content hashes. A completed
run stores the three revision names and its engineering result revision points
to the immutable database rows. Changing code does not reinterpret historical
projections.

## Track vote policy

The default uncalibrated policy uses all observations with a raw detector label
as eligible classification observations. The preferred score is:

```text
weighted_score(class) = sum(finite detector confidence for class)
```

The retained evidence also includes raw vote counts, weighted scores, both
shares, confidence min/max/mean and missing/invalid counts. Defaults are:

- minimum observations: `2`;
- minimum winning raw vote share: `0.60`;
- minimum winning weighted share: `0.60`;
- near-tie margin: `0.10`;
- confidence range: `[0, 1]`.

Missing or non-finite/out-of-range confidence is excluded from weighted sums,
but the raw class vote remains. If weighted evidence is incomplete, the
decision uses the deterministic unweighted vote and records a warning. Equal
or near-equal competing classes become `AMBIGUOUS`; fewer than two usable raw
observations become `INSUFFICIENT_EVIDENCE`; an unmapped raw class becomes
`UNSUPPORTED_RAW_CLASS`. Ordering is independent of dictionary and database
row insertion order.

The thresholds are deterministic starting defaults, not benchmark-derived
calibration. Calibration is a Milestone 6C activity.

## Compact track evidence

`compact-track-evidence-v1` stores bounded JSON only: observation counts, vote
statistics, first/last PTS, policy thresholds, decision/reason, at most 32
normalized anchor samples in deterministic PTS/frame order, crossing references
and detector/tracker/configuration provenance. It stores no image bytes, tensors
or detector-specific result objects. Full frame-level benchmark evidence is
out of scope for 6B.
