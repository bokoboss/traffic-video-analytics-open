# Portable build and runtime

This public repository contains the corresponding application and build source from
`ec1632349e7b6a121b678b2ba9e36a2eab273f66` for the frozen Windows 11 x64 `0.1.0-pilot` artifact.

For a source checkout, follow BUILDING.md. Portable builds use Python 3.12.10,
the 61-distribution hash lock in packages/portable/windows-x64-cp312-runtime.lock.json,
the locked frontend graph in pnpm-lock.yaml, and the source-controlled builder.
Prepare an approved wheelhouse matching every lock filename/hash. No private
history, media or database is required.

```powershell
pnpm install --frozen-lockfile
pnpm run build:frontend
python scripts/build_portable_distribution.py --build-mode development --wheelhouse <approved-wheelhouse> --accept-downloads
```

Development output is explicitly non-qualifiable. A public checkout cannot use
the private commit as its own qualification HEAD: its public Git identity is
different. Future qualification must explicitly select that public HEAD and an
accepted ancestor with --expected-source-sha and --accepted-baseline-sha.
The qualified 0.1.0-pilot binary was built from the frozen private source identity above; this public history intentionally has a different Git identity.

The builder pins acquisition identities for CPython, BtbN FFmpeg, MSVC and
YOLO11n; the runtime lock pins Python wheels. It compiles frontend assets and
uses the portable launcher/static server. Normal startup does not download.
Use START_TRAFFIC_VIDEO_ANALYTICS.bat in an extracted portable package, and
STOP_TRAFFIC_VIDEO_ANALYTICS.bat to stop owned application processes.

Model weights, runtime binaries and ZIP artifacts are excluded from this tree.
For the 0.1.0-pilot distribution, the exact FFmpeg corresponding-source/build bundle is published as a separate release asset, and the owner has confirmed the Ultralytics AGPL/public-source route, MSVC redistribution entitlement, and applicable NVIDIA CUDA/cuDNN terms/notices. The packaged artifact retains detailed third-party license/provenance inventories. This document records engineering release evidence and is not legal advice.
