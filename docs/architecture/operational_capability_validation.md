# Operational capability validation

6D reports whether the selected stack can be exercised in the current local
environment. It does not turn a runtime probe into an accuracy or pilot claim.

## Device contract

The request is `AUTO`, `CPU`, or `CUDA`. The worker records the requested mode
and the resolved device (`cpu` or `cuda:0`). `AUTO` may fall back to CPU with a
typed warning. An explicit CUDA request without a CUDA device is a typed
configuration/runtime failure; it is not silently treated as CUDA.

## Runtime evidence

The real adapter records, when available:

- model-load time;
- first inference time and warm inference summary;
- decoded/processed frames and detection/track counts;
- short-track exclusions;
- tracker update and crossing-engine time;
- GPU memory allocation/reservation;
- processed FPS and media-time/wall-time processing ratio;
- model/tracker revisions, weight identifier/SHA-256, and PTS semantics.

`real_time_status` remains `NOT_ESTABLISHED` until hardware-specific evidence
and an owner-approved validation protocol exist.

## Validation statuses

- `READY`: an environment or deterministic engineering check has completed;
- `PENDING`: approved media/ground truth or another required evidence source is
  missing;
- `NOT_READY`: the local runtime cannot run the requested capability;
- `INCOMPLETE`: a run failed before producing complete evidence.

Real-media and benchmark validation remain `PENDING` in the repository’s
default state because no rights-cleared reusable media or approved ground truth
is committed. A completed operational preview alone cannot produce accuracy,
qualification, or certification.

## Candidate selection

Selections are append-only and may be `PROFILE_DEFAULT`, `OPERATOR_SELECTED`,
`BENCHMARK_SUPPORTED`, or `DIAGNOSTIC_ONLY`. They produce an
`OPERATIONAL_CANDIDATE` or `DIAGNOSTIC_ONLY` status. `QUALIFIED_FOR_PILOT` is
owned by the 6C benchmark qualification gates and is never inferred here.
