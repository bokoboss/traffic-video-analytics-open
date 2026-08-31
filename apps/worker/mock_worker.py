from __future__ import annotations

import argparse
import json
import time


def main() -> int:
    parser = argparse.ArgumentParser(description="Separate mock processing worker.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--segments", type=int, default=4)
    args = parser.parse_args()
    for index in range(args.segments):
        event = {
            "run_id": args.run_id,
            "segment_index": index,
            "progress_percent": int(((index + 1) / args.segments) * 100),
            "boundary": "separate_process",
        }
        print(json.dumps(event), flush=True)
        time.sleep(0.01)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
