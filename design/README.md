# Design Assets

## Files

- `tokens.json` — tool-neutral design-token seed
- `tokens.css` — CSS custom-property seed

These files define visual values, not a frontend architecture.

Codex may adapt them into the selected styling system, but must preserve semantic token names and document any changed values.

## Font note

The proposed font families must pass dependency and license review before being bundled or loaded. Do not commit font files to this repository unless their license and distribution method are documented.

## Contrast

Core text and action colours were selected to support WCAG AA contrast on their intended surfaces. Final implementation must verify actual computed contrast, including hover, disabled and dark video states.
