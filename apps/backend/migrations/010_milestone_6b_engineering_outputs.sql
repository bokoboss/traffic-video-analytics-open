CREATE TABLE taxonomy_revisions (
  id TEXT PRIMARY KEY,
  revision TEXT NOT NULL UNIQUE,
  content_hash TEXT NOT NULL UNIQUE,
  content_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE taxonomy_classes (
  id TEXT PRIMARY KEY,
  taxonomy_revision_id TEXT NOT NULL REFERENCES taxonomy_revisions(id),
  code TEXT NOT NULL,
  display_name_en TEXT NOT NULL,
  display_name_th TEXT NOT NULL,
  object_domain TEXT NOT NULL,
  capability_state TEXT NOT NULL,
  target_reference TEXT,
  display_order INTEGER NOT NULL,
  UNIQUE(taxonomy_revision_id, code)
);

CREATE TABLE taxonomy_mapping_revisions (
  id TEXT PRIMARY KEY,
  revision TEXT NOT NULL UNIQUE,
  content_hash TEXT NOT NULL UNIQUE,
  content_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE taxonomy_mappings (
  id TEXT PRIMARY KEY,
  mapping_revision_id TEXT NOT NULL REFERENCES taxonomy_mapping_revisions(id),
  raw_class_name TEXT NOT NULL,
  engineering_class TEXT NOT NULL,
  provisional_class TEXT NOT NULL,
  status TEXT NOT NULL,
  reason TEXT NOT NULL,
  UNIQUE(mapping_revision_id, raw_class_name)
);

CREATE TABLE classification_policy_revisions (
  id TEXT PRIMARY KEY,
  revision TEXT NOT NULL UNIQUE,
  content_hash TEXT NOT NULL UNIQUE,
  content_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TRIGGER taxonomy_revisions_no_update
BEFORE UPDATE ON taxonomy_revisions
BEGIN
  SELECT RAISE(ABORT, 'taxonomy_revisions are immutable');
END;

CREATE TRIGGER taxonomy_revisions_no_delete
BEFORE DELETE ON taxonomy_revisions
BEGIN
  SELECT RAISE(ABORT, 'taxonomy_revisions are immutable');
END;

CREATE TRIGGER taxonomy_classes_no_update
BEFORE UPDATE ON taxonomy_classes
BEGIN
  SELECT RAISE(ABORT, 'taxonomy_classes are immutable');
END;

CREATE TRIGGER taxonomy_classes_no_delete
BEFORE DELETE ON taxonomy_classes
BEGIN
  SELECT RAISE(ABORT, 'taxonomy_classes are immutable');
END;

CREATE TRIGGER taxonomy_mapping_revisions_no_update
BEFORE UPDATE ON taxonomy_mapping_revisions
BEGIN
  SELECT RAISE(ABORT, 'taxonomy_mapping_revisions are immutable');
END;

CREATE TRIGGER taxonomy_mapping_revisions_no_delete
BEFORE DELETE ON taxonomy_mapping_revisions
BEGIN
  SELECT RAISE(ABORT, 'taxonomy_mapping_revisions are immutable');
END;

CREATE TRIGGER taxonomy_mappings_no_update
BEFORE UPDATE ON taxonomy_mappings
BEGIN
  SELECT RAISE(ABORT, 'taxonomy_mappings are immutable');
END;

CREATE TRIGGER taxonomy_mappings_no_delete
BEFORE DELETE ON taxonomy_mappings
BEGIN
  SELECT RAISE(ABORT, 'taxonomy_mappings are immutable');
END;

CREATE TRIGGER classification_policy_revisions_no_update
BEFORE UPDATE ON classification_policy_revisions
BEGIN
  SELECT RAISE(ABORT, 'classification_policy_revisions are immutable');
END;

CREATE TRIGGER classification_policy_revisions_no_delete
BEFORE DELETE ON classification_policy_revisions
BEGIN
  SELECT RAISE(ABORT, 'classification_policy_revisions are immutable');
END;

CREATE TABLE engineering_result_revisions (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES analysis_runs(id),
  result_version TEXT NOT NULL,
  taxonomy_revision_id TEXT NOT NULL REFERENCES taxonomy_revisions(id),
  mapping_revision_id TEXT NOT NULL REFERENCES taxonomy_mapping_revisions(id),
  classification_policy_revision_id TEXT NOT NULL REFERENCES classification_policy_revisions(id),
  result_status TEXT NOT NULL DEFAULT 'IN_PROGRESS',
  engineering_ready INTEGER NOT NULL DEFAULT 0,
  disclosure_json TEXT NOT NULL DEFAULT '[]',
  source_time_status TEXT NOT NULL DEFAULT 'UNCONFIGURED',
  source_timezone_name TEXT,
  summary_schema_version TEXT NOT NULL DEFAULT 'engineering-summary-v1',
  stale INTEGER NOT NULL DEFAULT 0,
  generated_at TEXT NOT NULL,
  UNIQUE(run_id, result_version)
);

CREATE TABLE engineering_track_evidence (
  id TEXT PRIMARY KEY,
  engineering_result_revision_id TEXT NOT NULL REFERENCES engineering_result_revisions(id),
  run_id TEXT NOT NULL REFERENCES analysis_runs(id),
  track_id TEXT NOT NULL,
  evidence_schema_version TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  provenance_json TEXT NOT NULL DEFAULT '{}',
  stale INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE(engineering_result_revision_id, track_id)
);

CREATE TABLE engineering_event_projections (
  id TEXT PRIMARY KEY,
  engineering_result_revision_id TEXT NOT NULL REFERENCES engineering_result_revisions(id),
  run_id TEXT NOT NULL REFERENCES analysis_runs(id),
  source_event_id TEXT NOT NULL REFERENCES auto_count_events(id),
  technical_key TEXT NOT NULL,
  source_fingerprint_sha256 TEXT NOT NULL,
  scene_revision TEXT NOT NULL,
  counting_line_id TEXT NOT NULL,
  counting_line_name TEXT NOT NULL,
  side_a_name TEXT NOT NULL,
  side_b_name TEXT NOT NULL,
  canonical_direction TEXT NOT NULL,
  readable_direction_name TEXT NOT NULL,
  track_id TEXT NOT NULL,
  event_pts_ms INTEGER NOT NULL,
  source_frame_index INTEGER,
  source_frame_pts_ms INTEGER,
  absolute_event_time TEXT,
  event_timezone_name TEXT,
  event_time_status TEXT NOT NULL,
  raw_detector_class_id INTEGER,
  raw_detector_class_name TEXT,
  track_voted_raw_class_id INTEGER,
  track_voted_raw_class_name TEXT,
  provisional_class TEXT NOT NULL,
  engineering_class TEXT NOT NULL,
  classification_status TEXT NOT NULL,
  classification_reason TEXT NOT NULL,
  taxonomy_revision TEXT NOT NULL,
  mapping_revision TEXT NOT NULL,
  classification_policy_revision TEXT NOT NULL,
  detector_confidence_summary_json TEXT NOT NULL DEFAULT '{}',
  track_evidence_ref TEXT,
  processing_provenance_json TEXT NOT NULL DEFAULT '{}',
  qc_state TEXT NOT NULL,
  stale INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE(engineering_result_revision_id, source_event_id)
);

CREATE TABLE engineering_intervals (
  id TEXT PRIMARY KEY,
  engineering_result_revision_id TEXT NOT NULL REFERENCES engineering_result_revisions(id),
  run_id TEXT NOT NULL REFERENCES analysis_runs(id),
  interval_index INTEGER NOT NULL,
  source_relative_start_pts_ms INTEGER NOT NULL,
  source_relative_end_pts_ms INTEGER NOT NULL,
  absolute_start TEXT,
  absolute_end TEXT,
  timezone_name TEXT,
  display_label TEXT NOT NULL,
  time_status TEXT NOT NULL,
  partial INTEGER NOT NULL DEFAULT 0,
  UNIQUE(engineering_result_revision_id, interval_index)
);

CREATE TABLE engineering_summary_rows (
  id TEXT PRIMARY KEY,
  engineering_result_revision_id TEXT NOT NULL REFERENCES engineering_result_revisions(id),
  run_id TEXT NOT NULL REFERENCES analysis_runs(id),
  interval_index INTEGER NOT NULL,
  counting_line_id TEXT NOT NULL,
  counting_line_name TEXT NOT NULL,
  canonical_direction TEXT NOT NULL,
  engineering_class TEXT NOT NULL,
  count INTEGER NOT NULL,
  UNIQUE(engineering_result_revision_id, interval_index, counting_line_id, canonical_direction, engineering_class)
);

CREATE TABLE reconciliation_reports (
  id TEXT PRIMARY KEY,
  engineering_result_revision_id TEXT NOT NULL REFERENCES engineering_result_revisions(id),
  run_id TEXT NOT NULL REFERENCES analysis_runs(id),
  status TEXT NOT NULL,
  report_json TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  UNIQUE(engineering_result_revision_id)
);

CREATE INDEX idx_engineering_events_run_pts
ON engineering_event_projections(run_id, event_pts_ms, counting_line_id, track_id);

CREATE INDEX idx_engineering_summary_run_dimensions
ON engineering_summary_rows(run_id, counting_line_id, canonical_direction, engineering_class, interval_index);

ALTER TABLE track_summaries ADD COLUMN evidence_schema_version TEXT NOT NULL DEFAULT 'legacy-track-summary-v1';
ALTER TABLE aggregate_snapshots ADD COLUMN engineering_result_revision_id TEXT;
ALTER TABLE aggregate_snapshots ADD COLUMN reconciliation_status TEXT NOT NULL DEFAULT 'NOT_GENERATED';
ALTER TABLE aggregate_snapshots ADD COLUMN summary_schema_version TEXT NOT NULL DEFAULT 'legacy-aggregate-v1';
ALTER TABLE video_sources ADD COLUMN recording_time_status TEXT NOT NULL DEFAULT 'UNCONFIGURED';
ALTER TABLE analysis_runs ADD COLUMN taxonomy_revision TEXT;
ALTER TABLE analysis_runs ADD COLUMN mapping_revision TEXT;
ALTER TABLE analysis_runs ADD COLUMN classification_policy_revision TEXT;

UPDATE video_sources
SET recording_time_status = CASE WHEN recording_time_configured = 1 THEN 'CONFIRMED' ELSE 'UNCONFIGURED' END;
