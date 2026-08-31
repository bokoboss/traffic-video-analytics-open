# Export Schema Guide

All Milestone 6E exports identify the certification revision, reviewed
projection revision, review revision and source/calculation provenance. Values
are derived from the certified projection, not from a mutable live queue.

Export revisions persist the format-specific schema revision, language,
options, creator/time, artifact manifest, content hash, row count and sheet
count. New provenance-aware exports use `production-export-csv-v2`,
`production-export-xlsx-v2`, and `audit-export-v2`; historical v1 artifacts
remain immutable.

## CSV

The CSV is a UTF-8 normalized event ledger. Its stable columns are:

`project_id`, `source_id`, `source_fingerprint`, `recording_time_status`,
`counting_line_id`, `counting_line_label`, `side_a_label`, `side_b_label`,
`direction`, `crossing_pts_ms`, `absolute_event_time`,
`interval_start_pts_ms`, `interval_end_pts_ms`, `interval_label`,
`partial_interval`, `engineering_class`, `classification_status`, `origin`,
`review_status`, `corrected`, `duplicate_of`, `source_event_id`,
`reviewed_event_id`, `certification_revision_id`,
`runtime_configuration_hash`, `actual_runtime_configuration_hash`, and
`runtime_provenance_status`.

## XLSX

The workbook contains these sheets in stable order:

1. `Summary`: project/source/review/certification summary and disclosures.
2. `15-min Counts`: effective active reviewed counts by line, direction, class
   and reporting interval, including partial-interval status.
3. `Event Ledger`: one row per reviewed event, with origin and effective
   values.
4. `Review Adjustments`: action ID, action order, action time, reviewer, action
   type, target, payload, reason, comment and reversal relationship. The sheet
   contains the complete action history through the certification revision
   cutoff; it is not limited by the public action API page size.
5. `QC and Reconciliation`: the equation, dimensions, status and diagnostics.
6. `Methodology`: time authority, direction semantics, overlay semantics,
   human-add disclosure and classification limitations.
7. `Provenance`: source fingerprint, result/configuration/scene/taxonomy
   revisions, review and certification identities, configured/actual runtime
   hashes and payload evidence, status, disclosures and hash.

Text cells are protected against spreadsheet formula interpretation, including
formula markers preceded by whitespace, tabs or newlines. The application does
not add formulas or hidden external links.

## Audit JSON

Audit JSON has `schema_revision: "audit-export-v2"` and includes the
certification payload, review scope, reviewed projection event ledger, the
`review_action_revision_cutoff`, complete ordered action references with
reversal relationships, reconciliation report and configured/actual runtime
provenance. It is intended
for audit or reproduction tooling; it is not a replacement for the immutable
SQLite source records.

## Determinism

For the same certified revision, format and language, content is generated with
canonical JSON ordering, stable row ordering and fixed OOXML zip timestamps.
Each artifact's SHA-256 is persisted in both the export manifest and artifact
row. Certification provenance includes the deterministic
`certification_content_hash`, its `certification_content_hash_status`, and the
attestation `certification_hash`. Legacy rows use `LEGACY_UNRESOLVED` rather
than copying the historical attestation hash into the deterministic field. The
artifact row and bytes are immutable. `artifact_generation_status`
describes the historical generation (`COMPLETED`, `FAILED` or `STALE`), while
the API derives `source_certification_status` (`CURRENT`, `STALE`, `REVOKED`)
and `effective_export_status` (`COMPLETED`, `STALE_SOURCE`,
`REVOKED_SOURCE`). A historical artifact is not silently relabeled as current
after certification revocation or source staleness.
