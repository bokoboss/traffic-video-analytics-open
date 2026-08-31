from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.backend.app.media import media_runtime_status  # noqa: E402
from scripts.node_runtime import frontend_runtime_status  # noqa: E402


@dataclass(frozen=True)
class RuntimeReadiness:
    support_level: str
    python: str
    node: str | None
    node_source: str
    node_executable: str | None
    pnpm: str | None
    pnpm_executable: str | None
    backend_import_ready: bool
    frontend_lockfile_present: bool
    frontend_setup_complete: bool
    normal_launch_supported: bool
    ffmpeg: dict[str, Any]
    ffprobe: dict[str, Any]
    cpu_architecture: str
    total_ram_gb: float | None
    gpu: dict[str, Any]
    cpu_benchmark_supported: bool
    gpu_benchmark_supported: bool
    warnings: list[str]


def command_version(command: str) -> str | None:
    executable = resolve_command(command)
    if executable is None:
        return None
    try:
        result = subprocess.run([executable, "--version"], text=True, capture_output=True, check=False, timeout=10)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.splitlines()[0] if result.stdout else None


def resolve_command(command: str) -> str | None:
    executable = shutil.which(command)
    if executable is not None:
        return executable
    if command == "node":
        pnpm = shutil.which("pnpm")
        if pnpm is not None:
            candidate = Path(pnpm).resolve().parents[2] / "node" / "bin" / "node.exe"
            if candidate.exists():
                return str(candidate)
    return None


def nvidia_status() -> dict[str, Any]:
    if shutil.which("nvidia-smi") is None:
        return {"available": False, "vendor": None, "model": None, "driver_version": None}
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return {"available": False, "vendor": "NVIDIA", "model": None, "driver_version": None}
    first = result.stdout.splitlines()[0].split(",")
    return {
        "available": True,
        "vendor": "NVIDIA",
        "model": first[0].strip(),
        "driver_version": first[1].strip() if len(first) > 1 else None,
    }


def total_ram_gb() -> float | None:
    if platform.system() == "Windows":
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        try:
            return round(int(result.stdout.strip()) / (1024**3), 2)
        except (TypeError, ValueError):
            return None
    return None


def backend_import_ready() -> bool:
    try:
        __import__("apps.backend.app.main")
        return True
    except Exception:
        return False


def readiness() -> RuntimeReadiness:
    media = media_runtime_status()
    frontend_runtime = frontend_runtime_status(ROOT)
    node = frontend_runtime.node.version if frontend_runtime.node else None
    node_source = frontend_runtime.node.source_label if frontend_runtime.node else "node_missing"
    node_executable = frontend_runtime.node.executable if frontend_runtime.node else None
    pnpm = frontend_runtime.pnpm.version
    pnpm_executable = frontend_runtime.pnpm.executable
    backend_ready = backend_import_ready()
    frontend_lockfile = (ROOT / "pnpm-lock.yaml").exists()
    gpu = nvidia_status()
    warnings: list[str] = []
    if not media.reference_frame_extraction_available:
        warnings.append("media_runtime_incomplete")
    if not gpu["available"]:
        warnings.append("gpu_not_detected")
    warnings.extend(frontend_runtime.warnings)
    if node is None or pnpm is None or not backend_ready or not frontend_lockfile:
        support = "unsupported_or_incomplete"
    elif media.reference_frame_extraction_available:
        support = "media_runtime_ready"
    else:
        support = "app_review_ready"
    return RuntimeReadiness(
        support_level=support,
        python=platform.python_version(),
        node=node,
        node_source=node_source,
        node_executable=node_executable,
        pnpm=pnpm,
        pnpm_executable=pnpm_executable,
        backend_import_ready=backend_ready,
        frontend_lockfile_present=frontend_lockfile,
        frontend_setup_complete=frontend_runtime.setup_complete,
        normal_launch_supported=frontend_runtime.normal_launch_supported and backend_ready and frontend_lockfile,
        ffmpeg=asdict(media.ffmpeg),
        ffprobe=asdict(media.ffprobe),
        cpu_architecture=platform.machine(),
        total_ram_gb=total_ram_gb(),
        gpu=gpu,
        cpu_benchmark_supported=True,
        gpu_benchmark_supported=bool(gpu["available"]),
        warnings=warnings,
    )


def main() -> int:
    print(json.dumps(asdict(readiness()), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
