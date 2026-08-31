ALTER TABLE analysis_runs ADD COLUMN job_state TEXT;
ALTER TABLE analysis_runs ADD COLUMN progress_phase TEXT;
ALTER TABLE analysis_runs ADD COLUMN progress_completed_units INTEGER DEFAULT 0;
ALTER TABLE analysis_runs ADD COLUMN progress_total_units INTEGER;
ALTER TABLE analysis_runs ADD COLUMN progress_fraction REAL;
ALTER TABLE analysis_runs ADD COLUMN progress_message_code TEXT;
ALTER TABLE analysis_runs ADD COLUMN queued_at TEXT;
ALTER TABLE analysis_runs ADD COLUMN started_at TEXT;
ALTER TABLE analysis_runs ADD COLUMN last_heartbeat_at TEXT;
ALTER TABLE analysis_runs ADD COLUMN cancellation_requested_at TEXT;
ALTER TABLE analysis_runs ADD COLUMN cancelled_at TEXT;
ALTER TABLE analysis_runs ADD COLUMN failed_at TEXT;
ALTER TABLE analysis_runs ADD COLUMN worker_id TEXT;
ALTER TABLE analysis_runs ADD COLUMN claim_token TEXT;
ALTER TABLE analysis_runs ADD COLUMN claimed_at TEXT;
ALTER TABLE analysis_runs ADD COLUMN lease_expires_at TEXT;
ALTER TABLE analysis_runs ADD COLUMN idempotency_key TEXT;
ALTER TABLE analysis_runs ADD COLUMN request_fingerprint TEXT;
ALTER TABLE analysis_runs ADD COLUMN engine_mode TEXT;
ALTER TABLE analysis_runs ADD COLUMN processing_config_json TEXT;
ALTER TABLE analysis_runs ADD COLUMN source_fingerprint_sha256 TEXT;
ALTER TABLE analysis_runs ADD COLUMN scene_revision INTEGER;
ALTER TABLE analysis_runs ADD COLUMN scene_semantic_hash TEXT;
ALTER TABLE analysis_runs ADD COLUMN retry_of_run_id TEXT;
ALTER TABLE analysis_runs ADD COLUMN error_code TEXT;
ALTER TABLE analysis_runs ADD COLUMN error_category TEXT;
ALTER TABLE analysis_runs ADD COLUMN error_detail TEXT;
ALTER TABLE analysis_runs ADD COLUMN result_ready INTEGER DEFAULT 0;

UPDATE analysis_runs
SET job_state = CASE
    WHEN state = 'processing_complete' THEN 'COMPLETED'
    WHEN state = 'failed' THEN 'FAILED'
    ELSE 'RUNNING'
  END,
  progress_phase = CASE
    WHEN state = 'processing_complete' THEN 'COMPLETED'
    WHEN state = 'failed' THEN 'FAILED'
    ELSE 'PROCESSING'
  END,
  progress_completed_units = progress_percent,
  progress_total_units = 100,
  progress_fraction = progress_percent / 100.0,
  result_ready = CASE WHEN state = 'processing_complete' THEN 1 ELSE 0 END
WHERE job_state IS NULL;

CREATE UNIQUE INDEX idx_analysis_runs_project_idempotency
ON analysis_runs(project_id, idempotency_key)
WHERE idempotency_key IS NOT NULL;

CREATE INDEX idx_analysis_runs_project_job_state
ON analysis_runs(project_id, job_state, created_at);

CREATE INDEX idx_analysis_runs_request_fingerprint
ON analysis_runs(project_id, request_fingerprint, job_state);

CREATE INDEX idx_analysis_runs_worker_lease
ON analysis_runs(job_state, lease_expires_at);

CREATE TABLE IF NOT EXISTS processing_job_events (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES analysis_runs(id),
  event_type TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TRIGGER processing_job_events_no_update
BEFORE UPDATE ON processing_job_events
BEGIN
  SELECT RAISE(ABORT, 'processing job events are append-only');
END;

CREATE TRIGGER processing_job_events_no_delete
BEFORE DELETE ON processing_job_events
BEGIN
  SELECT RAISE(ABORT, 'processing job events are append-only');
END;
