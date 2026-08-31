# API Contract

The foundation API is versioned under `/api/v1`.

## Endpoints

- `GET /api/v1/health`
- `GET /api/v1/release`
- `GET /api/v1/readiness`
- `POST /api/v1/projects`
- `GET /api/v1/projects`
- `GET /api/v1/projects/{project_id}`
- `POST /api/v1/projects/{project_id}/processing-runs/{run_id}/select`
- `PUT /api/v1/projects/{project_id}`
- `POST /api/v1/sources`
- `POST /api/v1/projects/{project_id}/sources/upload`
- `GET /api/v1/media-runtime`
- `POST /api/v1/projects/{project_id}/reference-frames`
- `GET /api/v1/projects/{project_id}/reference-frames/{frame_id}/preview`
- `PUT /api/v1/projects/{project_id}/time-configuration`
- `POST /api/v1/scenes`
- `GET /api/v1/projects/{project_id}/scene-configuration`
- `POST /api/v1/projects/{project_id}/scene-configuration/validate`
- `PUT /api/v1/projects/{project_id}/scene-configuration`
- `POST /api/v1/projects/{project_id}/mock-analysis`
- `POST /api/v1/projects/{project_id}/processing-jobs`
- `GET /api/v1/projects/{project_id}/processing-jobs`
- `GET /api/v1/processing-jobs/{run_id}`
- `POST /api/v1/processing-jobs/{run_id}/cancel`
- `POST /api/v1/processing-jobs/{run_id}/retry`
- `POST /api/v1/runs/{run_id}/segments/{segment_index}/retry`
- `GET /api/v1/runs/{run_id}/events`
- `GET /api/v1/runs/{run_id}/events?limit={limit}&offset={offset}`
- `GET /api/v1/runs/{run_id}/crossing-events?limit={limit}&offset={offset}`
- `POST /api/v1/review-actions`
- `POST /api/v1/projects/{project_id}/review-complete`
- `POST /api/v1/projects/{project_id}/runs/{run_id}/certifications`
- `POST /api/v1/projects/{project_id}/runs/{run_id}/exports/{export_format}`

## Milestone 6C benchmark contract

The additive benchmark API is:

- `GET /api/v1/benchmarks/overview`
- `GET /api/v1/benchmarks/experiments?limit={limit}&offset={offset}` for
  persisted candidate/Pareto comparisons
- `GET /api/v1/benchmarks/corpora` and `GET /api/v1/benchmarks/corpora/{revision}`
- `POST /api/v1/benchmarks/corpora/validate` and
  `POST /api/v1/benchmarks/corpora/import`
- `POST /api/v1/benchmarks/ground-truth/validate`,
  `POST /api/v1/benchmarks/ground-truth/import`, and
  `GET /api/v1/benchmarks/ground-truth/{benchmark_source_id}`
- `POST /api/v1/benchmarks/runs/evaluate`
- `GET /api/v1/benchmarks/runs`,
  `GET /api/v1/benchmarks/runs/{benchmark_run_id}`,
  `GET /api/v1/benchmarks/runs/{benchmark_run_id}/matches`,
  `/metrics`, and `/qualification`.

Corpus and ground-truth imports validate before transactional persistence.
Response payloads expose rights, split, source fingerprint, revision,
qualification, metric and report provenance. They do not expose media bytes or
private local paths. Benchmark runs reference immutable automatic/engineering
outputs and do not mutate them. Qualification status is explicitly non-
production until policy and evidence gates pass.

`POST /api/v1/benchmarks/runs/evaluate` accepts an optional typed
`approved_policy` with `schema_version: "qualification-threshold-v1"`, policy
approval metadata, applicable corpus revision, required split, minimum source
and eligible ground-truth counts, condition coverage, and one or more
thresholds. Each threshold has an allowlisted `metric_path`, one of `GTE`,
`LTE`, `GT`, `LT`, or `EQ`, a numeric (not numeric-string) `required_value`, a
compatible `unit`, `required`, and explicit `undefined_behavior` of
`FAIL_CLOSED` or `ALLOW_UNDEFINED`. Unknown paths, operators, units, malformed
approval metadata and non-finite values are rejected before persistence.

