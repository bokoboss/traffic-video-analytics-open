from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.backend.app.benchmarking import render_report_markdown  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a persisted 6C benchmark report as Markdown.")
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.report.read_text(encoding="utf-8"))
    report = payload.get("report", payload) if isinstance(payload, dict) else payload
    markdown = render_report_markdown(report) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(markdown, encoding="utf-8")
    else:
        print(markdown, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
