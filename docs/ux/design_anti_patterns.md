# Design Anti-Patterns

## Visual slop

Reject:

- purple/pink/blue gradient hero;
- glowing AI orb;
- sparkle icon as a substitute for meaning;
- glassmorphism across the entire product;
- oversized 20–32 px radii on routine controls;
- equal card grids for unrelated metrics;
- icon tile plus heading plus three lines of text repeated across screens;
- random accent colours;
- side stripes on every card;
- every section floating above the background;
- generic stock illustration in operational empty states.

## Product slop

Reject:

- centred operational forms;
- model confidence shown as the main product outcome;
- raw detector/tracker settings in normal mode;
- totals without drill-through;
- success modals;
- stale data silently reused;
- disabled actions without a reason;
- destructive actions disguised as primary actions;
- `0` used where the value is unavailable;
- filters that reset review position without warning.

## Motion slop

Reject:

- count-up animation for authoritative values;
- bounce on routine controls;
- parallax inside the application;
- stagger longer than 300 ms;
- route animation that delays work;
- hover scaling that causes layout movement;
- looping skeleton shimmer during multi-minute processing;
- animation without reduced-motion behaviour.

## Data-visualization slop

Reject:

- 3D charts;
- rainbow heatmaps;
- red-green-only comparison;
- gradients under every line;
- pie charts for many vehicle classes;
- rounded decorative bars that impair reading;
- missing baseline where baseline matters;
- charts that hide exact values.

## Engineering UI slop

Reject:

- raw exceptions in user-facing messages;
- document-level horizontal overflow;
- tiny controls over video;
- geometry meaning communicated only by colour;
- invisible frame/time distinction;
- FPS-derived authoritative time;
- a “complete” green state while mandatory review remains.
