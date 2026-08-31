from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from pathlib import Path
from typing import Any

from .worker_heartbeat import DEFAULT_HEARTBEAT_TTL_SECONDS, read_worker_heartbeat


READINESS_STATES = {"STARTING", "READY", "READY_WITH_WARNINGS", "BLOCKED", "NOT_CONFIGURED", "NOT_PROBED"}


def component(
    name: str,
    *,
    status: str,
    required: bool,
    ready: bool,
    version: str | None = None,
    detail: str | None = None,
    remediation: str | None = None,
    state: str | None = None,
) -> dict[str, Any]:
    normalized = status if status in READINESS_STATES else "NOT_PROBED"
    return {
        "component": name,
        "status": normalized,
        "required": required,
        "version": version,
        "state": state or normalized.lower(),
        "ready": ready,
        "detail": detail,
        "remediation": remediation,
    }


def _local_data_root() -> Path:
    return Path(os.getenv("TVA_LOCAL_DATA_DIR", Path(__file__).resolve().parents[3] / ".local-data")).expanduser().resolve()


def _disk_component(root: Path) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(root)
        free_gb = round(usage.free / (1024**3), 2)
    except OSError as exc:
        return component(
            "disk_space",
            status="BLOCKED",
            required=True,
            ready=False,
            detail="Disk space could not be measured.",
            remediation="Check the local data drive and permissions.",
            state=f"disk_probe_failed:{type(exc).__name__}",
        )
    if free_gb < 1:
        return component(
            "disk_space",
            status="BLOCKED",
            required=True,
            ready=False,
            version=f"{free_gb:.2f} GB free",
            detail="Less than 1 GB is available for local runtime data.",
            remediation="Free space before importing media or processing.",
        )
    if free_gb < 10:
        return component(
            "disk_space",
            status="READY_WITH_WARNINGS",
            required=True,
            ready=True,
            version=f"{free_gb:.2f} GB free",
            detail="Storage is usable but below the pilot warning threshold.",
            remediation="Keep at least 10 GB free for source, preview and export artifacts.",
        )
    return component("disk_space", status="READY", required=True, ready=True, version=f"{free_gb:.2f} GB free", detail="Local data volume is writable.")


def _frontend_component(frontend_url: str | None) -> dict[str, Any]:
    url = (frontend_url or "").strip()
    if not url:
        return component(
            "frontend",
            status="BLOCKED",
            required=True,
            ready=False,
            state="OFFLINE",
            detail="No launcher-owned frontend URL is configured.",
            remediation="Start the Vite frontend through run_app.bat before opening the browser.",
        )
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise URLError("frontend probe must remain localhost-only")
        request = Request(url, headers={"User-Agent": "traffic-video-analytics-readiness/1"})
        with urlopen(request, timeout=0.5) as response:
            if int(response.status) >= 400:
                raise URLError(f"HTTP {response.status}")
    except (OSError, URLError, ValueError):
        return component(
            "frontend",
            status="BLOCKED",
            required=True,
            ready=False,
            state="OFFLINE",
            detail="The configured frontend URL did not respond successfully.",
            remediation="Start or restart the frontend and confirm its localhost URL is reachable.",
        )
    return component(
        "frontend",
        status="READY",
        required=True,
        ready=True,
        state="READY",
        detail=f"Frontend HTTP probe succeeded at {url}.",
    )


