# Design Review Checklist

## Context

- [ ] `PRODUCT.md` was read
- [ ] `DESIGN.md` was read
- [ ] Screen purpose and primary action are explicit
- [ ] Operational macrostructure was chosen before decoration

## Tokens

- [ ] Semantic colour tokens used
- [ ] Spacing uses named scale
- [ ] Radius follows component role
- [ ] Typography follows display/UI/mono roles
- [ ] No arbitrary shadow or gradient
- [ ] Geometry uses approved colour and pattern

## Layout

- [ ] Works at 1440×900
- [ ] Works at 1280×720
- [ ] No document horizontal overflow
- [ ] Evidence receives appropriate priority
- [ ] Inspector and timeline preserve context
- [ ] Scroll ownership is clear
- [ ] Thai labels do not truncate critical meaning

## Components

- [ ] Hover, focus, active and disabled states
- [ ] Loading, empty, error and stale states
- [ ] Primary action is unique
- [ ] No unnecessary cards
- [ ] Tables use tabular numerals and stable columns
- [ ] Dialogs reserved for consequential decisions
- [ ] Success uses toast or quiet saved state

## Accessibility

- [ ] Contrast verified
- [ ] Focus visible
- [ ] Keyboard workflow complete
- [ ] Icon-only actions named
- [ ] Status does not rely on colour
- [ ] Reduced motion verified
- [ ] 125% and 150% scaling checked

## Motion and performance

- [ ] Motion explains state
- [ ] Duration under specified limits
- [ ] No count-up animation
- [ ] Skeleton matches eventual structure
- [ ] Large lists virtualized where necessary
- [ ] Evidence is prefetched conservatively
- [ ] Partial result is not presented as complete

## Product integrity

- [ ] Auto and effective values remain distinguishable
- [ ] Stale/current state visible
- [ ] Uncertainty is explicit
- [ ] Aggregate drills through to events
- [ ] Review/certification state is accurate
- [ ] No unsupported accuracy or AI claim

## Evidence

- [ ] Screenshots captured at target viewport
- [ ] Screenshots captured at minimum viewport
- [ ] Thai and English screenshots captured
- [ ] Long data and empty/error screenshots captured
- [ ] Intentional deviations documented
