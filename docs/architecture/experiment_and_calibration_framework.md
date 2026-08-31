# Experiment and calibration framework

## Configuration

Experiment configurations are immutable content-hashed records. The current
adapter contract exposes detector confidence, inference image size, frame
stride, tracker configuration/buffer, bottom-center crossing anchor, matching
tolerance, and classification policy thresholds. `tracker_match_threshold` is
explicitly unavailable until the runtime exposes it; the suite rejects it
instead of pretending it was controlled.

Every candidate carries its configuration hash/revision and the processing
configuration is kept distinct from evaluation policy. Evaluation identity also
includes the normalized qualification-policy hash and automatic-result
revision, so a retry against changed evidence cannot reuse an old report.

## Split isolation

Calibration parameters may be selected only from `CALIBRATION` data. `HOLDOUT`
data is evaluated after selection and is never fed back into the candidate
choice. `DIAGNOSTIC_ONLY` sources can explain failure modes but cannot qualify
a pilot. The run and report retain the split and isolation gate.

## Pareto comparison

The comparison reports non-dominated candidates over recall/precision,
duplicate and direction error, interval/timestamp error, class macro F1,
fragmentation, processing ratio and resource use when those measurements are
available. Missing measurements are not imputed. The result is
`PARETO_CANDIDATES` and is not a weighted optimization or approval.

## Qualification

Qualification is a policy gate, not a model score. It requires rights and
checksum evidence, adjudicated ground truth, scene/revision provenance,
current structurally valid 6B output, complete required metrics, holdout
isolation, reproducibility metadata and an owner-approved threshold policy.
The policy is `qualification-threshold-v1`: it declares corpus revision, split,
minimum source/event support, condition coverage, and mandatory comparisons over
an allowlisted metric path with a typed unit and deterministic operator. Invalid
paths/operators/units/numeric encodings and undefined required metrics fail
closed. A valid `HOLDOUT` policy can produce `QUALIFIED_FOR_PILOT` only when all
integrity gates and mandatory thresholds pass. Calibration and diagnostic
comparisons remain non-qualifying, and no result can become
`PRODUCTION_APPROVED` in Milestone 6C.
