from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ai_stack.registry import load_registry  # noqa: E402


DIMENSIONS = [
    "license_suitability",
    "runtime_stability",
    "windows_python312_fit",
    "cpu_operation",
    "gpu_operation",
    "adapter_complexity",
    "offline_operation",
    "traffic_observable_fit",
    "tims_limitation_clarity",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit the Milestone 5.2B candidate decision matrix.")
    parser.add_argument("--registry", type=Path, default=Path("model_registry.json"))
    args = parser.parse_args()

    registry = load_registry(args.registry)
    rows = []
    for record in registry.get("models", []):
        if record.get("role") != "detector":
            continue
        rows.append(
            {
                "model_id": record["model_id"],
                "approval_state": record["approval_state"],
                "dimensions": {
                    "license_suitability": record["code_license"],
                    "runtime_stability": record["readiness_state"],
                    "windows_python312_fit": "requires preparation validation",
                    "cpu_operation": record["cpu_support"],
                    "gpu_operation": record["gpu_support"],
                    "adapter_complexity": "low" if record["model_id"].startswith("detector.ultralytics") else "medium/high",
                    "offline_operation": "supported after explicit weight preparation",
                    "traffic_observable_fit": record["supported_observable_classes"],
                    "tims_limitation_clarity": record["unsupported_distinctions"],
                },
                "decision_rationale": (
                    "Primary pilot balances mature Windows/Python ergonomics with accepted AGPL source policy."
                    if record["approval_state"] == "approved primary pilot"
                    else "Fallback retained to reduce permanent lock-in and preserve permissive-source path."
                ),
            }
        )
    print(
        json.dumps(
            {
                "schema_version": "model-candidate-comparison-v1",
                "opaque_single_score_used": False,
                "dimensions": DIMENSIONS,
                "rows": rows,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
