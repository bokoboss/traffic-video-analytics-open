# Traffic Video Analytics

Traffic Video Analytics is a Thai-first, local-first Windows application for
auditable vehicle and pedestrian counting from local video. It combines a
React/Vite browser UI, a FastAPI backend, a separate Python processing worker,
SQLite persistence, deterministic counting contracts, and optional media/AI
adapters.

The `0.1.0-pilot` source snapshot is intended for a controlled local Windows
pilot with documented limitations. It is an AI-assisted, human-certified
workflow: automatic events are immutable, while review actions and
certification form a separate authoritative layer. Synthetic fixtures validate
workflow and domain behavior; they are not detector-accuracy evidence.

This repository is a sanitized corresponding-source repository with fresh
public history. It does not contain private development history, private media,
customer data, local databases, generated evidence, or build-machine state.

## What the application does

- manages a local study and a local video source;
- records source fingerprint, timebase, recording-time and scene revisions;
- uses PTS/media timestamps and half-open analysis intervals;
- supports an undirected counting line with explicit Side A/Side B semantics
  and canonical `A_TO_B` / `B_TO_A` directions;
- preserves immutable automatic events and append-only review corrections;
- exposes deterministic synthetic validation, benchmark contracts and
  provenance-aware certification/export workflows; and
- keeps optional detector, tracker, FFmpeg and model runtimes outside the
  normal core installation.

The pilot does not claim detector or tracker accuracy, complete TIMS
classification, universal real-time performance, duplicate-proof vehicle
identity, clean-machine qualification, or live-source support. See
[`docs/PRIVACY.md`](docs/PRIVACY.md) and
[`docs/PORTABLE_BUILD.md`](docs/PORTABLE_BUILD.md) for the release boundary.

## Quick start

The supported development path is a clean checkout with Python 3.11+ and
Node.js 20.19+ and `<27`, plus pnpm `11.9.0`.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pytest tests/backend

pnpm install --frozen-lockfile
pnpm run build:frontend
pnpm run test:frontend
python scripts/validate_public_release.py
```

The frontend is served locally with `pnpm run dev:frontend`. The API can be
run with `uvicorn apps.backend.app.main:app --reload`. Generated API contracts
are checked with `pnpm run check:api` after the approved toolchain is
available.

For the Windows pilot launcher, prepare the approved Python, Node.js and pnpm
runtimes, then run `setup_app.bat`, `run_app.bat`, and `stop_app.bat`. The
launcher does not download or bundle runtimes. FFmpeg/ffprobe are optional for
core synthetic workflow and required for media inspection/reference-frame
operations. Optional AI packages and model weights are prepared separately;
they are never downloaded by normal startup.

## Public source and portable releases

The source includes the application, migrations, frontend, worker boundary,
build scripts, launcher scripts, dependency manifests, provenance records,
synthetic fixtures and public-safe tests needed to inspect and validate the
pilot source. See [`docs/BUILDING.md`](docs/BUILDING.md) and
[`docs/SOURCE_SNAPSHOT.md`](docs/SOURCE_SNAPSHOT.md).

This repository includes the portable builder, verifier, helpers and dependency
locks for the frozen 0.1.0-pilot distribution. Runtime binaries and model weights
remain separate licensed artifacts. Follow docs/PORTABLE_BUILD.md for source builds. For 0.1.0-pilot, binary distribution is paired with this public corresponding source, the recorded FFmpeg corresponding-source/build bundle, and the owner-confirmed MSVC/NVIDIA/Ultralytics distribution decisions. Release artifacts retain their own third-party notices and provenance.

## Privacy and licensing

Local media stays local by default. Do not commit videos, customer data,
databases, exports, evidence, credentials, runtime binaries, model weights or
machine-specific paths. Use synthetic or metadata-only fixtures when adding
tests.

The application source is licensed under the GNU Affero General Public License
version 3, only (`AGPL-3.0-only`). See the complete [`LICENSE`](LICENSE) and
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). Third-party packages,
runtime binaries, model weights and datasets retain their own licensing and
provenance requirements.
