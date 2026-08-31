# Windows Launcher

`run_app.bat` owns three localhost processes: FastAPI on `127.0.0.1:8000`, the
Python processing worker, and Vite on `127.0.0.1:5174`.

## Startup sequence

1. Validate the setup marker, Python, approved Node/pnpm and ports.
2. Set the local database/data root, release version and Git SHA environment.
3. Start the backend and poll `/api/v1/health` until JSON status is `ok`.
4. Start the worker and perform a short non-exit sanity check.
5. Poll `/api/v1/readiness` until the backend observes the matching worker
   heartbeat as `READY` or `BUSY`.
6. Start the frontend and poll its HTTP endpoint.
7. Poll `/api/v1/readiness` until `application_ready=true` and
   `application_status` is `READY` or `READY_WITH_WARNINGS`.
8. Open the browser unless `TVA_NO_BROWSER=1` is set.

Readiness is based on endpoint JSON, a backend-observable worker heartbeat and
the frontend HTTP probe, not a fixed startup sleep. The five-second worker
heartbeat expiry is the authoritative liveness threshold. Optional media/AI
capability is reported separately from core and application readiness; a
synthetic-only worker state is visible as a warning and is not a real-media
READY signal.

## Ownership and duplicate launch

Each process record under `.local-data/runtime/` contains PID, repository root,
command, port, launcher run ID, owner token and process start time. A second
launcher continues only when all three owned records, backend health and
frontend HTTP checks are valid. Incomplete/stale records are passed to
`stop_app.ps1`, which validates root, command line and process start time before
stopping anything.

The launcher never uses broad `taskkill`, binds beyond localhost, or removes
SQLite/data/export files during stop. Log files are rotated at a bounded size.

The frontend process is currently the repository-managed Vite development
server started with the approved Node/pnpm runtime. The launcher contract is
for this pilot workflow and does not claim a final installer, bundled frontend
runtime or production packaging design.

## Stop and restart

Run `stop_app.bat` before maintenance. It stops only matching project-owned
processes and removes only their runtime records. If a PID no longer matches,
it is left untouched and the record remains for diagnosis.
