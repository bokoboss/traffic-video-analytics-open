from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.benchmark.harness import run_benchmark  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and run the benchmark smoke fixture.")
    parser.add_argument("--manifest", type=Path, default=ROOT / "tools/benchmark/fixtures/stub_manifest.json")
    parser.add_argument("--config", type=Path, default=ROOT / "tools/benchmark/fixtures/stub_config.json")
    args = parser.parse_args()
    result = run_benchmark(args.manifest, args.config)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["count_metrics"]["matches"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
