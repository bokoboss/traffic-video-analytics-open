# Interaction and Product States

## State grammar

Every state communicates:

1. what happened;
2. what consequence it has;
3. what the user can do next.

## Project states

| State | Meaning | Primary action |
|---|---|---|
| Draft | Project exists but setup is incomplete | Continue setup |
| Source ready | Source and time confirmed | Configure scene |
| Scene configured | Geometry saved | Run trial |
| Trial validated | Trial accepted | Process |
| Processing | Worker active | Pause/stop safely |
| Processing complete | Auto results complete | Review |
| Needs review | Required QC unresolved | Review next |
| Review complete | Review policy satisfied | Certify |
| Certified | Exact result version signed off | Export |
| Exported | Current result exported | Open export |
| Stale | Calculation input changed | Reprocess |
| Incomplete | Run did not commit all required segments | Resume/retry |
| Camera moved | Scene compatibility failed | Realign/new version |
| Unsupported | Source cannot be used | Replace source |
| Error | Operation failed | Retry/view details |

## Visual encoding

- Neutral: draft and historical
- Navy/info: current, ready and processing
- Amber: review, caution and incomplete
- Green: reviewed/certified/current
- Red: destructive, critical error and stale consequence

Do not use green merely because processing finished; green implies validated/current.

## Loading

### Short

Inline progress indicator or pressed state.

### Structural

Skeleton matching final layout.

### Long processing

Determinate progress, current segment and safe controls.

## Empty

Examples:

- no projects;
- no source;
- no events for filter;
- no unresolved review;
- no exports.

An empty result after filtering is not an error.

## Stale

Stale must remain visible:

- on result header;
- on workflow rail;
- in export area;
- in project ledger.

The interface names the changed input and the earliest step that must be rerun.

## Error

Error copy avoids raw Python/JavaScript exceptions.

Provide:

- plain-language summary;
- safe next action;
- diagnostic reference;
- expandable technical detail.

## Disabled

A disabled control must have a discoverable reason.

Prefer an explanatory helper or tooltip to silently disabling the primary action.

## Save state

Use:

- Saving…
- Saved
- Save failed

Do not show repetitive “Saved” toasts during autosave. A quiet top-bar state is sufficient.
