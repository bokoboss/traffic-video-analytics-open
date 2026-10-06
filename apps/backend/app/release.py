from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
RELEASE_FILE = REPOSITORY_ROOT / "release.json"


def _git_commit_sha() -> str:
    configured = os.getenv("TVA_GIT_COMMIT_SHA") or os.getenv("TVA_GIT_SHA")
    if configured and len(configured.strip()) <= 64:
        return configured.strip()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_release_root(),
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    value = result.stdout.strip()
    return value if result.returncode == 0 and len(value) <= 64 else "unknown"


def _release_root() -> Path:
    configured = os.getenv("TVA_SOURCE_ROOT") or os.getenv("TVA_APP_ROOT")
    return Path(configured).expanduser().resolve() if configured else REPOSITORY_ROOT


def _release_file() -> Path:
    configured = os.getenv("TVA_RELEASE_FILE")
    return Path(configured).expanduser().resolve() if configured else _release_root() / "release.json"


def release_identity() -> dict[str, Any]:
    """Return operator-visible identity without exposing local filesystem paths."""

    payload: dict[str, Any] = {
        "schema_version": "release-identity-v1",
        "application_name": "Traffic Video Analytics",
        "release_version": "0.1.0-pilot",
        "release_channel": "pilot",
        "build_label": "Pilot Release",
    }
    try:
        loaded = json.loads(_release_file().read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            for key in ("schema_version", "application_name", "release_version", "release_channel", "build_label"):
                if isinstance(loaded.get(key), str) and loaded[key].strip():
                    payload[key] = loaded[key].strip()
    except (OSError, json.JSONDecodeError):
        payload["identity_warning"] = "release_file_unavailable"
    payload["git_commit_sha"] = _git_commit_sha()
    return payload
