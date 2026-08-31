from __future__ import annotations

from dataclasses import replace

import pytest

from apps.backend.app.processing_profiles import build_configuration
from apps.backend.app.real_inference import RealProcessingConfig, evaluate_runtime_provenance


def test_pre_run_configuration_does_not_claim_resolved_runtime_identity() -> None:
    configuration = build_configuration("BALANCED", {}, {}, cuda_available=None)

    assert configuration["resolved_device"] == "AUTO"
    assert configuration["configuration_hash"]
    assert configuration["request_provenance_hash"]
    assert configuration["runtime_configuration_hash"] is None
    assert configuration["runtime_provenance_status"] == "AWAITING_RUNTIME_RESOLUTION"


def _resolved_identity(requested: str, resolved: str, *, actual_model_revision: str | None = None) -> dict:
    configuration = build_configuration("CUSTOM", {}, {"device_mode": requested}, cuda_available=None)
    config = replace(RealProcessingConfig.from_payload(configuration["resolved_parameters"]), resolved_device=resolved)
    actual = {
        **configuration["runtime_provenance"],
        "model_revision": actual_model_revision or configuration["model_revision"],
    }
    return evaluate_runtime_provenance(
        config=config,
        configured_runtime_provenance=configuration["runtime_provenance"],
        actual_runtime_provenance=actual,
    )


@pytest.mark.parametrize(
    ("requested", "resolved"),
    (("AUTO", "cuda:0"), ("AUTO", "cpu"), ("CPU", "cpu"), ("CUDA", "cuda:0")),
)
def test_runtime_identity_is_verified_only_after_device_resolution(requested: str, resolved: str) -> None:
    identity = _resolved_identity(requested, resolved)

    assert identity["provenance_status"] == "VERIFIED_RUNTIME_PROVENANCE"
    assert identity["runtime_configuration_hash"] == identity["actual_runtime_configuration_hash"]
    assert identity["configured_runtime_payload"] == identity["actual_runtime_payload"]
    assert identity["configured_runtime_payload"]["resolved_device"] == resolved
    assert identity["provenance_mismatches"] == []


def test_genuine_authoritative_runtime_change_still_mismatches() -> None:
    identity = _resolved_identity("AUTO", "cuda:0", actual_model_revision="pypi:changed-model")

    assert identity["provenance_status"] == "RUNTIME_PROVENANCE_MISMATCH"
    assert identity["runtime_configuration_hash"] != identity["actual_runtime_configuration_hash"]
    assert identity["provenance_mismatches"] == [
        {
            "field": "model_revision",
            "configured": "pypi:8.4.103",
            "actual": "pypi:changed-model",
        }
    ]
