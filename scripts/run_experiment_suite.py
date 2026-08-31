from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.backend.app.benchmarking import (  # noqa: E402
    BenchmarkValidationError,
    build_experiment_configuration,
    pareto_frontier,
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate controlled 6C experiments and compare candidates.")
    parser.add_argument("--configurations", required=True, type=Path)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = load_json(args.configurations)
    rows = payload.get("configurations", payload) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise SystemExit("configurations must be a list or an object with configurations")
    configurations = []
    errors = []
    for index, row in enumerate(rows):
        try:
            configurations.append(build_experiment_configuration(row))
        except BenchmarkValidationError as exc:
            errors.append({"index": index, "error": str(exc), "errors": list(exc.errors)})
    result_rows = load_json(args.results) if args.results else []
    if isinstance(result_rows, dict):
        result_rows = result_rows.get("results", [])
    output = {
        "schema_version": "benchmark-experiment-suite-v1",
        "status": "INVALID_CONFIGURATION" if errors else "CANDIDATES_READY",
        "calibration_split_only": True,
        "holdout_isolation": "holdout results are diagnostic and cannot select calibration parameters",
        "qualification_status": "NOT_APPLIED",
        "no_pilot_qualification_claim": True,
        "configurations": configurations,
        "errors": errors,
        "pareto": pareto_frontier(result_rows) if result_rows else {"status": "NOT_RUN", "frontier": []},
    }
    serialized = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
