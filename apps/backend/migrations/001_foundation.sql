CREATE TABLE projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  location TEXT NOT NULL,
  study_type TEXT NOT NULL,
  language TEXT NOT NULL CHECK (language IN ('th', 'en')),
  state TEXT NOT NULL,
  stale INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE video_sources (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  file_name TEXT NOT NULL,
  fingerprint_sha256 TEXT NOT NULL,
  source_started_at TEXT NOT NULL,
  timezone_name TEXT NOT NULL,
  analysis_start_pts_ms INTEGER NOT NULL,
  analysis_end_pts_ms INTEGER NOT NULL,
  interval_origin_pts_ms INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE scene_versions (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  version INTEGER NOT NULL,
  template TEXT NOT NULL,
  geometry_json TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(project_id, version)
);

CREATE TABLE counting_rule_versions (
  id TEXT PRIMARY KEY,
  scene_version_id TEXT NOT NULL REFERENCES scene_versions(id),
  version INTEGER NOT NULL,
  rule_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE analysis_runs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  source_id TEXT NOT NULL REFERENCES video_sources(id),
  scene_version_id TEXT NOT NULL REFERENCES scene_versions(id),
  state TEXT NOT NULL,
  progress_percent INTEGER NOT NULL DEFAULT 0,
  result_version TEXT NOT NULL,
  created_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE TABLE processing_segments (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES analysis_runs(id),
  segment_index INTEGER NOT NULL,
  start_pts_ms INTEGER NOT NULL,
  end_pts_ms INTEGER NOT NULL,
  retry_count INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL,
  UNIQUE(run_id, segment_index)
);

CREATE TABLE model_bundles (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  license_status TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE tracker_configurations (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  config_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE track_summaries (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES analysis_runs(id),
  track_id TEXT NOT NULL,
  summary_json TEXT NOT NULL,
  UNIQUE(run_id, track_id)
);

CREATE TABLE auto_count_events (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES analysis_runs(id),
  technical_key TEXT NOT NULL,
  pts_ms INTEGER NOT NULL,
  track_id TEXT NOT NULL,
  rule_id TEXT NOT NULL,
  object_domain TEXT NOT NULL,
  classification TEXT NOT NULL,
  movement TEXT NOT NULL,
  confidence REAL NOT NULL,
  qc_state TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(run_id, technical_key)
);

CREATE TRIGGER auto_count_events_no_update
BEFORE UPDATE ON auto_count_events
BEGIN
  SELECT RAISE(ABORT, 'auto_count_events are immutable');
END;

CREATE TRIGGER auto_count_events_no_delete
BEFORE DELETE ON auto_count_events
BEGIN
  SELECT RAISE(ABORT, 'auto_count_events are immutable');
END;

CREATE TABLE qc_flags (
  id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES auto_count_events(id),
  severity TEXT NOT NULL,
  reason TEXT NOT NULL,
  mandatory INTEGER NOT NULL DEFAULT 1,
  resolved_at TEXT
);

CREATE TABLE review_actions (
  id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES auto_count_events(id),
  action_type TEXT NOT NULL,
  reviewer TEXT NOT NULL,
  new_classification TEXT,
  new_movement TEXT,
  reason TEXT,
  reverses_action_id TEXT,
  created_at TEXT NOT NULL
);

CREATE TRIGGER review_actions_no_update
BEFORE UPDATE ON review_actions
BEGIN
  SELECT RAISE(ABORT, 'review_actions are append-only');
END;

CREATE TRIGGER review_actions_no_delete
BEFORE DELETE ON review_actions
BEGIN
  SELECT RAISE(ABORT, 'review_actions are append-only');
END;

CREATE TABLE aggregate_snapshots (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES analysis_runs(id),
  result_version TEXT NOT NULL,
  fifteen_minute_counts_json TEXT NOT NULL,
  hourly_total INTEGER NOT NULL,
  phf REAL,
  stale INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE certification_records (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  run_id TEXT NOT NULL REFERENCES analysis_runs(id),
  result_version TEXT NOT NULL,
  certified_by TEXT NOT NULL,
  certified_at TEXT NOT NULL,
  UNIQUE(run_id, result_version)
);

CREATE TABLE export_manifests (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  run_id TEXT NOT NULL REFERENCES analysis_runs(id),
  result_version TEXT NOT NULL,
  format TEXT NOT NULL,
  status TEXT NOT NULL,
  provenance_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
