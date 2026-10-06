# Third-Party Notices

This register is an engineering notice file for source, package, model-weight,
dataset, and external-runtime provenance. It is not legal advice.

## Project License

Traffic Video Analytics source code is licensed as AGPL-3.0-only as of
Milestone 5.2B. Network-accessible deployments, binary distributions, and
modified versions must provide corresponding source as required by AGPL-3.0.

## Core Application Dependencies

The core application dependency register remains in
`docs/development/dependency_license_register.md`.

## AI Pilot Candidates

AI packages and model weights are optional and are not installed by normal app
startup. Runtime files, model binaries, downloaded datasets, generated smoke
media, normalized artifacts, and benchmark outputs must remain outside Git.

| Component | Role | License status | Included in Git |
|---|---|---|---|
| Ultralytics YOLO | detector candidate | AGPL-3.0 by default or Enterprise license | No package or weight binaries |
| PyTorch / torchvision CUDA 12.8 wheels | optional inference runtime | BSD-style PyTorch license plus NVIDIA CUDA runtime components in wheel distribution | No package binaries |
| supervision | smoke adapter utility | MIT package | No package binaries |
| RT-DETR / RT-DETRv2 official implementation | detector fallback candidate | Apache-2.0 source; weight terms require per-weight provenance | No package or weight binaries |
| MMDetection RTMDet | detector fallback candidate | Apache-2.0 source/package; checkpoint provenance required | No package or weight binaries |
| Roboflow `trackers` ByteTrack/BoT-SORT | tracker candidate | Apache-2.0 package | No package binaries |
| FoundationVision ByteTrack | tracker reference | MIT source | No source vendored |
| FFmpeg gyan.dev essentials build | local media runtime | GPL-enabled build; accepted only as ignored local runtime | No binaries |

## Dataset and Weight Notices

COCO-pretrained detector weights can support coarse observable classes such as
person, bicycle, motorcycle, car, bus, and truck. They do not establish full
Thai TIMS vehicle classification and cannot be treated as authority-ready
traffic-survey evidence without later specialized training, validation, and
human certification.

Milestone 5.2B.1 records the YOLO11n weight SHA-256 in `model_registry.json`.
The weight file remains in ignored `.local-tools/models/` and is not included
in Git.

## Smoke Media Notice

Milestone 5.2B.1 local smoke media is generated outside Git from the public
Ultralytics example image at `https://ultralytics.com/images/bus.jpg`. The
fixture exists only to prove runtime wiring, FFmpeg frame extraction, YOLO11n
inference, ByteTrack tracking, and normalized artifact generation. It is not an
approved survey clip, dataset, benchmark, training input, or certified evidence.
