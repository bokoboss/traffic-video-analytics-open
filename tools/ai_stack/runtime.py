from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from tools.benchmark.runtime import gpu_status


@dataclass(frozen=True)
class AiPackageStatus:
    package: str
    required_version: str | None
    installed: bool
    installed_version: str | None
    matches_required: bool


@dataclass(frozen=True)
class WeightStatus:
    model_id: str
    filename: str | None
    expected_sha256: str | None
    path: str | None
    present: bool
    actual_sha256: str | None
    matches_expected: bool | None


@dataclass(frozen=True)
class AiRuntimeStatus:
    schema_version: str
    python: str
    python_executable: str
    platform: str
    ai_venv_expected: str
    models_dir: str
    artifacts_dir: str
    packages: tuple[AiPackageStatus, ...]
    weights: tuple[WeightStatus, ...]
    torch: dict
    cpu_provider_available: bool
    gpu_provider_available: bool
    gpu: dict
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]


def ai_python(root: Path) -> Path:
    return root / ".venv-ai" / "Scripts" / "python.exe"


def package_status(package: str, required_version: str | None, python_executable: Path | None = None) -> AiPackageStatus:
    if python_executable is not None and python_executable.exists():
        code = (
            "import importlib.metadata, json, sys\n"
            f"pkg={package!r}\n"
            "try:\n"
            "    v=importlib.metadata.version(pkg)\n"
            "    print(json.dumps({'installed': True, 'version': v}))\n"
            "except importlib.metadata.PackageNotFoundError:\n"
            "    print(json.dumps({'installed': False, 'version': None}))\n"
        )
        try:
            result = subprocess.run(
                [str(python_executable), "-c", code],
                check=False,
                text=True,
                capture_output=True,
                timeout=20,
            )
            payload = json.loads(result.stdout.strip() or "{}")
            installed = bool(payload.get("installed"))
            installed_version = payload.get("version")
            normalized_installed = str(installed_version).split("+", 1)[0] if installed_version is not None else None
            return AiPackageStatus(
                package,
                required_version,
                installed,
                str(installed_version) if installed_version is not None else None,
                installed and (required_version is None or required_version in {installed_version, normalized_installed}),
            )
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return AiPackageStatus(package, required_version, False, "probe_failed", False)
    try:
        installed_version = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return AiPackageStatus(package, required_version, False, None, False)
    normalized_installed = installed_version.split("+", 1)[0]
    return AiPackageStatus(
        package,
        required_version,
        True,
        installed_version,
        required_version is None or required_version in {installed_version, normalized_installed},
    )


