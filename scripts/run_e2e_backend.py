from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    db_path = Path(os.getenv("TVA_DB_PATH", ".local-data/playwright.sqlite"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    uvicorn.run("apps.backend.app.main:app", host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
