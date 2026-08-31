# Stale Result Contract Seed

## Calculation-affecting inputs

Changes to any of the following invalidate dependent results:

- source file or fingerprint
- confirmed start time/timezone
- analysis window
- interval origin
- scene geometry
- scene compatibility
- counting rules
- movement mapping
- taxonomy
- detector/classifier
- tracker and configuration
- thresholds
- processing/calculation logic version

## Required behavior

When invalidated:

- previous results remain viewable as historical
- current-state UI clearly shows stale status
- certification is blocked
- current export is blocked
- old exports remain in history with their manifest
- user is told what changed and what must be rerun

## Review-only changes

A review action invalidates aggregates and exports derived from the previous effective event projection but does not require video inference to rerun unless the underlying auto event generation inputs changed.
