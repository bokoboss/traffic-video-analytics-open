# Interaction, Motion and Perceived Performance

## Motion principle

Motion explains what happened to what.

It is never an ornamental layer applied after layout.

## Durations

| Interaction | Duration |
|---|---:|
| Press response | 100–140 ms |
| Hover/focus | 140–180 ms |
| Small state transition | 160–200 ms |
| Inspector/panel | 220–260 ms |
| Shared evidence transition | ≤320 ms |
| Stagger group | ≤300 ms total |

## Easing

Default entrance/settle:

```css
cubic-bezier(0.16, 1, 0.3, 1)
```

Standard state:

```css
cubic-bezier(0.2, 0, 0, 1)
```

Use spring behaviour only for direct manipulation such as geometry handles or a drawer that physically follows the pointer.

## Reduced motion

With reduced motion:

- remove transforms and shared-element movement;
- retain immediate state changes;
- retain focus and selection cues;
- replace motion with opacity only when necessary;
- never remove progress information.

## Button feedback

On press:

- scale to 0.98;
- slightly deepen fill;
- return immediately on release.

Do not grow controls under the pointer.

## Shared context

Use shared-element or FLIP-style continuity for:

- project row → project overview;
- result cell → filtered review queue;
- event thumbnail → evidence viewer.

The effect must remain below 320 ms and must not delay input.

## Skeletons

Use when content takes long enough for an empty region to feel broken.

Skeleton shape must match:

- ledger row;
- table;
- review queue;
- evidence frame;
- inspector fields.

Do not shimmer continuously during multi-minute processing; use determinate progress instead.

## Optimistic UI

Allowed for reversible, low-risk actions:

- filters;
- local selection;
- approval with rollback;
- non-critical notes.

Not allowed for:

- certification;
- deleting a source;
- destructive event changes without history;
- export completion;
- model/config changes;
- processing completion.

## Prefetch

Prefetch or prepare:

- next/previous review evidence;
- project overview on row hover/focus;
- likely route modules during idle time;
- adjacent timeline thumbnails.

Never prefetch entire large videos.

## Caching

Retain:

- project summary;
- recent results;
- review filters;
- recently viewed evidence;
- taxonomy and scene metadata.

Clearly separate cached display from stale analytical results.

## Streaming and incremental rendering

Use incremental delivery for:

- processing progress;
- newly committed segments;
- event counts;
- aggregate preview;
- export progress.

Do not imply a partial aggregate is certified or complete.

## Virtualization

Virtualize:

- review queue;
- event ledger;
- long tables;
- thumbnail strips where necessary.

Keyboard navigation and screen-reader semantics must remain functional.

## Toast versus dialog

Toast:

- saved;
- copied;
- review action applied;
- export queued/created.

Dialog:

- delete source/project;
- abandon changes with consequence;
- certify;
- replace calculation-affecting input.

## Command palette

`Ctrl+K` opens commands globally.

It should feel instant, search locally first and expose shortcuts.

## Value changes

For changed counts:

- display the final value immediately;
- apply a 300–600 ms subtle background highlight;
- show delta under disclosure if useful.

Do not animate counts from zero.
