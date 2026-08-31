from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from typing import Any

OBSERVABLE_CLASS_MAP = {
    "person": "pedestrian",
    "bicycle": "bicycle",
    "motorcycle": "motorcycle",
    "car": "passenger_vehicle",
    "bus": "bus",
    "truck": "truck",
}

NEVER_TIMS_VEHICLE_CLASSES = {"train", "boat", "traffic light", "stop sign"}


def observable_class(native_class: str) -> str:
    normalized = native_class.strip().lower()
    if normalized in NEVER_TIMS_VEHICLE_CLASSES:
        return "unknown"
    return OBSERVABLE_CLASS_MAP.get(normalized, "unknown")


def canonical_payload(payload: Any) -> str:
    def default(value: Any) -> Any:
        if is_dataclass(value):
            return asdict(value)
        raise TypeError(f"unsupported value for canonical JSON: {type(value)!r}")

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=default)


def artifact_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_payload(payload).encode("utf-8")).hexdigest()


def validate_normalized_track_artifact(payload: dict[str, Any]) -> tuple[str, ...]:
    errors: list[str] = []
    if payload.get("schema_version") != "normalized-track-artifact-v1":
        errors.append("unsupported_normalized_track_schema")
    tracks = payload.get("tracks")
    if not isinstance(tracks, list):
        return tuple(errors + ["tracks_must_be_list"])
    for track in tracks:
        if not isinstance(track, dict):
            errors.append("track_must_be_object")
            continue
        observations = track.get("observations")
        if not isinstance(observations, list) or not observations:
            errors.append("track_observations_missing")
            continue
        pts_values: list[int] = []
        for observation in observations:
            if not isinstance(observation, dict):
                errors.append("observation_must_be_object")
                continue
            pts_values.append(int(observation.get("pts_ms", -1)))
            bbox = observation.get("bbox_xywh")
            point = observation.get("representative_point")
            if not _bounded_tuple(bbox, 4):
                errors.append("observation_bbox_out_of_bounds")
            if not _bounded_tuple(point, 2):
                errors.append("observation_representative_point_out_of_bounds")
            if any(key.startswith("_") for key in observation):
                errors.append("framework_private_key_leaked")
        if pts_values != sorted(pts_values):
            errors.append("track_observations_not_ordered_by_pts")
    return tuple(dict.fromkeys(errors))


def _bounded_tuple(value: Any, length: int) -> bool:
    if not isinstance(value, list | tuple) or len(value) != length:
        return False
    try:
        return all(0 <= float(item) <= 1 for item in value)
    except (TypeError, ValueError):
        return False
