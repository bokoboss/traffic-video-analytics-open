# Support Diagnostics

The support bundle is intentionally opt-in because diagnostics can contain
operational detail. Run:

```powershell
python scripts/create_support_bundle.py --acknowledge
```

or use the operator-facing diagnostic workflow when it is exposed by the
launcher/UI. Without `--acknowledge`, the command creates no file.

The ZIP contains release identity, OS/runtime versions, migration/readiness
summary, bounded recent log tails and an omission manifest. It excludes source
videos, preview/evidence media, exports, SQLite contents, model weights,
credentials, tokens, user-supplied absolute paths and private executable
locations. Review the ZIP before sharing it.

Set `TRAFFIC_APP_DIAGNOSTICS=1` only for a bounded troubleshooting session.
The backend then emits request IDs, timing and lifecycle events to the local
diagnostic log. Disable it for normal low-noise operation.
