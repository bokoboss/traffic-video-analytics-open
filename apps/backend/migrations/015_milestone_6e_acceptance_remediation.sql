-- Milestone 6E acceptance remediation.  Migration 014 may already have been
-- applied in local development databases, so all contract additions here are
-- additive and preserve the immutable history created by 014.

ALTER TABLE certification_revisions
  ADD COLUMN certification_content_hash TEXT NOT NULL DEFAULT '';

-- 014 called this field certification_hash.  New rows populate the explicit
-- content-hash field. Legacy rows remain empty until migration 016 can assign
-- an explicit provenance status without presenting the historical attestation
-- hash as deterministic content identity.

CREATE INDEX idx_certifications_content_hash
  ON certification_revisions(certification_content_hash)
  WHERE certification_content_hash <> '';

ALTER TABLE reviewed_events
  ADD COLUMN event_confidence REAL;
ALTER TABLE reviewed_events
  ADD COLUMN benchmark_error_category TEXT;
ALTER TABLE reviewed_events
  ADD COLUMN has_correction INTEGER NOT NULL DEFAULT 0 CHECK (has_correction IN (0, 1));
ALTER TABLE reviewed_events
  ADD COLUMN is_duplicate INTEGER NOT NULL DEFAULT 0 CHECK (is_duplicate IN (0, 1));

UPDATE reviewed_events
SET has_correction = CASE
      WHEN corrected_fields_json IS NOT NULL
       AND corrected_fields_json NOT IN ('', '[]') THEN 1
      ELSE 0
    END,
    is_duplicate = CASE WHEN duplicate_of IS NOT NULL AND duplicate_of <> '' THEN 1 ELSE 0 END;

CREATE INDEX idx_reviewed_events_queue_filters
  ON reviewed_events(
    reviewed_projection_revision_id,
    review_status,
    effective_line_id,
    effective_direction,
    effective_class,
    origin,
    effective_crossing_pts_ms,
    id
  );
CREATE INDEX idx_reviewed_events_confidence
  ON reviewed_events(reviewed_projection_revision_id, event_confidence, id);
CREATE INDEX idx_reviewed_events_benchmark_category
  ON reviewed_events(reviewed_projection_revision_id, benchmark_error_category, id);

ALTER TABLE export_revisions
  ADD COLUMN reviewed_projection_revision_id TEXT REFERENCES reviewed_projection_revisions(id);
ALTER TABLE export_revisions
  ADD COLUMN export_schema_revision TEXT NOT NULL DEFAULT 'audit-export-v1';
ALTER TABLE export_revisions
  ADD COLUMN language TEXT NOT NULL DEFAULT 'th';
ALTER TABLE export_revisions
  ADD COLUMN options_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE export_revisions
  ADD COLUMN artifact_manifest_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE export_revisions
  ADD COLUMN content_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE export_revisions
  ADD COLUMN artifact_generation_status TEXT NOT NULL DEFAULT 'COMPLETED';
ALTER TABLE export_revisions
  ADD COLUMN artifact_sha256 TEXT;
ALTER TABLE export_revisions
  ADD COLUMN row_count INTEGER;
ALTER TABLE export_revisions
  ADD COLUMN sheet_count INTEGER;

UPDATE export_revisions
SET reviewed_projection_revision_id = COALESCE(
      reviewed_projection_revision_id,
      (SELECT reviewed_projection_revision_id
       FROM certification_revisions
       WHERE certification_revisions.id = export_revisions.certification_revision_id)
    ),
    export_schema_revision = CASE format
      WHEN 'CSV' THEN 'production-export-csv-v1'
      WHEN 'XLSX' THEN 'production-export-xlsx-v1'
      ELSE 'audit-export-v1'
    END,
    language = COALESCE(NULLIF(json_extract(manifest_json, '$.language'), ''), language),
    options_json = COALESCE(NULLIF(json_extract(manifest_json, '$.options'), ''), options_json),
    artifact_manifest_json = CASE
      WHEN artifact_manifest_json = '{}' THEN manifest_json
      ELSE artifact_manifest_json
    END,
    content_hash = COALESCE(NULLIF(json_extract(manifest_json, '$.content_hash'), ''), content_hash),
    artifact_sha256 = COALESCE(NULLIF(json_extract(manifest_json, '$.sha256'), ''), artifact_sha256),
    row_count = COALESCE(json_extract(manifest_json, '$.row_count'), row_count),
    sheet_count = COALESCE(json_extract(manifest_json, '$.sheet_count'), sheet_count),
    artifact_generation_status = CASE status
      WHEN 'COMPLETED' THEN 'COMPLETED'
      WHEN 'FAILED' THEN 'FAILED'
      WHEN 'STALE' THEN 'STALE'
      ELSE 'REQUESTED'
    END;

CREATE INDEX idx_reviewed_projection_reuse
  ON reviewed_projection_revisions(
    review_session_id,
    source_engineering_result_revision_id,
    review_revision,
    stale_status,
    created_at,
    id
  );

CREATE INDEX idx_reviewed_events_queue_order
  ON reviewed_events(
    reviewed_projection_revision_id,
    effective_crossing_pts_ms,
    effective_line_id,
    reviewed_event_id
  );

CREATE INDEX idx_reviewed_events_line_direction_class
  ON reviewed_events(
    reviewed_projection_revision_id,
    effective_line_id,
    effective_direction,
    effective_class,
    reviewed_event_id
  );

CREATE INDEX idx_export_revisions_projection
  ON export_revisions(reviewed_projection_revision_id, created_at, id);
