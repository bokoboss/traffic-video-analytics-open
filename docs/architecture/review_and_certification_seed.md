# Review and Certification Seed

## Event states

Suggested machine/review states:

- `auto`
- `needs_review`
- `approved`
- `corrected`
- `excluded`
- `manual`
- `superseded`

The final schema may separate machine state and review state.

## Review actions

- approve
- change class
- change movement
- exclude
- add manual event
- link tracks
- annotate
- undo/reverse prior action

Review actions are append-only.

## Certification

Certification records:

- who certified
- when
- scope
- unresolved QC policy
- source/run/result versions
- notes

Certification must fail when mandatory QC items remain unresolved.

## Export labels

An export must identify whether it contains:

- automatic/unreviewed results
- partially reviewed results
- reviewed results
- certified results

Do not present an automatic result as certified.
