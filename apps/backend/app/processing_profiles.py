from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping

from .engineering_outputs import RAW_MAPPING_BY_NAME


PROFILE_SCHEMA_REVISION = "processing-profile-v1"
PARAMETER_SCHEMA_REVISION = "processing-parameter-schema-v1"
CONFIGURATION_SCHEMA_REVISION = "processing-configuration-v1"
RUNTIME_CONFIGURATION_SCHEMA_REVISION = "runtime-configuration-v1"
CONFIGURATION_IDENTITY_SCHEMA_REVISION = "processing-configuration-identity-v1"
REQUEST_PROVENANCE_SCHEMA_REVISION = "processing-request-provenance-v1"
CROSSING_POLICY_REVISION = "canonical-crossing-policy-v2"
CLASSIFICATION_POLICY_REVISION = "track-vote-policy-v1"

# These are the adapter revisions that the current typed configuration can
# actually select. They mirror the authoritative entries in model_registry.json
# and are included in configuration identity before a worker is started.
STATIC_ADAPTER_PROVENANCE = {
    "detector_id": "detector.ultralytics-yolo11n-coco",
    "model_revision": "pypi:8.4.103",
    "weight_sha256": "0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1",
    "tracker_id": "tracker.trackers-bytetrack",
    "tracker_revision": "pypi:2.5.0.post0",
    "crossing_policy_revision": CROSSING_POLICY_REVISION,
    "classification_policy_revision": CLASSIFICATION_POLICY_REVISION,
}

PROFILE_CODES = (
    "BALANCED",
    "HIGH_ACCURACY",
    "FAST_PROCESSING",
    "SMALL_DISTANT_OBJECTS",
    "DENSE_TRAFFIC",
    "MOTORCYCLE_HEAVY",
    "PEDESTRIAN_COUNTING",
    "CUSTOM",
)

GUIDED_DEFAULTS = {
    "analysis_quality": "STANDARD",
    "processing_preference": "BALANCED",
    "detection_sensitivity": "BALANCED",
    "scene_type": "GENERAL_TRAFFIC",
    "occlusion": "MEDIUM",
    "object_size": "NORMAL",
}

GUIDED_OPTIONS = {
    "analysis_quality": ("STANDARD", "HIGH", "VERY_HIGH"),
    "processing_preference": ("SPEED", "BALANCED", "DETAIL"),
    "detection_sensitivity": ("CONSERVATIVE", "BALANCED", "SENSITIVE"),
    "scene_type": (
        "GENERAL_TRAFFIC",
        "DENSE_TRAFFIC",
        "MOTORCYCLE_HEAVY",
        "PEDESTRIAN",
        "MIXED_TRAFFIC",
        "SMALL_DISTANT_OBJECTS",
    ),
    "occlusion": ("LOW", "MEDIUM", "HIGH"),
    "object_size": ("NORMAL", "SMALL", "VERY_SMALL"),
}

SUPPORTED_RAW_CLASSES = tuple(sorted(RAW_MAPPING_BY_NAME))
IMAGE_SIZE_CHOICES = (320, 480, 640, 960, 1280, 1536, 1920, 2560, 4096)


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    value_type: str
    default: Any
    minimum: float | int | None = None
    maximum: float | int | None = None
    choices: tuple[Any, ...] = ()
    unit: str | None = None
    dependency: str | None = None

    def public(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "value_type": self.value_type,
            "default": self.default,
            "supported": True,
        }
        if self.minimum is not None:
            payload["minimum"] = self.minimum
        if self.maximum is not None:
            payload["maximum"] = self.maximum
        if self.choices:
            payload["choices"] = list(self.choices)
        if self.unit:
            payload["unit"] = self.unit
        if self.dependency:
            payload["dependency"] = self.dependency
        return payload


