# Third-Party Notices

This register separates application-source licensing from package, model,
dataset and runtime provenance. It is an engineering notice, not legal advice.

## Application source

Traffic Video Analytics source code is licensed under `AGPL-3.0-only`. A
modified or network-accessible version must comply with the corresponding-source
and notice requirements of the AGPL. The complete license text is in
[`LICENSE`](LICENSE).

## Packages

Pinned package versions and the current license register are maintained in
[`docs/development/dependency_license_register.md`](docs/development/dependency_license_register.md),
`requirements-*.txt`, `package.json`, and `pnpm-lock.yaml`.

| Component | Role | Source/package license | Included in Git |
|---|---|---|---|
| FastAPI, Pydantic | API and schema | MIT | Application imports only |
| Uvicorn | Local ASGI server | BSD-3-Clause | Application imports only |
| React / React DOM | UI runtime | MIT | Application imports only |
| Vite / TypeScript | Frontend build | MIT / Apache-2.0 | Application imports only |
| Playwright | Browser validation | Apache-2.0 | Application imports only |
| `trackers` | Optional ByteTrack/BoT-SORT adapter | Apache-2.0 | Not vendored |
| `supervision` | Optional detection bridge | MIT | Not vendored |
| OpenMMLab MMDetection | Optional RTMDet candidate | Apache-2.0 | Not vendored |
| RT-DETR official implementation | Optional detector candidate | Apache-2.0 | Not vendored |

## Models and datasets

`model_registry.json` records candidate implementation source, versions,
weight identifiers/hashes when known, dataset provenance, class limitations,
and redistribution status. A model-weight license is recorded separately from
the application license. The registry does not grant permission to download
or redistribute a weight.

The optional candidates use COCO-pretrained or COCO-oriented metadata for
coarse observable classes. That does not establish complete Thai TIMS
classification or traffic-survey accuracy. No model weights, datasets, smoke
media, or benchmark media are committed here.

## External runtimes

FFmpeg/ffprobe are optional external executables. Their license depends on the
build configuration; the project does not bundle or silently download them.
PyTorch/CUDA wheels and NVIDIA components retain their own license and
redistribution terms and are prepared only in ignored local runtime
directories. Node.js and pnpm are also external runtimes; see
[`docs/PORTABLE_BUILD.md`](docs/PORTABLE_BUILD.md).

## Dataset and media boundary

Private or rights-controlled media remains outside Git and is referenced by
opaque IDs, hashes, or local paths only in local operational records. Public
fixtures in this repository are synthetic or metadata-only and contain no
survey video, customer data, database, export, or evidence image.
