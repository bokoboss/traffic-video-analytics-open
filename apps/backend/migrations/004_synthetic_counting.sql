CREATE TABLE synthetic_counting_runs (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES analysis_runs(id),
  project_id TEXT NOT NULL REFERENCES projects(id),
  source_id TEXT NOT NULL REFERENCES video_sources(id),
  scene_version_id TEXT NOT NULL REFERENCES scene_versions(id),
  synthetic_input_json TEXT NOT NULL,
  synthetic_input_fingerprint TEXT NOT NULL,
  engine_version TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  tolerance_json TEXT NOT NULL,
  taxonomy_version TEXT NOT NULL,
  provenance_json TEXT NOT NULL,
  stale INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE(run_id)
);

CREATE TABLE crossing_event_ledger (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES analysis_runs(id),
  source_fingerprint_sha256 TEXT NOT NULL,
  scene_revision TEXT NOT NULL,
  counting_line_id TEXT NOT NULL,
  counting_line_label TEXT NOT NULL,
  track_id TEXT NOT NULL,
  crossing_direction TEXT NOT NULL,
  crossing_timestamp_ms INTEGER NOT NULL,
  real_world_time TEXT NOT NULL,
  reporting_bin_start_ms INTEGER NOT NULL,
  synthetic_class TEXT NOT NULL,
  roi_eligible INTEGER NOT NULL,
  previous_observation_index INTEGER NOT NULL,
  next_observation_index INTEGER NOT NULL,
  interpolation_parameter REAL NOT NULL,
  crossing_point_json TEXT NOT NULL,
  calculation_method TEXT NOT NULL,
  engine_version TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  event_status TEXT NOT NULL,
  provenance TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(run_id, counting_line_id, track_id, crossing_timestamp_ms, crossing_direction)
);

CREATE TRIGGER crossing_event_ledger_no_update
BEFORE UPDATE ON crossing_event_ledger
BEGIN
  SELECT RAISE(ABORT, 'crossing_event_ledger rows are immutable');
END;

CREATE TRIGGER crossing_event_ledger_no_delete
BEFORE DELETE ON crossing_event_ledger
BEGIN
  SELECT RAISE(ABORT, 'crossing_event_ledger rows are immutable');
END;

CREATE TABLE synthetic_counting_exclusions (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES analysis_runs(id),
  track_id TEXT NOT NULL,
  counting_line_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  timestamp_ms INTEGER,
  detail TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE synthetic_counting_warnings (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES analysis_runs(id),
  code TEXT NOT NULL,
  field TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