EXPERT_PARAMETER_SPECS: tuple[ParameterSpec, ...] = (
    ParameterSpec("device_mode", "enum", "AUTO", choices=("AUTO", "CPU", "CUDA")),
    ParameterSpec(
        "detector_id",
        "enum",
        "detector.ultralytics-yolo11n-coco",
        choices=("detector.ultralytics-yolo11n-coco",),
    ),
    ParameterSpec(
        "tracker_id",
        "enum",
        "tracker.trackers-bytetrack",
        choices=("tracker.trackers-bytetrack",),
    ),
    ParameterSpec("confidence_threshold", "float", 0.25, 0.0, 1.0),
    ParameterSpec("iou_threshold", "float", 0.70, 0.0, 1.0),
    ParameterSpec("image_size", "integer", 640, choices=IMAGE_SIZE_CHOICES, unit="pixels"),
    ParameterSpec("frame_stride", "integer", 1, 1, 8, unit="decoded_frames"),
    ParameterSpec(
        "class_allowlist",
        "string[]",
        list(SUPPORTED_RAW_CLASSES),
        choices=SUPPORTED_RAW_CLASSES,
        dependency="detector_raw_class_name",
    ),
    ParameterSpec("track_activation_threshold", "float", 0.25, 0.0, 1.0),
    ParameterSpec("lost_track_buffer", "integer", 30, 1, 300, unit="frames"),
    ParameterSpec("minimum_iou_threshold", "float", 0.10, 0.0, 1.0),
    ParameterSpec("minimum_consecutive_frames", "integer", 1, 1, 60, unit="frames"),
    ParameterSpec("minimum_track_duration_ms", "integer", 0, 0, 120_000, unit="milliseconds"),
    ParameterSpec("minimum_track_observations", "integer", 2, 1, 100, unit="observations"),
    ParameterSpec("crossing_anchor", "enum", "bottom_center", choices=("bottom_center",)),
    ParameterSpec("crossing_tolerance", "float", 1e-9, 0.0, 0.2, unit="normalized_distance"),
    ParameterSpec("crossing_hysteresis", "float", 0.002, 0.0, 0.2, unit="normalized_distance"),
    ParameterSpec("minimum_movement_distance", "float", 0.002, 0.0, 1.0, unit="normalized_distance"),
    ParameterSpec("minimum_side_stability_frames", "integer", 1, 1, 60, unit="frames"),
    ParameterSpec("duplicate_crossing_cooldown_ms", "integer", 250, 0, 10_000, unit="milliseconds"),
    ParameterSpec("classification_min_observations", "integer", 2, 1, 100, unit="observations"),
    ParameterSpec("classification_min_winning_vote_share", "float", 0.60, 0.0, 1.0),
    ParameterSpec("classification_min_weighted_share", "float", 0.60, 0.0, 1.0),
    ParameterSpec("classification_near_tie_margin", "float", 0.10, 0.0, 0.5),
)

EXPERT_PARAMETER_NAMES = frozenset(item.name for item in EXPERT_PARAMETER_SPECS)
PARAMETER_SPEC_BY_NAME = {item.name: item for item in EXPERT_PARAMETER_SPECS}


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def configuration_hash(payload: Mapping[str, Any]) -> str:
    return content_hash(dict(payload))[:16]


