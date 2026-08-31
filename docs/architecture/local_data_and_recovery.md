# Local Data and Recovery

The default local root is `.local-data/` (or `TVA_LOCAL_DATA_DIR`). It contains
managed media, previews, exports, diagnostics, logs, backups, runtime records
and the SQLite database. The path is local operational state, not a Git
artifact.

Calculation-affecting changes invalidate dependent results. Automatic event
rows remain immutable. Review actions, reviewed projections, certifications and
exports are append-only/revision-bound records. The selected processing run is
an explicit project pointer introduced by migration `017_pilot_release_runtime`;
runtime heartbeat state introduced by migration `018_pilot_runtime_heartbeats`;
it changes which completed run is shown, not the historical run contents.

Recovery order:

1. stop owned processes;
2. preserve the current database/logs for diagnosis;
3. restore a verified SQLite backup with `restore_app.bat` if needed;
4. reopen the project and inspect source fingerprint, scene revision, selected
   run, stale warnings, certification and export state;
5. reprocess only after the operator confirms the source/time/scene contract.

Backups do not include source media, model weights or external runtime
directories. A project can therefore reopen with a blocked capability or a
missing source; that limitation must remain visible.
