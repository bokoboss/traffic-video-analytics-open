from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DATASET_MANIFEST_VERSION = "benchmark-dataset-manifest-v1"
GROUND_TRUTH_VERSION = "benchmark-ground-truth-v1"
ALLOWED_RIGHTS = {"synthetic_fixture", "owner_authorized", "public_license_verified", "restricted_internal"}
ALLOWED_REVIEW_STATUS = {"draft", "single_annotated", "reviewed", "adjudicated"}
ALLOWED_UNCERTAINTY = {"certain", "ambiguous", "unclassified", "human_review_required"}
ALLOWED_DIRECTIONS = {"a_to_b", "b_to_a"}


@dataclass(frozen=True)
class ValidationMessage:
    code: str
    field: str
    detail: str = ""


class DatasetValidationError(ValueError):
    pass


def load_json(path: Path, max_bytes: int = 2_000_000) -> dict[str, Any]:
    if path.stat().st_size > max_bytes:
        raise DatasetValidationError("json file exceeds maximum allowed size")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise DatasetValidationError("json root must be an object")
    return payload


def validate_manifest(payload: dict[str, Any]) -> tuple[ValidationMessage, ...]:
    errors: list[ValidationMessage] = []
    if payload.get("manifest_version") != DATASET_MANIFEST_VERSION:
        errors.append(ValidationMessage("unsupported_manifest_version", "manifest_version"))
    clips = payload.get("clips")
    if not isinstance(clips, list) or not clips:
        errors.append(ValidationMessage("missing_clips", "clips"))
        return tuple(errors)
    seen_clip_ids: set[str] = set()
    for index, clip in enumerate(clips):
        prefix = f"clips[{index}]"
        if not isinstance(clip, dict):
            errors.append(ValidationMessage("invalid_clip", prefix))
            continue
        clip_id = str(clip.get("clip_id", ""))
        if not clip_id:
            errors.append(ValidationMessage("missing_clip_id", f"{prefix}.clip_id"))
        if clip_id in seen_clip_ids:
            errors.append(ValidationMessage("duplicate_clip_id", f"{prefix}.clip_id"))
        seen_clip_ids.add(clip_id)
        rights = str(clip.get("rights_status", ""))
        if rights not in ALLOWED_RIGHTS:
            errors.append(ValidationMessage("invalid_rights_status", f"{prefix}.rights_status"))
        local_ref = str(clip.get("local_artifact_ref", ""))
        if Path(local_ref).is_absolute() or ".." in Path(local_ref).parts:
            errors.append(ValidationMessage("unsafe_local_artifact_ref", f"{prefix}.local_artifact_ref"))
        start_ms = _int_or_none(clip.get("clip_start_ms"))
        end_ms = _int_or_none(clip.get("clip_end_ms"))
        if start_ms is None or end_ms is None or end_ms <= start_ms:
            errors.append(ValidationMessage("invalid_clip_window", f"{prefix}.clip_end_ms"))
        if not isinstance(clip.get("counting_lines"), list) or not clip.get("counting_lines"):
            errors.append(ValidationMessage("missing_counting_lines", f"{prefix}.counting_lines"))
        if str(clip.get("annotation_status", "")) not in ALLOWED_REVIEW_STATUS:
            errors.append(ValidationMessage("invalid_annotation_status", f"{prefix}.annotation_status"))
    return tuple(errors)


def validate_ground_truth(payload: dict[str, Any], manifest: dict[str, Any]) -> tuple[ValidationMessage, ...]:
    errors: list[ValidationMessage] = []
    if payload.get("ground_truth_version") != GROUND_TRUTH_VERSION:
        errors.append(ValidationMessage("unsupported_ground_truth_version", "ground_truth_version"))
    clip_lookup = {
        str(clip.get("clip_id")): clip
        for clip in manifest.get("clips", [])
        if isinstance(clip, dict) and clip.get("clip_id")
    }
    events = payload.get("events")
    if not isinstance(events, list):
        errors.append(ValidationMessage("missing_events", "events"))
        return tuple(errors)
    seen_event_ids: set[str] = set()
    for index, event in enumerate(events):
        prefix = f"events[{index}]"
        if not isinstance(event, dict):
            errors.append(ValidationMessage("invalid_event", prefix))
            continue
        event_id = str(event.get("event_id", ""))
        if not event_id:
            errors.append(ValidationMessage("missing_event_id", f"{prefix}.event_id"))
        if event_id in seen_event_ids:
            errors.append(ValidationMessage("duplicate_event_id", f"{prefix}.event_id"))
        seen_event_ids.add(event_id)
        clip_id = str(event.get("clip_id", ""))
        clip = clip_lookup.get(clip_id)
        if clip is None:
            errors.append(ValidationMessage("unknown_clip_id", f"{prefix}.clip_id"))
            continue
        line_ids = {str(line.get("line_id")) for line in clip.get("counting_lines", []) if isinstance(line, dict)}
        if str(event.get("line_id", "")) not in line_ids:
            errors.append(ValidationMessage("unknown_line_id", f"{prefix}.line_id"))
        if str(event.get("direction", "")) not in ALLOWED_DIRECTIONS:
            errors.append(ValidationMessage("invalid_direction", f"{prefix}.direction"))
        timestamp = _int_or_none(event.get("crossing_timestamp_ms"))
        if timestamp is None or not int(clip["clip_start_ms"]) <= timestamp < int(clip["clip_end_ms"]):
            errors.append(ValidationMessage("timestamp_outside_clip", f"{prefix}.crossing_timestamp_ms"))
        tolerance = _int_or_none(event.get("timestamp_tolerance_ms"))
        if tolerance is None or tolerance < 0:
            errors.append(ValidationMessage("invalid_timestamp_tolerance", f"{prefix}.timestamp_tolerance_ms"))
        if str(event.get("uncertainty", "")) not in ALLOWED_UNCERTAINTY:
            errors.append(ValidationMessage("invalid_uncertainty", f"{prefix}.uncertainty"))
        if str(event.get("review_status", "")) not in ALLOWED_REVIEW_STATUS:
            errors.append(ValidationMessage("invalid_review_status", f"{prefix}.review_status"))
        if str(event.get("tims_class", "")) and event.get("uncertainty") != "certain":
            errors.append(ValidationMessage("unsupported_tims_assignment", f"{prefix}.tims_class"))
    return tuple(errors)


def validation_payload(errors: tuple[ValidationMessage, ...]) -> dict[str, Any]:
    return {
        "valid": not errors,
        "errors": [error.__dict__ for error in errors],
    }


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
