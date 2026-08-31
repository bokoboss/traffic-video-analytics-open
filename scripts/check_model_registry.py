from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ai_stack.registry import load_registry, validate_registry  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the versioned AI model registry.")
    parser.add_argument("--registry", type=Path, default=Path("model_registry.json"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    payload = load_registry(args.registry)
    errors = validate_registry(payload)
    result = {
        "schema_version": payload.get("schema_version"),
        "registry": str(args.registry),
        "valid": not errors,
        "error_count": len(errors),
        "errors": [asdict(error) for error in errors],
    }
    if args.json or errors:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("model registry ready")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
