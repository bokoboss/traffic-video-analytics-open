from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.backend.app.media import media_runtime_status
from scripts.runtime_readiness import readiness


def main() -> int:
    parser = argparse.ArgumentParser(description="Check local runtime and FFmpeg media availability.")
    parser.add_argument("--strict", action="store_true", help="Return non-zero when ffmpeg or ffprobe is unavailable.")
    parser.add_argument("--readiness", action="store_true", help="Include broader app and benchmark readiness.")
    args = parser.parse_args()

    status = media_runtime_status()
    payload = {
        "ffmpeg": status.ffmpeg.__dict__,
        "ffprobe": status.ffprobe.__dict__,
        "readiness_state": status.readiness_state,
        "paired_version_consistent": status.paired_version_consistent,
        "warnings": status.warnings,
        "metadata_inspection_available": status.metadata_inspection_available,
        "reference_frame_extraction_available": status.reference_frame_extraction_available,
    }
    if args.readiness:
        payload["runtime_readiness"] = asdict(readiness())
    print(json.dumps(payload, indent=2))
    if args.strict and not status.reference_frame_extraction_available:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
