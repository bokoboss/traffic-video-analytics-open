# Product Context

## Product

Traffic Video Analytics is a Thai-first, local Windows application for auditable vehicle and pedestrian counting from video.

The product serves traffic engineers, survey reviewers and project managers. It must turn a video into defensible 15-minute, hourly and PHF results while preserving every machine event, review action, version and source reference needed to audit the result.

## Core workflow

1. Create or open a study.
2. Select a local video.
3. Confirm its real start date, time and timezone.
4. Select an analysis window.
5. Configure a road segment or intersection scene.
6. Run a short trial.
7. Process the video.
8. Review low-confidence or irregular events.
9. Inspect tables, movements, intervals and PHF.
10. Certify and export the current result.

## Counting-line contract

A counting line is an undirected side-transition boundary. One geometric line
defines Side A and Side B, may carry optional user-facing names for those sides,
and can produce separate `A_TO_B` and `B_TO_A` counts. Direction is derived from
stable side transition, not from an arrowhead, road compass direction, vehicle
heading, drawing gesture or frontend-only state.

The canonical signed-side convention is documented in ADR 0019. Scene save,
reload, endpoint editing, coordinate scaling, processing, review, aggregation
and export must preserve physical side meaning.

## Primary users

### Traffic engineer

Reads totals, movement matrices, classifications and PHF. Needs confidence that interval boundaries and counting semantics are correct.

### Survey reviewer

Works quickly through evidence. Needs keyboard-first review, stable video context and reversible actions.

### Project manager

Needs a clear view of project state, pending review, certification and export readiness without understanding model internals.

## Product character

- Calm
- Precise
- Refined
- Trustworthy
- Operational
- Evidence-led
- Thai-first
- Premium without decoration

## Product register

This is product software, not a marketing page.

## Frontend architecture direction

The target product is a local desktop-style web application launched by batch
scripts on Windows: Python API/backend, separate worker or processing runtime
where applicable, React/Vite frontend, and a browser-hosted localhost UI. `.exe`
packaging is not required for the target workflow unless a future decision adds
it.

Any Streamlit workflow in the repository is transitional or legacy. Streamlit
must not define canonical scene, counting, event, review, aggregation or export
semantics.

Use asymmetry, editorial hierarchy and refined typography to create character, but preserve the density, predictability and legibility required for long engineering sessions.

## Design promise

The interface should feel like a carefully made professional instrument:

- quiet rather than empty;
- premium rather than ornamental;
- fast in perceived response as well as actual response;
- technical without looking institutional or outdated;
- beautiful because hierarchy, spacing and interaction are resolved.

## Anti-references

Do not produce:

- purple or blue-pink gradient software;
- glass cards over every surface;
- a row of generic KPI cards as the default results layout;
- oversized rounded rectangles;
- three-column icon-tile feature grids;
- AI sparkle icons or fabricated intelligence language;
- centred layouts for operational screens;
- rainbow geometry without a semantic system;
- motion that obscures numerical state;
- an old enterprise form with dense borders and no visual hierarchy.

## Authoritative project documents

- `docs/inputs/product_requirements.md`
- `docs/inputs/product_constraints.md`
- `docs/inputs/accepted_development_plan.md`
- `DESIGN.md`
- `docs/ux/ux_ui_bible.md`
