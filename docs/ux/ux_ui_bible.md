# UX/UI Bible

## 1. Design vision

### Precision with Elegance

The product is a professional engineering instrument with the finish of premium product software.

“Premium” means:

- resolved hierarchy;
- disciplined spacing;
- refined typography;
- predictable interaction;
- scarce and intentional colour;
- excellent loading, empty and error states;
- no visual noise around evidence.

It does not mean:

- luxury motifs;
- gold decoration;
- dramatic gradients;
- excessive blur;
- ornamental animation;
- reduced information density.

### Intended emotional response

When opening the application, the user should feel:

- the system is calm;
- the current study is under control;
- evidence is close at hand;
- errors will be visible rather than hidden;
- the product respects professional judgement.

## 2. Design principles

### 2.1 Evidence is the hero

On setup and review screens, video evidence receives the largest and darkest working surface.

Configuration and metadata support the evidence; they do not compete with it.

### 2.2 Hierarchy before containers

Use size, spacing, alignment and typography before adding a card.

A screen with five cards is not automatically clearer than a screen with two sections and one working surface.

### 2.3 Operational asymmetry

The most important work area should occupy visibly more space.

Do not divide a screen into equal columns merely because a grid makes it easy.

### 2.4 Calm density

Long tables and review queues may be dense, but density must be organized:

- compact row rhythm;
- stable columns;
- visible grouping;
- sticky headers;
- keyboard operation;
- optional details.

### 2.5 Explicit uncertainty

Unknown, ambiguous and needs-review are legitimate states.

Do not hide uncertainty behind colour or convert it into a forced classification.

### 2.6 Motion explains

Use motion to show:

- panel relationship;
- selected-event continuity;
- successful direct manipulation;
- loading sequence;
- saved state.

Never use motion to make engineering values feel more exciting.

### 2.7 Professional Thai

Thai is the primary interface language.

Copy must be natural, direct and technically correct. English terms may appear where they are standard, but not as decoration.

### 2.8 Design is testable

Visual quality must be reviewed against:

- token use;
- component states;
- layout at target and minimum viewport;
- keyboard access;
- reduced motion;
- long Thai labels;
- large data sets;
- loading/error/stale states.

## 3. Product design register

The application combines three registers:

### Application shell

Light, quiet, warm-neutral and editorial.

### Video workspace

Dark, focused and tool-like.

### Analytical report

Light, typographically structured and table-led.

The registers must feel related through common type, spacing, control and status systems.

## 4. Information hierarchy

### Level 1 — current task

One clear page title and one clear primary action.

### Level 2 — status and consequence

Current project state, review state, stale state and export readiness.

### Level 3 — working data

Video, geometry, events, tables, movements and intervals.

### Level 4 — details

IDs, model versions, fingerprints and diagnostic data, normally available through inspectors or disclosure sections.

## 5. App shell

### Top bar

Purpose:

- establish project identity;
- show save/current-state status;
- provide global actions;
- avoid becoming a generic navigation bar.

Contains:

- compact product mark or wordmark;
- project name;
- project state;
- save indicator;
- global search/command button;
- optional diagnostics;
- contextual primary action.

### Workflow rail

Use a left vertical rail for the study sequence.

The rail is:

- ordered;
- stateful;
- compact;
- visually quiet;
- collapsible only if workspace width requires it.

Each step shows:

- number or icon;
- Thai label;
- state marker;
- optional issue count.

The rail is not a collection of equal navigation links. It expresses process progression.

### Inspector

The right inspector is contextual.

It should:

- show properties of the selected object;
- preserve the main workspace;
- support scrolling independently;
- avoid modal interruption for routine editing.

### Timeline

The bottom timeline is part of the video instrument.

It shows:

- source time;
- elapsed time;
- analysis range;
- event markers;
- current playhead;
- frame stepping;
- zoom.

## 6. Surface hierarchy

### Level 0 — application ground

Warm neutral background.

### Level 1 — base surface

Primary content and analytical sections.

### Level 2 — elevated surface

Inspector, command palette, menus, popovers and selected evidence.

### Level 3 — blocking surface

Dialogs for destructive actions or decisions that create a real fork.

Do not use elevation merely to separate every section.

## 7. Page posture

### Projects

Use an editorial ledger:

- strong project title column;
- secondary study metadata;
- restrained status lane;
- clear last activity;
- no large decorative hero.

### Source & Time

Use a source preview and time worksheet:

- video/source summary on the larger side;
- date/time and interval controls on the smaller side;
- four interval blocks as a visible consequence of the selected origin.

### Scene Setup

Use a studio posture:

- video canvas dominates;
- tools are compact;
- inspector is precise;
- timeline stays visible.

### Processing

Use a calm progress posture:

- one progress narrative;
- one determinate bar where possible;
- technical details behind disclosure;
- no wall of GPU gauges.

### Review

Use an evidence workstation:

- queue;
- evidence;
- details/actions.

### Results

Use a report posture:

- concise result header;
- table/matrix as primary evidence;
- charts secondary;
- drill-through available from every aggregate.

### Certification & Export

Use a sign-off posture:

- current version;
- unresolved items;
- certifier;
- included artifacts;
- provenance;
- one primary certification/export action.

## 8. Responsiveness

### Target

1440 × 900

### Minimum supported working viewport

1280 × 720

### 1024–1279 px

Supported as constrained desktop/tablet landscape:

- workflow rail collapses to icons;
- inspector becomes an overlay drawer;
- main evidence remains usable;
- tables may use horizontal internal scrolling;
- no document-level horizontal overflow.

### Below 1024 px

Not a V1 working target.

Show a clear “desktop workspace recommended” message rather than presenting a broken editor.

## 9. Accessibility

- WCAG AA contrast minimum for normal text
- visible `focus-visible`
- all geometry tools keyboard reachable where practical
- colour plus shape/pattern for overlay meaning
- captions and labels remain readable over video
- reduced-motion mode
- minimum 36 px control height; 40 px default
- no hover-only critical information
- screen reader names for icon-only controls
- table semantics preserved
- errors identified next to fields and in a summary when needed

## 10. Design governance

Before accepting a frontend milestone:

1. verify token use;
2. run the UI design-review skill;
3. inspect target and minimum viewport;
4. inspect Thai and English;
5. inspect keyboard operation;
6. inspect reduced motion;
7. inspect loading, empty, stale and error states;
8. inspect with large realistic event data;
9. record screenshots;
10. list intentional deviations from the bible.

No “temporary” visual divergence is acceptable without a tracked decision because temporary UI tends to become permanent.
