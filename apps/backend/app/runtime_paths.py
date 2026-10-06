"""Explicit runtime path resolution for development and portable packages.

The application keeps the existing repository-local defaults for developer
workflows.  A portable launcher supplies the ``TVA_*`` values before starting
the backend and worker so no path is inferred from the machine that built the
package.
"""

from __future__ import annotations

import os
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[3]


def _configured_path(name: str) -> Path | None:
    value = os.getenv(name, "").strip()
    return Path(value).expanduser().resolve() if value else None


def source_root(default: Path | None = None) -> Path:
    """Return the source/app root containing ``model_registry.json``."""

    return (
        _configured_path("TVA_SOURCE_ROOT")
        or _configured_path("TVA_APP_ROOT")
        or (default or SOURCE_ROOT)
    )


def package_root(default: Path | None = None) -> Path:
    """Return the extracted package root when the launcher supplies it."""

    return _configured_path("TVA_PACKAGE_ROOT") or (default or source_root()).resolve()


def local_data_dir(default: Path | None = None) -> Path:
    return (
        _configured_path("TVA_LOCAL_DATA_DIR")
        or ((default or source_root()) / ".local-data").resolve()
    )


def model_dir(root: Path | None = None) -> Path:
    return (
        _configured_path("TVA_MODEL_DIR")
        or ((root or source_root()) / ".local-tools" / "models").resolve()
    )


def ai_python(root: Path | None = None) -> Path:
    configured = _configured_path("TVA_AI_PYTHON") or _configured_path("TVA_PYTHON")
    if configured is not None:
        return configured
    base = root or source_root()
    return (base / ".venv-ai" / "Scripts" / "python.exe").resolve()


def ai_site_packages(root: Path | None = None) -> tuple[Path, ...]:
    configured = _configured_path("TVA_AI_SITE_PACKAGES")
    if configured is not None:
        return (configured,)
    base = root or source_root()
    candidates = [base / ".venv-ai" / "Lib" / "site-packages"]
    candidates.extend((base / ".venv-ai" / "lib").glob("python*/site-packages"))
    return tuple(path.resolve() for path in candidates)


def frontend_dist_dir(default: Path | None = None) -> Path:
    return (
        _configured_path("TVA_FRONTEND_DIST_DIR")
        or ((default or package_root()) / "frontend" / "dist").resolve()
    )


def release_file(default_root: Path | None = None) -> Path:
    return (
        _configured_path("TVA_RELEASE_FILE")
        or (source_root(default_root) / "release.json").resolve()
    )
