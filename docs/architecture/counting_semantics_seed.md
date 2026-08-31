# Counting Semantics Seed

## Line crossing

A count occurs when the configured anchor point of a confirmed track transitions from one stable side of an undirected counting line to the other and satisfies hysteresis and minimum-history rules.

Required concepts:

- undirected finite counting line with ordered endpoints for signed-side calculation
- Side A and Side B
- side-of-line state
- track confirmation
- anchor point
- hysteresis band
- minimum displacement
- allowed object domains/classes
- crossing sequence
- event idempotency key

For an ordered line from `P1` to `P2`, the canonical domain convention is:

```text
cross(P2 - P1, Q - P1) > 0 => Side A
cross(P2 - P1, Q - P1) < 0 => Side B
inside tolerance => ON_LINE
```

Stable Side A then stable Side B emits `A_TO_B`. Stable Side B then stable Side
A emits `B_TO_A`. Temporary `ON_LINE` observations do not erase the last stable
side. The line is rendered without an arrowhead, and direction is not inferred
from vehicle heading, compass direction, drawing gesture or frontend-only state.

## Movement counting

An intersection movement is inferred from an ordered entry-zone and exit-zone sequence.

The user maps each valid pair to a movement label such as:

- NB-L
- NB-T
- NB-R
- NB-U
- Ped-North

Do not infer left/right solely from screen coordinates.

## Technical idempotency

Retrying the same processing segment must not insert the same technical event twice.

## Analytical duplicate risk

A vehicle may receive more than one track identity because of fragmentation, occlusion or re-entry. Database uniqueness alone cannot prevent this.

The system should support:

- suspected duplicate QC flags
- track-link review action
- duplicate-risk metrics
- evidence review

## Non-events

Examples:

- object enters ROI but never completes a valid crossing
- object touches or oscillates near a line without valid transition
- track is unconfirmed
- movement entry has no valid exit
- object class is excluded by rule