Qualification responses expose the normalized policy, per-threshold observed
value/availability/pass/failure reason, all gate reasons, and
`automatic_result_blockers`. The persisted states distinguish `NOT_RUN`,
`INCOMPLETE`/`NOT_SCORABLE`, `HOLDOUT_INSUFFICIENT`, `NOT_QUALIFIED`, and
`QUALIFIED_FOR_PILOT`. A stale, structurally invalid, missing-reconciliation,
or non-engineering-ready automatic result creates an `INCOMPLETE` run without
headline metric snapshots or match rows; it never becomes a zero-valued
score.

Match diagnostics are deterministically ordered and paginated with `limit`,
`offset`, and optional `category` filtering. They retain false, missed,
duplicate, direction, class, timestamp and unscorable categories without
mutating automatic events. Experiment comparison reads expose calibration and
holdout split metadata, candidate configurations, evaluated runs, and the
descriptive Pareto frontier; they do not invent a weighted approval score.

The match payload includes primary-category counts and accounting invariants:
`TP + DIRECTION_ERROR + FN == eligible_gt` and
`TP + DIRECTION_ERROR + DUPLICATE_AUTOMATIC + FALSE_POSITIVE == eligible_auto`.
Wrong-direction duplicates may carry a secondary `DIRECTION_ERROR` flag but do
not consume a truth event twice. Experiment comparison responses are explicitly
calibration/descriptive and include `no_automatic_pilot_claim: true`.

Generate the OpenAPI artifact with:

```powershell
python scripts/export_openapi.py
```

Generate the frontend TypeScript API types with:

```powershell
python scripts/generate_ts_client.py
```

Or run both with:

```powershell
pnpm run generate:api
```

`pnpm run check:api` regenerates both artifacts and fails when the generated OpenAPI or TypeScript output is stale.

## Milestone 6E review, certification and export contract

The 6E contract is additive to the legacy review/certification routes above.
The versioned workflow is:

- `POST /api/v1/projects/{project_id}/review-sessions`
- `GET /api/v1/projects/{project_id}/review-sessions`
- `GET /api/v1/review-sessions/{session_id}`
- `GET /api/v1/review-sessions/{session_id}/actions`
- `POST /api/v1/review-sessions/{session_id}/actions`
- `POST /api/v1/review-sessions/{session_id}/actions/reverse`
- `GET /api/v1/review-sessions/{session_id}/queue`
- `GET /api/v1/review-sessions/{session_id}/events/{event_id}/evidence`
- `GET /api/v1/review-sessions/{session_id}/progress`
- `POST /api/v1/review-sessions/{session_id}/projections`
- `GET /api/v1/review-sessions/{session_id}/projections`
- `POST /api/v1/review-sessions/{session_id}/reconciliation`
- `POST /api/v1/review-sessions/{session_id}/complete`
- `POST /api/v1/review-sessions/{session_id}/supersede`
- `POST /api/v1/review-sessions/{session_id}/certification`
- `GET /api/v1/projects/{project_id}/certifications`
- `GET /api/v1/certifications/{certification_id}`
- `POST /api/v1/certifications/{certification_id}/revoke`
- `POST /api/v1/certifications/{certification_id}/exports`
- `GET /api/v1/certifications/{certification_id}/exports`
- `GET /api/v1/exports/{export_revision_id}`
- `GET /api/v1/export-artifacts/{artifact_id}/download`

Review sessions accept only explicit scopes: `FULL_RESULT`, `LINE`,
`TIME_INTERVAL` and `DIAGNOSTIC_SUBSET`. Actions use the closed vocabulary in
the review overlay model and carry `expected_review_revision`. A stale revision
returns HTTP 409 with the current revision and actions since the expected
revision. Material corrections require a reason; manual additions require
line, canonical direction, PTS and evidence/observation metadata.

