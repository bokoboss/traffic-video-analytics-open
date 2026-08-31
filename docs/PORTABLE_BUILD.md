# Portable build and runtime boundary

## What P1 publishes

The public source contains the application source, database migrations,
frontend, worker boundary, launcher scripts, runtime probes, dependency
manifests, model/provenance registry, synthetic fixtures, and validation tools.
The launcher resolves approved runtimes from operator-configured or
repository-local ignored directories and keeps operational state outside Git.

The source is therefore inspectable and buildable from a clean public checkout
without access to private history or private data.

## What P1 does not bundle

The first public source snapshot deliberately does not contain or download:

- Node.js or pnpm binaries;
- FFmpeg/ffprobe binaries;
- PyTorch, CUDA, or other AI runtime wheels;
- Ultralytics, RT-DETR, MMDetection, tracker, or other package binaries;
- detector/tracker weights; or
- survey video, customer media, datasets, databases, evidence, or exports.

These items have separate license, provenance, platform, and redistribution
requirements. `model_registry.json` records candidate source, versions, hashes
when known, dataset provenance, and weight policy; it is not a license grant.

## Local runtime preparation

1. Prepare an approved Python installation and create `.venv`.
2. Prepare an approved Node.js runtime in `TVA_NODE_DIR`, on `PATH`, or under
   the ignored `.local-tools/node` directory.
3. Prepare pnpm `11.9.0` through `TVA_PNPM_CMD`, `PATH`, or the ignored
   `.local-tools/pnpm/pnpm.cmd` path. The included
   `scripts/prepare_portable_pnpm.py` can verify and prepare that ignored
   runtime from the official npm registry after explicit operator consent.
4. Install FFmpeg/ffprobe separately when media operations are required. The
   included preparation script can verify an ignored local runtime, but does
   not place binaries in Git.
5. Install optional AI packages and weights only when their source, hash,
   license, and redistribution basis have been reviewed. Keep them in ignored
   local runtime/data directories.
6. Run `setup_app.bat` and the validation commands in `BUILDING.md`.

## Corresponding-source assessment

The P1 candidate includes all application and launcher source present at the
accepted Pilot baseline and the preparation/provenance code that is tracked at
that baseline. It is suitable as the public source for the source-built pilot
workflow.

There is no final single-file installer or complete bundled-runtime packager in
this baseline. A future portable distribution must add and review a clearly
separated packaging change, publish its source and notices, and document the
exact runtime and binary redistribution basis. P1 does not silently claim that
gap is closed.
