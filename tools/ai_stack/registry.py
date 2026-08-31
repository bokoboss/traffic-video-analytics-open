from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REGISTRY_SCHEMA_VERSION = "model-registry-v1"
APPROVAL_STATES = {
    "approved primary pilot",
    "approved fallback",
    "approved classifier candidate",
    "technical evaluation only",
    "legal review required",
    "rejected",
    "deprecated",
}
REQUIRED_MODEL_FIELDS = {
    "model_id",
    "role",
    "family",
    "implementation_source",
    "package",
    "exact_version",
    "commit_or_tag",
    "model_filename",
    "source_url",
    "sha256",
    "code_license",
    "package_license",
    "weight_license",
    "training_dataset",
    "dataset_license_provenance",
    "supported_observable_classes",
    "candidate_tims_mappings",
    "unsupported_distinctions",
    "cpu_support",
    "gpu_support",
    "expected_vram",
    "readiness_state",
    "approval_state",
    "redistribution_policy",
    "source_required_status",
    "attribution_requirements",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class RegistryError:
    code: str
    message: str
    model_id: str | None = None


def load_registry(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("model registry root must be an object")
    return payload


def validate_registry(payload: dict[str, Any]) -> tuple[RegistryError, ...]:
    errors: list[RegistryError] = []
    if payload.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        errors.append(RegistryError("unsupported_schema_version", "registry schema_version is not supported"))

    models = payload.get("models")
    if not isinstance(models, list) or not models:
        return tuple(errors + [RegistryError("missing_models", "registry must contain at least one model")])

    seen: set[str] = set()
    primary_roles: set[str] = set()
    fallback_roles: set[str] = set()
    for record in models:
        if not isinstance(record, dict):
            errors.append(RegistryError("invalid_model_record", "model record must be an object"))
            continue
        model_id = str(record.get("model_id", ""))
        missing = sorted(REQUIRED_MODEL_FIELDS - set(record))
        for field in missing:
            errors.append(RegistryError("missing_required_field", f"missing field: {field}", model_id or None))
        if not model_id:
            errors.append(RegistryError("missing_model_id", "model_id is required"))
        elif model_id in seen:
            errors.append(RegistryError("duplicate_model_id", "model_id must be unique", model_id))
        seen.add(model_id)

        approval = record.get("approval_state")
        role = str(record.get("role", ""))
        if approval not in APPROVAL_STATES:
            errors.append(RegistryError("invalid_approval_state", "approval_state is not recognized", model_id))
        if approval == "approved primary pilot":
            if role in primary_roles:
                errors.append(RegistryError("duplicate_primary_for_role", role, model_id))
            primary_roles.add(role)
        if approval == "approved fallback":
            fallback_roles.add(role)

        sha256 = record.get("sha256")
        if sha256 is not None and not (isinstance(sha256, str) and SHA256_RE.match(sha256)):
            errors.append(RegistryError("invalid_sha256", "sha256 must be null or 64 lowercase hex", model_id))

        if record.get("code_license") == record.get("weight_license") and "not applicable" not in str(
            record.get("weight_license", "")
        ).lower():
            errors.append(
                RegistryError(
                    "collapsed_code_and_weight_license",
                    "code_license and weight_license must be recorded as distinct fields",
                    model_id,
                )
            )
        unsupported = record.get("unsupported_distinctions")
        if not isinstance(unsupported, list) or not unsupported:
            errors.append(
                RegistryError(
                    "missing_unsupported_distinctions",
                    "unsupported TIMS distinctions must be explicit",
                    model_id,
                )
            )

    provisional_primary_readiness = {"provisional_until_real_smoke_passes", "runtime_smoke_qualified"}
    if "detector" not in primary_roles and not any(
        record.get("role") == "detector" and record.get("readiness_state") in provisional_primary_readiness
        for record in models
        if isinstance(record, dict)
    ):
        errors.append(RegistryError("missing_primary_detector", "one detector must be approved primary pilot"))
    if "tracker" not in primary_roles and not any(
        record.get("role") == "tracker" and record.get("readiness_state") in provisional_primary_readiness
        for record in models
        if isinstance(record, dict)
    ):
        errors.append(RegistryError("missing_primary_tracker", "one tracker must be approved primary pilot"))
    if "detector" not in fallback_roles:
        errors.append(RegistryError("missing_fallback_detector", "one detector fallback must be approved"))
    if "tracker" not in fallback_roles:
        errors.append(RegistryError("missing_fallback_tracker", "one tracker fallback must be approved"))
    return tuple(errors)
