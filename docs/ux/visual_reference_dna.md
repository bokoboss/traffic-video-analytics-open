# Visual Reference DNA

## Purpose

This document extracts reusable design principles from the supplied references. It does not authorize pixel copying, brand imitation or direct reuse of protected assets.

Reference sites:

- Impeccable — https://impeccable.style/
- Hallmark — https://www.usehallmark.com/
- The Feel Lab — https://toys.geppettostudio.co/feellab/

## Impeccable: shared design vocabulary and context

Useful principles:

- Agents produce better interfaces when hierarchy, contrast, type, colour, layout, restraint and motion are named explicitly.
- Product context must be read before visual decisions are made.
- A portable `DESIGN.md` should preserve tokens, components and conventions across tools.
- New work should inherit the established system rather than overwrite it with model defaults.
- Brand pages and product interfaces operate in different registers.
- Anti-pattern detection should occur before the interface ships.

Application to this product:

- Keep `PRODUCT.md` and `DESIGN.md` at repository root.
- Require agents to read them before frontend work.
- Treat the design system as a contract, not a mood-board suggestion.
- Add deterministic design-review gates to milestone completion.
- Keep decorative brand techniques out of evidence-heavy operational views.

## Hallmark: structure before decoration

Useful principles:

- Choose the macrostructure before applying a theme.
- Study references by extracting structural DNA rather than copying pixels.
- Use one anchor hue, a named spacing scale and a visible type hierarchy.
- Bias layouts intentionally instead of centring every section.
- Resist generic AI patterns such as purple gradients, repetitive icon cards and default navigation templates.
- Restraint is a design decision.

Application to this product:

- Video, evidence and analytical tables determine each screen’s macrostructure.
- Use a consistent deep-navy anchor with restrained functional colour.
- Use asymmetry to clarify workspace priority.
- Avoid equal-weight dashboard grids where one element is operationally primary.
- Use typography and spacing before adding cards, icons or decoration.

## The Feel Lab: perceived performance and explanatory motion

Useful principles:

- Perceived responsiveness can be improved even when actual processing time is unchanged.
- Skeletons communicate incoming structure better than an isolated spinner.
- Virtual lists, caching, prefetching and incremental rendering make large products feel materially faster.
- Motion should explain where an object went or what changed.
- Shared-element transitions preserve visual context.
- Keyboard command palettes make a frequently used product feel like a tool rather than a website.
- Focus rings, button press feedback and toasts improve certainty without interrupting flow.

Application to this product:

- Use skeletons for project, event and evidence loading.
- Virtualize event lists and large tables.
- Prefetch evidence around the selected event.
- Preserve the selected event’s visual identity when moving from a result cell to evidence review.
- Provide `Ctrl+K` command access and review shortcuts.
- Use toasts for save confirmation; reserve dialogs for destructive or irreversible decisions.
- Keep motion brief and provide reduced-motion equivalents.

## Deliberate exclusions

The following techniques are not adopted as general styling:

- mesh gradients;
- broad glassmorphism;
- decorative grain over analytical surfaces;
- parallax;
- scroll-triggered marketing reveals;
- count-up animations for authoritative engineering figures;
- swipe-only or long-press-only actions on desktop.

They may be considered only where they serve a specific functional purpose and have an accessible alternative.
