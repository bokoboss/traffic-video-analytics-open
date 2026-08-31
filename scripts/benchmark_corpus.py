from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.backend.app.benchmark_service import BenchmarkService  # noqa: E402
from apps.backend.app.benchmarking import (  # noqa: E402
    BenchmarkValidationError,
    validate_corpus_manifest,
)
from apps.backend.app.db import connect, migrate  # noqa: E402


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or import a 6C benchmark corpus manifest.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="validate without opening SQLite")
    validate.add_argument("manifest", type=Path)
    importer = subparsers.add_parser("import", help="import an immutable corpus revision")
    importer.add_argument("manifest", type=Path)
    importer.add_argument("--database", type=Path, default=Path(":memory:"))
    importer.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    payload = read_json(args.manifest)

    if args.command == "validate":
        result = validate_corpus_manifest(payload)
        print_json(result)
        return 0 if result["valid"] else 2

    database = str(args.database)
    connection = connect(database)
    migrate(connection)
    service = BenchmarkService(connection)
    try:
        print_json(service.import_manifest(payload, dry_run=args.dry_run))
    except (BenchmarkValidationError, ValueError) as exc:
        print_json({"valid": False, "error": str(exc), "errors": list(getattr(exc, "errors", ()))})
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
