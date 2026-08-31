from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.backend.app.benchmark_service import BenchmarkService  # noqa: E402
from apps.backend.app.benchmarking import BenchmarkValidationError  # noqa: E402
from apps.backend.app.db import connect, migrate  # noqa: E402


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or import 6C ground-truth revisions.")
    parser.add_argument("ground_truth", type=Path)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    connection = connect(str(args.database))
    migrate(connection)
    service = BenchmarkService(connection)
    payload = read_json(args.ground_truth)
    try:
        if args.dry_run:
            result = service.validate_ground_truth(payload)
            result["dry_run"] = True
            print_json(result)
        else:
            print_json(service.import_ground_truth(payload))
    except (BenchmarkValidationError, ValueError) as exc:
        print_json({"valid": False, "error": str(exc), "errors": list(getattr(exc, "errors", ()))})
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
