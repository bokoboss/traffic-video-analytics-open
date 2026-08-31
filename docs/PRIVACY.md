# Privacy and release boundary

Traffic Video Analytics is local-first. Video files are selected from local
storage, copied into the managed local data root when the application is run,
and are not uploaded by default. The application keeps source fingerprints,
timebase metadata, scene revisions, event records, review actions and export
provenance separate so an operator can inspect what produced a result.

## Never publish

Keep the following outside Git and outside public fixtures:

- real survey or customer video and extracted frames/clips;
- customer names, private filenames, survey identifiers, or private locations;
- SQLite databases, exports, evidence, logs, support bundles, and caches;
- credentials, API keys, tokens, signed URLs, `.env` files, and private keys;
- virtual environments, `node_modules`, runtime binaries, and downloaded
  packages; and
- detector/tracker weights or other model/data binaries.

The repository `.gitignore` protects common local paths, but a contributor
must still inspect every new file before staging it.

## Public fixtures

Tests and benchmark fixtures are synthetic or metadata-only. They may exercise
time, geometry, counting, review, export, benchmark and runtime contracts, but
must not embed media bytes or private evidence. External media is represented
by an opaque identifier, fingerprint, or `external://...` reference.

## Human certification boundary

Automatic output is not a certified traffic survey. The pilot keeps automatic
events immutable and applies human corrections through append-only review
actions. Certification and export are bound to an explicit result/review
revision and scope. Rights, consent, retention, and redistribution authority
remain operator responsibilities.

## Validation

Run `python scripts/validate_public_release.py` before creating a public
commit. For the final candidate check, use
`python scripts/validate_public_release.py --tracked --json`; this bounded mode
enumerates exactly the paths tracked by Git and ignores untracked local
runtime/cache files. The validator checks for forbidden paths, email addresses,
secret-like content/files, local-state directories, private-data artifacts,
model binaries, unexpected large files, and paths outside the public allowlist.