def torch_status(python_executable: Path) -> dict:
    if not python_executable.exists():
        return {"available": False, "error_code": "ai_environment_missing"}
    code = r"""
import json
try:
    import torch
    payload = {
        "available": True,
        "version": getattr(torch, "__version__", None),
        "cuda_runtime": getattr(torch.version, "cuda", None),
        "cuda_available": bool(torch.cuda.is_available()),
        "cuda_device_count": int(torch.cuda.device_count()),
        "devices": [],
    }
    for index in range(torch.cuda.device_count()):
        props = torch.cuda.get_device_properties(index)
        payload["devices"].append({
            "index": index,
            "name": torch.cuda.get_device_name(index),
            "capability": list(torch.cuda.get_device_capability(index)),
            "total_memory_mib": int(props.total_memory // (1024 * 1024)),
        })
    print(json.dumps(payload))
except Exception as exc:
    print(json.dumps({"available": False, "error_code": "torch_probe_failed", "error": str(exc)}))
"""
    try:
        result = subprocess.run(
            [str(python_executable), "-c", code],
            check=False,
            text=True,
            capture_output=True,
            timeout=30,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        return json.loads(result.stdout.strip() or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return {"available": False, "error_code": "torch_probe_invocation_failed", "error": str(exc)}


def python_version(python_executable: Path) -> str:
    if not python_executable.exists():
        return platform.python_version()
    try:
        result = subprocess.run(
            [str(python_executable), "-c", "import platform; print(platform.python_version())"],
            check=False,
            text=True,
            capture_output=True,
            timeout=10,
        )
        return result.stdout.strip() or platform.python_version()
    except (OSError, subprocess.TimeoutExpired):
        return platform.python_version()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_status(root: Path, registry: dict) -> AiRuntimeStatus:
    python_executable = ai_python(root)
    models_dir = root / ".local-tools" / "models"
    artifacts_dir = root / ".local-data" / "ai-artifacts"
    primary_runtime_states = {"approved primary pilot", "technical evaluation only"}
    primary_readiness_states = {
        "runtime_ready_after_preparation",
        "provisional_until_real_smoke_passes",
        "runtime_smoke_qualified",
    }
    required_packages = {
        str(record["package"]): str(record["exact_version"])
        for record in registry.get("models", [])
        if str(record.get("package", "")).lower() in {"ultralytics", "trackers"}
        and str(record.get("approval_state")) in primary_runtime_states
        and str(record.get("readiness_state")) in primary_readiness_states
    }
    required_packages.update(
        {
            "numpy": "2.2.6",
            "opencv-python": "4.12.0.88",
            "pillow": "12.3.0",
            "supervision": "0.29.1",
            "torch": "2.10.0",
            "torchvision": "0.25.0",
        }
    )
    packages = tuple(
        package_status(package, version, python_executable) for package, version in sorted(required_packages.items())
    )

    weights: list[WeightStatus] = []
    for record in registry.get("models", []):
        filename = record.get("model_filename")
        if not filename:
            weights.append(WeightStatus(str(record.get("model_id")), None, record.get("sha256"), None, False, None, None))
            continue
        path = models_dir / str(filename)
        actual = file_sha256(path) if path.exists() else None
        expected = record.get("sha256")
        weights.append(
            WeightStatus(
                str(record.get("model_id")),
                str(filename),
                expected,
                str(path),
                path.exists(),
                actual,
                None if expected is None or actual is None else actual == expected,
            )
        )

    blockers = []
    warnings = []
    if not python_executable.exists():
        blockers.append("ai_environment_missing")
    for package in packages:
        if not package.installed:
            blockers.append(f"package_missing:{package.package}")
        elif not package.matches_required:
            blockers.append(f"package_version_mismatch:{package.package}")
    for weight in weights:
        if weight.filename and not weight.present and weight.model_id == "detector.ultralytics-yolo11n-coco":
            blockers.append(f"weight_missing:{weight.model_id}")
        if weight.matches_expected is False:
            blockers.append(f"weight_hash_mismatch:{weight.model_id}")
        if weight.expected_sha256 is None and weight.filename:
            warnings.append(f"weight_hash_not_recorded:{weight.model_id}")

    gpu = gpu_status()
    torch = torch_status(python_executable)
    cpu_provider = any(package.installed for package in packages if package.package in {"ultralytics", "mmdet"})
    gpu_provider = bool(torch.get("cuda_available"))
    if gpu.available and not gpu_provider:
        warnings.append("gpu_detected_without_ai_provider")
    if not gpu.available:
        warnings.append(gpu.error_code or "gpu_unavailable")

    return AiRuntimeStatus(
        schema_version="ai-runtime-status-v1",
        python=python_version(python_executable),
        python_executable=str(python_executable),
        platform=platform.platform(),
        ai_venv_expected=str(root / ".venv-ai"),
        models_dir=str(models_dir),
        artifacts_dir=str(artifacts_dir),
        packages=packages,
        weights=tuple(weights),
        torch=torch,
        cpu_provider_available=cpu_provider,
        gpu_provider_available=gpu_provider,
        gpu=asdict(gpu),
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def runtime_status_json(root: Path, registry: dict) -> str:
    return json.dumps(asdict(runtime_status(root, registry)), indent=2, ensure_ascii=False)
