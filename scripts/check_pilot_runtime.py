from __future__ import annotations

import argparse
import json
import os
import platform
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOCAL_DATA = Path(os.getenv("TVA_LOCAL_DATA_DIR", ROOT / ".local-data")).expanduser().resolve()
REQUIRED_DIRS = ("runtime", "logs", "media", "previews", "exports", "diagnostics", "backups")


def _write_probe(path: Path) -> bool:
    probe = path / ".pilot-write-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def check(strict_windows: bool = False) -> dict[str, Any]:
    for name in REQUIRED_DIRS:
        (LOCAL_DATA / name).mkdir(parents=True, exist_ok=True)
    os_status = "READY" if platform.system() == "Windows" and platform.machine().upper() in {"AMD64", "X86_64"} else "BLOCKED"
    if not strict_windows and platform.system() != "Windows":
        os_status = "NOT_PROBED"
    python_ready = sys.version_info >= (3, 11)
    db_path = Path(os.getenv("TVA_DB_PATH", LOCAL_DATA / "tva.sqlite3")).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    database_ready = False
    migration_version: str | None = None
    try:
        connection = sqlite3.connect(db_path)
        connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        migration_version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        connection.close()
        database_ready = _write_probe(db_path.parent)
    except sqlite3.Error:
        database_ready = False
    return {
        "schema_version": "pilot-runtime-check-v1",
        "os": {"status": os_status, "system": platform.system(), "machine": platform.machine()},
        "python": {"status": "READY" if python_ready else "BLOCKED", "version": platform.python_version()},
        "database": {"status": "READY" if database_ready else "BLOCKED", "migration_version": migration_version},
        "local_data": {"status": "READY" if _write_probe(LOCAL_DATA) else "BLOCKED", "directories": list(REQUIRED_DIRS)},
        "core_ready": os_status in {"READY", "NOT_PROBED"} and python_ready and database_ready,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Pilot Release core installation readiness.")
    parser.add_argument("--strict-windows", action="store_true")
    args = parser.parse_args()
    payload = check(strict_windows=args.strict_windows)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload["core_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
