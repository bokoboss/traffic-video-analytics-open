# Third-Party Notices

This register records engineering license and provenance information for the
0.1.0-pilot source and portable distribution. It is not legal advice.

## Project license

Traffic Video Analytics application source is licensed AGPL-3.0-only. The
complete license is in `LICENSE`. This public repository is the corresponding
application/build source for the 0.1.0-pilot distribution.

## Portable 0.1.0-pilot distribution

The portable ZIP contains third-party runtime components that retain their own
licenses. The package itself carries a detailed `THIRD_PARTY_LICENSES.json`
and copied license/notice files. The source tree also records exact runtime
identities in `packages/portable/windows-x64-cp312-runtime.lock.json`,
`packages/portable/source-snapshot.json`, and `model_registry.json`.

| Component | Release identity / role | Release treatment |
|---|---|---|
| Ultralytics | 8.4.103; YOLO11n detector with `yolo11n.pt` | AGPL route selected for the pilot; corresponding application/build source is public; model identity/hash is in `model_registry.json` |
| PyTorch / torchvision | 2.10.0+cu128 / 0.25.0+cu128 | Bundled in the locked Python runtime; PyTorch notices and NVIDIA runtime-component evidence are packaged |
| Roboflow trackers | 2.5.0.post0 | Bundled tracker runtime; Apache-2.0 package evidence is packaged |
| supervision | 0.29.1 | Bundled runtime dependency; MIT package evidence is packaged |
| BtbN FFmpeg | `ffmpeg-n8.1.2-50-g1a748fe2cd-win64-lgpl-8.1.zip`, tag `autobuild-2026-08-31-13-27` | Bundled FFmpeg/ffprobe; exact artifact SHA-256 `f6274bbd9c247f9e90c1bbed066b03ed4a3907cece2fb91be6dd352393936365`; exact corresponding-source/build bundle is a separate release asset |
| Microsoft VC runtime | 14.50.35719 app-local runtime files | Bundled; owner confirmed redistribution entitlement for this release |
| NVIDIA CUDA/cuDNN runtime components | Components provided through the locked PyTorch/TorchVision runtime | Bundled where required by the qualified GPU runtime; component inventory/notices are packaged; owner accepted applicable redistribution terms/notices |
| React / React DOM | 19.2.0 | Compiled frontend runtime; MIT license evidence is packaged |
| lucide-react | 0.468.0 | Compiled frontend runtime; ISC license evidence is packaged |
| scheduler | 0.27.0 | React DOM runtime dependency; MIT license evidence is packaged |

The locked portable runtime contains 61 Python distributions. The authoritative
per-distribution filenames, versions and hashes are the portable runtime lock;
the built artifact carries the complete resolved license inventory.

## Model and dataset boundary

The pilot's YOLO11n weight is COCO-pretrained. This does not establish complete
Thai TIMS classification or formal detector/counting accuracy. The release does
not redistribute COCO training data.

Fallback detector/tracker candidates documented in `model_registry.json` are
not represented as qualified production alternatives unless their exact
runtime and weight provenance is separately established.

## Source/runtime separation

Runtime binaries, model weights, databases, private media, generated evidence
and local build state are intentionally excluded from this public Git tree.
They may appear only in separately qualified release artifacts with their own
provenance and notices.
