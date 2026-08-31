ALTER TABLE analysis_runs ADD COLUMN processing_mode TEXT NOT NULL DEFAULT 'SYNTHETIC';
ALTER TABLE analysis_runs ADD COLUMN detector_id TEXT;
ALTER TABLE analysis_runs ADD COLUMN tracker_id TEXT;
ALTER TABLE analysis_runs ADD COLUMN model_revision TEXT;
ALTER TABLE analysis_runs ADD COLUMN tracker_revision TEXT;
ALTER TABLE analysis_runs ADD COLUMN device_mode TEXT;
ALTER TABLE analysis_runs ADD COLUMN resolved_device TEXT;
ALTER TABLE analysis_runs ADD COLUMN configuration_revision TEXT;
ALTER TABLE analysis_runs ADD COLUMN progress_indeterminate INTEGER NOT NULL DEFAULT 0;
ALTER TABLE analysis_runs ADD COLUMN processing_stats_json TEXT;

ALTER TABLE video_sources ADD COLUMN recording_time_configured INTEGER NOT NULL DEFAULT 0;

ALTER TABLE crossing_event_ledger ADD COLUMN raw_class_id INTEGER;
ALTER TABLE crossing_event_ledger ADD COLUMN raw_class_name TEXT;
ALTER TABLE crossing_event_ledger ADD COLUMN provisional_class TEXT;
ALTER TABLE crossing_event_ledger ADD COLUMN detector_confidence REAL;
ALTER TABLE crossing_event_ledger ADD COLUMN classification_review_state TEXT NOT NULL DEFAULT 'needs_review';
ALTER TABLE crossing_event_ledger ADD COLUMN source_frame_index INTEGER;
ALTER TABLE crossing_event_ledger ADD COLUMN source_frame_pts_ms INTEGER;
ALTER TABLE crossing_event_ledger ADD COLUMN absolute_event_time TEXT;
ALTER TABLE crossing_event_ledger ADD COLUMN event_time_status TEXT NOT NULL DEFAULT 'confirmed';
ALTER TABLE crossing_event_ledger ADD COLUMN processing_mode TEXT NOT NULL DEFAULT 'SYNTHETIC';
ALTER TABLE crossing_event_ledger ADD COLUMN detector_id TEXT;
ALTER TABLE crossing_event_ledger ADD COLUMN tracker_id TEXT;
ALTER TABLE crossing_event_ledger ADD COLUMN configuration_revision TEXT;

ALTER TABLE auto_count_events ADD COLUMN raw_class_id INTEGER;
ALTER TABLE auto_count_events ADD COLUMN raw_class_name TEXT;
ALTER TABLE auto_count_events ADD COLUMN provisional_class TEXT;
ALTER TABLE auto_count_events ADD COLUMN detector_confidence REAL;
ALTER TABLE auto_count_events ADD COLUMN classification_review_state TEXT NOT NULL DEFAULT 'needs_review';
ALTER TABLE auto_count_events ADD COLUMN source_frame_index INTEGER;
ALTER TABLE auto_count_events ADD COLUMN source_frame_pts_ms INTEGER;
ALTER TABLE auto_count_events ADD COLUMN absolute_event_time TEXT;
ALTER TABLE auto_count_events ADD COLUMN event_time_status TEXT NOT NULL DEFAULT 'confirmed';
ALTER TABLE auto_count_events ADD COLUMN processing_mode TEXT NOT NULL DEFAULT 'SYNTHETIC';
ALTER TABLE auto_count_events ADD COLUMN detector_id TEXT;
ALTER TABLE auto_count_events ADD COLUMN tracker_id TEXT;
ALTER TABLE auto_count_events ADD COLUMN configuration_revision TEXT;

CREATE TABLE real_inference_runs (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES analysis_runs(id),
  project_id TEXT NOT NULL REFERENCES projects(id),
  source_id TEXT NOT NULL REFERENCES video_sources(id),
  scene_version_id TEXT NOT NULL REFERENCES scene_versions(id),
  detector_id TEXT NOT NULL,
  tracker_id TEXT NOT NULL,
  device_mode TEXT NOT NULL,
  resolved_device TEXT NOT NULL,
  configuration_revision TEXT NOT NULL,
  configuration_json TEXT NOT NULL,
  stats_json TEXT NOT NULL,
  provenance_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(run_id)
);

CREATE INDEX idx_crossing_event_ledger_run_pts
ON crossing_event_ledger(run_id, crossing_timestamp_ms, counting_line_id, track_id);

CREATE INDEX idx_auto_count_events_run_pts
ON auto_count_events(run_id, pts_ms, track_id);
