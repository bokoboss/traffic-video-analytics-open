# Backup and Restore

Use `backup_app.bat` and `restore_app.bat` from the repository root.

## Backup

1. Run `stop_app.bat`.
2. Run `backup_app.bat`.
3. Keep the generated SQLite file and its JSON checksum/metadata sidecar in a
   protected operator location.

The backup uses SQLite's online backup API, runs an integrity check, records the
latest migration and SHA-256, and does not copy source videos or derived
artifacts.

## Restore

1. Stop the app and verify the backup was produced by this application.
2. Run `restore_app.bat <backup.sqlite3>`.
3. The tool validates the backup, creates a timestamped pre-restore safety copy,
   restores atomically, checks integrity and leaves application migration on
   the next backend start.
4. Start with `run_app.bat` and inspect the project/release/readiness state.

Restore refuses to run while owned runtime records exist. It does not infer or
restore media rights, model weights, or external files referenced by a project.
