from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.benchmark.dataset import load_json, validate_ground_truth, validate_manifest, validation_payload  # noqa: E402

def main() -> int:
    parser = argparse.ArgumentParser(description="Validate benchmark dataset manifest and ground truth.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--ground-truth", required=True, type=Path)
    args = parser.parse_args()
    manifest = load_json(args.manifest)
    ground_truth = load_json(args.ground_truth)
    errors = validate_manifest(manifest) + validate_ground_truth(ground_truth, manifest)
    print(json.dumps(validation_payload(errors), indent=2, ensure_ascii=False))
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
