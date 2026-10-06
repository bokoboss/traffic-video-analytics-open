"""Lifecycle launcher for the self-contained Traffic Video Analytics package."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

try:
    from .process_ownership import (
        is_owned_record,
        process_command_line,
        process_identity,
        record_process,
        stop_owned_process,
    )
except ImportError:  # direct execution from the extracted package/source tree
    # The embedded Python runtime uses a _pth file and does not automatically
    # add the directory containing a directly executed script to sys.path.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from process_ownership import (  # type: ignore[no-redef]
        is_owned_record,
        process_command_line,
        process_identity,
        record_process,
        stop_owned_process,
    )


BACKEND_PORT = 8000
FRONTEND_PORT = 5174
ROLES = ("backend", "worker", "frontend")


class LauncherError(RuntimeError):
    """An operator-facing startup or lifecycle failure."""


def discover_package_root() -> Path:
    configured = os.getenv("TVA_PACKAGE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "PACKAGE_PROVENANCE.json").is_file() and (candidate / "app").is_dir():
            return candidate
    # Source checkout fallback: the package launcher is also used by tests and
    # by the source-controlled development helper.
    return Path(__file__).resolve().parents[2]


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LauncherError(f"json_read_failed:{path.name}:{exc}") from exc
    if not isinstance(payload, dict):
        raise LauncherError(f"json_object_required:{path.name}")
    return payload


def package_is_extracted(root: Path) -> bool:
    return (root / "runtime" / "python" / "python.exe").is_file() and (root / "app").is_dir()


def app_root_for(root: Path) -> Path:
    return root / "app" if package_is_extracted(root) else root


def python_for(root: Path) -> Path:
    candidate = root / "runtime" / "python" / "python.exe"
    if candidate.is_file():
        return candidate
    candidate = root / ".venv" / "Scripts" / "python.exe"
    if candidate.is_file():
        return candidate
    raise LauncherError("python_runtime_missing")


def provenance_for(root: Path) -> dict[str, Any]:
    path = root / "PACKAGE_PROVENANCE.json"
    return read_json(path) if path.is_file() else {}


def build_environment(root: Path, owner_token: str | None = None) -> dict[str, str]:
    app_root = app_root_for(root)
    local_data = root / ".local-data"
    model_dir = (
        root / "models" if package_is_extracted(root) else app_root / ".local-tools" / "models"
    )
    ffmpeg_dir = (
        root / "runtime" / "ffmpeg"
        if package_is_extracted(root)
        else app_root / ".local-tools" / "ffmpeg" / "bin"
    )
    python = python_for(root)
    ai_python = python
    if not package_is_extracted(root):
        legacy_ai_python = app_root / ".venv-ai" / "Scripts" / "python.exe"
        if legacy_ai_python.is_file():
            ai_python = legacy_ai_python
    ai_site_packages = ai_python.parent.parent / "Lib" / "site-packages"
    if package_is_extracted(root):
        ai_site_packages = python.parent / "Lib" / "site-packages"
    provenance = provenance_for(root)
    source = provenance.get("source", {}) if isinstance(provenance.get("source"), dict) else {}
    release_path = app_root / "release.json"
    release = read_json(release_path) if release_path.is_file() else {}
    release_version = str(
        release.get("release_version") or provenance.get("package_version") or "unknown"
    )
    commit_sha = str(source.get("head_sha") or os.getenv("TVA_GIT_COMMIT_SHA", "unknown"))
    environment = os.environ.copy()
    environment.update(
        {
            "TVA_PACKAGE_ROOT": str(root),
            "TVA_SOURCE_ROOT": str(app_root),
            "TVA_APP_ROOT": str(app_root),
            "TVA_PYTHON": str(python),
            "TVA_AI_PYTHON": str(ai_python),
            "TVA_AI_SITE_PACKAGES": str(ai_site_packages),
            "TVA_MODEL_DIR": str(model_dir),
            "TVA_FFMPEG_DIR": str(ffmpeg_dir),
            "TVA_FRONTEND_DIST_DIR": str(
                root / "frontend" / "dist"
                if package_is_extracted(root)
                else app_root / "apps" / "frontend" / "dist"
            ),
            "TVA_LOCAL_DATA_DIR": str(local_data),
            "TVA_DB_PATH": str(local_data / "tva.sqlite3"),
            "TVA_EXPORT_ROOT": str(local_data / "exports"),
            "TVA_RELEASE_FILE": str(release_path),
            "TVA_RELEASE_VERSION": release_version,
            "TVA_GIT_COMMIT_SHA": commit_sha,
            "TVA_FRONTEND_URL": f"http://127.0.0.1:{FRONTEND_PORT}",
            "VITE_API_BASE_URL": "http://127.0.0.1:8000",
            "TVA_WORKER_HEARTBEAT_TTL_SECONDS": "5",
        }
    )
    if owner_token:
        environment["TVA_OWNER_TOKEN"] = owner_token
    existing_python_path = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = str(app_root) + (
        os.pathsep + existing_python_path if existing_python_path else ""
    )
    return environment


def runtime_dir(root: Path) -> Path:
    path = root / ".local-data" / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir(root: Path) -> Path:
    path = root / ".local-data" / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def record_path(root: Path, role: str) -> Path:
    return runtime_dir(root) / f"{role}.json"


def read_record(root: Path, role: str) -> dict[str, Any] | None:
    path = record_path(root, role)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def write_record(root: Path, role: str, payload: dict[str, Any]) -> None:
    destination = record_path(root, role)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, destination)


def remove_record(root: Path, role: str) -> None:
    try:
        record_path(root, role).unlink()
    except FileNotFoundError:
        pass


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(0.25)
        try:
            client.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


def http_json(url: str) -> dict[str, Any] | None:
    try:
        request = Request(url, headers={"User-Agent": "TrafficVideoAnalytics-portable-launcher/1"})
        with urlopen(request, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return None


def http_ready(url: str) -> bool:
    try:
        request = Request(
            url, method="GET", headers={"User-Agent": "TrafficVideoAnalytics-portable-launcher/1"}
        )
        with urlopen(request, timeout=2) as response:
            return 200 <= response.status < 400
    except (OSError, URLError, TimeoutError):
        return False


def wait_until(predicate: Any, timeout_seconds: float, label: str) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.3)
    raise LauncherError(f"startup_timeout:{label}")


def owned(root: Path, role: str, record: dict[str, Any] | None = None) -> bool:
    value = record if record is not None else read_record(root, role)
    if value is None:
        return False
    expected = python_for(root)
    return is_owned_record(value, root, expected_role=role, expected_executable=expected)


def _cleanup_unrecorded_process(process: subprocess.Popen[bytes], role: str) -> None:
    """Terminate a child whose ownership proof could not be persisted."""

    try:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LauncherError(f"spawn_cleanup_failed:{role}:process_still_running:{exc}") from exc
    except OSError as exc:
        raise LauncherError(f"spawn_cleanup_failed:{role}:terminate_or_wait:{exc}") from exc


def spawn_child(
    *,
    root: Path,
    role: str,
    command: list[str],
    port: int | None,
    environment: dict[str, str],
    owner_token: str,
) -> subprocess.Popen[bytes]:
    log_root = logs_dir(root)
    log_root.mkdir(parents=True, exist_ok=True)
    stdout_path = log_root / f"{role}.out.log"
    stderr_path = log_root / f"{role}.err.log"
    stdout = stdout_path.open("ab")
    stderr = stderr_path.open("ab")
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200) | getattr(
            subprocess, "DETACHED_PROCESS", 0x00000008
        )
    try:
        process = subprocess.Popen(
            command,
            cwd=app_root_for(root),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            close_fds=True,
            creationflags=creationflags,
        )
    finally:
        stdout.close()
        stderr.close()
    try:
        deadline = time.monotonic() + 3
        identity = None
        command_line = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            identity = process_identity(process.pid)
            if identity is not None and process.poll() is None:
                observed = process_command_line(process.pid)
                if observed and process.poll() is None:
                    command_line = observed
                    break
            time.sleep(0.05)
        if identity is None:
            raise LauncherError(f"process_identity_unavailable:{role}")
        if command_line is None:
            raise LauncherError(f"process_command_line_unavailable:{role}")
        write_record(
            root,
            role,
            record_process(
                identity=identity,
                package_root=root,
                role=role,
                command_line=command_line,
                port=port,
                owner_token=owner_token,
            ),
        )
        return process
    except Exception as exc:
        try:
            _cleanup_unrecorded_process(process, role)
        except LauncherError as cleanup_error:
            raise cleanup_error from exc
        raise


def stop_owned(root: Path, verbose: bool = True) -> bool:
    all_safe = True
    for role in reversed(ROLES):
        record = read_record(root, role)
        if record is None:
            continue
        pid = record.get("pid")
        if not isinstance(pid, int):
            if verbose:
                print(f"{role}: invalid record left untouched")
            all_safe = False
            continue
        result = stop_owned_process(
            record,
            root,
            expected_role=role,
            expected_executable=python_for(root),
            timeout_seconds=5,
        )
        if result.safe:
            remove_record(root, role)
            if verbose:
                print(f"{role}: {result.status}")
        else:
            if verbose:
                print(f"{role}: PID {pid} stop unsafe ({result.status}); left untouched")
            all_safe = False
    return all_safe


def duplicate_instance(root: Path) -> bool:
    records = [read_record(root, role) for role in ROLES]
    if not any(records):
        return False
    if all(owned(root, role, record) for role, record in zip(ROLES, records)):
        health = http_json(f"http://127.0.0.1:{BACKEND_PORT}/api/v1/health")
        readiness = http_json(f"http://127.0.0.1:{BACKEND_PORT}/api/v1/readiness")
        return bool(
            health
            and health.get("status") == "ok"
            and readiness
            and readiness.get("application_ready")
        )
    if not stop_owned(root):
        raise LauncherError("existing_instance_cleanup_unsafe")
    return False


def run_instance(root: Path, no_browser: bool) -> int:
    if duplicate_instance(root):
        print("Traffic Video Analytics is already running for this extracted package.")
        if not no_browser:
            webbrowser.open(f"http://127.0.0.1:{FRONTEND_PORT}")
        return 0
    for port in (BACKEND_PORT, FRONTEND_PORT):
        if port_open(port):
            raise LauncherError(f"port_conflict:{port}:non_owned_process")
    app_root = app_root_for(root)
    frontend_dist = (
        root / "frontend" / "dist"
        if package_is_extracted(root)
        else app_root / "apps" / "frontend" / "dist"
    )
    if not (frontend_dist / "index.html").is_file():
        raise LauncherError(f"frontend_dist_missing:{frontend_dist}")
    python = python_for(root)
    environment = build_environment(root, secrets.token_hex(16))
    owner_token = environment["TVA_OWNER_TOKEN"]
    local_data = root / ".local-data"
    local_data.mkdir(parents=True, exist_ok=True)
    worker_id = f"portable-worker-{socket.gethostname()}"
    worker_instance_token = secrets.token_hex(24)
    environment.update(
        {
            "TVA_WORKER_ID": worker_id,
            "TVA_WORKER_INSTANCE_TOKEN": worker_instance_token,
        }
    )
    backend_command = [
        str(python),
        "-m",
        "uvicorn",
        "apps.backend.app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(BACKEND_PORT),
    ]
    worker_command = [
        str(python),
        "-m",
        "apps.worker.processing_worker",
        "--db",
        str(local_data / "tva.sqlite3"),
        "--worker-id",
        worker_id,
        "--instance-token",
        worker_instance_token,
    ]
    static_command = [
        str(python),
        str(app_root / "scripts" / "portable" / "static_server.py"),
        "--root",
        str(frontend_dist),
        "--port",
        str(FRONTEND_PORT),
    ]
    try:
        spawn_child(
            root=root,
            role="backend",
            command=backend_command,
            port=BACKEND_PORT,
            environment=environment,
            owner_token=owner_token,
        )
        wait_until(
            lambda: bool(http_json(f"http://127.0.0.1:{BACKEND_PORT}/api/v1/health")),
            30,
            "backend_health",
        )
        spawn_child(
            root=root,
            role="frontend",
            command=static_command,
            port=FRONTEND_PORT,
            environment=environment,
            owner_token=owner_token,
        )
        wait_until(lambda: http_ready(f"http://127.0.0.1:{FRONTEND_PORT}/"), 15, "frontend_static")
        spawn_child(
            root=root,
            role="worker",
            command=worker_command,
            port=None,
            environment=environment,
            owner_token=owner_token,
        )
        wait_until(
            lambda: bool(
                (readiness := http_json(f"http://127.0.0.1:{BACKEND_PORT}/api/v1/readiness"))
                and readiness.get("application_ready")
                and readiness.get("processing", {}).get("real_inference", {}).get("ready")
            ),
            90,
            "application_real_readiness",
        )
    except Exception as exc:
        if not stop_owned(root):
            raise LauncherError("startup_cleanup_unsafe") from exc
        raise
    print(f"Backend:  http://127.0.0.1:{BACKEND_PORT}/api/v1/health")
    print(f"Frontend: http://127.0.0.1:{FRONTEND_PORT}")
    print("REAL_VIDEO readiness: PASS")
    if not no_browser:
        webbrowser.open(f"http://127.0.0.1:{FRONTEND_PORT}")
    return 0


def ensure_stopped(root: Path) -> None:
    for role in ROLES:
        record = read_record(root, role)
        if (
            record
            and isinstance(record.get("pid"), int)
            and process_identity(record["pid"]) is not None
        ):
            if owned(root, role, record):
                raise LauncherError(f"stop_required_before_data_operation:{role}")
            raise LauncherError(f"non_owned_process_record_present:{role}")


def data_operation(root: Path, operation: str, backup: Path | None = None) -> int:
    ensure_stopped(root)
    python = python_for(root)
    app_root = app_root_for(root)
    script = app_root / "scripts" / "database_backup.py"
    local_data = root / ".local-data"
    database = local_data / "tva.sqlite3"
    if operation == "backup":
        destination = (
            local_data / "backups" / f"tva-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.sqlite3"
        )
        command = [
            str(python),
            str(script),
            "backup",
            "--database",
            str(database),
            "--destination",
            str(destination),
        ]
    else:
        if backup is None:
            raise LauncherError("restore_requires_backup_path")
        command = [
            str(python),
            str(script),
            "restore",
            "--database",
            str(database),
            "--backup",
            str(backup.resolve()),
        ]
    environment = build_environment(root)
    result = subprocess.run(command, cwd=app_root, env=environment, check=False)
    return result.returncode


def open_guide(root: Path) -> int:
    guide = root / "คู่มือการใช้งาน.html"
    if not guide.is_file():
        raise LauncherError("guide_missing")
    if hasattr(os, "startfile"):
        os.startfile(str(guide))  # type: ignore[attr-defined]
    else:
        webbrowser.open(guide.as_uri())
    return 0


def env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Traffic Video Analytics portable lifecycle launcher."
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--no-browser", action="store_true")
    subparsers.add_parser("stop")
    subparsers.add_parser("backup")
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("backup", type=Path)
    subparsers.add_parser("open-guide")
    args = parser.parse_args()
    root = discover_package_root()
    try:
        if args.operation == "run":
            return run_instance(root, bool(args.no_browser or env_flag("TVA_NO_BROWSER")))
        if args.operation == "stop":
            return 0 if stop_owned(root) else 2
        if args.operation == "backup":
            return data_operation(root, "backup")
        if args.operation == "restore":
            return data_operation(root, "restore", args.backup)
        return open_guide(root)
    except LauncherError as exc:
        print(f"PORTABLE_LAUNCHER_BLOCKED: {exc}", file=sys.stderr)
        return 2
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"PORTABLE_LAUNCHER_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
