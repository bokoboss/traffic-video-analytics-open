from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.backend.app.main import create_app


def main() -> None:
    out = Path("packages/contracts/openapi.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    app = create_app()
    out.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
