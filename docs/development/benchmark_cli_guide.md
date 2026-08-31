# Benchmark CLI guide

All commands use the repository Python environment and JSON manifests. They
do not download media or model weights.

```powershell
python scripts/benchmark_corpus.py validate tools/benchmark/fixtures/corpus_manifest_6c.json
python scripts/benchmark_corpus.py import tools/benchmark/fixtures/corpus_manifest_6c.json --database .local-data/benchmark.sqlite
python scripts/benchmark_ground_truth.py tools/benchmark/fixtures/ground_truth_6c.json --database .local-data/benchmark.sqlite --dry-run
```

For an offline evaluation from normalized event JSON:

```powershell
python scripts/run_benchmark.py `
  --corpus tools/benchmark/fixtures/corpus_manifest_6c.json `
  --ground-truth tools/benchmark/fixtures/ground_truth_6c.json `
  --predicted-events tools/benchmark/fixtures/predicted_events_6c.json `
  --source-id synthetic-calibration-01 `
  --output .local-data/benchmark-report.json `
  --markdown .local-data/benchmark-report.md
```

The command is intentionally fail-closed when `--evaluation-config` is
omitted: the automatic result is treated as not engineering-ready and the
report is `INCOMPLETE`/`NOT_SCORABLE`. Supply valid current-result metadata in
that file to generate scored metrics; a missing threshold policy can still
produce a non-qualifying calibration/holdout report.

Controlled configurations can be validated and compared with:

```powershell
python scripts/run_experiment_suite.py --configurations experiments.json --results results.json
python scripts/generate_benchmark_report.py .local-data/benchmark-report.json
```

The optional `--evaluation-config` JSON carries evaluation provenance and an
owner-approved threshold policy. A minimal shape is:

```json
{
  "engineering_ready": true,
  "reconciliation_status": "STRUCTURALLY_VALID",
  "taxonomy_revision": "engineering-taxonomy-v1",
  "mapping_revision": "engineering-mapping-v1",
  "classification_policy_revision": "classification-policy-v1",
  "code_commit_sha": "<commit>",
  "configuration_hash": "<configuration-hash>",
  "approved_policy": {
    "schema_version": "qualification-threshold-v1",
    "policy_revision": "owner-policy-1",
    "approved_by": "owner",
    "approved_at": "2026-08-05T00:00:00+00:00",
    "applicable_corpus_revision": "synthetic-6c-corpus-v1",
    "required_split": "HOLDOUT",
    "minimum_source_count": 1,
    "minimum_ground_truth_event_count": 1,
    "minimum_condition_coverage": ["daytime"],
    "thresholds": [
      {"metric_path": "event_metrics.recall", "operator": "GTE", "required_value": 0.95, "unit": "ratio", "required": true, "undefined_behavior": "FAIL_CLOSED"}
    ]
  }
}
```

Policy metric paths and operators are allowlisted and normalized before the
report is written. Null/invalid values and zero-denominator ratios fail closed
unless `undefined_behavior` explicitly says `ALLOW_UNDEFINED`. Reports include
threshold observations, availability, gate reasons, and blockers.

Use a path outside Git for private manifests/reports. The CLI may emit
`NOT_RUN`, `INCOMPLETE`, `CALIBRATION_CANDIDATE`, `NOT_QUALIFIED`, or
`HOLDOUT_INSUFFICIENT`; this is expected while rights, representative evidence,
structural integrity, or owner-approved thresholds are incomplete. Only an
explicit, passing holdout policy can emit `QUALIFIED_FOR_PILOT`; the CLI never
claims production approval.
