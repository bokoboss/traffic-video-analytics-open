from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
LOCAL_DATA = Path(os.getenv("TVA_LOCAL_DATA_DIR", ROOT / ".local-data")).expanduser().resolve()


PRIVATE_KEY_NAMES = {
    "executable", "python_executable", "node_executable", "pnpm_executable", "path",
    "root", "models_dir", "ai_venv_expected", "managed_media_path", "source_path",
    "preview_path", "database_path", "local_data_dir",
}
PRIVATE_STRING = re.compile(r"(?i)(?:[A-Z]:\\|/Users/|/home/|/private/)[^\s\"']+")
TOKEN_STRING = re.compile(r"(?i)(bearer\s+|token[=:]\s*|secret[=:]\s*|password[=:]\s*)[^\s,\"']+")


def sanitize(value: Any, key: str = "") -> Any:
    if key.lower() in PRIVATE_KEY_NAMES:
        return "<redacted-local-path>"
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {str(item_key): sanitize(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item, key) for item in value]
    if isinstance(value, str):
        return TOKEN_STRING.sub(r"\1<redacted>", PRIVATE_STRING.sub("<redacted-local-path>", value))
    return value


def command_version(command: str) -> str | None:
    executable = shutil.which(command)
    if not executable:
        return None
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, text=True, check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip().splitlines()[0] if result.stdout.strip() else None


def recent_logs(limit: int = 6) -> dict[str, str]:
    logs: dict[str, str] = {}
    log_root = LOCAL_DATA / "logs"
    if not log_root.is_dir():
        return logs
    candidates = sorted(
        (path for path in log_root.iterdir() if path.is_file() and path.suffix.lower() in {".log", ".jsonl", ".txt"}),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:limit]
    for path in candidates:
        try:
            logs[path.name] = sanitize(path.read_text(encoding="utf-8", errors="replace")[-20_000:])
        except OSError:
            logs[path.name] = "<log_unreadable>"
    return logs


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a sanitized local support bundle.")
    parser.add_argument("--acknowledge", action="store_true", help="Acknowledge that source media and derived artifacts are excluded.")
    parser.add_argument("--output", type=Path, help="Destination zip; defaults to .local-data/diagnostics.")
    args = parser.parse_args()
    if not args.acknowledge:
        print("Support bundles exclude source media, derived evidence, exports, credentials, and private local paths. Re-run with --acknowledge.")
        return 2

    from apps.backend.app.release import release_identity
    from scripts.runtime_readiness import readiness

    LOCAL_DATA.mkdir(parents=True, exist_ok=True)
    destination = (args.output or LOCAL_DATA / "diagnostics" / f"tva-support-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.zip").expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "support-bundle-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "release": release_identity(),
        "platform": {"system": platform.system(), "release": platform.release(), "machine": platform.machine(), "python": sys.version.split()[0]},
        "runtime_versions": {name: command_version(name) for name in ("node", "pnpm", "ffmpeg", "ffprobe", "nvidia-smi")},
        "runtime_readiness": sanitize(asdict(readiness())),
        "environment_flags": {key: "set" for key in ("TRAFFIC_APP_DIAGNOSTICS", "TVA_NO_BROWSER", "TVA_FFMPEG_DIR") if os.getenv(key)},
        "recent_logs": recent_logs(),
        "omitted": ["source videos", "preview and evidence media", "exports", "SQLite database contents", "model weights", "credentials", "absolute local paths"],
    }
    manifest = json.dumps(sanitize(payload), indent=2, ensure_ascii=False) + "\n"
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("support_manifest.json", manifest)
        bundle.writestr("README.txt", "Sanitized Traffic Video Analytics pilot support bundle.\nSource media, derived artifacts, exports, credentials, database contents and absolute local paths are intentionally excluded.\n")
    print(json.dumps({"status": "created", "filename": destination.name, "size_bytes": destination.stat().st_size}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
