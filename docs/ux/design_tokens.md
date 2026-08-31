# Design Tokens

## Token philosophy

Use semantic names in product code.

Good:

```text
color.text.primary
color.status.review
color.geometry.entry
```

Avoid:

```text
gray700
orange500
purpleThing
```

Semantic tokens permit future visual refinement without changing domain meaning.

## Colour roles

### Warm neutrals

The shell uses warm neutrals to reduce institutional coldness while preserving document-like clarity.

| Role | Value | Use |
|---|---:|---|
| Canvas | `#F6F4EF` | App ground |
| Surface | `#FCFBF8` | Primary sections |
| Elevated | `#FFFFFF` | Popovers, inspector and selected surfaces |
| Primary text | `#182027` | Headings and body |
| Secondary text | `#5D646B` | Metadata and helper text |
| Default border | `#D9D6CF` | Controls and section boundaries |

### Anchor and functional colours

| Role | Value | Use |
|---|---:|---|
| Primary navy | `#17354D` | Primary action and current step |
| Critical/count red | `#B03D36` | Critical state and core count emphasis |
| Certified green | `#2E6652` | Certified/current |
| Review amber | `#8A5D1E` | Needs review and caution |
| Information blue | `#2B607F` | Information and progress |

### Geometry palette

Geometry is drawn over varied video. Colours must be paired with line patterns and labels.

| Geometry | Colour | Pattern |
|---|---:|---|
| Count line | `#E35A50` | Solid undirected line with Side A / Side B labels |
| Directional event labels | semantic text/status colour | `A_TO_B` and `B_TO_A` shown in tables, inspectors and exports |
| Entry zone | `#D39A3C` | Dashed outline, `IN` label |
| Exit zone | `#55A87E` | Solid outline, `OUT` label |
| ROI | `#AAB9C5` | Long dash |
| Exclusion | `#B18AAF` | Diagonal hatch |
| Pedestrian | `#59B7A8` | Dot-dash |

## Typography

### Families

- Display: Anuphan
- UI/body: IBM Plex Sans Thai
- Technical: IBM Plex Mono

Fallbacks are defined in `DESIGN.md`.

### Scale

| Token | Size | Typical use |
|---|---:|---|
| Caption | 12 | Timestamps, badges, legends |
| Label | 13 | Field labels and compact controls |
| Body | 14 | Default UI and table |
| Body large | 16 | Explanatory copy |
| Small title | 18 | Panel title |
| Page title | 24 | Operational page title |
| Display | 36 | Projects greeting or major result only |

Do not use display type inside dense workspaces.

### Weight

- 400 — body
- 500 — label, selected row and table emphasis
- 600 — headings and primary buttons
- 700 — rare; major result only

## Spacing

Use the 4 px scale:

```text
4, 8, 12, 16, 20, 24, 32, 40, 48, 64
```

No arbitrary spacing without an explicit reason.

## Radius

- 4 px: tags and small utility shapes
- 8 px: controls
- 12 px: panels
- 16 px: major workspace silhouette
- pill: status chips only

Avoid nesting multiple rounded surfaces with the same radius.

## Shadow

Base content uses border and spacing.

Use the layered floating shadow only for:

- command palette;
- menus;
- popovers;
- inspector overlay;
- selected evidence raised above the queue.

## Focus

Every interactive element uses a visible focus ring.

Do not remove outlines without replacing them.

## Dark video surface

The application is not globally dark in V1.

The video workspace is dark to:

- frame the evidence;
- preserve overlay contrast;
- reduce glare;
- create clear visual separation from forms and reports.
