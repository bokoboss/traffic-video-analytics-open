# Review Operator Guide

This guide describes the Milestone 6E local review workflow. It does not make
an accuracy or TIMS-certification claim.

## 1. Open a result and choose scope

Open a completed engineering result from the project workspace. The review
session records the source fingerprint, result revision, scene revision,
taxonomy/mapping/policy revisions, runtime hash and the selected scope:

- `FULL_RESULT` for the complete result;
- `LINE` for one active scene counting line;
- `TIME_INTERVAL` for a half-open PTS interval;
- `DIAGNOSTIC_SUBSET` for explicitly listed event IDs.

Scope is immutable. If the scene, source or calculation inputs change, open a
new session instead of trying to repair a stale session.

## 2. Inspect the queue

The queue is ordered by numeric source-relative crossing PTS, then line and
reviewed-event ID. It is server-paginated: filters, allowlisted ordering,
`COUNT(*)`, `LIMIT` and `OFFSET` run against the persisted current projection;
the browser receives only the requested page. The projection revision is
visible in the audit strip. Use the status filter to focus on unreviewed,
corrected, rejected, duplicate or unscorable items. Select an event to see its
effective values, automatic/human origin, active action history and the
project-scoped media seek point.

The evidence panel deliberately does not show private local media paths.

## 3. Record a decision

Enter a concise reason grounded in the available evidence. Use:

- **Confirm event** when the event is supported;
- **Reject false positive** when it should not count;
- class or direction correction when the effective value is wrong;
- **Mark unscorable** when evidence is insufficient;
- **Add note** for context that should remain in the audit trail;
- **Add missed event** only when a human-observed event has line, canonical
  direction, PTS, class and evidence/observation note.

Actions are append-only. The original automatic row is not edited. Confirmation
does not cancel a rejection, unscorable mark or duplicate suppression. If a
prior decision was wrong, use **Reverse latest action** to append an explicit
reversal of that action; do not delete or rewrite the history. Reversal of a
reversal is also append-only and restores the targeted action's effect.

## 4. Resolve concurrency and stale states

If another reviewer changed the session, the API returns HTTP 409 with the
current revision and actions since the expected revision. Refresh, inspect the
new action history and retry with the current revision. A recovered conflict is
retained as an audit count but does not prevent completion.

If the session is stale, stop editing and reopen the current engineering result
as a new review session. Do not certify or export a stale scope.

## 5. Complete and certify

Completion requires every event in the explicit scope to be addressed and a
passed reconciliation. Certification additionally records reviewer identity,
rights disclosure, qualification disclosure and whether the scope is partial.
Certification means human certification of the reviewed scope, not proof of
detector accuracy.

## 6. Export

After certification, request CSV, XLSX or audit JSON. Confirm the manifest's
format, schema revision, SHA-256 and row/sheet counts. Download through the
controlled artifact link. Historical exports retain immutable
`artifact_generation_status = COMPLETED`, but the UI/API also discloses
`source_certification_status` and `effective_export_status`. A revoked source
is shown as `REVOKED_SOURCE`; a stale source is shown as `STALE_SOURCE`.
Historical files remain available when policy permits, but new exports require
a current certification. If upstream data changes or certification is revoked,
request a new review/certification revision rather than reusing the old
artifact.