Completion requires a current result, passed reconciliation and zero unreviewed
events within the selected scope. Certification requires completion and a
reviewer identity. Partial scopes are explicitly disclosed. Export requires a
current non-revoked certification and the exact projection/reconciliation
revision recorded by that certification. Export responses include a safe
filename, artifact ID, content length and SHA-256; they do not expose private
absolute paths.

The queue is bounded and deterministically ordered. It supports review status,
line, direction, class, origin, classification status, corrected/duplicate
flags, automatic-versus-human origin, half-open `start_pts_ms`/`end_pts_ms`,
confidence bounds when detector confidence evidence is available, and an
optional benchmark error category when 6C diagnostics are attached.

## Milestone 2 Local Source Contract

Browser code must not send arbitrary local filesystem paths and the backend must not trust browser path strings. The supported local media flow is explicit user selection through a file input and multipart upload to:

```text
POST /api/v1/projects/{project_id}/sources/upload
```

The backend stores a managed local copy under `TVA_LOCAL_DATA_DIR`, computes a full-file SHA-256 fingerprint, records the original filename separately from the managed path, and attempts metadata inspection with `ffprobe` when available.

`PUT /api/v1/projects/{project_id}/time-configuration` records the user-confirmed real-world start instant, IANA timezone, source offset, and half-open analysis window.

The API explicitly identifies the TIMS taxonomy as pending official verification and the project license as provisional.

## Pilot runtime and current-run contract

`GET /api/v1/health` is a liveness response and includes application, pilot
release version and Git SHA; it intentionally does not include processing
readiness. `GET /api/v1/release` returns the same operator identity without
local filesystem paths.

`GET /api/v1/readiness` distinguishes three explicit concepts: `core_status`
(`CORE_READY`), `processing_status` (`PROCESSING_READY`) and
`application_status`/`application_ready` (`APPLICATION_READY`). Core readiness
covers the API, SQLite/migrations, managed artifact storage and disk space.
Processing capability covers the separate worker boundary, FFmpeg/FFprobe,
optional model runtime, detector/tracker, CUDA claim and real-inference gate.
The worker component is READY only when the backend sees the launcher-owned
worker row with a matching instance identity and a heartbeat no older than five
seconds; absent, exited and expired workers are OFFLINE or STALE and block
processing. Synthetic capability is reported separately and never turns the
real-inference component green. The frontend component is based on a live
localhost HTTP probe. A component exposes canonical status, required flag,
version, detail and remediation. Optional real-video components do not prevent
a synthetic workflow, but they prevent `PROCESSING_READY` for real media.

Project snapshots expose `selected_run_id`, `selected_run_explicit`, bounded
`processing_runs`, selected `processing_configuration`, previews and
`stale_warnings`. Migration 017 assigns the latest existing completed run as
the initial current run for legacy projects; the first completed run becomes
the initial current run for new projects. Later runs do not silently replace
it. The selection endpoint accepts only a run with `result_ready=true`;
certification/export state is filtered to the selected processing run. Migration
018 stores the worker heartbeat fields needed for backend-observable liveness;
the instance token and internal PID are never returned by the API. All
source events and historical runs remain intact.

## Scene and Reference Frame Contract

Reference-frame extraction requires an uploaded persisted source and local `ffmpeg`/`ffprobe` executables. Preview images are served only through project-scoped routes and are not exposed by local filesystem path.

Current scene saves use `scene-geometry-v2`, normalized coordinates in `[0, 1]`,
and top-left origin in orientation-adjusted reference-frame display space.
Backend validation is authoritative.

Counting lines are undirected side-transition boundaries. Each line stores
ordered endpoints for the signed-side convention, optional `side_a_name` and
`side_b_name`, and an active-direction setting of `A_TO_B`, `B_TO_A` or
`BIDIRECTIONAL`. Legacy `scene-geometry-v1` payloads with lowercase
`a_to_b`/`b_to_a`/`bidirectional` and `direction_a_label`/`direction_b_label`
load deterministically through compatibility mapping, but new saves use the v2
contract.

