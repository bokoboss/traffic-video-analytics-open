from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.benchmark.dataset import load_json  # noqa: E402
from tools.benchmark.metrics import aggregate_count_errors, match_crossing_events  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare benchmark event ledger against crossing ground truth.")
    parser.add_argument("--predicted-events", required=True, type=Path)
    parser.add_argument("--ground-truth", required=True, type=Path)
    args = parser.parse_args()
    predicted = load_json(args.predicted_events).get("events", [])
    truth = load_json(args.ground_truth).get("events", [])
    payload = {
        "crossing_event_metrics": match_crossing_events(predicted, truth),
        "aggregate_count_metrics": aggregate_count_errors(predicted, truth),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
