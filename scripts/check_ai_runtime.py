from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ai_stack.registry import load_registry  # noqa: E402
from dataclasses import asdict  # noqa: E402

from tools.ai_stack.runtime import runtime_status  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Report optional AI runtime readiness.")
    parser.add_argument("--registry", type=Path, default=Path("model_registry.json"))
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    registry = load_registry(args.registry)
    status = runtime_status(root, registry)
    payload = asdict(status)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 1 if args.strict and payload["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
