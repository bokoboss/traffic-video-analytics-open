# Dependency and License Register

Project source is AGPL-3.0-only as of Milestone 5.2B. Earlier milestone notes
that described the license as provisional are superseded by ADR 0016.

## Python

| Dependency | Version | Purpose | License status |
|---|---:|---|---|
| FastAPI | 0.116.1 | Versioned API framework | MIT |
| Uvicorn | 0.35.0 | Local ASGI server | BSD-3-Clause |
| Pydantic | 2.11.7 | API schema validation | MIT |
| HTTPX | 0.28.1 | FastAPI test client dependency | BSD-3-Clause |
| python-multipart | 0.0.20 | Multipart upload parsing for explicit local media selection | Apache-2.0 |
| tzdata | 2025.2 | IANA timezone database for reliable Windows time semantics | Apache-2.0 |
| pytest | 8.4.1 | Automated tests | MIT |
| Ruff | 0.12.4 | Python linting | MIT |

## Frontend

| Dependency | Version constraint | Purpose | License status |
|---|---:|---|---|
| React / React DOM | latest lockfile | UI runtime | MIT |
| Vite | latest lockfile | Frontend dev/build tool | MIT |
| TypeScript | latest lockfile | Static typing | Apache-2.0 |
| lucide-react | latest lockfile | Accessible UI icons | ISC |
| Vitest | latest lockfile | Frontend unit tests | MIT |
| Playwright | 1.54.1 | Browser validation | Apache-2.0 |
| Testing Library | latest lockfile | Component tests | MIT |

## External Tools

| Tool | Version | Purpose | License status |
|---|---:|---|---|
| FFmpeg `ffprobe` | Source checkout: local executable; portable 0.1.0-pilot: pinned BtbN LGPL build | Media metadata and stream timebase inspection | Portable distribution bundles the exact qualified BtbN LGPL artifact; corresponding-source/build bundle is distributed separately |
| FFmpeg `ffmpeg` | Source checkout: local executable; portable 0.1.0-pilot: pinned BtbN LGPL build | Reference-frame extraction and packaged media runtime | Portable distribution bundles the exact qualified BtbN LGPL artifact; corresponding-source/build bundle is distributed separately |

## Milestone 5 Benchmark Candidates

| Candidate | Version constraint | Purpose | License status |
|---|---:|---|---|
| Ultralytics YOLO family | not added | Detector benchmark candidate | AGPL-3.0 or Enterprise per official Ultralytics licensing; benchmark-only/requires legal review before production |
| FoundationVision ByteTrack | not added | Tracker benchmark candidate | MIT upstream; not yet a project dependency |
| Roboflow Trackers | not added | Tracker benchmark candidate | Apache-2.0 upstream; not yet a project dependency |
| NVIDIA CUDA Toolkit | external when installed | Optional GPU benchmark runtime | NVIDIA EULA; not bundled or installed by the app |
| Ultralytics | 8.4.103, optional only | Primary detector pilot candidate | AGPL-3.0 package/model default; not installed by normal app setup |
| trackers | 2.5.0.post0, optional only | ByteTrack and BoT-SORT pilot tracker candidates | Apache-2.0 package; not installed by normal app setup |
| torch | 2.10.0+cu128, optional only | PyTorch inference runtime for 5.2B.1 smoke qualification | BSD-style PyTorch license; CUDA wheel installed only in ignored `.venv-ai` |
| torchvision | 0.25.0+cu128, optional only | PyTorch vision runtime dependency for detector stack | BSD-style PyTorch license; installed only in ignored `.venv-ai` |
| supervision | 0.29.1, optional only | Detection container bridge for ByteTrack smoke tooling | MIT; installed only in ignored `.venv-ai` |
| numpy | 2.2.6, optional AI runtime pin | Tensor and detection array handling for AI smoke tooling | BSD-3-Clause; installed only in ignored `.venv-ai` |
| pillow | 12.3.0, optional AI runtime pin | Local smoke media frame preparation and image sizing | HPND; installed only in ignored `.venv-ai` |
| opencv-python | 4.12.0.88, optional AI runtime pin | Vision dependency required by Ultralytics and supervision | Apache-2.0 package; OpenCV is Apache-2.0, installed only in ignored `.venv-ai` |
| MMDetection `mmdet` | 3.3.0, optional only | RTMDet fallback evaluation candidate | Apache-2.0 package; not installed by normal app setup |

## Portable 0.1.0-pilot distribution

The portable release differs from a development source checkout: it bundles the locked 61-distribution Python runtime, Ultralytics 8.4.103 with the pinned YOLO11n weight, trackers 2.5.0.post0, PyTorch 2.10.0+cu128, torchvision 0.25.0+cu128, the pinned BtbN FFmpeg LGPL build, app-local MSVC runtime files, and the NVIDIA CUDA/cuDNN runtime components required by the qualified PyTorch stack. Exact package identities/hashes and copied license evidence are recorded by the portable runtime lock, model registry, builder/verifier and packaged third-party inventory.

The owner selected the Ultralytics AGPL/public-source route and confirmed the recorded MSVC and NVIDIA redistribution terms/entitlements for this pilot release. The FFmpeg corresponding-source/build bundle is distributed separately from the application ZIP.

## Excluded

RTSP, HLS and YouTube integrations are not included in 0.1.0-pilot. The NVIDIA CUDA Toolkit and display driver are not bundled. Fallback RT-DETR/RTMDet checkpoints are not distributed unless their exact weight provenance is separately established.
