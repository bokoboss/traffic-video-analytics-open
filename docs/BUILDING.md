# Building from source

This document describes a clean public-checkout build of the `0.1.0-pilot`
source. It does not require private Git history, private data, or model
weights.

## Prerequisites

- Windows 11 x64 for the supported pilot launcher;
- Python 3.11 or newer;
- Node.js 20.19.0 or newer and below 27.0.0;
- pnpm 11.9.0; and
- FFmpeg/ffprobe only when media inspection or reference-frame extraction is
  needed.

The core synthetic workflow does not require CUDA, FFmpeg, or a model runtime.

## Python environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

Run the backend tests and a Python import/compile check:

```powershell
python -m pytest tests/backend
python -m compileall -q apps tools scripts tests
```

Run the local API with:

```powershell
uvicorn apps.backend.app.main:app --reload
```

The default database and generated runtime state live under the ignored
`.local-data/` directory. Set `TVA_DB_PATH` or `TVA_LOCAL_DATA_DIR` when a
different local location is required.

## Frontend

Install from the committed lockfile and build/test the frontend:

```powershell
pnpm install --frozen-lockfile
pnpm run build:frontend
pnpm run test:frontend
pnpm run lint:frontend
```

The browser UI is available through `pnpm run dev:frontend`. Playwright E2E
tests require a locally installed Chromium browser:

```powershell
pnpm exec playwright install chromium
pnpm run test:e2e
```

## API contracts and release validation

The OpenAPI document and generated TypeScript types are versioned source. Use:

```powershell
python scripts/export_openapi.py
python scripts/generate_ts_client.py
pnpm run check:api
python scripts/check_model_registry.py
python scripts/validate_public_release.py
```

`check:api` is a source-tree cleanliness check: it must leave the generated
OpenAPI and TypeScript files unchanged. If a generator dependency produces a
different normalization, align the approved toolchain before committing the
generated result.

## Windows launcher

After Python and frontend prerequisites are prepared, `setup_app.bat` creates
the local virtual environment, installs development dependencies, builds the
frontend, and writes an ignored setup marker. `run_app.bat` starts the local
backend, worker and Vite server. `stop_app.bat` stops only owned processes.

The launcher never downloads model weights, FFmpeg, CUDA, Node.js, or pnpm.
See [`PORTABLE_BUILD.md`](PORTABLE_BUILD.md) for the separate runtime and
packaging boundary.
