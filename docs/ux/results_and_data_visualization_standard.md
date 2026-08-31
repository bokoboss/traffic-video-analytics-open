# Results and Data Visualization Standard

## Principle

Results are an analytical report workspace, not a generic dashboard.

Tables and matrices are primary. Charts explain patterns and intervals; they do not replace exact values.

## Result header

Show:

- analysis window;
- current/stale state;
- review/certification state;
- total volume;
- unresolved count;
- PHF when valid.

Avoid a row of equal KPI cards. Use one structured result header with clear hierarchy.

## Class × direction table

- vehicle classes in authoritative order;
- pedestrian in a separate section/domain;
- canonical directions `A_TO_B` and `B_TO_A` as columns, with readable side-name
  labels where configured;
- total row and total column;
- right-aligned tabular figures;
- click-through to events;
- corrected cells may expose review impact under disclosure.

## Entry × exit matrix

- approach labels remain sticky;
- movement names are user-defined;
- zero values remain readable but quiet;
- selected cells show source event count;
- totals use typographic weight, not a saturated fill.

## Fifteen-minute chart

Recommended default:

- grouped or stacked bars depending on task;
- direct interval labels;
- source-time labels;
- visible one-hour boundary;
- accessible tooltip with exact values;
- no gradient fill;
- no animation that delays the true value.

## PHF

Show:

- value;
- exact formula inputs;
- peak 15-minute interval;
- scope/filter;
- validity state.

If the hour is incomplete, do not show a normal PHF value. Explain why it is unavailable.

## Colour

Use semantic movement or class colour only when it improves comparison.

Do not assign a unique saturated colour to all 13 vehicle classes.

Recommended:

- one anchor colour for totals;
- selected comparison colours;
- muted remainder;
- pattern or direct label when colour becomes dense.

## Heatmaps

A restrained single-hue scale may be used for movement matrices.

Requirements:

- values remain printed;
- contrast remains readable;
- zero state is visually neutral;
- no rainbow or red-green scale;
- legend is explicit.

## Drill-through

Every aggregate should be traceable to effective events.

The transition must preserve:

- source filter;
- interval;
- class;
- movement;
- review state.

## Export preview

Preview the workbook/CSV structure and provenance before export.

Show:

- included sheets/files;
- result version;
- certification state;
- source and configuration version;
- unresolved warnings.
