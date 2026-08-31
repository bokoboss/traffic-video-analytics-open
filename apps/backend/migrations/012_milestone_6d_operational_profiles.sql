CREATE TABLE processing_profile_revisions (
  id TEXT PRIMARY KEY,
  profile_code TEXT NOT NULL,
  profile_revision TEXT NOT NULL,
  display_name_en TEXT NOT NULL,
  display_name_th TEXT NOT NULL,
  description_en TEXT NOT NULL,
  description_th TEXT NOT NULL,
  intended_use TEXT NOT NULL,
  resolved_parameters_json TEXT NOT NULL,
  supported_domains_json TEXT NOT NULL,
  hardware_expectation TEXT NOT NULL,
  known_tradeoffs_json TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE,
  supersedes_revision TEXT,
  status TEXT NOT NULL,
  schema_revision TEXT NOT NULL,
  UNIQUE(profile_code, profile_revision)
);

CREATE TABLE processing_configuration_revisions (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id),
  profile_id TEXT NOT NULL,
  profile_revision TEXT NOT NULL,
  guided_settings_json TEXT NOT NULL,
  requested_expert_overrides_json TEXT NOT NULL,
  resolved_parameters_json TEXT NOT NULL,
  parameter_schema_revision TEXT NOT NULL,
  model_revision TEXT,
  weight_sha256 TEXT,
  tracker_revision TEXT,
  crossing_policy_revision TEXT NOT NULL,
  classification_policy_revision TEXT NOT NULL,
  device_request TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE,
  validation_result_json TEXT NOT NULL,
  status TEXT NOT NULL
);

CREATE TABLE preview_runs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  source_id TEXT NOT NULL REFERENCES video_sources(id),
  source_fingerprint_sha256 TEXT NOT NULL,
  scene_version_id TEXT NOT NULL REFERENCES scene_versions(id),
  scene_revision TEXT NOT NULL,
  scene_semantic_hash TEXT NOT NULL,
  start_pts_ms INTEGER NOT NULL,
  end_pts_ms INTEGER NOT NULL,
  processing_configuration_revision_id TEXT NOT NULL REFERENCES processing_configuration_revisions(id),
  run_type TEXT NOT NULL DEFAULT 'PREVIEW_ONLY',
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  idempotency_key TEXT,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  promoted_full_run_id TEXT,
  UNIQUE(project_id, idempotency_key)
);

