# Contributing

Keep changes scoped to the public application and its documented contracts.
Changes that affect timestamps, counting semantics, event immutability,
review/certification, exports, or stale-result behavior should include a
focused test and an explanation of compatibility impact.

## Pull requests

Please describe:

- problem and scope;
- behavior, schema, or API changes;
- tests and validation commands;
- documentation and migration changes;
- license/provenance implications; and
- known risks and limitations.

UX changes should include a screenshot or a reproducible description of the
affected state. Keep generated OpenAPI and TypeScript contracts synchronized.

## Data and artifact policy

Never commit real survey video, customer data, private datasets, evidence
crops, exports, local databases, credentials, `.env` files, runtime binaries,
or detector/model weights. Synthetic or sanitized metadata-only fixtures are
welcome when their provenance and purpose are recorded.

Before opening a pull request, run:

```powershell
python scripts/validate_public_release.py
python -m pytest tests/backend
pnpm run build:frontend
pnpm run test:frontend
```
