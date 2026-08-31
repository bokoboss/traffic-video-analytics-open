# Reviewed Event Projection and Reconciliation

The reviewed projection is a persisted, immutable read model generated from a
review session's immutable engineering result scope plus its append-only action
history. It is a revisioned artifact, not a mutable status column on the
automatic event.

## Projection fields

Each reviewed event retains:

- reviewed event ID and origin (`AUTOMATIC` or `HUMAN_ADDED`);
- source automatic event ID or human-add action ID;
- effective line, canonical direction, crossing PTS and engineering class;
- classification status and review status, including `UNKNOWN`, `AMBIGUOUS`,
  `UNREVIEWED`, `UNSCORABLE` and `STALE` where applicable;
- duplicate target, corrected fields, active action IDs and evidence;
- runtime configuration, scene and taxonomy provenance.

Projection IDs include the projection revision, so event identity remains unique
across revisions. Historical projections remain queryable for certification and
audit reconstruction. The projection `content_hash` is deterministic within its
immutable review-session scope and includes the session identity, source result
revision, review revision, effective events and stale reason. This preserves the
existing database uniqueness contract without treating two different review
scopes as the same persisted projection row.

## Projection reuse and bounded reads

The current projection is keyed by `review_session_id`, `review_revision`,
`engineering_result_revision_id` and effective stale state. Repeated queue,
evidence and progress requests reuse that immutable projection. A new review
action creates exactly one new projection revision when the next request needs
the changed input; unchanged requests do not append another projection.

Normal queue reads select only the current projection's `reviewed_events` in
SQLite. Allowlisted sort expressions, normalized confidence/benchmark fields,
SQL filters, `COUNT(*)`, `LIMIT` and `OFFSET` are applied before rows enter the
application response. The reviewed-event ID is a deterministic final
tie-breaker. Evidence lookup selects the requested event and a bounded nearby
candidate set; it does not rebuild or materialize the full ledger.

## Reconciliation equation

For a scope, the report records:

```text
calculated_active_reviewed_events
  = automatic_events
  + active_human_added_events
  - rejected_false_positives
  - duplicate_suppressed_events
  - unscorable_excluded_events
```

The calculated value must equal the effective active reviewed-event count. The
report also verifies automatic source-event identity, duplicate targets,
canonical direction, supported class, half-open interval membership and the
by-line, by-direction, by-class and 15-minute interval dimensions.

Any diagnostic makes the reconciliation `FAILED` and blocks completion. An
empty scope is not silently treated as a complete full-result review; the
scope and counts remain explicit in the report.

## Staleness

Projection status records whether the source/result context was current when it
was built. The service rechecks source fingerprint, scene semantic revision,
taxonomy, mapping, classification policy, runtime/result status and newer
result revisions before accepting actions, completion, certification or export.
Stale records remain available for audit but cannot be presented as current.
