# Design System — Precision with Elegance

## Intent

Create a premium minimalist engineering workspace: quiet luxury, editorial hierarchy and precise operational behaviour.

The interface must look intentionally designed without sacrificing the speed, density and predictability required for reviewing long traffic videos and large event tables.

## Visual register

- Warm off-white application background
- White and near-white working surfaces
- Charcoal primary text
- Deep navy anchor hue
- Restrained red for counting geometry and critical emphasis
- Muted functional colours for review, certification and warnings
- Fine borders, limited layered shadows and disciplined radius
- Dark video canvas embedded inside a light application shell

## Typography

- Display and major Thai headings: `Anuphan`, fallback `IBM Plex Sans Thai`, `Noto Sans Thai`, sans-serif
- UI, body and tables: `IBM Plex Sans Thai`, fallback `Noto Sans Thai`, `Segoe UI`, sans-serif
- Technical timestamps and IDs only: `IBM Plex Mono`, `Cascadia Mono`, monospace
- Use tabular numerals for counts, timestamps and PHF
- Display styling is reserved for page titles and major analytical figures; operational labels stay compact

## Colour

Use one anchor hue. Accent usage should remain visually scarce.

Core tokens:

- Background: `#F6F4EF`
- Surface: `#FCFBF8`
- Elevated: `#FFFFFF`
- Text: `#182027`
- Secondary text: `#5D646B`
- Border: `#D9D6CF`
- Primary navy: `#17354D`
- Critical/count red: `#B03D36`
- Certified green: `#2E6652`
- Review amber: `#8A5D1E`
- Video ground: `#14191E`

Never use a decorative gradient as a main product surface.

## Shape

- Controls: 8 px radius
- Panels/cards: 12 px radius
- Large workspace containers: 16 px only where the silhouette benefits
- Status chips: pill radius
- Avoid rounding every nested surface
- Use lines and spacing before introducing another card

## Layout

Desktop-first.

- Target design viewport: 1440 × 900
- Supported working minimum: 1280 × 720
- No mobile workflow requirement in V1
- 56 px top bar
- 200–216 px workflow rail
- Flexible main workspace
- 304–336 px contextual inspector
- 68–80 px timeline/control strip when applicable

Operational pages are left-biased or workspace-biased, not centred.

## Motion

Motion explains state changes.

- Press feedback: 100–140 ms, scale to 0.98
- Hover/focus: 140–180 ms
- Panels and route changes: 200–260 ms
- Shared evidence transition: up to 320 ms
- Easing: `cubic-bezier(0.16, 1, 0.3, 1)`
- Stagger only small related groups; total under 300 ms
- Always support reduced motion

Do not animate authoritative counts upward from zero. Use a brief value-change highlight instead.

## Product surfaces

- Projects: editorial study ledger, not file-manager tiles
- Setup: video-first workspace with contextual inspector
- Processing: calm progress, not a monitoring dashboard
- Review: three-pane evidence workstation
- Results: analytical report workspace led by tables and direct evidence
- Certification: sign-off surface with provenance and current-state checks

## Perceived performance

Use:

- skeletons matching the eventual layout;
- virtualized long event lists;
- cached project/session state;
- prefetch of likely evidence frames;
- streaming or incremental results;
- toasts for confirmation;
- command palette for power users.

Do not use optimistic UI for certification, destructive actions or exports.

## Non-negotiable states

Every relevant component and screen must specify:

- default
- hover
- focus-visible
- active/pressed
- disabled
- loading
- empty
- warning
- error
- stale
- needs review
- certified/current

Status must never rely on colour alone.

## Source of truth

Detailed rules live under `docs/ux/`.

When code and this document disagree, stop and resolve the discrepancy rather than silently inventing a third system.