def build_components(
    *,
    database: sqlite3.Connection,
    media_runtime: dict[str, Any],
    processing: dict[str, Any],
    local_data_dir: Path | None = None,
    worker_id: str | None = None,
    worker_instance_token: str | None = None,
    worker_heartbeat_ttl_seconds: float = DEFAULT_HEARTBEAT_TTL_SECONDS,
    frontend_url: str | None = None,
) -> dict[str, dict[str, Any]]:
    root = local_data_dir or _local_data_root()
    root.mkdir(parents=True, exist_ok=True)
    try:
        migration = database.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
        migration_version = str(migration["version"] or "unknown")
        migration_component = component("migrations", status="READY", required=True, ready=True, version=migration_version, detail="Database migrations are applied.")
    except sqlite3.Error as exc:
        migration_component = component("migrations", status="BLOCKED", required=True, ready=False, detail="Migration state could not be read.", remediation="Run setup_app.bat and inspect the setup log.", state=f"migration_probe_failed:{type(exc).__name__}")

    ffmpeg = media_runtime.get("ffmpeg", {})
    ffprobe = media_runtime.get("ffprobe", {})
    ffmpeg_ready = bool(ffmpeg.get("available"))
    ffprobe_ready = bool(ffprobe.get("available"))
    model_runtime = processing.get("model_runtime", {})
    detector = processing.get("detector", {})
    tracker = processing.get("tracker", {})
    model_weights = processing.get("model_weights", {})
    real_inference = processing.get("real_inference", {})
    worker_probe = read_worker_heartbeat(
        database,
        expected_worker_id=worker_id if worker_id is not None else os.getenv("TVA_WORKER_ID"),
        expected_instance_token=worker_instance_token if worker_instance_token is not None else os.getenv("TVA_WORKER_INSTANCE_TOKEN"),
        ttl_seconds=worker_heartbeat_ttl_seconds,
    )
    worker_ready = bool(worker_probe["ready"])
    runtime_ready = bool(real_inference.get("ready"))
    model_weights_ready = bool(model_weights.get("ready") or detector.get("weight", {}).get("matches_expected"))
    components = {
        "backend": component("backend", status="READY", required=True, ready=True, version=sys.version.split()[0], detail="FastAPI process is responding."),
        "database": component("database", status="READY", required=True, ready=True, version=sqlite3.sqlite_version, detail="SQLite connection is available."),
        "migrations": migration_component,
        "artifact_storage": component("artifact_storage", status="READY", required=True, ready=True, detail="The local data root is available for managed artifacts."),
        "disk_space": _disk_component(root),
        "frontend": _frontend_component(frontend_url if frontend_url is not None else os.getenv("TVA_FRONTEND_URL", "http://127.0.0.1:5174")),
        "worker": component(
            "worker",
            status=str(worker_probe["status"]),
            required=True,
            ready=worker_ready,
            version=worker_probe.get("version"),
            detail=worker_probe.get("detail"),
            remediation=worker_probe.get("remediation"),
            state=str(worker_probe["state"]),
        ),
        "ffmpeg": component("ffmpeg", status="READY" if ffmpeg_ready else "BLOCKED", required=False, ready=ffmpeg_ready, version=ffmpeg.get("version"), detail=ffmpeg.get("path_source") or ffmpeg.get("error_code"), remediation="Configure TVA_FFMPEG_DIR or place the approved FFmpeg runtime under .local-tools/ffmpeg/bin." if not ffmpeg_ready else None),
        "ffprobe": component("ffprobe", status="READY" if ffprobe_ready else "BLOCKED", required=False, ready=ffprobe_ready, version=ffprobe.get("version"), detail=ffprobe.get("path_source") or ffprobe.get("error_code"), remediation="Provide the paired FFprobe executable before importing real media." if not ffprobe_ready else None),
        "model_runtime": component("model_runtime", status="READY" if model_runtime.get("ready") else "NOT_CONFIGURED", required=False, ready=bool(model_runtime.get("ready")), version=None, detail="Optional AI environment is available." if model_runtime.get("ready") else "Optional AI environment is not installed.", remediation="Install the approved optional AI environment only when real-video qualification is in scope." if not model_runtime.get("ready") else None),
        "detector": component("detector", status="READY" if detector.get("ready") else "NOT_CONFIGURED", required=False, ready=bool(detector.get("ready")), version=str(detector.get("id")) if detector.get("id") else None, detail=detector.get("state"), remediation="Provide the approved detector package and verified weight before REAL_VIDEO processing." if not detector.get("ready") else None),
        "model_weights": component("model_weights", status="READY" if model_weights_ready else "NOT_CONFIGURED", required=False, ready=model_weights_ready, version=str(model_weights.get("state", "optional_real_mode")), detail="Verified detector weights are available." if model_weights_ready else "Verified model-weight state is not available from this probe.", remediation="Provide and checksum the approved model weights before REAL_VIDEO processing." if not model_weights_ready else None),
        "tracker": component("tracker", status="READY" if tracker.get("ready") else "NOT_CONFIGURED", required=False, ready=bool(tracker.get("ready")), version=str(tracker.get("id")) if tracker.get("id") else None, detail=tracker.get("state"), remediation="Provide the approved tracker adapter before REAL_VIDEO processing." if not tracker.get("ready") else None),
        "cuda": component("cuda", status="READY" if processing.get("device", {}).get("cuda_available") else "NOT_CONFIGURED", required=False, ready=bool(processing.get("device", {}).get("cuda_available")), version=None, detail="CUDA availability is claimed by the worker only." if processing.get("device", {}).get("cuda_available") else "CUDA was not claimed by the worker probe.", remediation="GPU acceleration is optional for this pilot; use CPU only after hardware-specific validation."),
        "synthetic_capability": component(
            "synthetic_capability",
            status="READY" if worker_ready else "BLOCKED",
            required=False,
            ready=worker_ready,
            state="SYNTHETIC_ONLY" if worker_ready and not runtime_ready else ("READY" if worker_ready else "WORKER_OFFLINE"),
            detail="Deterministic synthetic workflow is available through the live worker; it is not real-media evidence." if worker_ready else "Synthetic job execution also requires a live worker heartbeat.",
            remediation=None if worker_ready else "Start run_app.bat and wait for the worker heartbeat.",
        ),
        "real_inference": component(
            "real_inference",
            status="READY" if worker_ready and runtime_ready else ("BLOCKED" if not worker_ready else "NOT_CONFIGURED"),
            required=False,
            ready=worker_ready and runtime_ready,
            version=str(real_inference.get("mode", "REAL_VIDEO")),
            state="READY" if worker_ready and runtime_ready else ("WORKER_OFFLINE" if not worker_ready else str(real_inference.get("state", "NOT_CONFIGURED"))),
            detail=("Real-video processing is available through the live worker." if worker_ready and runtime_ready else "Real-video processing also requires a fresh worker heartbeat." if not worker_ready else real_inference.get("detail")),
            remediation=None if worker_ready and runtime_ready else "Resolve the worker and real-video readiness blockers; synthetic validation remains separate." ,
        ),
    }
    return components
