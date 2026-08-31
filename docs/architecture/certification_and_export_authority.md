# Certification and Export Authority

Certification is a signed-by-content, revisioned statement that one review
session and one reviewed projection passed the human-review and reconciliation
gates at a point in time.

## Certification gate

The service requires:

- a completed review session;
- a current source, scene, processing and engineering-result context;
- a current immutable reviewed projection at the same review revision;
- a passed reconciliation run;
- no unreviewed event in the explicit scope;
- reviewer identity, qualification disclosure and rights disclosure.

Non-full scopes are certified as partial scopes and the disclosure explicitly
retains that limitation. A certification does not prove detector accuracy,
complete TIMS coverage or unique-vehicle correctness across multiple lines.

Certification rows are immutable. Revocation is represented by an append-only
`certification_revocations` row, so the original certification and its reason
remain auditable. Derived status is `CERTIFIED`, `REVOKED` or `STALE` based on
that record and current upstream/projection state.

`certification_content_hash` is the deterministic hash of the frozen
certification content: reviewed projection content hash, review scope and
revision, reconciliation content hash, source fingerprint, runtime/config,
scene, taxonomy/mapping/policy revisions, benchmark and rights disclosures,
and the review summary. It intentionally excludes the random certification
revision ID. The legacy-compatible `certification_hash` is an attestation hash
over that content hash plus reviewer/time; it is not used as frozen-content
identity. `certification_content_hash_status` is `VERIFIED_CONTENT_HASH` for
new-format certifications and `LEGACY_UNRESOLVED` when an older attestation
cannot be reconstructed as deterministic content with certainty. The latter
is a provenance limitation, not a corruption claim.

## Export gate

Exports are authorized only from a current `CERTIFIED` revision and the exact
projection/reconciliation IDs recorded by that certification. A stale or
revoked certification returns a typed conflict and never creates a current
artifact.

The export revision stores the certification revision, reviewed projection
revision, format-specific schema revision, language/options, creator/time,
artifact manifest, content hash, row/sheet counts and safe filename. Its
immutable `artifact_generation_status` remains `COMPLETED`, `FAILED` or
`STALE` even if the source changes later. The API separately derives
`source_certification_status` (`CURRENT`, `STALE` or `REVOKED`) and
`effective_export_status` (`COMPLETED`, `STALE_SOURCE` or `REVOKED_SOURCE` for
completed artifacts). Historical artifacts remain downloadable when policy
allows, but are never silently presented as current. New exports are blocked
unless the source certification is current.

CSV, XLSX and audit JSON are deterministic for the same certified revision,
language and format. Export artifacts do not overwrite prior artifacts.

## Disclosure

The production export carries review scope, effective event status, automatic
versus human-added origin, active action references, QC reconciliation,
methodology, provenance, benchmark qualification disclosure and rights
disclosure. The public review-actions endpoint remains capped at 500 rows, but
exports use an internal keyset-paginated action reader and include every action
with `action_order <= certification.review_revision`, including reversal rows
and their source relationships. Actions appended later can stale the source
certification but cannot change a completed historical artifact. The export
also discloses the revision cutoff and content-hash status. It does not expose
private local filesystem paths or claim that a database idempotency key
prevents detector track fragmentation duplicates.
