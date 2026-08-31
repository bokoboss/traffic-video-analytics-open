from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from typing import Any


WORKER_STATES = {"STARTING", "READY", "BUSY", "DEGRADED", "STALE", "OFFLINE"}
LIVE_WORKER_STATES = {"READY", "BUSY"}
DEFAULT_HEARTBEAT_TTL_SECONDS = 5.0


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone(timezone.utc).isoformat()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def upsert_worker_heartbeat(
    connection: sqlite3.Connection,
    *,
    worker_id: str,
    instance_token: str,
    pid: int | None,
    started_at: datetime | str,
    runtime_version: str,
    state: str,
    now: datetime | None = None,
) -> None:
    if state not in WORKER_STATES:
        raise ValueError(f"unsupported worker heartbeat state: {state}")
    started_timestamp = started_at if isinstance(started_at, str) else timestamp(started_at)
    now_timestamp = timestamp(now)
    connection.execute(
        """
        INSERT INTO runtime_heartbeats(
            component, instance_token, worker_id, pid, started_at,
            last_heartbeat_at, runtime_version, state, updated_at
        ) VALUES ('worker', ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(component) DO UPDATE SET
            instance_token = excluded.instance_token,
            worker_id = excluded.worker_id,
            pid = excluded.pid,
            started_at = excluded.started_at,
            last_heartbeat_at = excluded.last_heartbeat_at,
            runtime_version = excluded.runtime_version,
            state = excluded.state,
            updated_at = excluded.updated_at
        """,
        (
            instance_token,
            worker_id,
            pid,
            started_timestamp,
            now_timestamp,
            runtime_version,
            state,
            now_timestamp,
        ),
    )
    connection.commit()


def read_worker_heartbeat(
    connection: sqlite3.Connection,
    *,
    expected_worker_id: str | None,
    expected_instance_token: str | None,
    ttl_seconds: float = DEFAULT_HEARTBEAT_TTL_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a public-safe liveness projection without exposing the token or PID."""

    base = {
        "component": "worker",
        "required": True,
        "version": None,
        "state": "OFFLINE",
        "ready": False,
        "status": "BLOCKED",
        "detail": "No live processing-worker heartbeat is registered.",
        "remediation": "Start run_app.bat and wait for the worker heartbeat before processing.",
    }
    if not expected_worker_id or not expected_instance_token:
        return base

    row = connection.execute("SELECT * FROM runtime_heartbeats WHERE component = 'worker'").fetchone()
    if row is None or row["worker_id"] != expected_worker_id or row["instance_token"] != expected_instance_token:
        return base

    try:
        last_heartbeat = _parse_timestamp(str(row["last_heartbeat_at"]))
        age_seconds = max(0.0, ((now or utc_now()) - last_heartbeat).total_seconds())
    except (TypeError, ValueError):
        return {
            **base,
            "state": "STALE",
            "detail": "The processing-worker heartbeat timestamp is invalid.",
            "remediation": "Restart the application so the worker can register a fresh heartbeat.",
        }

    if age_seconds > max(0.1, float(ttl_seconds)):
        return {
            **base,
            "state": "STALE",
            "version": str(row["runtime_version"]),
            "detail": f"Worker heartbeat is stale ({age_seconds:.1f}s old; expiry is {float(ttl_seconds):.1f}s).",
            "remediation": "Restart the application or inspect the worker log before processing.",
        }

    state = str(row["state"])
    if state in LIVE_WORKER_STATES:
        return {
            **base,
            "status": "READY",
            "version": str(row["runtime_version"]),
            "state": state,
            "ready": True,
            "detail": f"Worker {expected_worker_id} heartbeat is fresh ({age_seconds:.1f}s old).",
            "remediation": None,
        }
    if state == "DEGRADED":
        return {
            **base,
            "status": "READY_WITH_WARNINGS",
            "version": str(row["runtime_version"]),
            "state": state,
            "detail": f"Worker {expected_worker_id} is alive but degraded ({age_seconds:.1f}s old).",
            "remediation": "Inspect the worker log and resolve the active processing warning.",
        }
    if state == "STARTING":
        return {
            **base,
            "status": "STARTING",
            "version": str(row["runtime_version"]),
            "state": state,
            "detail": f"Worker {expected_worker_id} is starting; heartbeat is fresh ({age_seconds:.1f}s old).",
            "remediation": "Wait for the worker to report READY, or inspect the worker log.",
        }
    return {
        **base,
        "state": "OFFLINE",
        "version": str(row["runtime_version"]),
        "detail": f"Worker {expected_worker_id} reported {state}.",
        "remediation": "Start the processing worker before processing.",
    }