Crossing event outputs persist canonical `crossing_direction` values
`A_TO_B`/`B_TO_A`, the event-time side labels and a readable direction label.
Review actions and exports must preserve the canonical direction independently
of readable labels.

## Milestone 5.3A migration-readiness additions

Event-list endpoints support `limit` and `offset` query parameters so the future
React review/results surfaces can request bounded pages instead of loading all
events into browser state. The default page size is 1000 and the maximum page
size is 5000.

Backend request timing is available only when `TRAFFIC_APP_DIAGNOSTICS=1`.
Diagnostic responses include `X-TVA-Request-ID` and `Server-Timing` headers.
Normal runtime stays low-noise by default.

## Milestone 6A Processing Contract

`POST /api/v1/projects/{project_id}/processing-jobs` accepts an explicit
`mode` of `SYNTHETIC` or `REAL_VIDEO`. Real jobs snapshot detector/tracker
configuration, requested device, sampling and anchor policy, then wait for the
separate worker. `/api/v1/readiness` exposes detector, tracker, weight
checksum, media-tool and device capability state under
`processing.real_inference`; the API does not load model weights during
startup. Completed real jobs return persisted detector/tracker revisions,
weight identifier/checksum, resolved device, timing statistics and
`processing_stats` alongside the existing event endpoints.

## Milestone 6B Engineering Output Contract

The versioned read contracts are:

- `GET /api/v1/taxonomy` and `GET /api/v1/taxonomy/{revision}`;
- `GET /api/v1/classification-policy`;
- `GET /api/v1/runs/{run_id}/engineering-summary`;
- `GET /api/v1/runs/{run_id}/engineering-events`;
- `GET /api/v1/runs/{run_id}/tracks/{track_id}/evidence`;
- `GET /api/v1/runs/{run_id}/engineering/line-summary`;
- `GET /api/v1/runs/{run_id}/engineering/direction-summary`;
- `GET /api/v1/runs/{run_id}/engineering/class-direction-matrix`;
- `GET /api/v1/runs/{run_id}/engineering/intervals` and
  `/engineering/interval-rows`;
- `GET /api/v1/runs/{run_id}/engineering/reconciliation`;
- `GET /api/v1/runs/{run_id}/engineering/disclosures`.

The primary summary and event responses are typed Pydantic contracts. The
summary exposes line totals, line/class totals, direction/class matrix,
interval rows, source-time status, revision names, provenance and a typed
reconciliation report. Engineering events expose raw detector class,
track-voted raw class, provisional class, engineering class, classification
status/reason, PTS, absolute time, timezone, evidence reference and staleness.

Automatic classifications are provisional. `engineering_ready` means only that
the persisted projection passed structural reconciliation; it does not mean
accurate, TIMS-qualified or survey-certified. `UNKNOWN` and `AMBIGUOUS` events
remain in totals. When several lines are configured, the overall total is an
event total and is not a unique-vehicle total.

The typed reconciliation response also contains:

- `invalid_direction_exclusions`: source events whose original direction was
  not exactly `A_TO_B` or `B_TO_A`, including `event_id`, `technical_key`,
  `original_direction`, `expected_domain`, `expected_count`,
  `resulting_count`, and `resulting_count_difference`;
- `classification_exclusions`: source/projection rows whose status and
  engineering class violate the shared decision invariant, including the
  persisted fields, allowed classes, count difference and reason.

Both lists are auditable diagnostics. A non-empty list makes the report
`STRUCTURALLY_INVALID` and `engineering_ready=false`; invalid events are not
included in any engineering dimension, while the immutable 6A source event is
unchanged. The read path recomputes these checks so contradictory persisted
projections cannot be reported as ready.

## Milestone 6D Operational Processing Contract

Profile and parameter contracts:

