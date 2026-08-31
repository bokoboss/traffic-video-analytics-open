from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from apps.backend.app.db import connect, migrate
from apps.backend.app.release import release_identity
from apps.backend.app.services import FoundationService
from apps.backend.app.worker_heartbeat import upsert_worker_heartbeat


def _default_db(root: Path) -> str:
    return os.getenv("TVA_DB_PATH", str(root / ".local-data" / "tva.sqlite3"))


def _emit(event: str, **payload: object) -> None:
    identity = release_identity()
    print(
        json.dumps(
            {
                "event": event,
                "release_version": identity["release_version"],
                "git_commit_sha": identity["git_commit_sha"],
                **payload,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


class WorkerHeartbeat:
    def __init__(self, *, database_path: str, worker_id: str, instance_token: str, interval: float) -> None:
        self.database_path = database_path
        self.worker_id = worker_id
        self.instance_token = instance_token
        self.interval = max(0.25, interval)
        self.started_at = datetime.now(timezone.utc)
        self.runtime_version = str(release_identity()["release_version"])
        self._state = "STARTING"
        self._state_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def set_state(self, state: str) -> None:
        with self._state_lock:
            self._state = state

    def _get_state(self) -> str:
        with self._state_lock:
            return self._state

    def _write(self, connection, state: str) -> None:
        upsert_worker_heartbeat(
            connection,
            worker_id=self.worker_id,
            instance_token=self.instance_token,
            pid=os.getpid(),
            started_at=self.started_at,
            runtime_version=self.runtime_version,
            state=state,
        )

    def start(self) -> None:
        connection = connect(self.database_path, check_same_thread=True)
        try:
            migrate(connection)
            self._write(connection, "STARTING")
        finally:
            connection.close()
        self._thread = threading.Thread(target=self._run, name="tva-worker-heartbeat", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        connection = connect(self.database_path, check_same_thread=True)
        try:
            migrate(connection)
            while not self._stop.is_set():
                self._write(connection, self._get_state())
                self._stop.wait(self.interval)
        except Exception as exc:  # the API will deterministically mark this worker stale after the TTL
            _emit(
                "worker_heartbeat_failed",
                worker_id=self.worker_id,
                error_code="WORKER_HEARTBEAT_FAILED",
                exception_class=type(exc).__name__,
            )
        finally:
            connection.close()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval * 2))
        connection = connect(self.database_path, check_same_thread=True)
        try:
            migrate(connection)
            self._write(connection, "OFFLINE")
        finally:
            connection.close()


def run_worker(*, database_path: str, worker_id: str, instance_token: str, poll_interval: float, heartbeat_interval: float, once: bool) -> int:
    connection = connect(database_path, check_same_thread=True)
    migrate(connection)

    def processing_event_sink(event: str, payload: dict[str, object]) -> None:
        _emit(event, **payload)

    def callback_connection_factory():
        return connect(database_path, check_same_thread=False)
    service = FoundationService(
        connection,
        # Callback connections are never shared during inference. The same-thread
        # guard is disabled only so the worker can close the watcher-owned
        # connection after the decoder has joined that watcher.
        preview_connection_factory=callback_connection_factory,
        processing_connection_factory=callback_connection_factory,
        preview_event_sink=processing_event_sink,
        processing_event_sink=processing_event_sink,
    )
    heartbeat = WorkerHeartbeat(
        database_path=database_path,
        worker_id=worker_id,
        instance_token=instance_token,
        interval=heartbeat_interval,
    )
    heartbeat.start()
    heartbeat.set_state("READY")
    try:
        while True:
            service.fail_stale_processing_jobs()
            job = service.claim_next_processing_job(worker_id, lease_seconds=120)
            if job is None:
                preview = service.claim_next_preview_run(worker_id, lease_seconds=120)
                if preview is None:
                    if once:
                        return 0
                    time.sleep(max(0.05, poll_interval))
                    continue
                heartbeat.set_state("BUSY")
                preview_id = str(preview["id"])
                _emit("preview_run_claimed", preview_id=preview_id, worker_id=worker_id, mode=preview.get("mode"))
                try:
                    result = service.execute_claimed_preview_run(preview_id, worker_id)
                    _emit(
                        "preview_run_finished",
                        preview_id=preview_id,
                        worker_id=worker_id,
                        state=result.get("status"),
                    )
                except Exception as exc:
                    heartbeat.set_state("DEGRADED")
                    try:
                        service.fail_claimed_preview_run(preview_id, worker_id, "Unexpected preview worker failure.")
                    except Exception:
                        # The worker may have lost its lease; the fenced state is
                        # authoritative and will be requeued or already terminal.
                        pass
                    _emit(
                        "preview_run_failed",
                        preview_id=preview_id,
                        worker_id=worker_id,
                        error_code="PREVIEW_WORKER_EXCEPTION",
                        exception_class=type(exc).__name__,
                    )
                heartbeat.set_state("READY")
                if once:
                    return 0
                continue
            heartbeat.set_state("BUSY")
            run_id = str(job["id"])
            _emit("processing_job_claimed", run_id=run_id, worker_id=worker_id, mode=job.get("processing_mode"))
            try:
                result = service.execute_claimed_job(run_id, worker_id)
                _emit(
                    "processing_job_finished",
                    run_id=run_id,
                    worker_id=worker_id,
                    state=result.get("job_state"),
                    result_ready=result.get("result_ready"),
                )
            except Exception as exc:  # keep the worker alive and make the failure durable
                heartbeat.set_state("DEGRADED")
                try:
                    current = service._processing_job_or_raise(run_id)
                    if current["job_state"] not in {"COMPLETED", "FAILED", "CANCELLED"}:
                        service._fail_processing_job_owned(
                            run_id,
                            worker_id,
                            str(current["claim_token"] or ""),
                            "WORKER_EXCEPTION",
                            "worker",
                            "Unexpected processing worker failure.",
                        )
                finally:
                    _emit(
                        "processing_job_failed",
                        run_id=run_id,
                        worker_id=worker_id,
                        error_code="WORKER_EXCEPTION",
                        exception_class=type(exc).__name__,
                    )
            heartbeat.set_state("READY")
            if once:
                return 0
    finally:
        heartbeat.stop()
        connection.close()


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Traffic Video Analytics processing worker.")
    parser.add_argument("--db", default=_default_db(root))
    parser.add_argument("--worker-id", default=f"worker-{socket.gethostname()}-{os.getpid()}")
    parser.add_argument("--instance-token", default=os.getenv("TVA_WORKER_INSTANCE_TOKEN") or secrets.token_urlsafe(24))
    parser.add_argument("--poll-interval", type=float, default=0.25)
    parser.add_argument("--heartbeat-interval", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    Path(args.db).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    return run_worker(
        database_path=str(args.db),
        worker_id=str(args.worker_id),
        instance_token=str(args.instance_token),
        poll_interval=float(args.poll_interval),
        heartbeat_interval=float(args.heartbeat_interval),
        once=bool(args.once),
    )


if __name__ == "__main__":
    raise SystemExit(main())
