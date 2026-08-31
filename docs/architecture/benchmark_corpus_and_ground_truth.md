# Benchmark corpus and ground-truth architecture

## Source manifest

`benchmark-corpus-v1` is an immutable corpus revision containing one or more
source records. Each source has:

- stable benchmark source ID and corpus revision;
- full SHA-256 fingerprint, duration and dimensions;
- optional nominal FPS as descriptive metadata only;
- recording/timezone status and scene/annotation revision identifiers;
- condition tags and `CALIBRATION`, `HOLDOUT`, or `DIAGNOSTIC_ONLY` split;
- `rights_status`, `rights_basis`, permission reference,
  `redistribution_status`, `storage_status`, and checksum verification.

Absolute local paths are reduced to an `external://filename` reference. Media
bytes are never accepted by the SQLite contract. `NOT_CLEARED` and `UNKNOWN`
are valid provenance states for inventory, but fail qualification rights gates.

## Ground truth revisions

Ground truth is append-only by revision. A revision is tied to one benchmark
source fingerprint and scene revision. Each event uses:

- an immutable event ID;
- a counting line ID and canonical `A_TO_B` or `B_TO_A` direction;
- `crossing_pts_ms` in the source-relative PTS domain;
- an engineering class and explicit classification status;
- timestamp and annotation statuses, including `UNCERTAIN`, `IGNORE`, and
  `UNSCORABLE`;
- bounded evidence references, optional identity ID, and optional frame/PTS
  references without embedded media.

Reviewer annotations identify reviewer, annotation revision, event decision,
class/direction/timestamp decisions, and notes. They are not edits to the
original annotation event. Adjudication is a revision-level status.

## Persistence boundary

Migration `011_milestone_6c_benchmark.sql` adds corpus/source/rights,
ground-truth/reviewer, evaluation configuration, run/match/metric,
fragmentation/throughput, experiment, and qualification report tables. The
tables are additive to 6B. Automatic 6A events and 6B engineering projections
remain immutable; benchmark runs reference their IDs and persist derived
evaluation artifacts separately.

Import validates the complete normalized manifest before opening its write
transaction. Reusing a revision with a different content hash fails. A new
annotation or policy revision is required for a calculation-affecting change.

## Privacy and licensing

Repository fixtures contain only synthetic metadata. Rights records are
provenance and eligibility evidence, not a license grant. External or private
media remains on the analyst’s local machine or approved storage and is
referenced by fingerprint/opaque reference only.
