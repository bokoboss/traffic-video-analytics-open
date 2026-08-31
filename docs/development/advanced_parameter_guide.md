# Advanced parameter guide

The expert form exposes only parameters connected to the current adapters and
canonical counting/classification engines. The authoritative schema is
available from `GET /api/v1/processing-parameter-schema`.

## Detection and sampling

`image_size` is one of the supported input sizes. `confidence_threshold` and
`iou_threshold` are bounded in `[0, 1]`. `frame_stride` is bounded and is
reported because skipping decoded frames can miss short crossings.
`class_allowlist` uses raw detector labels (`car`, `person`, `motorcycle`, and
the other registered labels); these are not TIMS class codes.

## Tracking and crossing

ByteTrack activation/buffer/IoU/consecutive-frame settings are persisted with
the adapter revision. Minimum track observations/duration exclude insufficient
evidence before crossing. The canonical bottom-center anchor, crossing
tolerance, spatial hysteresis, minimum movement distance, side stability, and
duplicate cooldown are passed to the deterministic counting engine. Events use
source-relative media PTS and `[start, end)` windows.

## Classification

Minimum observations, winning vote share, weighted share, and near-tie margin
are passed to the shared track-vote policy. Unsupported, ambiguous, and
insufficient evidence remain explicit states; the settings do not turn an
automatic class into a human certification.

## Validation behavior

Unknown keys, wrong types, unsupported values, invalid ranges, empty
allowlists, device failures, and windows exceeding known media duration return
typed validation errors. CPU fallback from `AUTO`, hysteresis concerns, and
resource trade-offs return warnings. A normalized field retains the requested
override and the resolved value in the immutable revision.

## Reset, diff, and comparison

Reset-to-profile clears guided and expert overrides and asks the backend to
resolve the selected profile baseline. The UI compares the resolved parameter
snapshot with the selected profile before submission; a changed value is
shown as a diff rather than being hidden behind a display label. Completed
previews from distinct immutable revisions can be compared on the same
source/scene segment. Without approved ground truth the comparison is
diagnostic only and does not display precision, recall, or an accuracy winner.
