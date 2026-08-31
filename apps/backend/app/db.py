from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path


MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def connect(path: str | Path = ":memory:", *, check_same_thread: bool = False) -> sqlite3.Connection:
    connection = sqlite3.connect(path, check_same_thread=check_same_thread)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    if str(path) != ":memory:":
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def database_identity_hash(connection: sqlite3.Connection) -> str:
    """Return a stable, path-free identity for the connection's main database."""
    rows = connection.execute("PRAGMA database_list").fetchall()
    main = next((row for row in rows if str(row[1]) == "main"), None)
    filename = str(main[2] or "") if main is not None else ""
    if filename:
        identity = str(Path(filename).resolve()).casefold()
    else:
        identity = f":memory:{id(connection)}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def database_schema_version(connection: sqlite3.Connection) -> str | None:
    try:
        row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    except sqlite3.Error:
        return None
    return str(row[0]) if row is not None and row[0] is not None else None


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {
        row["version"] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
    }
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = migration.stem
        if version in applied:
            continue
        connection.executescript(migration.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, datetime('now'))",
            (version,),
        )
    connection.commit()
