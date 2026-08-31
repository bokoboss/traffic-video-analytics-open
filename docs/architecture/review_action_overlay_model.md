# Review Action Overlay Model

Milestone 6E review is an append-only overlay over immutable automatic and
engineering output. The original event remains the evidence anchor; a human
decision is a new `review_actions` row and is never an in-place update.

## Action identity and targets

Each action has a stable action ID, session ID, action order, reviewer identity,
reason/comment, source result revision, content hash and optional client request
ID. Automatic-event actions target `target_event_id`. A manual addition has no
automatic target and is represented as `target_type = REVIEW_EVENT` with its
payload in the append-only action row. Reversals target an earlier action ID.

The request model accepts only the explicit action vocabulary:

`CONFIRM_EVENT`, `REJECT_FALSE_POSITIVE`, `CHANGE_CLASS`,
`CHANGE_DIRECTION`, `CHANGE_LINE`, `ADJUST_TIMESTAMP`, `MARK_DUPLICATE`,
`ADD_MISSED_EVENT`, `MARK_UNSCORABLE`, `ADD_NOTE` and `REVERSE_ACTION`.

Material actions require a reason. Manual additions require line, canonical
direction, integer crossing PTS, supported engineering class and evidence or
an observation note. PTS is validated against the half-open analysis interval
`[analysis_start_pts_ms, analysis_end_pts_ms)`.

## Effective-value precedence

Projection is deterministic and does not mutate the action rows:

1. Resolve reversal chains. The newest direct reversal controls its target;
   reversing that reversal restores the prior action. Cycles fail closed and
   are rejected at persistence validation.
2. Apply active field corrections in append order. The latest active correction
   for class, direction, line or timestamp supplies the effective value.
3. Apply active decisions using deterministic precedence after reversal
   resolution: `STALE` > `UNSCORABLE` > `REJECTED_FALSE_POSITIVE` >
   `DUPLICATE_SUPPRESSED` > `CORRECTED` > `CONFIRMED` > `UNREVIEWED`.
   `CONFIRM_EVENT` is not an implicit reversal. A reject, unscorable or
   duplicate action remains effective even when a later confirmation exists;
   only an active `REVERSE_ACTION` targeting that specific action can remove
   its effect. Scalar corrections and notes remain independent overlays. The
   resulting review status is explicit and separate from classification status.
4. Preserve notes, evidence references, source event identity, runtime hash,
   scene revision and taxonomy/mapping/policy provenance.

The active action IDs are retained in the reviewed event for audit. A reversal
does not erase history; it changes which action is active in the next immutable
projection revision.

## Concurrency and idempotency

Every action carries `expected_review_revision`. A mismatch returns HTTP 409
with the current revision, the actions since the expected revision and retry
guidance. The mismatch is counted as an audit conflict but does not permanently
block a session after the reviewer refreshes and retries successfully.

`client_request_id` is scoped to the review session. Replaying the same request
returns the original action. Reusing the ID with a different payload returns a
typed idempotency conflict.

## Certification-bound audit history

The public `GET .../actions` endpoint is intentionally paginated and capped at
500 rows. A production export does not use that public cap. It reads the
append-only action table through an internal deterministic keyset-paginated
path, ordered by `action_order`, `created_at` and `id`, and freezes the result
at `action_order <= certification.review_revision`. All action rows remain in
the audit representation, including `REVERSE_ACTION` rows, their
`reverses_action_id` relationships and the source actions they reverse. A
later action makes the certification stale; it does not rewrite an already
generated artifact.

## Safety boundaries

Review actions do not change automatic event rows, engineering event rows,
benchmark rows or source media. Each derived projection and reconciliation run
is immutable. Human additions remain visible as `HUMAN_ADDED` and do not enter
the automatic-event ledger.
