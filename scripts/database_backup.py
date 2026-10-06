from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCAL_DATA = ROOT / ".local-data"


def _default_database() -> Path:
    return Path(os.getenv("TVA_DB_PATH", DEFAULT_LOCAL_DATA / "tva.sqlite3")).expanduser().resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _integrity_check(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"sqlite_integrity_failed:{result[0] if result else 'no_result'}")
    finally:
        connection.close()


def _sqlite_backup_to_path(source: Path, destination: Path) -> None:
    """Materialize a consistent SQLite backup, including committed WAL state."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="tva-backup-", suffix=".sqlite3", dir=destination.parent, delete=False
        ) as handle:
            temp_path = Path(handle.name)
        source_connection = sqlite3.connect(source)
        try:
            destination_connection = sqlite3.connect(temp_path)
            try:
                source_connection.backup(destination_connection)
                destination_connection.commit()
            finally:
                destination_connection.close()
        finally:
            source_connection.close()
        _integrity_check(temp_path)
        os.replace(temp_path, destination)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def _backup(source: Path, destination: Path) -> dict[str, Any]:
    if not source.is_file():
        raise FileNotFoundError(f"database_not_found:{source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"backup_exists:{destination}")
    _sqlite_backup_to_path(source, destination)
    _integrity_check(destination)
    result = {
        "operation": "backup",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "schema_migrations": _schema_version(destination),
        "sha256": _sha256(destination),
        "size_bytes": destination.stat().st_size,
        "backup_filename": destination.name,
    }
    destination.with_suffix(destination.suffix + ".json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def _schema_version(path: Path) -> str | None:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return str(row[0]) if row and row[0] is not None else None
    except sqlite3.Error:
        return None
    finally:
        connection.close()


def _remove_sqlite_sidecars(path: Path) -> None:
    """Remove journal files that belong to the database being replaced."""
    for suffix in ("-wal", "-shm", "-journal"):
        path.with_name(f"{path.name}{suffix}").unlink(missing_ok=True)


def _restore(source: Path, destination: Path) -> dict[str, Any]:
    if not source.is_file():
        raise FileNotFoundError(f"backup_not_found:{source}")
    _integrity_check(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    safety_copy: Path | None = None
    if destination.exists():
        safety_copy = destination.with_name(f"{destination.name}.pre-restore-{_timestamp()}.bak")
        _sqlite_backup_to_path(destination, safety_copy)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="tva-restore-", suffix=".sqlite3", dir=destination.parent, delete=False) as handle:
            temp_path = Path(handle.name)
        _sqlite_backup_to_path(source, temp_path)
        _integrity_check(temp_path)
        os.replace(temp_path, destination)
        _remove_sqlite_sidecars(destination)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()
    return {
        "operation": "restore",
        "restored_at": datetime.now(timezone.utc).isoformat(),
        "schema_migrations": _schema_version(destination),
        "sha256": _sha256(destination),
        "size_bytes": destination.stat().st_size,
        "safety_copy_filename": safety_copy.name if safety_copy else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Traffic Video Analytics SQLite backup and restore utility.")
    subparsers = parser.add_subparsers(dest="operation", required=True)
    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--database", type=Path, default=_default_database())
    backup_parser.add_argument("--destination", type=Path)
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--database", type=Path, default=_default_database())
    restore_parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.operation == "backup":
            database = args.database.expanduser().resolve()
            destination = (args.destination or (DEFAULT_LOCAL_DATA / "backups" / f"tva-{_timestamp()}.sqlite3")).expanduser().resolve()
            result = _backup(database, destination)
        else:
            result = _restore(args.backup.expanduser().resolve(), args.database.expanduser().resolve())
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (FileExistsError, FileNotFoundError, OSError, RuntimeError, sqlite3.Error) as exc:
        print(json.dumps({"operation": args.operation, "status": "failed", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
