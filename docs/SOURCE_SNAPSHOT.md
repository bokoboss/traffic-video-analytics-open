# Public source snapshot

This repository is the P1 sanitized corresponding-source candidate for the
Traffic Video Analytics Pilot.

| Field | Value |
|---|---|
| Release identity | `0.1.0-pilot` |
| Accepted application source | `8229b50396366e62fb6b1c564a36318929d9f533` |
| Materialization | `git archive` of the accepted source commit |
| Public Git history | Fresh root history; private commits are not imported |
| Public media/data | None; fixtures are synthetic or metadata-only |
| Model/runtime binaries | None; prepared outside Git |
| Public license | `AGPL-3.0-only` |

Only application/build source, dependency and provenance metadata, public-safe
documentation, synthetic fixtures, tests, and license notices are selected.
Control-plane instructions, private development metadata, historical evidence
reports, local runtime state, private media, databases, exports, and model
weights are intentionally excluded.

The accepted source commit is a provenance reference for this snapshot. It is
not a parent of the public repository and does not expose the private
repository's history. To inspect the public history after the first local
commit, use `git rev-list --max-parents=0 HEAD` and confirm that it contains
exactly one root commit.
