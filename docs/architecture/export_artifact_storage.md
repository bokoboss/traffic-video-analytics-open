# Export Artifact Storage

Milestone 6E stores generated artifacts under a managed local root selected by
`TVA_EXPORT_ROOT`, falling back to the ignored local-data export directory.
Database rows store only a sanitized relative path plus the artifact hash and
download metadata.

## File safety

- Absolute paths, drive-absolute paths, UNC paths, `..` components and
  symlinked intermediate directories are rejected.
- Lexical components are checked before resolution. A symlink component or
  target returns the typed symlink-specific rejection; only then is resolved
  root containment checked. This keeps Windows and POSIX behavior
  distinguishable and deterministic without accepting an escape.
- The resolved target must remain beneath the managed root.
- Writes use a same-directory temporary file, flush/fsync and atomic rename.
- Filenames contain only a safe project component, export ID and extension.
- Downloads revalidate the managed relative path and reject symlink targets.
- The API returns an artifact ID and safe filename, not a private absolute path.

## Formats

- CSV is UTF-8 with a stable header and event ledger rows.
- XLSX is a deterministic OOXML zip generated without a runtime spreadsheet
  dependency. Sheets are `Summary`, `15-min Counts`, `Event Ledger`, `Review
  Adjustments`, `QC and Reconciliation`, `Methodology` and `Provenance`.
- Audit JSON is UTF-8 and includes the certified projection, effective events,
  action references, reconciliation and provenance.

Imported or user-controlled values in spreadsheet output are emitted as text
cells and are prefixed when they begin with `=`, `+`, `-` or `@`, including
after leading spaces, tabs or newlines. This prevents formula interpretation
on open; Unicode and bounded operator comments remain text.
