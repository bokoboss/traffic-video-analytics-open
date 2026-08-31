# Component Standards

## General rule

Components are functional contracts with visual states.

Every component must define:

- default
- hover
- focus-visible
- active
- disabled
- loading where applicable
- error where applicable
- compact and default density where applicable

## Button

### Variants

- Primary — one per local decision area
- Secondary — supporting action
- Ghost — toolbars and low-emphasis actions
- Destructive — irreversible action
- Icon — requires accessible name

### Sizes

- Compact: 36 px
- Default: 40 px
- Comfortable: 44 px

### Behaviour

- press scale: 0.98
- no upward scale on hover
- loading preserves width
- destructive actions are not visually identical to count-line red
- shortcut hint may appear after the label

## Field

Use visible top labels.

Do not use placeholder text as the only label.

Field structure:

```text
Label
Control
Helper or error
```

Use monospaced text only for:

- exact timestamps;
- frame/PTS values;
- hashes;
- IDs;
- paths.

## Select and combobox

- Use select for short static choices.
- Use searchable combobox for vehicle classes, movements or large lists.
- Display current value and source taxonomy version where material.
- Unknown and ambiguous are explicit options, not blank values.

## Checkbox and switch

- Checkbox: independent selection
- Switch: immediate binary state
- Avoid switches for destructive or expensive changes
- Explain when a switch makes results stale

## Status badge

Badges communicate state, not decoration.

Required badges include:

- Draft
- Ready
- Processing
- Needs review
- Review complete
- Certified
- Stale
- Incomplete
- Error

Use icon, text and colour.

## Banner

Use banners for persistent consequential state:

- stale result;
- unsupported source;
- camera moved;
- incomplete processing;
- certification blocked.

Banner structure:

```text
State title
Short consequence
Required action
Optional details
```

## Toast

Use for non-blocking confirmation:

- saved;
- review action applied;
- export created;
- copied.

Do not use a modal to announce success.

## Dialog

Use only for:

- destructive deletion;
- abandoning unresolved work;
- certification decision;
- replacing a source;
- actions with meaningful irreversible consequences.

## Drawer and inspector

Routine details belong in a side inspector.

The inspector:

- preserves current context;
- has its own title and action area;
- supports sticky primary action only when necessary;
- never hides the selected evidence without a compact-state alternative.

## Card

A card must represent a coherent object or elevated interaction.

Do not wrap every text section in a card.

Prefer:

- page section;
- divider;
- aligned columns;
- table group.

## Table

### Structure

- sticky header
- stable column widths
- numerical data right aligned
- tabular numerals
- row hover
- selected row
- keyboard selection
- optional density control
- internal horizontal scroll, never document overflow

### Large tables

Virtualize when row counts or rendering complexity justify it.

### Drill-through

Aggregate cells that open evidence must:

- appear interactive;
- preserve filter context;
- announce the resulting event count.

## Data matrix

Movement matrices use:

- sticky row/column headers;
- direct values;
- subtle cell emphasis;
- no rainbow heatmap;
- explicit total row and column;
- drill-through per cell.

## Tabs

Use tabs for peer views of the same object.

Do not use tabs to hide a required sequential workflow.

## Step rail

The workflow rail expresses progression.

States:

- not started
- available
- active
- complete
- complete with warning
- blocked

## Empty state

Empty states explain:

- what is missing;
- why it matters;
- one next action.

Avoid decorative illustrations that consume more space than the instruction.

## Skeleton

Skeletons match the final structure.

Do not show generic card skeletons when the final screen is a table, queue or video surface.

## Progress

Use determinate progress whenever possible.

Show:

- processing range;
- completed segment;
- source time;
- safe pause/stop.

Technical rate details sit behind disclosure.

## Command palette

Open with `Ctrl+K`.

Commands are grouped by:

- navigation;
- project;
- playback;
- review;
- export;
- diagnostics.

Dangerous commands require confirmation after selection.
