CREATE TABLE benchmark_corpus_revisions (
  id TEXT PRIMARY KEY,
  revision TEXT NOT NULL UNIQUE,
  content_hash TEXT NOT NULL UNIQUE,
  manifest_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL
);

CREATE TABLE benchmark_sources (
  id TEXT PRIMARY KEY,
  corpus_revision_id TEXT NOT NULL REFERENCES benchmark_corpus_revisions(id),
  benchmark_source_id TEXT NOT NULL,
  source_fingerprint_sha256 TEXT NOT NULL,
  file_name_or_external_reference TEXT NOT NULL,
  media_duration_ms INTEGER NOT NULL,
  source_width INTEGER NOT NULL,
  source_height INTEGER NOT NULL,
  nominal_fps_if_known REAL,
  recording_start_status TEXT NOT NULL,
  timezone_name TEXT,
  rights_status TEXT NOT NULL,
  rights_basis TEXT NOT NULL,
  permission_reference TEXT,
  redistribution_status TEXT NOT NULL,
  storage_status TEXT NOT NULL,
  checksum_verified INTEGER NOT NULL DEFAULT 0,
  scene_revision TEXT NOT NULL,
  annotation_revision TEXT,
  benchmark_split TEXT NOT NULL,
  condition_tags_json TEXT NOT NULL DEFAULT '[]',
  notes_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  UNIQUE(corpus_revision_id, benchmark_source_id),
  UNIQUE(corpus_revision_id, source_fingerprint_sha256)
);

CREATE TABLE benchmark_rights_records (
  id TEXT PRIMARY KEY,
  benchmark_source_id TEXT NOT NULL REFERENCES benchmark_sources(id),
  rights_status TEXT NOT NULL,
  rights_basis TEXT NOT NULL,
  permission_reference TEXT,
  redistribution_status TEXT NOT NULL,
  storage_status TEXT NOT NULL,
  notes_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  UNIQUE(benchmark_source_id)
);

CREATE TABLE ground_truth_revisions (
  id TEXT PRIMARY KEY,
  benchmark_source_id TEXT NOT NULL REFERENCES benchmark_sources(id),
  revision TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  annotation_schema_version TEXT NOT NULL,
  parent_revision_id TEXT REFERENCES ground_truth_revisions(id),
  reviewer_agreement_json TEXT NOT NULL DEFAULT '{}',
  notes_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  UNIQUE(benchmark_source_id, revision),
  UNIQUE(benchmark_source_id, content_hash)
);

CREATE TABLE ground_truth_events (
  id TEXT PRIMARY KEY,
  ground_truth_event_id TEXT NOT NULL,
  benchmark_source_id TEXT NOT NULL REFERENCES benchmark_sources(id),
  ground_truth_revision_id TEXT NOT NULL REFERENCES ground_truth_revisions(id),
  scene_revision TEXT NOT NULL,
  counting_line_id TEXT NOT NULL,
  canonical_direction TEXT NOT NULL,
  crossing_pts_ms INTEGER NOT NULL,
  engineering_class TEXT NOT NULL,
  classification_status TEXT NOT NULL,
  timestamp_status TEXT NOT NULL,
  annotation_status TEXT NOT NULL,
  evidence_refs_json TEXT NOT NULL DEFAULT '{}',
  notes TEXT NOT NULL DEFAULT '',
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  source_frame_index INTEGER,
  source_frame_pts_ms INTEGER,
  frame_start_index INTEGER,
  frame_end_index INTEGER,
  identity_id TEXT,
  UNIQUE(ground_truth_revision_id, ground_truth_event_id)
);

CREATE TABLE ground_truth_reviewer_annotations (
  id TEXT PRIMARY KEY,
  ground_truth_event_id TEXT NOT NULL REFERENCES ground_truth_events(id),
  reviewer_id TEXT NOT NULL,
  annotation_revision TEXT NOT NULL,
  event_decision TEXT NOT NULL,
  class_decision TEXT,
  direction_decision TEXT,
  timestamp_decision_ms INTEGER,
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE(ground_truth_event_id, reviewer_id, annotation_revision)
);

