# Architecture Baseline

## Current architecture

The repository currently contains a Python FastAPI backend, SQLite persistence,
worker/runtime boundaries, launcher scripts and a React/Vite localhost frontend.
The current frontend is sufficient for the active setup and validation
workflow, but later frontend work must continue to reuse backend/domain
contracts rather than reimplementing scene or counting semantics in UI state.

Streamlit is not the intended long-term frontend architecture. If any
Streamlit workflow exists in a transitional branch or local operation, it is
legacy/transitional and cannot define canonical scene, event, review,
aggregation or export semantics.

## Target architecture

The target local desktop-style web application is:

```text
run_app.bat
├── starts Python API/backend
├── starts background worker or processing runtime where applicable
├── starts React/Vite frontend
└── opens the localhost application in the default browser
```

The target does not require `.exe` packaging. Future RTSP/HLS or remote
deployment capability must preserve the same stable backend/domain contracts,
but remains outside Milestone 5.2D.

## Proposed logical architecture

```text
Frontend
  ├─ Project workflow
  ├─ Video/timeline workspace
  ├─ Scene geometry editor
  ├─ Review workspace
  └─ Results/export
        │
FastAPI API
  ├─ Validation and commands
  ├─ Query endpoints
  ├─ Progress channel
  └─ Job coordination
        │
Worker boundary
  ├─ Media adapter
  ├─ Detector adapter
  ├─ Tracker adapter
  ├─ Track classifier
  ├─ Counting engine
  ├─ Evidence generation
  └─ Processing checkpoints
        │
Domain/Application services
  ├─ Timebase
  ├─ Event ledger
  ├─ Review projection
  ├─ Aggregation
  ├─ Certification
  └─ Stale-result manager
        │
Persistence
  ├─ SQLite metadata/events/audit
  └─ Local artifact folders
```

## Mandatory boundaries

Interfaces must exist conceptually for:

- `MediaSource`
- `DetectorBackend`
- `TrackerBackend`
- `TrackClassifier`
- `MovementResolver`
- `EventRepository`
- `ArtifactStore`
- `ExportAdapter`

The exact package structure is for implementation to finalize during the initial product iteration.

## Stable migration contracts

The following contracts must survive the frontend migration unchanged:

- media/source registration and fingerprinting;
- PTS/media timestamp timebase;
- scene schema and normalized coordinate space;
- counting-line endpoint identity and Side A / Side B semantics;
- crossing direction values `A_TO_B` and `B_TO_A`;
- job and worker boundary;
- immutable automatic events and append-only review actions;
- aggregation, export and stale-result behavior.

## Process model

- FastAPI process handles short requests and job control.
- Worker process handles decode/inference/tracking.
- Database writes should have a clear ownership model.
- Frontend receives progress through WebSocket or SSE.
- Foundation milestone uses a mock worker.

## Local storage model

Suggested local artifact areas:

```text
.local-data/
├── videos/
├── databases/
├── models/
├── runs/
├── evidence/
├── exports/
└── temporary/
```

These are not committed to Git.

## Versioning

Persist versions or hashes for:

- source
- timebase
- scene
- counting rules
- movement mapping
- taxonomy
- detector
- classifier
- tracker and config
- thresholds
- application calculation logic
- review state
- export manifest
