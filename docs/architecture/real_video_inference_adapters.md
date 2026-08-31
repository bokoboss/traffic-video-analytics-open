# Real Video Inference Adapter Boundary

## Runtime boundary

FastAPI owns validation, job identity and SQLite persistence. It never imports
Ultralytics, loads model weights or decodes a long-running video. The launcher
starts `apps.worker.processing_worker` as a separate local process. The worker
claims one queued job, loads the optional AI runtime only after the job is
claimed, and writes result rows only during finalization.

```text
uploaded managed media
        |
        v
incremental FFmpeg decoder -- frame image + source-relative PTS
        |
        v
UltralyticsDetectorAdapter -- raw class id/name/confidence + pixel box
        |
        v
ByteTrackTrackerAdapter -- stable track id + box
        |
        v
normalized bottom-center observations
        |
        v
canonical signed-side crossing engine
        |
        v
immutable event ledger + review layer + aggregates
```

## Adapter contracts

`apps/backend/app/real_inference.py` owns the normalized adapter contracts.
Optional imports happen inside the worker execution path. The API process uses
only readiness probes and registry metadata.

- Detector: YOLO11n COCO, registry id
  `detector.ultralytics-yolo11n-coco`.
- Tracker: `trackers` ByteTrack, registry id
  `tracker.trackers-bytetrack`.
- Media: repository-local or PATH FFmpeg/ffprobe; source paths must resolve
  inside `TVA_LOCAL_DATA_DIR` and must not be symlinks.
- Counting: `execute_synthetic_counting` is the existing implementation-neutral
  engine. The name is retained for compatibility; real tracks use
  `real-tracks-v1` and `real_inference` provenance.

## Time and sampling

FFmpeg emits raw BGR frames incrementally. `showinfo` stderr is read by a
dedicated bounded thread and each decoded frame is paired with its media PTS.
The worker never derives authoritative event time from nominal FPS. The first
video PTS is used as the source-relative origin, and the source time contract
adds an absolute datetime only when `recording_time_configured=1`.

The default is every decoded frame (`frame_stride=1`). Sampling, anchor policy,
detector/tracker ids and thresholds are persisted in the job configuration.
The only supported anchor is the normalized bottom-center of the tracked box;
this is the point passed to the canonical line engine.

Tracks with fewer than two observations cannot define a crossing. They remain in
the persisted track-summary provenance, but are excluded from the normalized
counting input so one short-lived detector track cannot invalidate otherwise
countable tracks; the excluded count is recorded in run statistics.

Each completed real run also persists the effective detector and tracker
registry revisions, detector version, weight filename and verified SHA-256
checksum, resolved device, and timing statistics. These values are runtime
provenance, not user-editable configuration.

## Class policy

Raw COCO identity is preserved. The adapter uses the existing observable-class
map only to produce provisional labels: `pedestrian`, `bicycle`,
`motorcycle`, `passenger_vehicle`, `bus`, `truck` or `unknown`. No TIMS code is
assigned. Pedestrians remain a separate object domain. Fused class evidence
can be `unknown`, `ambiguous` or `needs_review`, and every automatic event is
still queued for human review.

## Job and failure semantics

Real jobs use the phases `VALIDATING`, `QUEUED`, `STARTING`,
`INITIALIZING_RUNTIME`, `LOADING_MODEL`, `PREPARING_MEDIA`, `DECODING`,
`PROCESSING`, `FINALIZING_EVENTS`, `AGGREGATING`, `PERSISTING_RESULTS` and
`COMPLETED` where applicable. Cancellation is cooperative: the worker polls
SQLite, terminates FFmpeg, discards in-memory partial observations and leaves
`result_ready=0`. Result rows are inserted in one SQLite transaction before the
job becomes complete.

## Readiness

`/api/v1/readiness` exposes an explicit `processing.real_inference` component
with detector, tracker, device, weight checksum, media-tool and worker state.
Readiness probing never loads the model. A real job is rejected when that
component is not ready; synthetic validation remains available.
