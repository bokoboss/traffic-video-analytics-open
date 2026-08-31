-- Milestone 6E: review overlays, reviewed projections, certification and
-- controlled production exports. All automatic/engineering/benchmark history
-- remains additive and immutable; human decisions live in this layer.

DROP TRIGGER IF EXISTS review_actions_no_update;
DROP TRIGGER IF EXISTS review_actions_no_delete;
ALTER TABLE review_actions RENAME TO review_actions_legacy_013;

CREATE TABLE review_sessions (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  source_id TEXT NOT NULL REFERENCES video_sources(id),
  source_fingerprint_sha256 TEXT NOT NULL,
  processing_run_id TEXT NOT NULL REFERENCES analysis_runs(id),
  processing_configuration_revision_id TEXT,
  runtime_configuration_hash TEXT,
  engineering_result_revision_id TEXT NOT NULL REFERENCES engineering_result_revisions(id),
  scene_revision TEXT NOT NULL,
  scene_semantic_hash TEXT NOT NULL,
  taxonomy_revision TEXT NOT NULL,
  mapping_revision TEXT NOT NULL,
  classification_policy_revision TEXT NOT NULL,
  review_scope_type TEXT NOT NULL CHECK (review_scope_type IN ('FULL_RESULT', 'LINE', 'TIME_INTERVAL', 'DIAGNOSTIC_SUBSET')),
  review_scope_filter_json TEXT NOT NULL DEFAULT '{}',
  review_status TEXT NOT NULL DEFAULT 'NOT_STARTED' CHECK (review_status IN ('NOT_STARTED', 'IN_REVIEW', 'REVIEW_COMPLETE', 'BLOCKED', 'STALE', 'SUPERSEDED')),
  review_revision INTEGER NOT NULL DEFAULT 0 CHECK (review_revision >= 0),
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  completed_by TEXT,
  completed_at TEXT,
  stale_status TEXT NOT NULL DEFAULT 'CURRENT' CHECK (stale_status IN ('CURRENT', 'STALE')),
  superseded_by TEXT,
  scope_event_count INTEGER NOT NULL DEFAULT 0,
  reviewed_event_count INTEGER NOT NULL DEFAULT 0,
  confirmed_count INTEGER NOT NULL DEFAULT 0,
  corrected_count INTEGER NOT NULL DEFAULT 0,
  rejected_count INTEGER NOT NULL DEFAULT 0,
  duplicate_count INTEGER NOT NULL DEFAULT 0,
  human_added_count INTEGER NOT NULL DEFAULT 0,
  unscorable_count INTEGER NOT NULL DEFAULT 0,
  unreviewed_count INTEGER NOT NULL DEFAULT 0,
  unresolved_diagnostics_count INTEGER NOT NULL DEFAULT 0,
  conflict_count INTEGER NOT NULL DEFAULT 0,
  review_coverage_percent REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE review_session_state_events (
  id TEXT PRIMARY KEY,
  review_session_id TEXT NOT NULL REFERENCES review_sessions(id),
  from_status TEXT,
  to_status TEXT NOT NULL,
  review_revision INTEGER NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  actor_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- Replace the foundation action table with an additive nullable-target form.
-- Existing rows are copied verbatim and remain available to the legacy API.
CREATE TABLE review_actions (
  id TEXT PRIMARY KEY,
  review_action_id TEXT UNIQUE,
  review_session_id TEXT REFERENCES review_sessions(id),
  event_id TEXT REFERENCES auto_count_events(id),
  target_type TEXT NOT NULL DEFAULT 'AUTOMATIC_EVENT',
  target_event_id TEXT REFERENCES auto_count_events(id),
  target_review_event_id TEXT,
  action_type TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  reason_code TEXT NOT NULL DEFAULT '',
  comment TEXT NOT NULL DEFAULT '',
  reviewer TEXT NOT NULL DEFAULT 'mock-reviewer',
  reviewer_id TEXT NOT NULL DEFAULT 'mock-reviewer',
  new_classification TEXT,
  new_movement TEXT,
  reason TEXT,
  created_at TEXT NOT NULL,
  expected_review_revision INTEGER NOT NULL DEFAULT 0,
  action_order INTEGER NOT NULL DEFAULT 0,
  client_request_id TEXT,
  reverses_action_id TEXT REFERENCES review_actions(id),
  source_event_revision TEXT NOT NULL DEFAULT '',
  content_hash TEXT NOT NULL DEFAULT '',
  UNIQUE(review_session_id, client_request_id)
);

INSERT INTO review_actions(
  id, review_action_id, review_session_id, event_id, target_type,
  target_event_id, action_type, payload_json, reason_code, comment,
  reviewer, reviewer_id, new_classification, new_movement, reason,
  created_at, expected_review_revision, action_order, client_request_id,
  reverses_action_id, source_event_revision, content_hash
)
SELECT
  id, id, NULL, event_id, 'AUTOMATIC_EVENT', event_id, action_type,
  json_object(
    'new_classification', new_classification,
    'new_movement', new_movement,
    'reason', COALESCE(reason, ''),
    'reverses_action_id', reverses_action_id
  ), '', COALESCE(reason, ''), reviewer, reviewer,
  new_classification, new_movement, reason, created_at, 0, 0, NULL,
  reverses_action_id, '', ''
FROM review_actions_legacy_013;

CREATE TRIGGER review_actions_legacy_013_no_update
BEFORE UPDATE ON review_actions_legacy_013
BEGIN
  SELECT RAISE(ABORT, 'legacy review_actions are append-only');
END;

CREATE TRIGGER review_actions_legacy_013_no_delete
BEFORE DELETE ON review_actions_legacy_013
BEGIN
  SELECT RAISE(ABORT, 'legacy review_actions are append-only');
END;

CREATE INDEX idx_review_actions_session_order
  ON review_actions(review_session_id, action_order, created_at, id);
CREATE INDEX idx_review_actions_target_event
  ON review_actions(review_session_id, target_event_id, action_order, id);
CREATE INDEX idx_review_actions_client_request
  ON review_actions(review_session_id, client_request_id);

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

CREATE TRIGGER review_session_scope_no_update
BEFORE UPDATE ON review_sessions
WHEN OLD.project_id IS NOT NEW.project_id
  OR OLD.source_id IS NOT NEW.source_id
  OR OLD.source_fingerprint_sha256 IS NOT NEW.source_fingerprint_sha256
  OR OLD.processing_run_id IS NOT NEW.processing_run_id
  OR COALESCE(OLD.processing_configuration_revision_id, '') IS NOT COALESCE(NEW.processing_configuration_revision_id, '')
  OR COALESCE(OLD.runtime_configuration_hash, '') IS NOT COALESCE(NEW.runtime_configuration_hash, '')
  OR OLD.engineering_result_revision_id IS NOT NEW.engineering_result_revision_id
  OR OLD.scene_revision IS NOT NEW.scene_revision
  OR OLD.scene_semantic_hash IS NOT NEW.scene_semantic_hash
  OR OLD.taxonomy_revision IS NOT NEW.taxonomy_revision
  OR OLD.mapping_revision IS NOT NEW.mapping_revision
  OR OLD.classification_policy_revision IS NOT NEW.classification_policy_revision
  OR OLD.review_scope_type IS NOT NEW.review_scope_type
  OR OLD.review_scope_filter_json IS NOT NEW.review_scope_filter_json
BEGIN
  SELECT RAISE(ABORT, 'review session scope is immutable');
END;

CREATE TRIGGER review_session_state_events_no_update
BEFORE UPDATE ON review_session_state_events
BEGIN
  SELECT RAISE(ABORT, 'review_session_state_events are append-only');
END;

CREATE TRIGGER review_session_state_events_no_delete
BEFORE DELETE ON review_session_state_events
BEGIN
  SELECT RAISE(ABORT, 'review_session_state_events are append-only');
END;

CREATE TABLE reviewed_projection_revisions (
  id TEXT PRIMARY KEY,
  reviewed_projection_revision_id TEXT NOT NULL UNIQUE,
  review_session_id TEXT NOT NULL REFERENCES review_sessions(id),
  source_engineering_result_revision_id TEXT NOT NULL REFERENCES engineering_result_revisions(id),
  review_revision INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK (status IN ('CURRENT', 'HISTORICAL', 'STALE')),
  runtime_configuration_hash TEXT,
  scene_revision TEXT NOT NULL,
  taxonomy_revision TEXT NOT NULL,
  mapping_revision TEXT NOT NULL,
  classification_policy_revision TEXT NOT NULL,
  stale_status TEXT NOT NULL DEFAULT 'CURRENT'
);

CREATE TABLE reviewed_events (
  id TEXT PRIMARY KEY,
  reviewed_event_id TEXT NOT NULL UNIQUE,
  reviewed_projection_revision_id TEXT NOT NULL REFERENCES reviewed_projection_revisions(id),
  review_session_id TEXT NOT NULL REFERENCES review_sessions(id),
  origin TEXT NOT NULL CHECK (origin IN ('AUTOMATIC', 'HUMAN_ADDED')),
  source_event_id TEXT REFERENCES auto_count_events(id),
  human_add_action_id TEXT REFERENCES review_actions(id),
  effective_line_id TEXT NOT NULL,
  effective_line_name TEXT NOT NULL,
  effective_direction TEXT NOT NULL,
  effective_crossing_pts_ms INTEGER NOT NULL,
  effective_class TEXT NOT NULL,
  classification_status TEXT NOT NULL,
  review_status TEXT NOT NULL CHECK (review_status IN ('UNREVIEWED', 'CONFIRMED', 'CORRECTED', 'REJECTED_FALSE_POSITIVE', 'DUPLICATE_SUPPRESSED', 'UNSCORABLE', 'STALE')),
  duplicate_of TEXT,
  corrected_fields_json TEXT NOT NULL DEFAULT '[]',
  effective_action_ids_json TEXT NOT NULL DEFAULT '[]',
  evidence_json TEXT NOT NULL DEFAULT '{}',
  runtime_configuration_hash TEXT,
  scene_revision TEXT NOT NULL,
  taxonomy_revision TEXT NOT NULL,
  stale_status TEXT NOT NULL DEFAULT 'CURRENT',
  created_at TEXT NOT NULL,
  FOREIGN KEY (source_event_id) REFERENCES auto_count_events(id)
);

CREATE INDEX idx_reviewed_events_queue
  ON reviewed_events(reviewed_projection_revision_id, effective_crossing_pts_ms, effective_line_id, id);
CREATE INDEX idx_reviewed_events_source
  ON reviewed_events(review_session_id, source_event_id, reviewed_projection_revision_id);

CREATE TRIGGER reviewed_projection_revisions_no_update
BEFORE UPDATE ON reviewed_projection_revisions
BEGIN
  SELECT RAISE(ABORT, 'reviewed_projection_revisions are immutable');
END;

CREATE TRIGGER reviewed_projection_revisions_no_delete
BEFORE DELETE ON reviewed_projection_revisions
BEGIN
  SELECT RAISE(ABORT, 'reviewed_projection_revisions are immutable');
END;

CREATE TRIGGER reviewed_events_no_update
BEFORE UPDATE ON reviewed_events
BEGIN
  SELECT RAISE(ABORT, 'reviewed_events are immutable');
END;

CREATE TRIGGER reviewed_events_no_delete
BEFORE DELETE ON reviewed_events
BEGIN
  SELECT RAISE(ABORT, 'reviewed_events are immutable');
END;

CREATE TABLE review_reconciliation_runs (
  id TEXT PRIMARY KEY,
  review_session_id TEXT NOT NULL REFERENCES review_sessions(id),
  reviewed_projection_revision_id TEXT NOT NULL REFERENCES reviewed_projection_revisions(id),
  review_revision INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('PASSED', 'FAILED', 'STALE')),
  equation_json TEXT NOT NULL,
  by_line_json TEXT NOT NULL,
  by_direction_json TEXT NOT NULL,
  by_class_json TEXT NOT NULL,
  by_interval_json TEXT NOT NULL,
  diagnostics_json TEXT NOT NULL DEFAULT '[]',
  content_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);

CREATE INDEX idx_review_reconciliation_session
  ON review_reconciliation_runs(review_session_id, review_revision, created_at);

CREATE TRIGGER review_reconciliation_no_update
BEFORE UPDATE ON review_reconciliation_runs
BEGIN
  SELECT RAISE(ABORT, 'review_reconciliation_runs are immutable');
END;

CREATE TRIGGER review_reconciliation_no_delete
BEFORE DELETE ON review_reconciliation_runs
BEGIN
  SELECT RAISE(ABORT, 'review_reconciliation_runs are immutable');
END;

CREATE TABLE certification_revisions (
  id TEXT PRIMARY KEY,
  certification_revision_id TEXT NOT NULL UNIQUE,
  project_id TEXT NOT NULL REFERENCES projects(id),
  review_session_id TEXT NOT NULL REFERENCES review_sessions(id),
  reviewed_projection_revision_id TEXT NOT NULL REFERENCES reviewed_projection_revisions(id),
  review_scope_json TEXT NOT NULL,
  review_revision INTEGER NOT NULL,
  reconciliation_revision_id TEXT NOT NULL REFERENCES review_reconciliation_runs(id),
  source_id TEXT NOT NULL REFERENCES video_sources(id),
  source_fingerprint_sha256 TEXT NOT NULL,
  processing_run_id TEXT NOT NULL REFERENCES analysis_runs(id),
  processing_configuration_revision_id TEXT,
  runtime_configuration_hash TEXT,
  scene_revision TEXT NOT NULL,
  taxonomy_revision TEXT NOT NULL,
  mapping_revision TEXT NOT NULL,
  classification_policy_revision TEXT NOT NULL,
  benchmark_reference_json TEXT NOT NULL DEFAULT '{}',
  qualification_disclosure TEXT NOT NULL,
  rights_disclosure TEXT NOT NULL,
  review_summary_json TEXT NOT NULL,
  certified_by TEXT NOT NULL,
  certified_at TEXT NOT NULL,
  certification_hash TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK (status IN ('DRAFT', 'CERTIFIED', 'REVOKED', 'STALE', 'SUPERSEDED')),
  stale_status TEXT NOT NULL DEFAULT 'CURRENT'
);

CREATE INDEX idx_certifications_project_time
  ON certification_revisions(project_id, certified_at, id);

CREATE TABLE certification_revocations (
  id TEXT PRIMARY KEY,
  revocation_id TEXT NOT NULL UNIQUE,
  certification_revision_id TEXT NOT NULL REFERENCES certification_revisions(id),
  reason TEXT NOT NULL,
  revoked_by TEXT NOT NULL,
  revoked_at TEXT NOT NULL
);

CREATE TRIGGER certification_revisions_no_update
BEFORE UPDATE ON certification_revisions
BEGIN
  SELECT RAISE(ABORT, 'certification_revisions are immutable');
END;

CREATE TRIGGER certification_revisions_no_delete
BEFORE DELETE ON certification_revisions
BEGIN
  SELECT RAISE(ABORT, 'certification_revisions are immutable');
END;

CREATE TRIGGER certification_revocations_no_update
BEFORE UPDATE ON certification_revocations
BEGIN
  SELECT RAISE(ABORT, 'certification_revocations are append-only');
END;

CREATE TRIGGER certification_revocations_no_delete
BEFORE DELETE ON certification_revocations
BEGIN
  SELECT RAISE(ABORT, 'certification_revocations are append-only');
END;

CREATE TABLE export_revisions (
  id TEXT PRIMARY KEY,
  export_revision_id TEXT NOT NULL UNIQUE,
  certification_revision_id TEXT NOT NULL REFERENCES certification_revisions(id),
  format TEXT NOT NULL CHECK (format IN ('CSV', 'XLSX', 'JSON')),
  status TEXT NOT NULL CHECK (status IN ('REQUESTED', 'COMPLETED', 'FAILED', 'STALE')),
  client_request_id TEXT,
  request_hash TEXT NOT NULL,
  manifest_json TEXT NOT NULL DEFAULT '{}',
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  error_code TEXT,
  UNIQUE(certification_revision_id, format, client_request_id)
);

CREATE TABLE export_artifacts (
  id TEXT PRIMARY KEY,
  artifact_id TEXT NOT NULL UNIQUE,
  export_revision_id TEXT NOT NULL REFERENCES export_revisions(id),
  safe_filename TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  content_length INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  row_count INTEGER,
  sheet_count INTEGER,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('COMPLETED', 'CLEANED_UP'))
);

CREATE INDEX idx_export_revisions_certification
  ON export_revisions(certification_revision_id, created_at, id);
CREATE INDEX idx_export_artifacts_export
  ON export_artifacts(export_revision_id);

CREATE TRIGGER export_revisions_no_update
BEFORE UPDATE ON export_revisions
BEGIN
  SELECT RAISE(ABORT, 'export_revisions are immutable');
END;

CREATE TRIGGER export_revisions_no_delete
BEFORE DELETE ON export_revisions
BEGIN
  SELECT RAISE(ABORT, 'export_revisions are immutable');
END;

CREATE TRIGGER export_artifacts_no_update
BEFORE UPDATE ON export_artifacts
BEGIN
  SELECT RAISE(ABORT, 'export_artifacts are immutable');
END;

CREATE TRIGGER export_artifacts_no_delete
BEFORE DELETE ON export_artifacts
BEGIN
  SELECT RAISE(ABORT, 'export_artifacts are immutable');
END;

CREATE INDEX idx_review_sessions_project_status
  ON review_sessions(project_id, review_status, created_at, id);
CREATE INDEX idx_review_sessions_run
  ON review_sessions(processing_run_id, created_at, id);