CREATE TABLE benchmark_evaluation_configurations (
  id TEXT PRIMARY KEY,
  revision TEXT NOT NULL UNIQUE,
  content_hash TEXT NOT NULL UNIQUE,
  match_tolerance_ms INTEGER NOT NULL,
  direction_matching_mode TEXT NOT NULL,
  class_evaluation_mode TEXT NOT NULL,
  ignore_region_policy TEXT NOT NULL,
  timestamp_outlier_ms INTEGER NOT NULL,
  interval_bucket_minutes INTEGER NOT NULL,
  config_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL
);

CREATE TABLE benchmark_runs (
  id TEXT PRIMARY KEY,
  benchmark_source_id TEXT NOT NULL REFERENCES benchmark_sources(id),
  corpus_revision_id TEXT NOT NULL REFERENCES benchmark_corpus_revisions(id),
  ground_truth_revision_id TEXT NOT NULL REFERENCES ground_truth_revisions(id),
  automatic_run_id TEXT NOT NULL REFERENCES analysis_runs(id),
  automatic_result_revision_id TEXT NOT NULL REFERENCES engineering_result_revisions(id),
  evaluation_configuration_id TEXT NOT NULL REFERENCES benchmark_evaluation_configurations(id),
  status TEXT NOT NULL,
  qualification_status TEXT NOT NULL DEFAULT 'NOT_RUN',
  source_fingerprint_sha256 TEXT NOT NULL,
  scene_revision TEXT NOT NULL,
  code_commit_sha TEXT NOT NULL,
  taxonomy_revision TEXT NOT NULL,
  mapping_revision TEXT NOT NULL,
  classification_policy_revision TEXT NOT NULL,
  model_id TEXT,
  weight_sha256 TEXT,
  detector_version TEXT,
  tracker_version TEXT,
  configuration_hash TEXT NOT NULL,
  environment_json TEXT NOT NULL DEFAULT '{}',
  run_configuration_json TEXT NOT NULL DEFAULT '{}',
  started_at TEXT NOT NULL,
  completed_at TEXT,
  result_summary_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(automatic_run_id, ground_truth_revision_id, evaluation_configuration_id)
);

