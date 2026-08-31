# Benchmark annotation guide

## Before annotating

1. Confirm the source fingerprint against the approved corpus manifest.
2. Confirm the scene revision, active counting line IDs, source-relative
   analysis window and PTS semantics.
3. Check the rights record. Do not copy private media, crops, frames or
   credentials into the repository.
4. Record conditions such as lighting, glare, occlusion, camera angle and
   traffic density using the controlled tags.

## Event decisions

Create one event ID per observed crossing. Use only canonical `A_TO_B` or
`B_TO_A`; retain `UNKNOWN` or `AMBIGUOUS` class when evidence is insufficient.
Use `VALID` only when the event and timestamp can be scored. Use `IGNORE` for a
known non-target or deliberate exclusion and `UNSCORABLE`/`UNCERTAIN` when the
scene evidence cannot support a reliable decision. Never move an event by
editing an earlier revision; create a new ground-truth revision.

Use the source PTS at the crossing. Frame indices and nominal FPS can be
supporting evidence, but they are not the authoritative timestamp.

## Review and adjudication

Each reviewer creates an annotation record with reviewer ID, annotation
revision, event decision, class/direction/timestamp decisions and bounded
notes. A revision becomes `ADJUDICATED` only after the project’s review policy
has resolved disagreements. Agreement summaries include reviewer count,
pairwise event/direction/class agreement, timestamp differences and kappa when
the sample supports it; degenerate/insufficient kappa is reported as such.

## Quality checks

The validator rejects duplicate event IDs/candidates, fingerprint or scene
mismatches, unknown lines when scene geometry is available, out-of-window PTS,
invalid directions/classes/statuses, unknown reviewer events, binary/media
payloads and oversized evidence. Store references, not media bytes.
