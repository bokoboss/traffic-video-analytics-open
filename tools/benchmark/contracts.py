from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BENCHMARK_MANIFEST_VERSION = "benchmark-manifest-v1"
BENCHMARK_CONFIG_VERSION = "benchmark-config-v1"
NORMALIZED_TRACK_SCHEMA_VERSION = "normalized-tracks-v1"


@dataclass(frozen=True)
class BenchmarkManifest:
    benchmark_id: str
    clip_id: str
    media_fingerprint: str
    usage_status: str
    clip_start_ms: int
    clip_end_ms: int
    scene_revision: str
    ground_truth: dict[str, Any]
    notes: str = ""


@dataclass(frozen=True)
class BenchmarkConfig:
    detector: str
    tracker: str
    device: str
    confidence_threshold: float
    representative_point: str
    weight_sha256: str | None = None
    random_seed: int = 0


class BenchmarkValidationError(ValueError):
    pass


def canonical_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise BenchmarkValidationError("json root must be an object")
    return payload


def parse_manifest(payload: dict[str, Any]) -> BenchmarkManifest:
    if payload.get("manifest_version") != BENCHMARK_MANIFEST_VERSION:
        raise BenchmarkValidationError("unsupported benchmark manifest version")
    required = [
        "benchmark_id",
        "clip_id",
        "media_fingerprint",
        "usage_status",
        "clip_start_ms",
        "clip_end_ms",
        "scene_revision",
        "ground_truth",
    ]
    missing = [field for field in required if field not in payload]
    if missing:
        raise BenchmarkValidationError(f"missing manifest fields: {', '.join(missing)}")
    if payload["usage_status"] not in {"synthetic_fixture", "approved_internal", "restricted_private"}:
        raise BenchmarkValidationError("usage_status is not recognized")
    if int(payload["clip_end_ms"]) <= int(payload["clip_start_ms"]):
        raise BenchmarkValidationError("clip_end_ms must be after clip_start_ms")
    return BenchmarkManifest(
        benchmark_id=str(payload["benchmark_id"]),
        clip_id=str(payload["clip_id"]),
        media_fingerprint=str(payload["media_fingerprint"]),
        usage_status=str(payload["usage_status"]),
        clip_start_ms=int(payload["clip_start_ms"]),
        clip_end_ms=int(payload["clip_end_ms"]),
        scene_revision=str(payload["scene_revision"]),
        ground_truth=dict(payload["ground_truth"]),
        notes=str(payload.get("notes", "")),
    )


def parse_config(payload: dict[str, Any]) -> BenchmarkConfig:
    if payload.get("config_version") != BENCHMARK_CONFIG_VERSION:
        raise BenchmarkValidationError("unsupported benchmark config version")
    detector = str(payload.get("detector", ""))
    tracker = str(payload.get("tracker", ""))
    if not detector or not tracker:
        raise BenchmarkValidationError("detector and tracker are required")
    confidence = float(payload.get("confidence_threshold", 0.25))
    if not 0 <= confidence <= 1:
        raise BenchmarkValidationError("confidence_threshold must be in [0, 1]")
    representative_point = str(payload.get("representative_point", "bottom_center"))
    if representative_point not in {"bottom_center", "centroid"}:
        raise BenchmarkValidationError("representative_point is not supported")
    return BenchmarkConfig(
        detector=detector,
        tracker=tracker,
        device=str(payload.get("device", "cpu")),
        confidence_threshold=confidence,
        representative_point=representative_point,
        weight_sha256=payload.get("weight_sha256"),
        random_seed=int(payload.get("random_seed", 0)),
    )