CREATE TABLE benchmark_event_matches (
  id TEXT PRIMARY KEY,
  benchmark_run_id TEXT NOT NULL REFERENCES benchmark_runs(id),
  automatic_event_id TEXT,
  ground_truth_event_id TEXT,
  primary_category TEXT NOT NULL,
  secondary_error_flags_json TEXT NOT NULL DEFAULT '[]',
  timestamp_error_ms INTEGER,
  absolute_timestamp_error_ms INTEGER,
  counting_line_id TEXT,
  automatic_direction TEXT,
  ground_truth_direction TEXT,
  automatic_track_id TEXT,
  technical_key TEXT,
  duplicate_event_ids_json TEXT NOT NULL DEFAULT '[]',
  selected INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE benchmark_metric_snapshots (
  id TEXT PRIMARY KEY,
  benchmark_run_id TEXT NOT NULL REFERENCES benchmark_runs(id),
  metric_scope TEXT NOT NULL,
  dimension_key TEXT NOT NULL,
  metrics_json TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(benchmark_run_id, metric_scope, dimension_key)
);

CREATE TABLE benchmark_fragmentation_metrics (
  id TEXT PRIMARY KEY,
  benchmark_run_id TEXT NOT NULL REFERENCES benchmark_runs(id),
  availability TEXT NOT NULL,
  metrics_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(benchmark_run_id)
);

CREATE TABLE benchmark_throughput_metrics (
  id TEXT PRIMARY KEY,
  benchmark_run_id TEXT NOT NULL REFERENCES benchmark_runs(id),
  metrics_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(benchmark_run_id)
);

CREATE TABLE benchmark_experiment_suites (
  id TEXT PRIMARY KEY,
  revision TEXT NOT NULL UNIQUE,
  corpus_revision_id TEXT NOT NULL REFERENCES benchmark_corpus_revisions(id),
  calibration_split TEXT NOT NULL,
  holdout_split TEXT NOT NULL,
  baseline_configuration_id TEXT,
  candidate_configurations_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL
);

CREATE TABLE benchmark_experiment_runs (
  id TEXT PRIMARY KEY,
  suite_id TEXT NOT NULL REFERENCES benchmark_experiment_suites(id),
  benchmark_run_id TEXT NOT NULL REFERENCES benchmark_runs(id),
  configuration_revision TEXT NOT NULL,
  benchmark_split TEXT NOT NULL,
  repetition_index INTEGER NOT NULL DEFAULT 0,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(suite_id, benchmark_run_id, configuration_revision, repetition_index)
);

CREATE TABLE benchmark_qualification_reports (
  id TEXT PRIMARY KEY,
  benchmark_run_id TEXT NOT NULL REFERENCES benchmark_runs(id),
  status TEXT NOT NULL,
  gates_json TEXT NOT NULL,
  policy_json TEXT NOT NULL DEFAULT '{}',
  report_json TEXT NOT NULL,
  report_markdown TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(benchmark_run_id)
);

CREATE INDEX idx_benchmark_sources_split ON benchmark_sources(corpus_revision_id, benchmark_split, rights_status);
CREATE INDEX idx_ground_truth_events_revision_pts ON ground_truth_events(ground_truth_revision_id, crossing_pts_ms, counting_line_id, canonical_direction);
CREATE INDEX idx_benchmark_runs_source_status ON benchmark_runs(benchmark_source_id, status, qualification_status);
CREATE INDEX idx_benchmark_matches_run_category ON benchmark_event_matches(benchmark_run_id, primary_category, id);
CREATE INDEX idx_benchmark_metrics_run_scope ON benchmark_metric_snapshots(benchmark_run_id, metric_scope, dimension_key);

CREATE TRIGGER benchmark_corpus_revisions_no_update
BEFORE UPDATE ON benchmark_corpus_revisions
BEGIN SELECT RAISE(ABORT, 'benchmark_corpus_revisions are immutable'); END;
CREATE TRIGGER benchmark_corpus_revisions_no_delete
BEFORE DELETE ON benchmark_corpus_revisions
BEGIN SELECT RAISE(ABORT, 'benchmark_corpus_revisions are immutable'); END;
CREATE TRIGGER benchmark_sources_no_update
BEFORE UPDATE ON benchmark_sources
BEGIN SELECT RAISE(ABORT, 'benchmark_sources are immutable'); END;
CREATE TRIGGER benchmark_sources_no_delete
BEFORE DELETE ON benchmark_sources
BEGIN SELECT RAISE(ABORT, 'benchmark_sources are immutable'); END;
CREATE TRIGGER benchmark_rights_records_no_update
BEFORE UPDATE ON benchmark_rights_records
BEGIN SELECT RAISE(ABORT, 'benchmark_rights_records are immutable'); END;
CREATE TRIGGER benchmark_rights_records_no_delete
BEFORE DELETE ON benchmark_rights_records
BEGIN SELECT RAISE(ABORT, 'benchmark_rights_records are immutable'); END;
CREATE TRIGGER ground_truth_revisions_no_update
BEFORE UPDATE ON ground_truth_revisions
BEGIN SELECT RAISE(ABORT, 'ground_truth_revisions are immutable'); END;
CREATE TRIGGER ground_truth_revisions_no_delete
BEFORE DELETE ON ground_truth_revisions
BEGIN SELECT RAISE(ABORT, 'ground_truth_revisions are immutable'); END;
CREATE TRIGGER ground_truth_events_no_update
BEFORE UPDATE ON ground_truth_events
BEGIN SELECT RAISE(ABORT, 'ground_truth_events are immutable'); END;
CREATE TRIGGER ground_truth_events_no_delete
BEFORE DELETE ON ground_truth_events
BEGIN SELECT RAISE(ABORT, 'ground_truth_events are immutable'); END;
CREATE TRIGGER ground_truth_reviewer_annotations_no_update
BEFORE UPDATE ON ground_truth_reviewer_annotations
BEGIN SELECT RAISE(ABORT, 'ground_truth_reviewer_annotations are append-only'); END;
CREATE TRIGGER ground_truth_reviewer_annotations_no_delete
BEFORE DELETE ON ground_truth_reviewer_annotations
BEGIN SELECT RAISE(ABORT, 'ground_truth_reviewer_annotations are append-only'); END;
CREATE TRIGGER benchmark_evaluation_configurations_no_update
BEFORE UPDATE ON benchmark_evaluation_configurations
BEGIN SELECT RAISE(ABORT, 'benchmark_evaluation_configurations are immutable'); END;
CREATE TRIGGER benchmark_evaluation_configurations_no_delete
BEFORE DELETE ON benchmark_evaluation_configurations
BEGIN SELECT RAISE(ABORT, 'benchmark_evaluation_configurations are immutable'); END;
CREATE TRIGGER benchmark_runs_no_update
BEFORE UPDATE ON benchmark_runs
WHEN OLD.status = 'COMPLETED'
BEGIN SELECT RAISE(ABORT, 'completed benchmark_runs are immutable'); END;
CREATE TRIGGER benchmark_runs_no_delete
BEFORE DELETE ON benchmark_runs
BEGIN SELECT RAISE(ABORT, 'benchmark_runs are append-only'); END;
CREATE TRIGGER benchmark_event_matches_no_update
BEFORE UPDATE ON benchmark_event_matches
BEGIN SELECT RAISE(ABORT, 'benchmark_event_matches are immutable'); END;
CREATE TRIGGER benchmark_event_matches_no_delete
BEFORE DELETE ON benchmark_event_matches
BEGIN SELECT RAISE(ABORT, 'benchmark_event_matches are immutable'); END;
CREATE TRIGGER benchmark_metric_snapshots_no_update
BEFORE UPDATE ON benchmark_metric_snapshots
BEGIN SELECT RAISE(ABORT, 'benchmark_metric_snapshots are immutable'); END;
CREATE TRIGGER benchmark_metric_snapshots_no_delete
BEFORE DELETE ON benchmark_metric_snapshots
BEGIN SELECT RAISE(ABORT, 'benchmark_metric_snapshots are immutable'); END;
CREATE TRIGGER benchmark_fragmentation_metrics_no_update
BEFORE UPDATE ON benchmark_fragmentation_metrics
BEGIN SELECT RAISE(ABORT, 'benchmark_fragmentation_metrics are immutable'); END;
CREATE TRIGGER benchmark_fragmentation_metrics_no_delete
BEFORE DELETE ON benchmark_fragmentation_metrics
BEGIN SELECT RAISE(ABORT, 'benchmark_fragmentation_metrics are immutable'); END;
CREATE TRIGGER benchmark_throughput_metrics_no_update
BEFORE UPDATE ON benchmark_throughput_metrics
BEGIN SELECT RAISE(ABORT, 'benchmark_throughput_metrics are immutable'); END;
CREATE TRIGGER benchmark_throughput_metrics_no_delete
BEFORE DELETE ON benchmark_throughput_metrics
BEGIN SELECT RAISE(ABORT, 'benchmark_throughput_metrics are immutable'); END;
CREATE TRIGGER benchmark_experiment_suites_no_update
BEFORE UPDATE ON benchmark_experiment_suites
BEGIN SELECT RAISE(ABORT, 'benchmark_experiment_suites are immutable'); END;
CREATE TRIGGER benchmark_experiment_suites_no_delete
BEFORE DELETE ON benchmark_experiment_suites
BEGIN SELECT RAISE(ABORT, 'benchmark_experiment_suites are immutable'); END;
CREATE TRIGGER benchmark_experiment_runs_no_update
BEFORE UPDATE ON benchmark_experiment_runs
BEGIN SELECT RAISE(ABORT, 'benchmark_experiment_runs are immutable'); END;
CREATE TRIGGER benchmark_experiment_runs_no_delete
BEFORE DELETE ON benchmark_experiment_runs
BEGIN SELECT RAISE(ABORT, 'benchmark_experiment_runs are immutable'); END;
CREATE TRIGGER benchmark_qualification_reports_no_update
BEFORE UPDATE ON benchmark_qualification_reports
BEGIN SELECT RAISE(ABORT, 'benchmark_qualification_reports are immutable'); END;
CREATE TRIGGER benchmark_qualification_reports_no_delete
BEFORE DELETE ON benchmark_qualification_reports
BEGIN SELECT RAISE(ABORT, 'benchmark_qualification_reports are immutable'); END;
