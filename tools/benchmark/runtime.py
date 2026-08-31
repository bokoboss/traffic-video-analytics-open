from __future__ import annotations

import importlib.util
import json
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from apps.backend.app.media import media_runtime_status


@dataclass(frozen=True)
class PythonPackageStatus:
    name: str
    available: bool
    version: str | None


@dataclass(frozen=True)
class ExecutableStatus:
    name: str
    available: bool
    path: str | None
    version: str | None
    error_code: str | None


@dataclass(frozen=True)
class GpuStatus:
    available: bool
    name: str | None
    driver_version: str | None
    cuda_driver_version: str | None
    memory_total_mib: int | None
    memory_free_mib: int | None
    error_code: str | None = None


@dataclass(frozen=True)
class BenchmarkRuntimeStatus:
    schema_version: str
    python: str
    python_executable: str
    platform: str
    benchmark_requirements_file: str
    ffmpeg: ExecutableStatus
    ffprobe: ExecutableStatus
    gpu: GpuStatus
    packages: dict[str, PythonPackageStatus]
    cpu_inference_available: bool
    gpu_inference_available: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]


def executable_status(name: str, timeout_seconds: int = 10) -> ExecutableStatus:
    if name in {"ffmpeg", "ffprobe"}:
        media = media_runtime_status(timeout_seconds)
        status = media.ffmpeg if name == "ffmpeg" else media.ffprobe
        return ExecutableStatus(name, status.available, status.executable, status.version, status.error_code)
    path = shutil.which(name)
    if path is None:
        return ExecutableStatus(name, False, None, None, f"{name}_missing")
    try:
        result = subprocess.run(
            [path, "-version"],
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
        )
    except PermissionError:
        return ExecutableStatus(name, False, path, None, "permission_failure")
    except subprocess.TimeoutExpired:
        return ExecutableStatus(name, False, path, None, "timeout")
    except OSError:
        return ExecutableStatus(name, False, path, None, "invocation_failure")
    if result.returncode != 0:
        return ExecutableStatus(name, False, path, None, "invocation_failure")
    return ExecutableStatus(name, True, path, result.stdout.splitlines()[0] if result.stdout else None, None)


def package_status(name: str) -> PythonPackageStatus:
    if importlib.util.find_spec(name) is None:
        return PythonPackageStatus(name, False, None)
    try:
        module = __import__(name)
    except Exception:
        return PythonPackageStatus(name, False, "import_failed")
    version = getattr(module, "__version__", None)
    return PythonPackageStatus(name, True, str(version) if version is not None else None)


def gpu_status(timeout_seconds: int = 10) -> GpuStatus:
    if shutil.which("nvidia-smi") is None:
        return GpuStatus(False, None, None, None, None, None, "nvidia_smi_missing")
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
        )
        cuda = subprocess.run(
            ["nvidia-smi"],
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return GpuStatus(False, None, None, None, None, None, "nvidia_smi_timeout")
    except OSError:
        return GpuStatus(False, None, None, None, None, None, "nvidia_smi_invocation_failure")
    if result.returncode != 0 or not result.stdout.strip():
        return GpuStatus(False, None, None, None, None, None, "nvidia_smi_failed")
    fields = [item.strip() for item in result.stdout.splitlines()[0].split(",")]
    cuda_driver = None
    marker = "CUDA Version:"
    if cuda.stdout and marker in cuda.stdout:
        cuda_driver = cuda.stdout.split(marker, 1)[1].split()[0]
    return GpuStatus(
        available=True,
        name=fields[0] if len(fields) > 0 else None,
        driver_version=fields[1] if len(fields) > 1 else None,
        cuda_driver_version=cuda_driver,
        memory_total_mib=_int_or_none(fields[2]) if len(fields) > 2 else None,
        memory_free_mib=_int_or_none(fields[3]) if len(fields) > 3 else None,
    )


def benchmark_runtime_status(root: Path) -> BenchmarkRuntimeStatus:
    ffmpeg = executable_status("ffmpeg")
    ffprobe = executable_status("ffprobe")
    gpu = gpu_status()
    packages = {name: package_status(name) for name in ("torch", "onnxruntime", "cv2", "numpy", "PIL")}
    cpu_runtime = packages["torch"].available or packages["onnxruntime"].available
    gpu_runtime = False
    warnings: list[str] = []
    blockers: list[str] = []
    if not ffmpeg.available:
        blockers.append("ffmpeg_missing")
    if not ffprobe.available:
        blockers.append("ffprobe_missing")
    if not cpu_runtime:
        blockers.append("no_cpu_inference_runtime")
    if gpu.available and not gpu_runtime:
        warnings.append("gpu_detected_without_inference_provider")
    if not gpu.available:
        warnings.append(gpu.error_code or "gpu_unavailable")
    return BenchmarkRuntimeStatus(
        schema_version="benchmark-runtime-status-v1",
        python=platform.python_version(),
        python_executable=sys.executable,
        platform=platform.platform(),
        benchmark_requirements_file=str(root / "requirements-benchmark.txt"),
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        gpu=gpu,
        packages=packages,
        cpu_inference_available=cpu_runtime,
        gpu_inference_available=gpu_runtime,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


def runtime_status_json(root: Path) -> str:
    return json.dumps(asdict(benchmark_runtime_status(root)), indent=2, ensure_ascii=False)


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None