def _canonicalize(value: Any, *, key: str | None = None) -> Any:
    """Canonicalize JSON values without allowing type/order aliases in hashes."""
    if isinstance(value, Mapping):
        return {str(name): _canonicalize(item, key=str(name)) for name, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        items = [_canonicalize(item, key=key) for item in value]
        if key == "class_allowlist":
            return sorted({str(item).strip().lower() for item in items})
        return items
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value.strip() if isinstance(value, str) and key in {"detector_id", "tracker_id", "device_mode", "sampling"} else value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("runtime identity cannot contain a non-finite number")
        if value == 0:
            return 0
        if value.is_integer():
            return int(value)
        return float(str(value))
    return str(value)


def normalize_runtime_parameters(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """Return only normalized values consumed by the active worker adapters."""
    resolved = default_resolved_parameters()
    resolved.update({key: value for key, value in dict(parameters).items() if key in EXPERT_PARAMETER_NAMES or key == "sampling"})
    normalized = _canonicalize(resolved)
    assert isinstance(normalized, dict)
    return normalized


def adapter_provenance_for_parameters(parameters: Mapping[str, Any]) -> dict[str, str]:
    detector_id = str(parameters.get("detector_id") or STATIC_ADAPTER_PROVENANCE["detector_id"])
    tracker_id = str(parameters.get("tracker_id") or STATIC_ADAPTER_PROVENANCE["tracker_id"])
    if detector_id != STATIC_ADAPTER_PROVENANCE["detector_id"] or tracker_id != STATIC_ADAPTER_PROVENANCE["tracker_id"]:
        raise ValueError("unsupported adapter provenance")
    return dict(STATIC_ADAPTER_PROVENANCE)


def runtime_configuration_payload(
    *,
    parameter_schema_revision: str,
    resolved_parameters: Mapping[str, Any],
    model_revision: str | None,
    weight_sha256: str | None,
    tracker_revision: str | None,
    crossing_policy_revision: str,
    classification_policy_revision: str,
    resolved_device: str,
) -> dict[str, Any]:
    normalized_device = str(resolved_device).strip().lower()
    if normalized_device == "auto" or not normalized_device:
        raise ValueError("runtime identity requires a worker-resolved execution device")
    payload = {
        "schema_revision": RUNTIME_CONFIGURATION_SCHEMA_REVISION,
        "parameter_schema_revision": parameter_schema_revision,
        "resolved_parameters": normalize_runtime_parameters(resolved_parameters),
        "model_revision": model_revision or "UNRESOLVED_MODEL_REVISION",
        "weight_sha256": weight_sha256 or "UNRESOLVED_WEIGHT_SHA256",
        "tracker_revision": tracker_revision or "UNRESOLVED_TRACKER_REVISION",
        "crossing_policy_revision": crossing_policy_revision,
        "classification_policy_revision": classification_policy_revision,
        "resolved_device": normalized_device,
    }
    return _canonicalize(payload)


def runtime_configuration_hash(
    *,
    parameter_schema_revision: str,
    resolved_parameters: Mapping[str, Any],
    model_revision: str | None,
    weight_sha256: str | None,
    tracker_revision: str | None,
    crossing_policy_revision: str,
    classification_policy_revision: str,
    resolved_device: str,
) -> str:
    return content_hash(
        runtime_configuration_payload(
            parameter_schema_revision=parameter_schema_revision,
            resolved_parameters=resolved_parameters,
            model_revision=model_revision,
            weight_sha256=weight_sha256,
            tracker_revision=tracker_revision,
            crossing_policy_revision=crossing_policy_revision,
            classification_policy_revision=classification_policy_revision,
            resolved_device=resolved_device,
        )
    )


def configuration_identity_payload(
    *,
    parameter_schema_revision: str,
    resolved_parameters: Mapping[str, Any],
    model_revision: str | None,
    weight_sha256: str | None,
    tracker_revision: str | None,
    crossing_policy_revision: str,
    classification_policy_revision: str,
) -> dict[str, Any]:
    normalized_parameters = normalize_runtime_parameters(resolved_parameters)
    return _canonicalize(
        {
            "schema_revision": CONFIGURATION_IDENTITY_SCHEMA_REVISION,
            "parameter_schema_revision": parameter_schema_revision,
            "resolved_parameters": normalized_parameters,
            "model_revision": model_revision or "UNRESOLVED_MODEL_REVISION",
            "weight_sha256": weight_sha256 or "UNRESOLVED_WEIGHT_SHA256",
            "tracker_revision": tracker_revision or "UNRESOLVED_TRACKER_REVISION",
            "crossing_policy_revision": crossing_policy_revision,
            "classification_policy_revision": classification_policy_revision,
            "requested_device": str(normalized_parameters.get("device_mode", "AUTO")).upper(),
        }
    )


def configuration_identity_hash(**kwargs: Any) -> str:
    return content_hash(configuration_identity_payload(**kwargs))


def request_provenance_hash(
    *,
    profile_revision: str,
    guided_settings: Mapping[str, Any],
    requested_expert_overrides: Mapping[str, Any],
    operator_requested_device: str,
    normalizations: list[Mapping[str, Any]],
) -> str:
    return content_hash(
        _canonicalize(
            {
                "schema_revision": REQUEST_PROVENANCE_SCHEMA_REVISION,
                "profile_revision": profile_revision,
                "guided_settings": dict(guided_settings),
                "requested_expert_overrides": dict(requested_expert_overrides),
                "operator_requested_device": operator_requested_device,
                "normalizations": list(normalizations),
            }
        )
    )


def preview_request_fingerprint(
    *,
    project_id: str,
    source_id: str,
    source_fingerprint: str,
    scene_version_id: str,
    scene_semantic_hash: str,
    start_pts_ms: int,
    end_pts_ms: int,
    processing_configuration_revision_id: str,
    configuration_hash: str,
    mode: str,
    fixture_id: str | None,
) -> str:
    return content_hash(
        _canonicalize(
            {
                "project_id": project_id,
                "source_id": source_id,
                "source_fingerprint": source_fingerprint,
                "scene_version_id": scene_version_id,
                "scene_semantic_hash": scene_semantic_hash,
                "start_pts_ms": start_pts_ms,
                "end_pts_ms": end_pts_ms,
                "processing_configuration_revision_id": processing_configuration_revision_id,
                "configuration_hash": configuration_hash,
                "mode": mode,
                "fixture_id": fixture_id,
            }
        )
    )


def default_resolved_parameters() -> dict[str, Any]:
    return {
        "detector_id": "detector.ultralytics-yolo11n-coco",
        "tracker_id": "tracker.trackers-bytetrack",
        "device_mode": "AUTO",
        "confidence_threshold": 0.25,
        "iou_threshold": 0.70,
        "image_size": 640,
        "frame_stride": 1,
        "class_allowlist": list(SUPPORTED_RAW_CLASSES),
        "track_activation_threshold": 0.25,
        "lost_track_buffer": 30,
        "minimum_iou_threshold": 0.10,
        "minimum_consecutive_frames": 1,
        "minimum_track_duration_ms": 0,
        "minimum_track_observations": 2,
        "crossing_anchor": "bottom_center",
        "crossing_tolerance": 1e-9,
        "crossing_hysteresis": 0.002,
        "minimum_movement_distance": 0.002,
        "minimum_side_stability_frames": 1,
        "duplicate_crossing_cooldown_ms": 250,
        "classification_min_observations": 2,
        "classification_min_winning_vote_share": 0.60,
        "classification_min_weighted_share": 0.60,
        "classification_near_tie_margin": 0.10,
        "sampling": "every_decoded_frame",
    }


def _profile_parameters(**overrides: Any) -> dict[str, Any]:
    result = default_resolved_parameters()
    result.update(overrides)
    return result


def _profile(
    code: str,
    display_name_en: str,
    display_name_th: str,
    description_en: str,
    description_th: str,
    intended_use: str,
    hardware_expectation: str,
    known_tradeoffs: list[str],
    **parameters: Any,
) -> dict[str, Any]:
    resolved = _profile_parameters(**parameters)
    return {
        "profile_code": code,
        "profile_revision": f"{code.lower()}-r1",
        "display_name_en": display_name_en,
        "display_name_th": display_name_th,
        "description_en": description_en,
        "description_th": description_th,
        "intended_use": intended_use,
        "resolved_parameters": resolved,
        "supported_domains": ["vehicle", "pedestrian"],
        "hardware_expectation": hardware_expectation,
        "known_tradeoffs": known_tradeoffs,
        "status": "ACTIVE",
        "schema_revision": PROFILE_SCHEMA_REVISION,
    }


def builtin_profiles() -> tuple[dict[str, Any], ...]:
    return (
        _profile(
            "BALANCED",
            "Balanced",
            "สมดุล",
            "A practical starting point for mixed traffic scenes.",
            "จุดเริ่มต้นสำหรับฉากจราจรผสมทั่วไป",
            "general operational preview",
            "CPU or entry GPU",
            ["Balances image detail, throughput, and track continuity."],
        ),
        _profile(
            "HIGH_ACCURACY",
            "High accuracy investigation",
            "ตรวจสอบเชิงลึก",
            "Higher spatial detail and longer track retention for investigation.",
            "เพิ่มรายละเอียดภาพและการคงแทร็กสำหรับการตรวจสอบ",
            "challenging scenes and evidence exploration",
            "GPU recommended",
            ["Higher processing cost; still not an accuracy qualification."],
            image_size=1280,
            confidence_threshold=0.20,
            lost_track_buffer=60,
            minimum_track_observations=3,
            classification_min_observations=3,
        ),
        _profile(
            "FAST_PROCESSING",
            "Fast processing",
            "ประมวลผลเร็ว",
            "Lower compute pressure for quick bounded previews.",
            "ลดภาระการประมวลผลสำหรับพรีวิวช่วงสั้น",
            "quick operational triage",
            "CPU-compatible; GPU preferred",
            ["May miss short or small objects when frame stride is greater than one."],
            image_size=640,
            confidence_threshold=0.35,
            frame_stride=2,
            lost_track_buffer=20,
        ),
        _profile(
            "SMALL_DISTANT_OBJECTS",
            "Small and distant objects",
            "วัตถุขนาดเล็กและระยะไกล",
            "Higher input resolution for small objects in the scene.",
            "เพิ่มความละเอียดอินพุตสำหรับวัตถุขนาดเล็ก",
            "distant approaches and wide views",
            "GPU strongly recommended",
            ["Slower and more memory-intensive; validate on representative footage."],
            image_size=1536,
            confidence_threshold=0.20,
            minimum_iou_threshold=0.08,
        ),
        _profile(
            "DENSE_TRAFFIC",
            "Dense traffic",
            "การจราจรหนาแน่น",
            "Longer track retention for short occlusions in dense scenes.",
            "คงแทร็กได้นานขึ้นเมื่อมีการบังในฉากหนาแน่น",
            "occlusion-heavy vehicle scenes",
            "GPU recommended",
            ["Long buffers can increase identity fragmentation risk when the scene is crowded."],
            confidence_threshold=0.20,
            lost_track_buffer=90,
            track_activation_threshold=0.20,
            minimum_track_observations=3,
        ),
        _profile(
            "MOTORCYCLE_HEAVY",
            "Motorcycle-heavy",
            "ฉากรถจักรยานยนต์หนาแน่น",
            "Keeps motorcycle and bicycle raw evidence in the operational allowlist.",
            "คงหลักฐานดิบของรถจักรยานยนต์และจักรยานในรายการอนุญาต",
            "motorcycle-heavy approaches",
            "CPU or GPU",
            ["Raw detector labels remain provisional engineering mappings."],
            confidence_threshold=0.20,
            class_allowlist=["bicycle", "car", "motorcycle", "person"],
        ),
        _profile(
            "PEDESTRIAN_COUNTING",
            "Pedestrian counting",
            "นับคนเดินเท้า",
            "Restricts detector input to the supported person raw label.",
            "จำกัดอินพุตตัวตรวจจับไว้ที่ป้ายกำกับ person ที่รองรับ",
            "pedestrian-only crossing lines",
            "CPU or GPU",
            ["Pedestrians are a separate object domain, not a TIMS vehicle class."],
            confidence_threshold=0.20,
            class_allowlist=["person"],
        ),
        _profile(
            "CUSTOM",
            "Custom",
            "กำหนดเอง",
            "A typed starting point for supported expert overrides.",
            "จุดเริ่มต้นสำหรับค่าผู้เชี่ยวชาญที่รองรับ",
            "operator-defined supported configuration",
            "Depends on resolved settings",
            ["Every override is validated and retained in an immutable configuration revision."],
        ),
    )


def profile_by_code(profile_code: str) -> dict[str, Any]:
    normalized = str(profile_code).strip().upper()
    for profile in builtin_profiles():
        if profile["profile_code"] == normalized:
            return profile
    raise ValueError(f"unknown_processing_profile:{normalized}")


def resolve_guided_settings(
    guided_settings: Mapping[str, Any] | None,
    *,
    base_parameters: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    provided = {key: value for key, value in dict(guided_settings or {}).items() if value is not None}
    source = dict(GUIDED_DEFAULTS)
    source.update(provided)
    base = dict(base_parameters or default_resolved_parameters())
    errors: list[dict[str, Any]] = []
    unknown_guided = sorted(set(provided) - set(GUIDED_OPTIONS))
    errors.extend(
        {
            "code": "unsupported_guided_parameter",
            "field": field,
            "detail": "This guided field is not supported.",
        }
        for field in unknown_guided
    )
    source = {key: source.get(key, default) for key, default in GUIDED_DEFAULTS.items()}
    for key, allowed in GUIDED_OPTIONS.items():
        value = str(source.get(key, GUIDED_DEFAULTS[key])).upper()
        if value not in allowed:
            errors.append({"code": "unsupported_guided_value", "field": key, "value": value, "allowed": list(allowed)})
            value = GUIDED_DEFAULTS[key]
        source[key] = value
    resolved = dict(base)
    normalizations: list[dict[str, Any]] = []

    if "analysis_quality" in provided:
        quality_image = {"STANDARD": 640, "HIGH": 960, "VERY_HIGH": 1280}[source["analysis_quality"]]
        resolved["image_size"] = quality_image
    if "processing_preference" in provided:
        resolved["frame_stride"] = {"SPEED": 2, "BALANCED": 1, "DETAIL": 1}[source["processing_preference"]]
    if "detection_sensitivity" in provided:
        resolved["confidence_threshold"] = {"CONSERVATIVE": 0.40, "BALANCED": 0.25, "SENSITIVE": 0.15}[source["detection_sensitivity"]]
    if "occlusion" in provided:
        resolved["lost_track_buffer"] = {"LOW": 20, "MEDIUM": 30, "HIGH": 60}[source["occlusion"]]
    if "object_size" in provided:
        if source["object_size"] == "SMALL":
            resolved["image_size"] = max(960, int(resolved.get("image_size", 640)))
        elif source["object_size"] == "VERY_SMALL":
            resolved["image_size"] = max(1280, int(resolved.get("image_size", 640)))
    if "scene_type" in provided:
        if source["scene_type"] == "DENSE_TRAFFIC":
            resolved["lost_track_buffer"] = max(60, int(resolved["lost_track_buffer"]))
            resolved["minimum_track_observations"] = max(3, int(resolved["minimum_track_observations"]))
        elif source["scene_type"] == "MOTORCYCLE_HEAVY":
            resolved["class_allowlist"] = ["bicycle", "car", "motorcycle", "person"]
        elif source["scene_type"] == "PEDESTRIAN":
            resolved["class_allowlist"] = ["person"]
        elif source["scene_type"] == "SMALL_DISTANT_OBJECTS":
            resolved["image_size"] = max(1280, int(resolved.get("image_size", 640)))
        elif source["scene_type"] == "MIXED_TRAFFIC":
            resolved["class_allowlist"] = ["bicycle", "bus", "car", "motorcycle", "person", "truck"]
    for key in ("image_size", "frame_stride", "confidence_threshold", "lost_track_buffer", "class_allowlist"):
        if key in provided or (key == "image_size" and "object_size" in provided) or (key == "class_allowlist" and "scene_type" in provided):
            if resolved.get(key) == base.get(key):
                continue
            normalizations.append(
                {
                    "field": key,
                    "reason": "guided_setting_resolution",
                    "value": resolved.get(key),
                }
            )
    return source, resolved, normalizations, errors


def _error(code: str, field: str, detail: str, value: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "field": field, "detail": detail}
    if value is not None:
        payload["value"] = value
    return payload


def validate_parameters(
    parameters: Mapping[str, Any],
    *,
    media_duration_ms: int | None = None,
    analysis_start_pts_ms: int | None = None,
    analysis_end_pts_ms: int | None = None,
    cuda_available: bool | None = None,
    fail_on_cuda_unavailable: bool = False,
) -> dict[str, Any]:
    requested = dict(parameters)
    resolved = default_resolved_parameters()
    resolved.update(requested)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    normalizations: list[dict[str, Any]] = []
    unknown = sorted(set(requested) - (EXPERT_PARAMETER_NAMES | {"sampling"}))
    errors.extend(_error("unsupported_parameter", field, "This parameter is not supported by the current adapters.") for field in unknown)
    for field, spec in PARAMETER_SPEC_BY_NAME.items():
        if field not in resolved:
            continue
        value = resolved[field]
        if spec.value_type == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append(_error("invalid_parameter_type", field, "Expected a number.", value))
                continue
            numeric = float(value)
            if spec.minimum is not None and numeric < float(spec.minimum) or spec.maximum is not None and numeric > float(spec.maximum):
                errors.append(_error("parameter_out_of_range", field, f"Expected {spec.minimum}..{spec.maximum}.", value))
            resolved[field] = numeric
        elif spec.value_type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                errors.append(_error("invalid_parameter_type", field, "Expected an integer.", value))
                continue
            if spec.choices and value not in spec.choices:
                errors.append(_error("unsupported_parameter_value", field, f"Expected one of {list(spec.choices)}.", value))
            if spec.minimum is not None and value < spec.minimum or spec.maximum is not None and value > spec.maximum:
                errors.append(_error("parameter_out_of_range", field, f"Expected {spec.minimum}..{spec.maximum}.", value))
        elif spec.value_type == "enum":
            if not isinstance(value, str) or value not in spec.choices:
                errors.append(_error("unsupported_parameter_value", field, f"Expected one of {list(spec.choices)}.", value))
        elif spec.value_type == "string[]":
            if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
                errors.append(_error("invalid_parameter_type", field, "Expected a non-empty list of strings.", value))
                continue
            normalized = sorted({item.strip().lower() for item in value})
            unsupported = sorted(set(normalized) - set(SUPPORTED_RAW_CLASSES))
            if unsupported:
                errors.append(_error("unsupported_class_allowlist_value", field, "Raw class is not supported.", unsupported))
            if normalized != value:
                normalizations.append({"field": field, "reason": "sorted_and_deduplicated", "from": value, "to": normalized})
            resolved[field] = normalized
    if resolved.get("sampling") != "every_decoded_frame":
        errors.append(_error("unsupported_sampling", "sampling", "Only every_decoded_frame is supported.", resolved.get("sampling")))
    winning_share = resolved.get("classification_min_winning_vote_share")
    weighted_share = resolved.get("classification_min_weighted_share")
    near_tie_margin = resolved.get("classification_near_tie_margin")
    if isinstance(winning_share, (int, float)) and not isinstance(winning_share, bool) and float(winning_share) < 0.5:
        errors.append(_error("classification_threshold_too_low", "classification_min_winning_vote_share", "Use at least 0.5."))
    if isinstance(weighted_share, (int, float)) and not isinstance(weighted_share, bool) and float(weighted_share) < 0.5:
        errors.append(_error("classification_threshold_too_low", "classification_min_weighted_share", "Use at least 0.5."))
    if isinstance(near_tie_margin, (int, float)) and not isinstance(near_tie_margin, bool) and float(near_tie_margin) > 0.5:
        errors.append(_error("classification_near_tie_margin_too_high", "classification_near_tie_margin", "Use at most 0.5."))
    hysteresis = resolved.get("crossing_hysteresis")
    tolerance = resolved.get("crossing_tolerance")
    if (
        isinstance(hysteresis, (int, float))
        and not isinstance(hysteresis, bool)
        and isinstance(tolerance, (int, float))
        and not isinstance(tolerance, bool)
        and float(hysteresis) < float(tolerance)
    ):
        warnings.append(
            {
                "code": "hysteresis_below_crossing_tolerance",
                "field": "crossing_hysteresis",
                "detail": "Hysteresis smaller than tolerance may produce more ambiguous side transitions.",
            }
        )
    if analysis_start_pts_ms is not None and analysis_end_pts_ms is not None:
        if analysis_start_pts_ms < 0 or analysis_end_pts_ms <= analysis_start_pts_ms:
            errors.append(_error("invalid_analysis_window", "analysis_window", "Use a positive half-open [start, end) interval."))
        if media_duration_ms is not None and analysis_end_pts_ms > media_duration_ms:
            errors.append(_error("analysis_window_exceeds_media", "analysis_end_pts_ms", "The analysis end exceeds media duration."))
        window_ms = analysis_end_pts_ms - analysis_start_pts_ms
        minimum_duration = resolved.get("minimum_track_duration_ms", 0)
        if isinstance(minimum_duration, int) and not isinstance(minimum_duration, bool) and minimum_duration > window_ms:
            warnings.append(
                {
                    "code": "minimum_track_duration_exceeds_window",
                    "field": "minimum_track_duration_ms",
                    "detail": "No track can satisfy this duration inside the selected analysis window.",
                }
            )
    device = str(resolved.get("device_mode", "AUTO"))
    resolved_device = "AUTO"
    if cuda_available is not None:
        resolved_device = "cuda:0" if device == "CUDA" or device == "AUTO" and cuda_available else "cpu"
        if device == "CUDA" and not cuda_available:
            issue = _error("cuda_unavailable", "device_mode", "CUDA was requested but no CUDA device is available.")
            (errors if fail_on_cuda_unavailable else warnings).append(issue)
        elif device == "AUTO" and not cuda_available:
            warnings.append(
                {
                    "code": "auto_device_fallback_cpu",
                    "field": "device_mode",
                    "detail": "AUTO resolved to CPU because CUDA is unavailable.",
                }
            )
    estimated = estimate_resource_impact(resolved, resolved_device)
    payload_for_hash = {"schema_revision": PARAMETER_SCHEMA_REVISION, "resolved_parameters": resolved}
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "requested_parameters": requested,
        "resolved_parameters": resolved,
        "normalizations": normalizations,
        "estimated_resource_impact": estimated,
        "configuration_hash": configuration_hash(payload_for_hash),
        "parameter_schema_revision": PARAMETER_SCHEMA_REVISION,
        "resolved_device": resolved_device,
        "adapter_support": {
            "detector": "ultralytics.predict(conf,iou,imgsz,device,classes) plus raw-label allowlist guard",
            "tracker": "ByteTrackTrackerAdapter(track_activation_threshold,lost_track_buffer,minimum_iou_threshold,minimum_consecutive_frames)",
            "crossing": "canonical finite-segment PTS interpolation with configured tolerances",
            "classification": "track-vote policy with configured observation/share thresholds",
        },
    }


def estimate_resource_impact(parameters: Mapping[str, Any], resolved_device: str = "AUTO") -> dict[str, Any]:
    try:
        image_size = int(parameters.get("image_size", 640))
    except (TypeError, ValueError):
        image_size = 640
    try:
        stride = int(parameters.get("frame_stride", 1))
    except (TypeError, ValueError):
        stride = 1
    try:
        lost_track_buffer = int(parameters.get("lost_track_buffer", 30))
    except (TypeError, ValueError):
        lost_track_buffer = 30
    multiplier = round((image_size / 640) ** 2 / stride, 2)
    return {
        "relative_compute_factor": multiplier,
        "estimated_memory_class": "HIGH" if image_size >= 1280 else "MEDIUM" if image_size >= 960 else "LOW",
        "device": resolved_device,
        "gpu_recommended": image_size >= 960 or stride == 1 and lost_track_buffer >= 60,
        "real_time_status": "NOT_ESTABLISHED",
        "disclosure": "FPS and compute estimates do not establish real-time capability.",
    }


def build_configuration(
    profile_code: str,
    guided_settings: Mapping[str, Any] | None,
    expert_overrides: Mapping[str, Any] | None,
    *,
    media_duration_ms: int | None = None,
    analysis_start_pts_ms: int | None = None,
    analysis_end_pts_ms: int | None = None,
    cuda_available: bool | None = None,
) -> dict[str, Any]:
    profile = profile_by_code(profile_code)
    requested_guided_settings = {
        key: value for key, value in dict(guided_settings or {}).items() if value is not None
    }
    guided, guided_parameters, guided_normalizations, guided_errors = resolve_guided_settings(
        guided_settings,
        base_parameters=profile["resolved_parameters"],
    )
    overrides = {key: value for key, value in dict(expert_overrides or {}).items() if value is not None}
    unknown_overrides = sorted(set(overrides) - EXPERT_PARAMETER_NAMES)
    validation = validate_parameters(
        {**guided_parameters, **overrides},
        media_duration_ms=media_duration_ms,
        analysis_start_pts_ms=analysis_start_pts_ms,
        analysis_end_pts_ms=analysis_end_pts_ms,
        cuda_available=cuda_available,
    )
    if guided_errors:
        validation["errors"] = [*guided_errors, *validation["errors"]]
        validation["valid"] = False
    if unknown_overrides:
        validation["errors"] = [
            *validation["errors"],
            *(_error("unsupported_expert_override", key, "Use a supported typed expert field.") for key in unknown_overrides),
        ]
        validation["valid"] = False
    validation["requested_expert_overrides"] = overrides
    validation["requested_guided_settings"] = requested_guided_settings
    validation["guided_settings"] = guided
    validation["normalizations"] = [*guided_normalizations, *validation["normalizations"]]
    if guided_normalizations:
        validation["warnings"] = [
            *validation["warnings"],
            *(
                {
                    "code": "guided_resolution_normalized",
                    "field": item["field"],
                    "detail": "The guided selection resolved this runtime value from the selected profile baseline.",
                    "resolved_value": item["value"],
                }
                for item in guided_normalizations
            ),
        ]
    validation["profile_code"] = profile["profile_code"]
    validation["profile_revision"] = profile["profile_revision"]
    validation["profile_id"] = profile["profile_code"]
    validation["configuration_schema_revision"] = CONFIGURATION_SCHEMA_REVISION
    adapter_provenance = adapter_provenance_for_parameters(validation["resolved_parameters"])
    validation.update(adapter_provenance)
    validation["device_request"] = str(
        overrides.get("device_mode")
        or validation["resolved_parameters"].get("device_mode", "AUTO")
    ).upper()
    validation["runtime_provenance_status"] = "AWAITING_RUNTIME_RESOLUTION"
    validation["runtime_provenance"] = {
        key: validation[key]
        for key in (
            "detector_id",
            "model_revision",
            "weight_sha256",
            "tracker_id",
            "tracker_revision",
            "crossing_policy_revision",
            "classification_policy_revision",
            "parameter_schema_revision",
        )
    }
    identity_arguments = dict(
        parameter_schema_revision=validation["parameter_schema_revision"],
        resolved_parameters=validation["resolved_parameters"],
        model_revision=validation["model_revision"],
        weight_sha256=validation["weight_sha256"],
        tracker_revision=validation["tracker_revision"],
        crossing_policy_revision=validation["crossing_policy_revision"],
        classification_policy_revision=validation["classification_policy_revision"],
    )
    validation["configuration_hash"] = configuration_identity_hash(**identity_arguments)
    # Runtime identity is deliberately absent until the worker has resolved the
    # execution device and inspected the actual adapter/model artifacts.
    validation["runtime_configuration_hash"] = None
    validation["request_provenance_hash"] = request_provenance_hash(
        profile_revision=validation["profile_revision"],
        guided_settings=requested_guided_settings,
        requested_expert_overrides=overrides,
        operator_requested_device=validation["device_request"],
        normalizations=validation["normalizations"],
    )
    return validation
