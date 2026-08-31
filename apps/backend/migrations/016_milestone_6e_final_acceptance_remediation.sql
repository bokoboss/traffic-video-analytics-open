-- Final Milestone 6E acceptance remediation.
--
-- Migration 015 may already have copied certification_hash into
-- certification_content_hash on an existing database. That value is an
-- attestation hash, not a deterministic frozen-content identity. This
-- migration adds explicit provenance status and repairs copied legacy values
-- while preserving certification_hash and the immutable certification table
-- contract.

ALTER TABLE certification_revisions
  ADD COLUMN certification_content_hash_status TEXT NOT NULL DEFAULT 'LEGACY_UNRESOLVED'
  CHECK (certification_content_hash_status IN ('VERIFIED_CONTENT_HASH', 'LEGACY_UNRESOLVED'));

-- certification_revisions are immutable at runtime. A schema migration may
-- normalize legacy provenance once, then immediately restore the append-only
-- trigger. Equality is the deterministic marker left by the old 015 copy;
-- unequal non-empty values are retained as known new-format content hashes.
DROP TRIGGER IF EXISTS certification_revisions_no_update;

UPDATE certification_revisions
SET certification_content_hash = CASE
      WHEN certification_content_hash <> ''
       AND certification_content_hash <> certification_hash THEN certification_content_hash
      ELSE ''
    END,
    certification_content_hash_status = CASE
      WHEN certification_content_hash <> ''
       AND certification_content_hash <> certification_hash THEN 'VERIFIED_CONTENT_HASH'
      ELSE 'LEGACY_UNRESOLVED'
    END;

CREATE TRIGGER certification_revisions_no_update
BEFORE UPDATE ON certification_revisions
BEGIN
  SELECT RAISE(ABORT, 'certification_revisions are immutable');
END;

CREATE INDEX idx_certifications_content_hash_status
  ON certification_revisions(certification_content_hash_status, certified_at, id);
