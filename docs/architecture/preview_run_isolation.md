# Preview run isolation

Preview runs are bounded operational diagnostics. They are deliberately not a
shortcut into production result storage.

## Persistence boundary

The `preview_runs`, `preview_run_events`, and `preview_run_statistics` tables
are separate from `analysis_runs`, `crossing_event_ledger`,
`auto_count_events`, and 6B engineering projections. Every preview response
includes `run_type=PREVIEW_ONLY` and the disclosures `PREVIEW_ONLY` and
`NOT_PRODUCTION_RESULT`.

Preview rows retain the source fingerprint, scene revision/semantic hash,
half-open source-relative PTS segment, immutable configuration revision, mode,
fixture, complete idempotency request fingerprint, runtime hash, worker
lifecycle, and requester. Events are compact bounded evidence and are never
inserted through `_insert_crossing_event`.

## Execution boundary

Synthetic previews can be completed in memory for deterministic workflow tests.
Real previews are queued in `preview_runs` and claimed by the separate worker.
The worker reuses the implementation-independent detector/tracker/crossing
boundary with the preview PTS window and writes only preview statistics/events.

Claims are fenced by worker ID and a private claim token. `heartbeat_at`,
`lease_expires_at`, and `attempt_number` are updated by the owner only. The
worker renews periodically and at decoder, inference, tracker, counting, and
finalization checkpoints. Cancellation is cooperative and terminalizes the
preview without allowing a stale worker to append events or overwrite the
statistics row. Expired running claims are requeued with an auditable state
event; terminal previews are immutable.

REAL_VIDEO production callbacks use the same persistent database through
explicit thread-owned callback connections. The worker's main connection
remains same-thread-owned; inference status/heartbeat callbacks and the
decoder cancellation watcher never use it. Callback connections are closed
after inference and any decoder watcher have joined. Production callback
failures are typed and fail closed, while worker/token/lease fencing remains
the single authority for job state.

The worker records model load time separately from first inference, processed
FPS, processing ratio, GPU memory when available, short-track exclusions,
warnings, and representative evidence. A throughput observation does not imply
real-time support.

## Promotion

Promotion requires a completed preview and creates a new full processing job
that references the same configuration revision. It records the full-run ID on
the preview for traceability. It does not copy preview events, alter preview
history, or alter the immutable 6A/6B/6C layers.

Cleanup may remove local preview artifacts in a future lifecycle operation, but
it must not remove configuration revisions or their audit records.
