# Repository Layout

## Root

- `pyproject.toml` and `requirements-dev.txt`: Python application and validation dependencies.
- `package.json`: npm workspace commands for the frontend and Playwright.
- `.github/workflows/ci.yml`: Windows CI for seed checks, backend tests, OpenAPI export, frontend build and browser checks.

## Applications

- `apps/backend/app`: FastAPI app, Pydantic schemas, domain rules, SQLite connection and services.
- `apps/backend/app/engineering_outputs.py`: versioned pilot taxonomy, deterministic track vote, time/interval semantics, structured engineering aggregation and reconciliation.
- `apps/backend/app/benchmarking.py`: rights-aware corpus/ground-truth contracts, deterministic PTS matching, metric families, agreement, experiments and qualification gates.
- `apps/backend/app/benchmark_service.py`: additive SQLite persistence for 6C benchmark revisions, matches, metrics and reports.
- `apps/backend/app/processing_profiles.py`: deterministic profile catalogue,
  guided resolution, typed parameter schema, validation, and resource-impact
  estimates for 6D.
- `apps/backend/app/operational_profiles.py`: append-only 6D profile,
  configuration revision, preview, comparison, candidate, and capability
  persistence.
- `apps/backend/app/reviewing.py`: detector-independent deterministic review
  overlay and reviewed-event projection semantics.
- `apps/backend/app/review_service.py`: 6E session/action/projection,
  reconciliation, certification and managed artifact adapter.
- `apps/backend/migrations`: deterministic SQL migrations from an empty database.
- `apps/backend/app/release.py`: tracked pilot identity and commit provenance.
- `apps/backend/app/runtime_readiness.py`: typed component readiness projection
  with operator-safe details and remediation.
- `apps/backend/migrations/017_pilot_release_runtime.sql`: explicit current
  processing-run pointer; historical runs remain append-only.
- `apps/backend/migrations/018_pilot_runtime_heartbeats.sql`: backend-observable
  owned-worker heartbeat and deterministic liveness expiry fields.
- `apps/backend/migrations/012_milestone_6d_operational_profiles.sql`: additive
  6D persistence and immutability triggers after the 6C migration.
- `apps/worker`: separate processing worker boundary; `mock_worker.py` remains a
  lightweight process-boundary fixture and `processing_worker.py` executes the
  real local-video pilot path.
- `apps/frontend`: Vite React app, shell UI, component state preview and browser tests.
- `release.json`: application/release/channel identity; no machine-specific
  paths or credentials.
- `scripts/database_backup.py`, `scripts/create_support_bundle.py`:
  app-aware recovery and sanitized diagnostics utilities.
- `setup_app.bat`, `run_app.bat`, `stop_app.bat`, `backup_app.bat` and
  `restore_app.bat`: operator entrypoints; runtime state stays ignored.

## Contracts and Tests

- `packages/contracts`: generated OpenAPI contract output.
- `tests/backend`: domain, migration, API and worker-boundary tests.
- `tests/backend/test_milestone6b_taxonomy_engineering.py`: deterministic 6B taxonomy, voting, time, aggregation and reconciliation coverage.
- `tests/backend/test_milestone6c_benchmark.py`: rights, revision, matching, metric, experiment, service and API coverage for 6C.
- `tests/backend/test_milestone6d_operational_profiles.py`: profile, typed
  validation, immutable revisions, preview isolation, comparison, candidate,
  and capability-status coverage for 6D.
- `tools/benchmark/fixtures/*_6c.json`: synthetic metadata-only benchmark fixtures; no media bytes.
- `scripts/benchmark_*.py`, `scripts/run_benchmark.py`, `scripts/run_experiment_suite.py`: local benchmark validation/evaluation entry points.
- `apps/frontend/src/OperationalSettingsWorkspace.tsx`: bilingual 6D profile,
  guided/expert, validation, revision, and preview workflow.
- `apps/frontend/src/ReviewWorkspace.tsx`: bilingual 6E review queue, evidence,
  correction, certification and controlled-export workspace.
- `tests/backend/test_milestone6e_review_certification_export.py`: focused 6E
  overlay, conflict, reversal, projection, certification, export and artifact
  safety coverage.

Private videos, datasets, model weights, generated evidence, exports and local databases remain ignored by `.gitignore`.
