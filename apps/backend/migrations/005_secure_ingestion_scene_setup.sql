ALTER TABLE video_sources ADD COLUMN media_id TEXT;
ALTER TABLE video_sources ADD COLUMN stored_internal_filename TEXT;
ALTER TABLE video_sources ADD COLUMN original_extension TEXT;
ALTER TABLE video_sources ADD COLUMN retention_state TEXT NOT NULL DEFAULT 'active';
ALTER TABLE video_sources ADD COLUMN media_schema_version TEXT NOT NULL DEFAULT 'media-metadata-v1';
ALTER TABLE video_sources ADD COLUMN probe_timestamp TEXT;
ALTER TABLE video_sources ADD COLUMN audio_present INTEGER;
ALTER TABLE video_sources ADD COLUMN readiness_state TEXT NOT NULL DEFAULT 'media_ready';

CREATE INDEX idx_video_sources_project_hash
ON video_sources(project_id, fingerprint_sha256);

CREATE TABLE scene_revision_audit (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  previous_scene_version_id TEXT,
  new_scene_version_id TEXT NOT NULL REFERENCES scene_versions(id),
  previous_version INTEGER,
  new_version INTEGER NOT NULL,
  changed_fields_json TEXT NOT NULL,
  reason TEXT,
  created_at TEXT NOT NULL
);
