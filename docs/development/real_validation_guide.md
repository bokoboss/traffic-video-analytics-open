# Real capability validation guide

This guide is for local engineering validation only. It is not a production
accuracy protocol.

## Before running

- use a rights-cleared local media file and keep it outside Git;
- record its fingerprint and source PTS/time semantics;
- confirm the scene revision and half-open preview segment;
- validate a configuration revision;
- confirm FFmpeg/ffprobe, Python packages, model registry, and weight SHA-256;
- choose `AUTO`, `CPU`, or `CUDA` and inspect the requested/resolved device.
- retain the configuration's canonical runtime hash and request provenance hash;
- verify the worker's actual model, weight SHA-256, tracker revision, and
  configured-vs-actual provenance status before interpreting the result.

## Evidence to retain

Record model-load time separately from first inference, warm inference timing,
decoded/processed frames, detections/tracks, short-track exclusions, GPU
memory, processing ratio, processed FPS, warnings, and representative overlay
evidence when available. A preview must be labeled `PREVIEW_ONLY` and
`NOT_PRODUCTION_RESULT`. Also retain the worker ID/attempt/heartbeat evidence
for operational review, while keeping private claim tokens and local paths out
of exported or user-facing evidence.

## Interpretation

Processing ratio and FPS are hardware observations; they do not establish
real-time performance. A runtime-ready result does not establish detector
accuracy. Accuracy requires 6C benchmark ground truth with rights and review
provenance. If those inputs are missing, create a `PENDING` capability record;
do not fill in accuracy or pilot qualification values.

## Recommended sequence

1. Run a 10–120 second bounded preview.
2. Inspect tracks, short-track exclusions, crossing events, warnings, and PTS.
3. Compare at least two immutable configurations when a trade-off matters.
4. Select `OPERATIONAL_CANDIDATE` only with an explicit rationale.
5. If the source and evidence are approved, create a separate 6C benchmark run.
6. Keep human review/correction/certification and production export in the
   later 6E scope.
