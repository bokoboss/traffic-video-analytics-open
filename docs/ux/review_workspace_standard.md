# Review Workspace Standard

## Objective

Make human review fast, focused and auditable.

The reviewer should be able to validate a long survey without repeatedly losing place in the video or reaching for the mouse.

## Three-pane layout

```text
Queue 300–360 │ Evidence flexible │ Details/actions 300–340
```

At constrained widths, the details pane becomes a drawer. Evidence keeps priority.

## Review queue

Each row shows:

- actual event time;
- elapsed time where useful;
- proposed class;
- movement;
- confidence band;
- QC reason;
- review state.

Avoid displaying every internal ID in the row.

### Queue behaviour

- virtualized;
- keyboard navigable;
- selected row pinned in view;
- filters preserve selection when possible;
- completed items may remain visible but subdued;
- “next unresolved” is always available.

## Evidence area

Primary:

- short clip or frame sequence;
- bounding region;
- track trail only if useful;
- count line or movement geometry;
- current event marker.

Secondary:

- best-frame thumbnails;
- previous/next nearby event;
- optional before/after machine classification comparison.

## Details

Show:

- auto class and confidence;
- effective class;
- auto movement;
- effective movement;
- QC reasons;
- model and scene version under disclosure;
- review history.

Do not overwrite or visually erase the original machine result.

## Actions

Primary keyboard actions:

- Approve
- Change class
- Change movement
- Exclude
- Add manual event
- Link tracks
- Undo

### Action safety

- Approve is reversible.
- Exclude requires a short reason when policy requires it.
- Link tracks previews the consequence before applying.
- Add manual event records origin and reviewer.
- Undo adds a reversal record rather than deleting history.

## Focus mode

Provide a focus mode that hides non-essential chrome while preserving:

- queue progress;
- current evidence;
- actions;
- source time;
- exit focus mode.

## Filters

- unresolved only
- class
- movement
- time interval
- QC type
- confidence band
- auto/manual
- reviewed by

Filters appear in a compact bar and active filters remain visible.

## Perceived performance

- prefetch evidence for adjacent unresolved events;
- retain recently viewed clips in memory;
- show skeletons shaped like the clip and detail panel;
- use shared-element transition when opening evidence from results;
- never block queue navigation on nonessential metadata.

## Review completion

The workspace reports:

- reviewed events;
- unresolved mandatory QC;
- corrected events;
- excluded events;
- manual events;
- suspected duplicate items.

“Review complete” is a policy result, not simply 100% of rows visited.