CREATE TABLE preview_run_events (
  id TEXT PRIMARY KEY,
  preview_run_id TEXT NOT NULL REFERENCES preview_runs(id),
  track_id TEXT,
  counting_line_id TEXT,
  crossing_pts_ms INTEGER,
  classification TEXT,
  movement TEXT,
  event_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE preview_run_statistics (
  id TEXT PRIMARY KEY,
  preview_run_id TEXT NOT NULL REFERENCES preview_runs(id),
  statistics_json TEXT NOT NULL,
  warnings_json TEXT NOT NULL DEFAULT '[]',
  evidence_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  UNIQUE(preview_run_id)
);

CREATE TABLE configuration_comparisons (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  source_fingerprint_sha256 TEXT NOT NULL,
  scene_revision TEXT NOT NULL,
  segment_start_pts_ms INTEGER NOT NULL,
  segment_end_pts_ms INTEGER NOT NULL,
  taxonomy_revision TEXT NOT NULL,
  configuration_revision_ids_json TEXT NOT NULL,
  comparison_json TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE operational_candidate_selections (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  processing_configuration_revision_id TEXT NOT NULL REFERENCES processing_configuration_revisions(id),
  selection_status TEXT NOT NULL,
  candidate_status TEXT NOT NULL,
  rationale TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE capability_validation_records (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  processing_configuration_revision_id TEXT NOT NULL REFERENCES processing_configuration_revisions(id),
  preview_run_id TEXT REFERENCES preview_runs(id),
  validation_type TEXT NOT NULL,
  status TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  environment_json TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);

ALTER TABLE analysis_runs ADD COLUMN processing_configuration_revision_id TEXT;
ALTER TABLE analysis_runs ADD COLUMN run_type TEXT NOT NULL DEFAULT 'PRODUCTION';
ALTER TABLE analysis_runs ADD COLUMN preview_run_id TEXT;
ALTER TABLE preview_runs ADD COLUMN worker_id TEXT;
ALTER TABLE preview_runs ADD COLUMN claim_token TEXT;
ALTER TABLE preview_runs ADD COLUMN started_at TEXT;
ALTER TABLE preview_runs ADD COLUMN lease_expires_at TEXT;

CREATE INDEX idx_processing_profiles_code ON processing_profile_revisions(profile_code, profile_revision);
CREATE INDEX idx_processing_configurations_project ON processing_configuration_revisions(project_id, created_at);
CREATE INDEX idx_preview_runs_source_segment ON preview_runs(source_fingerprint_sha256, scene_revision, start_pts_ms, end_pts_ms);
CREATE INDEX idx_preview_events_run_pts ON preview_run_events(preview_run_id, crossing_pts_ms, counting_line_id, track_id);
CREATE INDEX idx_capability_validation_config ON capability_validation_records(processing_configuration_revision_id, created_at);

CREATE TRIGGER processing_profile_revisions_no_update
BEFORE UPDATE ON processing_profile_revisions
BEGIN SELECT RAISE(ABORT, 'processing_profile_revisions are immutable'); END;
CREATE TRIGGER processing_profile_revisions_no_delete
BEFORE DELETE ON processing_profile_revisions
BEGIN SELECT RAISE(ABORT, 'processing_profile_revisions are immutable'); END;
CREATE TRIGGER processing_configuration_revisions_no_update
BEFORE UPDATE ON processing_configuration_revisions
BEGIN SELECT RAISE(ABORT, 'processing_configuration_revisions are immutable'); END;
CREATE TRIGGER processing_configuration_revisions_no_delete
BEFORE DELETE ON processing_configuration_revisions
BEGIN SELECT RAISE(ABORT, 'processing_configuration_revisions are immutable'); END;
CREATE TRIGGER preview_runs_no_update
BEFORE UPDATE ON preview_runs
WHEN OLD.status IN ('COMPLETED', 'FAILED', 'CANCELLED')
 AND (
   NEW.status <> OLD.status
   OR COALESCE(NEW.completed_at, '') <> COALESCE(OLD.completed_at, '')
   OR NEW.promoted_full_run_id IS OLD.promoted_full_run_id
 )
BEGIN SELECT RAISE(ABORT, 'completed preview_runs are immutable'); END;
CREATE TRIGGER preview_run_events_no_update
BEFORE UPDATE ON preview_run_events
BEGIN SELECT RAISE(ABORT, 'preview_run_events are immutable'); END;
CREATE TRIGGER preview_run_events_no_delete
BEFORE DELETE ON preview_run_events
BEGIN SELECT RAISE(ABORT, 'preview_run_events are immutable'); END;
CREATE TRIGGER preview_run_statistics_no_update
BEFORE UPDATE ON preview_run_statistics
BEGIN SELECT RAISE(ABORT, 'preview_run_statistics are immutable'); END;
CREATE TRIGGER preview_run_statistics_no_delete
BEFORE DELETE ON preview_run_statistics
BEGIN SELECT RAISE(ABORT, 'preview_run_statistics are immutable'); END;
CREATE TRIGGER configuration_comparisons_no_update
BEFORE UPDATE ON configuration_comparisons
BEGIN SELECT RAISE(ABORT, 'configuration_comparisons are immutable'); END;
CREATE TRIGGER configuration_comparisons_no_delete
BEFORE DELETE ON configuration_comparisons
BEGIN SELECT RAISE(ABORT, 'configuration_comparisons are immutable'); END;
CREATE TRIGGER operational_candidate_selections_no_update
BEFORE UPDATE ON operational_candidate_selections
BEGIN SELECT RAISE(ABORT, 'operational_candidate_selections are append-only'); END;
CREATE TRIGGER operational_candidate_selections_no_delete
BEFORE DELETE ON operational_candidate_selections
BEGIN SELECT RAISE(ABORT, 'operational_candidate_selections are append-only'); END;
CREATE TRIGGER capability_validation_records_no_update
BEFORE UPDATE ON capability_validation_records
BEGIN SELECT RAISE(ABORT, 'capability_validation_records are immutable'); END;
CREATE TRIGGER capability_validation_records_no_delete
BEFORE DELETE ON capability_validation_records
BEGIN SELECT RAISE(ABORT, 'capability_validation_records are immutable'); END;
