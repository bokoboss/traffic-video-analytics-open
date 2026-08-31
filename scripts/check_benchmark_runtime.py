from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.benchmark.runtime import benchmark_runtime_status  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Report optional Milestone 5.1 benchmark runtime status.")
    parser.add_argument("--strict", action="store_true", help="Fail when benchmark runtime blockers are present.")
    args = parser.parse_args()
    status = benchmark_runtime_status(ROOT)
    print(json.dumps(asdict(status), indent=2, ensure_ascii=False))
    return 2 if args.strict and status.blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
