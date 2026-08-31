CREATE TABLE reference_frames (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(id),
  source_id TEXT NOT NULL REFERENCES video_sources(id),
  source_fingerprint_sha256 TEXT NOT NULL,
  requested_pts_ms INTEGER NOT NULL,
  resolved_pts_ms INTEGER,
  decoder_seek_pts_ms INTEGER,
  first_decoded_pts_ms INTEGER,
  selected_pts_ms INTEGER,
  extraction_mode TEXT NOT NULL,
  preview_path TEXT NOT NULL,
  preview_width INTEGER,
  preview_height INTEGER,
  source_width INTEGER,
  source_height INTEGER,
  rotation_degrees INTEGER,
  warnings_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  UNIQUE(project_id, source_id, source_fingerprint_sha256, requested_pts_ms, extraction_mode)
);

ALTER TABLE scene_versions ADD COLUMN source_id TEXT;
ALTER TABLE scene_versions ADD COLUMN source_fingerprint_sha256 TEXT;
ALTER TABLE scene_versions ADD COLUMN reference_frame_id TEXT;
ALTER TABLE scene_versions ADD COLUMN reference_frame_pts_ms INTEGER;
ALTER TABLE scene_versions ADD COLUMN display_width INTEGER;
ALTER TABLE scene_versions ADD COLUMN display_height INTEGER;
ALTER TABLE scene_versions ADD COLUMN schema_version TEXT NOT NULL DEFAULT 'mock-scene-v0';
ALTER TABLE scene_versions ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE scene_versions ADD COLUMN superseded_at TEXT;
ALTER TABLE scene_versions ADD COLUMN semantic_hash TEXT;
