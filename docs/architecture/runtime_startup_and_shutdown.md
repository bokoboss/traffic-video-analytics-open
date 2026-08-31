# Runtime Startup and Shutdown

The runtime boundary is deliberately layered:

```text
setup_app.bat
    -> approved Python / Node / pnpm / optional media checks
run_app.bat
    -> backend health -> worker process sanity -> fresh worker heartbeat
    -> frontend HTTP -> application readiness -> browser
    -> operator selects capability and project workflow
stop_app.bat
    -> validate PID + root + command + start time -> stop owned process -> remove record
```

The FastAPI process owns API contracts and SQLite access. Long-running real
processing remains in `apps/worker/processing_worker.py`. The current Windows
launcher starts the repository-managed Vite development server on localhost;
that is a pilot development runtime contract, not a final installer or bundled
frontend architecture.

`ReadinessResponse` keeps legacy `status`, liveness/core/processing fields and
adds typed `components`, `core_status`, `processing_status`,
`application_status`, `application_ready`, `overall_status` and release
identity. These fields are intentionally separate: `CORE_READY` means the
backend, database, migrations, artifact storage and disk contract are ready;
`PROCESSING_READY` means a live worker and real-video capability are ready;
`APPLICATION_READY` means core, frontend HTTP and the worker are live for the
selected capability. A worker that is live but lacks real-video runtime is
reported as synthetic-only / `READY_WITH_WARNINGS`, never as full application
READY. A component has `status`, `required`, `version`, `detail`,
`remediation`, `state` and `ready`.

The worker registers one backend-observable row in `runtime_heartbeats` with
`worker_id`, an internal instance token, safe PID metadata, `started_at`,
`last_heartbeat_at`, release version and state. The worker refreshes it every
second; the backend uses an exact five-second expiry. Fresh `READY` or `BUSY`
is live, clean exit is `OFFLINE`, and a process that disappears without a
clean exit becomes `STALE` after the expiry. The token and PID are not exposed
through readiness. A 500 ms non-exit check remains only an early launcher
sanity check; the heartbeat is authoritative. The frontend is authoritative
through the launcher-configured localhost HTTP probe.

The Windows launcher owns process records and the backend owns durable job
state/leases. A worker restart may recover queued or expired jobs; it cannot
mutate immutable automatic events or replace a selected run.
