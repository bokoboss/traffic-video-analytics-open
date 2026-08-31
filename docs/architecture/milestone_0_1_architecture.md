# Milestone 0-1 Architecture

## Structure

```text
apps/backend/   FastAPI API, domain services, SQLite migrations
apps/worker/    separate mock processing worker process
apps/frontend/  React + TypeScript localhost UI
packages/contracts/ generated OpenAPI contract artifacts
tests/backend/  deterministic domain, persistence, API and worker tests
```

## Boundaries

The foundation keeps media sources, timestamp extraction, detector, tracker, classifier, counting rules, persistence and exports as separate contracts. No real AI, FFmpeg, CUDA, RTSP, HLS or YouTube integration is present.

FastAPI exposes `/api/v1` orchestration endpoints only. Long-running work is represented by `apps/worker/mock_worker.py`, which runs as a separate Python process and emits progress messages. The API service commits mock segments and events so the persistence and retry semantics can be tested before real processing is introduced.

## Time and Counting Semantics

Authoritative event time uses PTS milliseconds relative to a user-confirmed source start time. Interval buckets are half-open `[start, end)`. PHF is computed from exactly four 15-minute buckets and is unavailable for zero volume.

## Review and Certification

Automatic count events are immutable in SQLite through triggers and through service behavior. Human review uses append-only review actions. Undo is represented by a reversal action, not deletion. Certification and export are blocked when results are stale, incomplete or mandatory QC remains unresolved.

## Stale Results

Calculation-affecting changes mark projects and aggregate snapshots stale. Stale results remain inspectable but cannot be certified or exported as current results.