- `GET /api/v1/processing-profiles`
- `GET /api/v1/processing-profiles/{profile_code}?profile_revision={revision}`
- `GET /api/v1/processing-parameter-schema`
- `POST /api/v1/projects/{project_id}/processing-configurations/resolve`
- `POST /api/v1/projects/{project_id}/processing-configurations`
- `GET /api/v1/processing-configurations/{revision_id}`

The resolve/create requests use closed typed guided and expert models. Unknown
expert keys are rejected. Responses include requested/resolved values,
normalizations, typed errors/warnings, adapter support, resource impact,
parameter schema revision, device request, and configuration/request
identities. `configuration_hash` is the normalized pre-execution configuration
identity; `request_provenance_hash` preserves the operator request and
normalization history. `runtime_configuration_hash` remains null until a worker
resolves executable runtime state, then completed preview/full-run statistics
include configured and actual canonical payloads, hashes, field differences,
and provenance status. A created configuration revision is immutable and can be
reused by retries, previews, and full processing jobs.

Normal `REAL_VIDEO` full-run submission references the saved immutable
`processing_configuration_revision_id`; the operator path sends no fixture
identity or ad-hoc configuration override. `fixture_id` is synthetic-only:
explicit `fixture_id` on a REAL_VIDEO processing request is rejected with
typed validation, while SYNTHETIC processing retains its existing default
fixture compatibility. A saved configuration revision from another project is
rejected.

Preview and comparison contracts:

- `POST /api/v1/projects/{project_id}/previews`
- `GET /api/v1/projects/{project_id}/previews`
- `GET /api/v1/previews/{preview_id}`
- `POST /api/v1/previews/{preview_id}/cancel`
- `POST /api/v1/previews/{preview_id}/promote`
- `POST /api/v1/projects/{project_id}/processing-configurations/diff`
- `POST /api/v1/projects/{project_id}/configuration-comparisons`
- `POST /api/v1/projects/{project_id}/operational-candidates`
- `POST /api/v1/projects/{project_id}/capability-validations`
- `GET /api/v1/projects/{project_id}/capability-validations`

Preview requests use bounded source-relative `[start_pts_ms, end_pts_ms)`
segments (maximum 120 seconds), source/scene/configuration identity, a mode,
and optional idempotency key. `fixture_id` is required only for `SYNTHETIC`
preview requests and must be omitted for `REAL_VIDEO`; a synthetic request is
rejected when the authoritative source is a managed `local_upload`. The
API accepts `REAL_VIDEO` preview creation only for a fingerprinted,
media-ready `local_upload` with a managed media path, and the requested window
must remain inside the saved source analysis window. The
operator workspace routes a current media-ready `local_upload` to `REAL_VIDEO`
only when real-inference readiness is available, otherwise it blocks with the
reported readiness reason. Only the explicit approved fixture identity routes
to `SYNTHETIC`; unknown sources are not silently treated as fixtures. The
persisted request fingerprint also
includes the runtime hash and scene semantic hash. Reusing a key with a changed
fingerprint returns HTTP 409 with typed code `IDEMPOTENCY_CONFLICT`; identical
requests return the original preview. Responses disclose `PREVIEW_ONLY` and
`NOT_PRODUCTION_RESULT`; preview events do not appear in `/runs/{run_id}/events`
or 6B engineering results. Comparison rows are deterministic and descriptive;
they expose no opaque weighted ranking and state that accuracy requires 6C
ground truth. A configuration diff compares resolved values without selecting a
winner and explicitly discloses runtime-equivalent inputs only when both runtime
hashes match. Queued/running previews can be cancelled cooperatively, and
expired worker leases return to the queue. Claim ownership, heartbeat, attempt,
cancellation, and state-transition information is persisted but private claim
tokens and local media paths are never returned by the API. Candidate selection can record `OPERATIONAL_CANDIDATE` or
`DIAGNOSTIC_ONLY`, never `QUALIFIED_FOR_PILOT`; capability evidence can be
listed without mutating historical records.
