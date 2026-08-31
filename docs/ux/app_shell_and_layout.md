# App Shell and Layout

## Desktop grid

At 1440 px:

```text
┌──────────────────────────── Top bar 56 ────────────────────────────┐
│ Rail 208 │              Main workspace              │ Inspector 320│
│          │                                           │              │
│          │                                           │              │
├──────────┴──────────────── Timeline 72 ──────────────┴──────────────┤
└─────────────────────────────────────────────────────────────────────┘
```

The timeline appears only on video-related screens.

## Content margins

- Shell gap: 12–16 px
- Main page padding: 24 px
- Report page max readable width: 1440 px, without centring operational content into a narrow column
- Inspector padding: 16–20 px
- Section separation: 32–48 px

## Top bar

### Left

- compact product mark
- project title
- optional study type

### Centre or flexible area

- current workflow step
- stale/current state
- autosave state

### Right

- command palette
- diagnostics
- contextual primary action
- user identity only when multi-user exists

Avoid four centred navigation links and a right-side marketing CTA.

## Workflow rail

Default width: 208 px.

Collapsed width: 64 px.

The rail may collapse automatically below 1280 px but must expose labels via tooltip and accessible name.

## Inspector

Default width: 320 px.

Resize range: 288–400 px where technically practical.

At narrower viewport, it becomes a drawer.

## Workspace silhouette

The main workspace may use one 16 px outer radius.

Inside it:

- use dividers and tool rows;
- avoid nested card shells;
- keep the video edge crisp and deliberate.

## Scroll ownership

- App shell stays fixed.
- Workflow rail scrolls only if necessary.
- Main workspace owns primary page scroll.
- Inspector scrolls independently.
- Timeline remains fixed to the workspace.
- Tables use internal virtualization/scrolling.

Avoid nested scroll containers unless their boundaries are visually explicit.

## Layout adaptation

### 1280–1439 px

- rail 184–200 px;
- inspector 288–304 px;
- compact toolbar labels may become icons plus tooltips.

### 1024–1279 px

- rail collapses;
- inspector becomes overlay;
- timeline remains;
- evidence retains priority.

### Below 1024 px

- editing workflow is unsupported in V1;
- project summary may remain readable;
- show desktop recommendation for scene/review work.
