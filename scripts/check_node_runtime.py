from __future__ import annotations

from dataclasses import asdict
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.node_runtime import frontend_runtime_status


def main() -> int:
    status = frontend_runtime_status()
    print(json.dumps(asdict(status), indent=2))
    return 0 if status.node is not None and status.pnpm.available else 2


if __name__ == "__main__":
    raise SystemExit(main())
