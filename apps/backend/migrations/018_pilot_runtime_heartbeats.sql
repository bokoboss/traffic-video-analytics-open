CREATE TABLE runtime_heartbeats (
  component TEXT PRIMARY KEY CHECK (component IN ('worker')),
  instance_token TEXT NOT NULL,
  worker_id TEXT NOT NULL,
  pid INTEGER,
  started_at TEXT NOT NULL,
  last_heartbeat_at TEXT NOT NULL,
  runtime_version TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('STARTING', 'READY', 'BUSY', 'DEGRADED', 'STALE', 'OFFLINE')),
  updated_at TEXT NOT NULL
);

CREATE INDEX idx_runtime_heartbeats_last_heartbeat
  ON runtime_heartbeats(last_heartbeat_at);
