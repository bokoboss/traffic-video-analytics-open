# Local Development

## Python

```powershell
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pytest tests/backend
python scripts/export_openapi.py
python scripts/generate_ts_client.py
```

Run the API:

```powershell
uvicorn apps.backend.app.main:app --reload
```

Useful local environment variables:

```powershell
$env:TVA_DB_PATH=".local-data\\dev.sqlite"
$env:TVA_LOCAL_DATA_DIR=".local-data"
```

Run the mock worker:

```powershell
python apps/worker/mock_worker.py --run-id run_mock
```

## Frontend

The repository pins `pnpm@11.9.0` through `package.json`. On company-managed Windows machines, use the launcher-supported portable Node.js flow instead of installing Node system-wide:

```powershell
$env:TVA_NODE_DIR=(Join-Path (Get-Location) ".local-tools\node")
python scripts\prepare_portable_pnpm.py --accept-download
python scripts\check_node_runtime.py
setup_app.bat
run_app.bat
```

`TVA_NODE_DIR` must point to an IT-approved Node.js directory containing `node.exe`. The supported Node.js range is `>=20.19.0` and `<27.0.0`. Node.js v24 LTS remains the recommended stable default; Node.js v26.5.0 Current is allowed after the documented portable-runtime evaluation.

The repository-local pnpm command is `.local-tools\pnpm\pnpm.cmd`. The preparation script uses the official npm registry package for `pnpm@11.9.0`, verifies integrity, and writes only ignored local runtime files under `.local-tools` and `.local-data`.

```powershell
pnpm install
pnpm run dev:frontend
pnpm run build:frontend
pnpm run test:frontend
pnpm run test:e2e
pnpm run generate:api
pnpm run check:api
```

The frontend is a Vite app intended for localhost use. Milestone 2 calls the real FastAPI backend by default at `http://127.0.0.1:8000`; set `VITE_TVA_API_BASE` if the backend runs elsewhere.

## Media Inspection

Local video upload uses a browser file input and a backend multipart upload. The backend stores a managed copy under `TVA_LOCAL_DATA_DIR`, computes full-file SHA-256, and attempts `ffprobe` inspection.

Install FFmpeg separately for local metadata inspection. The application handles missing `ffprobe` as a controlled warning and does not download or bundle FFmpeg binaries.

Check media runtime status:

```powershell
python scripts/check_media_runtime.py
python scripts/check_media_runtime.py --strict
```

Milestone 3 reference-frame extraction requires both `ffmpeg` and `ffprobe`. Missing tools do not block project setup, but extraction endpoints return controlled dependency errors.
