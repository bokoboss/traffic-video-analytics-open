-- Additive acceptance remediation for databases that already applied 012.
-- Existing 6D rows remain intact; rows without enough immutable runtime
-- evidence are explicitly marked LEGACY_UNRESOLVED rather than guessed.

ALTER TABLE processing_configuration_revisions ADD COLUMN runtime_configuration_hash TEXT;
ALTER TABLE processing_configuration_revisions ADD COLUMN request_provenance_hash TEXT;
ALTER TABLE processing_configuration_revisions ADD COLUMN requested_guided_settings_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE processing_configuration_revisions ADD COLUMN provenance_status TEXT NOT NULL DEFAULT 'LEGACY_UNRESOLVED';
ALTER TABLE processing_configuration_revisions ADD COLUMN runtime_provenance_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE analysis_runs ADD COLUMN runtime_configuration_hash TEXT;
ALTER TABLE analysis_runs ADD COLUMN request_provenance_hash TEXT;
ALTER TABLE analysis_runs ADD COLUMN actual_runtime_configuration_hash TEXT;
ALTER TABLE analysis_runs ADD COLUMN runtime_provenance_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE analysis_runs ADD COLUMN provenance_status TEXT NOT NULL DEFAULT 'LEGACY_UNRESOLVED';

ALTER TABLE real_inference_runs ADD COLUMN runtime_configuration_hash TEXT;
ALTER TABLE real_inference_runs ADD COLUMN actual_runtime_configuration_hash TEXT;
ALTER TABLE real_inference_runs ADD COLUMN provenance_status TEXT NOT NULL DEFAULT 'LEGACY_UNRESOLVED';

ALTER TABLE preview_runs ADD COLUMN runtime_configuration_hash TEXT;
ALTER TABLE preview_runs ADD COLUMN request_fingerprint TEXT;
ALTER TABLE preview_runs ADD COLUMN heartbeat_at TEXT;
ALTER TABLE preview_runs ADD COLUMN attempt_number INTEGER NOT NULL DEFAULT 0;
ALTER TABLE preview_runs ADD COLUMN cancellation_requested_at TEXT;
ALTER TABLE preview_runs ADD COLUMN cancellation_reason TEXT;
ALTER TABLE preview_runs ADD COLUMN provenance_status TEXT NOT NULL DEFAULT 'LEGACY_UNRESOLVED';

ALTER TABLE benchmark_runs ADD COLUMN runtime_configuration_hash TEXT;
ALTER TABLE benchmark_runs ADD COLUMN provenance_status TEXT NOT NULL DEFAULT 'LEGACY_UNRESOLVED';

ALTER TABLE crossing_event_ledger ADD COLUMN runtime_configuration_hash TEXT;
ALTER TABLE crossing_event_ledger ADD COLUMN request_provenance_hash TEXT;
ALTER TABLE auto_count_events ADD COLUMN runtime_configuration_hash TEXT;
ALTER TABLE auto_count_events ADD COLUMN request_provenance_hash TEXT;
ALTER TABLE track_summaries ADD COLUMN runtime_configuration_hash TEXT;
ALTER TABLE track_summaries ADD COLUMN runtime_provenance_json TEXT NOT NULL DEFAULT '{}';

CREATE TABLE preview_run_state_events (
  id TEXT PRIMARY KEY,
  preview_run_id TEXT NOT NULL REFERENCES preview_runs(id),
  event_type TEXT NOT NULL,
  worker_id TEXT,
  attempt_number INTEGER,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX idx_processing_configurations_runtime_hash
ON processing_configuration_revisions(runtime_configuration_hash, created_at);
CREATE INDEX idx_processing_configurations_provenance_hash
ON processing_configuration_revisions(request_provenance_hash, created_at);
CREATE INDEX idx_analysis_runs_runtime_hash
ON analysis_runs(runtime_configuration_hash, created_at);
CREATE INDEX idx_preview_runs_request_fingerprint
ON preview_runs(project_id, idempotency_key, request_fingerprint);
CREATE INDEX idx_preview_runs_runtime_hash
ON preview_runs(runtime_configuration_hash, created_at);
CREATE INDEX idx_preview_runs_lease
ON preview_runs(status, lease_expires_at);
CREATE INDEX idx_preview_state_events_run_created
ON preview_run_state_events(preview_run_id, created_at, id);
CREATE INDEX idx_benchmark_runs_runtime_hash
ON benchmark_runs(runtime_configuration_hash, started_at);
CREATE INDEX idx_crossing_events_runtime_hash
ON crossing_event_ledger(runtime_configuration_hash, run_id);
CREATE INDEX idx_auto_count_events_runtime_hash
ON auto_count_events(runtime_configuration_hash, run_id);

CREATE TRIGGER preview_run_state_events_no_update
BEFORE UPDATE ON preview_run_state_events
BEGIN SELECT RAISE(ABORT, 'preview_run_state_events are append-only'); END;
CREATE TRIGGER preview_run_state_events_no_delete
BEFORE DELETE ON preview_run_state_events
BEGIN SELECT RAISE(ABORT, 'preview_run_state_events are append-only'); END;

DROP TRIGGER preview_runs_no_update;
CREATE TRIGGER preview_runs_no_update
BEFORE UPDATE ON preview_runs
WHEN OLD.status IN ('COMPLETED', 'FAILED', 'CANCELLED')
 AND (
   NEW.id IS NOT OLD.id
   OR NEW.project_id IS NOT OLD.project_id
   OR NEW.source_id IS NOT OLD.source_id
   OR NEW.source_fingerprint_sha256 IS NOT OLD.source_fingerprint_sha256
   OR NEW.scene_version_id IS NOT OLD.scene_version_id
   OR NEW.scene_revision IS NOT OLD.scene_revision
   OR NEW.scene_semantic_hash IS NOT OLD.scene_semantic_hash
   OR NEW.start_pts_ms IS NOT OLD.start_pts_ms
   OR NEW.end_pts_ms IS NOT OLD.end_pts_ms
   OR NEW.processing_configuration_revision_id IS NOT OLD.processing_configuration_revision_id
   OR NEW.run_type IS NOT OLD.run_type
   OR NEW.mode IS NOT OLD.mode
   OR NEW.status IS NOT OLD.status
   OR NEW.idempotency_key IS NOT OLD.idempotency_key
   OR NEW.created_by IS NOT OLD.created_by
   OR NEW.created_at IS NOT OLD.created_at
   OR NEW.completed_at IS NOT OLD.completed_at
   OR NEW.worker_id IS NOT OLD.worker_id
   OR NEW.claim_token IS NOT OLD.claim_token
   OR NEW.started_at IS NOT OLD.started_at
   OR NEW.lease_expires_at IS NOT OLD.lease_expires_at
   OR NEW.runtime_configuration_hash IS NOT OLD.runtime_configuration_hash
   OR NEW.request_fingerprint IS NOT OLD.request_fingerprint
   OR NEW.heartbeat_at IS NOT OLD.heartbeat_at
   OR NEW.attempt_number IS NOT OLD.attempt_number
   OR NEW.cancellation_requested_at IS NOT OLD.cancellation_requested_at
   OR NEW.cancellation_reason IS NOT OLD.cancellation_reason
   OR NEW.provenance_status IS NOT OLD.provenance_status
 )
BEGIN SELECT RAISE(ABORT, 'terminal preview_runs are immutable except promotion'); END;
