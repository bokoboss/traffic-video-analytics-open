# Local Runtime Launcher

The local Windows launcher scripts support product review and development validation.

## Entry Points

Run from File Explorer, Command Prompt or PowerShell:

```bat
setup_app.bat
run_app.bat
stop_app.bat
```

The scripts resolve the repository root from their own location, so they work when invoked from another directory. Paths containing spaces or `&` must be quoted by the calling shell.

## Node.js Runtime Resolution

The launcher does not require a system-wide Node.js installation. It resolves an approved runtime in this priority order:

1. configured directory from `TVA_NODE_DIR`;
2. repository-local portable runtime at `.local-tools\node\`;
3. normal system `PATH`;
4. verified pnpm-managed runtime fallback from the configured package-manager runtime.

The supported Node.js range is `>=20.19.0` and `<27.0.0`. The pinned package manager is `pnpm@11.9.0`. Node.js v24 LTS remains the recommended stable default. Other supported versions should pass the local portable-runtime checks before use.

The launcher modifies `PATH` only for child setup/run processes. It does not modify the permanent user or machine `PATH`, registry, Windows services or administrator-level configuration.

## Portable Runtime Contract

An IT-approved portable runtime can be extracted outside Git tracking:

```text
.local-tools/
└── node/
    ├── node.exe
    ├── npm.cmd
    ├── npx.cmd
    └── required Node distribution files
```

`.local-tools/` is ignored by Git. Do not commit Node binaries, archives or extracted runtime files.

The launcher verifies `node.exe`, runs `node --version`, validates the supported version range and records the resolved source as `configured`, `repository-local`, `system PATH` or `pnpm runtime fallback`.

pnpm must be prepared separately as one of:

- `TVA_PNPM_CMD` pointing to an approved `pnpm.cmd`;
- `.local-tools\pnpm\pnpm.cmd`;
- a system or existing local `pnpm` command already on child-process `PATH`.

Use `python scripts\prepare_portable_pnpm.py --accept-download` to prepare the repository-local `pnpm@11.9.0` command from the official npm registry package. The script verifies the official npm package integrity, installs only under ignored `.local-tools\pnpm`, writes `.local-tools\pnpm\pnpm.cmd`, and uses ignored `.local-data` for download/cache evidence. It is never run by `run_app.bat`.

If `TVA_PNPM_CMD` is set, the launcher treats it as explicit operator intent and fails when that command is missing, damaged or the wrong version. If `.local-tools\pnpm\pnpm.cmd` exists, the launcher treats it as the repository-local runtime and fails when it is damaged or the wrong version instead of silently falling back to `PATH`.

Normal `run_app.bat` never installs, downloads or activates package-manager shims.

## Setup

`setup_app.bat`:

- checks Python 3.11 or newer;
- resolves Node.js by the launcher priority above;
- reports the resolved Node.js source and version;
- locates the pinned `pnpm@11.9.0`;
- reports npm if present, but repository scripts do not require npm directly;
- creates or reuses `.venv`;
- installs Python dependencies from `requirements-dev.txt`;
- installs frontend dependencies from `pnpm-lock.yaml`;
- reports FFmpeg, ffprobe and NVIDIA GPU status;
- builds the frontend;
- writes `.local-data/runtime/setup-complete.json` on success.

No system software, model weights, package manager upgrade or FFmpeg binaries are downloaded by the script.

## Run

`run_app.bat`:

- requires setup completion;
- uses the already prepared Node.js and pnpm runtime;
- does not install or download anything;
- refuses to take over occupied ports from non-launcher processes;
- starts `uvicorn apps.backend.app.main:app` on `127.0.0.1:8000`;
- starts Vite on `127.0.0.1:5174` after backend health and worker heartbeat
  readiness;
- waits for `application_ready=true` after frontend HTTP and worker readiness;
- reports synthetic-only processing as `READY_WITH_WARNINGS`, never as real-media
  processing READY;
- writes logs under `.local-data/logs`;
- writes opt-in startup phase logs when `TRAFFIC_APP_DIAGNOSTICS=1`;
- records the actual listener PIDs under `.local-data/runtime`;
- reuses an already running healthy launcher instance on duplicate launch.
- skips browser launch for automated benchmarks when `TVA_NO_BROWSER=1`.

## Stop

`stop_app.bat`:

- reads launcher-owned runtime records;
- verifies backend/frontend command signatures before stopping;
- requests normal termination first and force-stops only after a bounded wait;
- removes stale records;
- leaves unrelated Python and Node processes alone.

## Readiness

`/api/v1/health` is a cheap liveness endpoint. `/api/v1/readiness` distinguishes:

- liveness: backend and database response;
- core readiness: project storage, API, migrations, artifact storage and disk;
- processing readiness: fresh owned worker plus real-video capability;
- application readiness: core, frontend HTTP and the worker are live;

Core readiness may be available for diagnosis while application readiness is
blocked. Unavailable FFmpeg, model runtime or model weights keep a synthetic
workflow visibly separate from real-media processing.

## Runtime Note

On a clean machine, provide Node.js and pnpm through `TVA_NODE_DIR`, `.local-tools`, or the system `PATH` according to the launcher priority above. FFmpeg and ffprobe are optional for synthetic-only checks but are required for real-media metadata inspection and reference-frame extraction.
