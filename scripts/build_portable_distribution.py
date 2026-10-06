"""Build the full self-contained Windows x64 REAL_VIDEO candidate.

Omitted build roots use ignored paths inside the Git checkout. Explicit output,
cache, wheelhouse, and staging roots are independent caller choices. The
builder accepts only pinned, hash-verified artifacts and refuses to produce a
package when a native dependency remains unresolved.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import email.parser
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import uuid
import zipfile
import traceback
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

try:
    from scripts.portable.archive_safety import (
        ArchiveMemberError,
        safe_zip_infos,
        safe_zip_members as _safe_zip_members,
        extract_zip as _extract_zip,
    )
    from scripts.portable.privacy import (
        PRIVATE_MARKERS,
        SECRET_PATTERNS,
        _private_path_pattern,
        current_private_roots,
        packaged_provenance_absolute_path_violations,
        scan_payloads,
    )
    from scripts.portable.native_closure import MSVC_DLLS, scan_native_closure
except ModuleNotFoundError:  # direct execution: Python places scripts/ on sys.path
    from portable.archive_safety import (  # type: ignore[no-redef]
        ArchiveMemberError,
        safe_zip_infos,
        safe_zip_members as _safe_zip_members,
        extract_zip as _extract_zip,
    )
    from portable.privacy import (  # type: ignore[no-redef]
        PRIVATE_MARKERS,
        SECRET_PATTERNS,
        _private_path_pattern,
        current_private_roots,
        packaged_provenance_absolute_path_violations,
        scan_payloads,
    )
    from portable.native_closure import MSVC_DLLS, scan_native_closure  # type: ignore[no-redef]
try:
    from scripts.portable.runtime_lock import (
        RuntimeLockError,
        calculate_runtime_path_budget,
        enforce_runtime_path_budget,
        install_locked_runtime,
        load_runtime_lock,
        normalize_distribution_name,
    )
except ModuleNotFoundError:  # direct execution: Python places scripts/ on sys.path
    from portable.runtime_lock import (  # type: ignore[no-redef]
        RuntimeLockError,
        calculate_runtime_path_budget,
        enforce_runtime_path_budget,
        install_locked_runtime,
        load_runtime_lock,
        normalize_distribution_name,
    )


CPYTHON_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
CPYTHON_FILENAME = "python-3.12.10-embed-amd64.zip"
CPYTHON_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
CPYTHON_SOURCE_PAGE = "https://www.python.org/downloads/release/python-31210/"

FFMPEG_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
    "autobuild-2026-08-31-13-27/"
    "ffmpeg-n8.1.2-50-g1a748fe2cd-win64-lgpl-8.1.zip"
)
FFMPEG_FILENAME = "ffmpeg-n8.1.2-50-g1a748fe2cd-win64-lgpl-8.1.zip"
FFMPEG_SHA256 = "f6274bbd9c247f9e90c1bbed066b03ed4a3907cece2fb91be6dd352393936365"
FFMPEG_REPOSITORY = "https://github.com/BtbN/FFmpeg-Builds"
FFMPEG_TAG = "autobuild-2026-08-31-13-27"

MSVC_REDIST_URL = "https://aka.ms/vs/18/release/14.50.35719/VC_redist.x64.exe"
MSVC_REDIST_FILENAME = "VC_redist.x64-14.50.35719.exe"
MSVC_REDIST_SHA256 = "8995548dfffcde7c49987029c764355612ba6850ee09a7b6f0fddc85bdc5c280"
MSVC_FILE_SHA256 = {
    "concrt140.dll": "b2faf3b85b23c840b654e57d5497a0ad31acd02fb01856cad4725a1715d5f78e",
    "msvcp140.dll": "def46aa6a8f72f27bafac0c43334419486a4d1dcdb6c479a8ef7034b3e1fa4cb",
    "msvcp140_1.dll": "2dd670f874562fbdca5b022df1943d70a57ba91fde559280e3a1daebe4db2380",
    "msvcp140_2.dll": "1d60da3ac2b06482912ca852fa7047436e6e474b4cfffa3bf77f4598cfbf454c",
    "msvcp140_atomic_wait.dll": "e7963645e0d1db08e300614d4c5fa7194bd8173e9ab7a5558859e6b232ed3241",
    "msvcp140_codecvt_ids.dll": "ae8d922b00cdd93e3ebecc37beb46c800f383ebdeb9f9e5b84e04a72428b6fb3",
    "vccorlib140.dll": "6b8d8a76c3e6664293407553650e60b94df9aaafc7c92057ea83032bd228e44f",
    "vcruntime140.dll": "184146852727a9db4eea06178716bec3cdbb1015c911f6b0f915b184ad7775b2",
    "vcruntime140_1.dll": "e6bfb3662ab4b1969a73441dbe35c96d51441b6bff8cf1fe7430bd5b246ca605",
    "vcruntime140_threads.dll": "a6222020b500a9a86b36e040c2dbd0e459716db1bf2810a11cd7512ea9b8d89b",
}

MODEL_URL = "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt"
MODEL_FILENAME = "yolo11n.pt"
MODEL_SHA256 = "0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1"

AGPL_SOURCE_URL = "https://www.gnu.org/licenses/agpl-3.0.txt"
AGPL_APPROVED_SHA256 = "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0"
EXPECTED_PYTHON_DISTRIBUTIONS = 61
FRONTEND_RUNTIME_PACKAGE_VERSIONS = {
    "react": "19.2.0",
    "react-dom": "19.2.0",
    "lucide-react": "0.468.0",
    "scheduler": "0.27.0",
}
FRONTEND_EMITTED_CODE_COMPONENT_VERSIONS = {"vite": "7.1.12"}
VITE_MODULEPRELOAD_POLYFILL_SIZE = 714
VITE_MODULEPRELOAD_POLYFILL_SHA256 = "ba4f9e90492bda90f86b7630f8b7a654d0c749f678bd92034c281f924f5e96d5"
FFMPEG_STATUS = "READY_WITH_SOURCE_BUNDLE_REQUIRED"
MSVC_STATUS = "READY_WITH_LICENSED_USER_CONFIRMATION"
NVIDIA_NOTICE_STATUS = "READY_WITH_HUMAN_APPROVAL"
NVIDIA_CUDA_TERMS_URL = "https://docs.nvidia.com/cuda/archive/12.8.0/eula/index.html"
NVIDIA_CUDNN_TERMS_URL = "https://docs.nvidia.com/deeplearning/cudnn/backend/latest/reference/eula.html"
NVIDIA_TERMS_REFERENCE = "LICENSES/nvidia/NVIDIA_COMPONENT_TERMS.txt"
NVIDIA_COMPONENT_FAMILIES = (
    "CUDA runtime / cudart",
    "cuBLAS",
    "cuBLAS Lt",
    "cuDNN",
    "cuFFT",
    "CUPTI / Perfworks",
    "cuRAND",
    "cuSOLVER",
    "cuSPARSE",
    "nvJitLink",
    "NVRTC / NVRTC builtins",
    "NVToolsExt",
    "nvJPEG",
)
SUPPLEMENTAL_LICENSE_SCHEMA = "supplemental-python-licenses-v1"
SUPPLEMENTAL_LICENSE_MANIFEST_PATH = "LICENSES/python/SUPPLEMENTAL_LICENSES.json"
SUPPLEMENTAL_LICENSE_FIELDS = {
    "normalized_package_name",
    "version",
    "locked_wheel_filename",
    "locked_wheel_sha256",
    "license_path",
    "license_text_sha256",
    "source_url",
    "source_distribution_filename",
    "source_distribution_member",
    "source_text_section",
    "source_distribution_url",
    "source_distribution_sha256",
}

AI_REQUIREMENTS = {
    "torch": "2.10.0+cu128",
    "torchvision": "0.25.0+cu128",
    "ultralytics": "8.4.103",
    "trackers": "2.5.0.post0",
    "supervision": "0.29.1",
    "numpy": "2.2.6",
    "opencv-python": "4.12.0.88",
    "Pillow": "12.3.0",
}

# ``.pth`` is also Python's site-path configuration format and is present in
# otherwise clean runtimes.  The supported model contract is yolo11n.pt only.
MODEL_EXTENSIONS = {".pt", ".onnx", ".engine", ".safetensors", ".ckpt"}


class BuildError(RuntimeError):
    """A hard-gate build error with an operator-facing reason."""


BUILDER_STAGE_PREFIX = ".s-"
BUILDER_STAGE_NAME_RE = re.compile(r"\.s-[a-z0-9_]{8}")
LEGACY_STAGE_NAME_RE = re.compile(r"\..+\.stage-[a-z0-9_]+")


@dataclass(frozen=True)
class BuildPaths:
    project_root: Path
    output: Path
    cache_dir: Path
    wheelhouse: Path
    stage_root: Path


def _canonical_path(path: Path) -> Path:
    try:
        return path.expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise BuildError(f"path_resolution_failed:{path}") from exc


def _project_local_path(path: Path, project_root: Path, label: str) -> Path:
    canonical = _canonical_path(path)
    try:
        canonical.relative_to(project_root)
    except ValueError as exc:
        raise BuildError(f"path_outside_project:{label}:{canonical}") from exc
    return canonical


def resolve_build_paths(
    source: Path,
    build_mode: str,
    provisional_sha: str,
    *,
    output: Path | None = None,
    cache_dir: Path | None = None,
    wheelhouse: Path | None = None,
    stage_root: Path | None = None,
) -> BuildPaths:
    """Resolve each writable root independently and fail closed on local escapes."""
    project_root = _canonical_path(source)
    local_root = project_root / ".local-data"
    defaults = {
        "output": local_root / "portable-builds" / f"p2-{build_mode}-{provisional_sha}",
        "cache": local_root / "portable-cache",
        "wheelhouse": local_root / "portable-cache" / "pr73-wheelhouse",
        "stage": local_root / "s",
    }
    supplied = {
        "output": output,
        "cache": cache_dir,
        "wheelhouse": wheelhouse,
        "stage": stage_root,
    }
    resolved: dict[str, Path] = {}
    for label, value in supplied.items():
        candidate = value if value is not None else defaults[label]
        resolved[label] = (
            _canonical_path(candidate)
            if value is not None
            else _project_local_path(candidate, project_root, label)
        )
    return BuildPaths(
        project_root=project_root,
        output=resolved["output"],
        cache_dir=resolved["cache"],
        wheelhouse=resolved["wheelhouse"],
        stage_root=resolved["stage"],
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def portable_path_budget_provenance(path_budget: dict[str, Any]) -> dict[str, Any]:
    """Keep measured path evidence without packaging machine-specific roots."""
    scalar_fields = (
        "status",
        "path_limit",
        "path_limit_basis",
        "path_utf16_code_units",
        "path_units_with_nul",
        "max_projected_path_length",
        "stage_root_utf16_code_units",
        "stage_root_length",
        "effective_site_packages_utf16_code_units",
        "effective_site_packages_length",
        "projected_member_count",
    )
    deepest_fields = (
        "distribution",
        "wheel_filename",
        "wheel_member",
        "materialization",
        "install_scheme",
        "materialized_target",
        "path_utf16_code_units",
        "path_units_with_nul",
        "path_length",
    )
    deepest = path_budget.get("deepest_member")
    if not isinstance(deepest, dict):
        raise BuildError("runtime_path_budget_deepest_member_invalid")
    return {
        **{key: path_budget[key] for key in scalar_fields if key in path_budget},
        "deepest_member": {key: deepest[key] for key in deepest_fields if key in deepest},
        "path_measurement_scope": "qualification_build_machine",
    }


def write_packaged_provenance(path: Path, provenance: dict[str, Any]) -> None:
    """Reject local absolute paths before writing distributed provenance."""
    violations = packaged_provenance_absolute_path_violations(provenance)
    if violations:
        raise BuildError(violations[0])
    write_json(path, provenance)


def native_failure_diagnostics(native_record: dict[str, Any]) -> dict[str, Any]:
    """Keep actionable native evidence when stage cleanup removes the package."""

    return {
        "schema_version": native_record.get("schema_version"),
        "status": native_record.get("status"),
        "files_scanned": native_record.get("files_scanned"),
        "unresolved": native_record.get("unresolved", []),
        "parse_failures": native_record.get("parse_failures", []),
        "unknown_architecture": native_record.get("unknown_architecture", []),
        "non_x64": native_record.get("non_x64", []),
        "reachability_model": native_record.get("reachability_model"),
    }


def relpath(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def lexical_project_relpath(path: Path, root: Path) -> str:
    root_path = Path(os.path.abspath(root.resolve()))
    lexical_path = Path(os.path.abspath(path))
    try:
        relative = lexical_path.relative_to(root_path)
    except ValueError as exc:
        raise BuildError("provenance_path_outside_project") from exc
    value = relative.as_posix()
    if relative.is_absolute() or ".." in relative.parts or ":" in value or "\\" in value:
        raise BuildError("provenance_path_not_project_relative")
    return value


def git_output(source: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source), *args],
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=20,
    )
    return result.stdout.strip()


def _valid_commit_sha(value: str | None, field: str) -> str:
    if not value or not re.fullmatch(r"[0-9a-fA-F]{40}", value):
        raise BuildError(f"{field}_invalid")
    return value.lower()


def _git_is_ancestor(source: Path, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(source), "merge-base", "--is-ancestor", ancestor, descendant],
        check=False,
        capture_output=True,
        timeout=20,
    )
    return result.returncode == 0


def source_state(
    source: Path,
    build_mode: str = "qualification",
    expected_source_sha: str | None = None,
    accepted_baseline_sha: str | None = None,
    allow_dirty: bool = False,
) -> dict[str, str | bool | None]:
    # Keep the former boolean call shape from silently producing a qualified
    # build while allowing old diagnostic callers to continue to run.
    if isinstance(build_mode, bool):
        allow_dirty = build_mode
        build_mode = "development" if allow_dirty else "qualification"
    head = git_output(source, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise BuildError("source_sha_unavailable")
    dirty = bool(git_output(source, "status", "--porcelain=v1", "--untracked-files=all"))
    if build_mode not in {"qualification", "development"}:
        raise BuildError(f"build_mode_invalid:{build_mode}")
    if build_mode == "qualification":
        expected = _valid_commit_sha(expected_source_sha, "expected_source_sha")
        baseline = _valid_commit_sha(accepted_baseline_sha, "accepted_baseline_sha")
        if head != expected:
            raise BuildError(f"source_sha_mismatch:expected={expected}:actual={head}")
        if dirty:
            raise BuildError("qualification_worktree_dirty")
        if git_output(source, "rev-parse", "--verify", f"{baseline}^{{commit}}") != baseline:
            raise BuildError(f"accepted_baseline_unavailable:{baseline}")
        if not _git_is_ancestor(source, baseline, head):
            raise BuildError(f"accepted_baseline_not_ancestor:{baseline}:{head}")
        return {
            "head_sha": head,
            "base_sha": baseline,
            "accepted_baseline_sha": baseline,
            "expected_source_sha": expected,
            "branch": git_output(source, "branch", "--show-current") or "unknown",
            "working_tree_dirty": False,
            "build_mode": "QUALIFICATION",
            "qualifiable": True,
            "baseline_is_ancestor": True,
        }
    return {
        "head_sha": head,
        "base_sha": accepted_baseline_sha.lower() if accepted_baseline_sha else None,
        "accepted_baseline_sha": accepted_baseline_sha.lower() if accepted_baseline_sha else None,
        "expected_source_sha": expected_source_sha.lower() if expected_source_sha else None,
        "branch": git_output(source, "branch", "--show-current") or "unknown",
        "working_tree_dirty": dirty,
        "build_mode": "DEVELOPMENT_ONLY",
        "qualifiable": False,
        "baseline_is_ancestor": None,
    }


def download_verified(
    *,
    url: str,
    filename: str,
    expected_sha256: str,
    cache_dir: Path,
    accept_downloads: bool,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / filename
    if destination.is_file():
        actual = sha256_file(destination)
        if actual != expected_sha256:
            raise BuildError(f"cached_artifact_hash_mismatch:{filename}:{actual}")
        return destination
    if not accept_downloads:
        raise BuildError(f"download_ack_required:{url}")
    temporary = cache_dir / f".{filename}.{uuid.uuid4().hex}.part"
    try:
        with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        actual = sha256_file(temporary)
        if actual != expected_sha256:
            raise BuildError(f"downloaded_artifact_hash_mismatch:{filename}:{actual}")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def safe_zip_members(archive: Path) -> Iterable[zipfile.ZipInfo]:
    try:
        yield from _safe_zip_members(archive)
    except ArchiveMemberError as exc:
        raise BuildError(str(exc)) from exc


def extract_zip(archive: Path, destination: Path) -> None:
    try:
        _extract_zip(archive, destination)
    except ArchiveMemberError as exc:
        raise BuildError(str(exc)) from exc


def copy_file(source: Path, destination: Path, *, prefer_hardlink: bool = False) -> None:
    if source.is_symlink():
        raise BuildError(f"symlink_not_allowed:{source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if prefer_hardlink:
        try:
            if destination.exists() or destination.is_symlink():
                destination.unlink()
            os.link(source, destination)
            return
        except OSError:
            # Cross-volume output or a filesystem without hard-link support.
            pass
    try:
        shutil.copy2(source, destination)
    except OSError as exc:
        raise BuildError(f"copy_failed:{source}:{destination}:{exc}") from exc


def copy_tree(source: Path, destination: Path, *, prefer_hardlink: bool = False) -> None:
    if not source.is_dir():
        raise BuildError(f"directory_missing:{source}")
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        if any(
            part in {"__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}
            for part in relative.parts
        ):
            continue
        if item.is_symlink():
            raise BuildError(f"symlink_not_allowed:{item}")
        target = destination / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file() and item.suffix.lower() not in {".pyc", ".pyo", ".pdb"}:
            copy_file(item, target, prefer_hardlink=prefer_hardlink)


def configure_embedded_python(runtime_python: Path) -> None:
    pth_candidates = sorted(runtime_python.glob("*._pth"))
    if len(pth_candidates) != 1:
        raise BuildError(f"embedded_pth_unexpected:{[path.name for path in pth_candidates]}")
    pth_candidates[0].write_text(
        "python312.zip\nLib/site-packages\n../../app\nimport site\n",
        encoding="ascii",
    )


def python_owned_files(archive_path: Path, python_root: Path) -> list[dict[str, Any]]:
    """Explicit non-wheel provenance, checked again by the complete runtime walk."""
    if sha256_file(archive_path) != CPYTHON_SHA256:
        raise BuildError("cpython_owned_archive_hash_mismatch")
    records = {}
    with zipfile.ZipFile(archive_path) as archive:
        for info in safe_zip_infos(archive):
            payload = archive.read(info)
            relative = info.filename.replace("\\", "/")
            generation = None
            if relative == "python312._pth":
                payload = b"python312.zip\nLib/site-packages\n../../app\nimport site\n"
                # write_text uses the platform newline convention.
                payload = payload.replace(b"\n", os.linesep.encode())
                generation = "configure_embedded_python"
            records[relative] = {
                "distribution": "CPython", "artifact_filename": CPYTHON_FILENAME,
                "artifact_sha256": CPYTHON_SHA256, "wheel_member": None,
                "archive_member": info.filename, "installed_record_path": None,
                "install_scheme": "builder_owned", "canonical_final_path": relative,
                "integrity_source": "generated_metadata" if generation else "locked_archive_member",
                "generation_contract": generation, "action": "retained",
                "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload), "status": "PASS",
            }
    for name, digest in MSVC_FILE_SHA256.items():
        path = python_root / name
        if path.is_file() and sha256_file(path) == digest:
            records[name] = {
                "distribution": "Microsoft VC Runtime", "artifact_filename": MSVC_REDIST_FILENAME,
                "artifact_sha256": MSVC_REDIST_SHA256, "wheel_member": None,
                "archive_member": name, "installed_record_path": None,
                "install_scheme": "builder_owned", "canonical_final_path": name,
                "integrity_source": "locked_redist_member", "action": "retained",
                "sha256": digest, "size": path.stat().st_size, "status": "PASS",
            }
    return list(records.values())


def resolve_pnpm(source: Path, explicit: Path | None) -> Path:
    candidates = [explicit] if explicit else []
    candidates.extend(
        [source / ".local-tools" / "pnpm.cmd", source / ".local-tools" / "pnpm" / "pnpm.cmd"]
    )
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate.resolve()
    command = shutil.which("pnpm.cmd") or shutil.which("pnpm")
    if command:
        return Path(command).resolve()
    raise BuildError("frontend_build_tool_missing: pnpm is required only at build time")


def build_frontend(source: Path, no_build: bool, explicit_pnpm: Path | None) -> Path:
    dist = source / "apps" / "frontend" / "dist"
    if not no_build:
        pnpm = resolve_pnpm(source, explicit_pnpm)
        command = f'"{pnpm}" run build:frontend'
        environment = os.environ.copy()
        environment["VITE_API_BASE_URL"] = "http://127.0.0.1:8000"
        result = subprocess.run(command, cwd=source, env=environment, shell=True, check=False)
        if result.returncode != 0:
            raise BuildError(f"frontend_build_failed:{result.returncode}")
    if not (dist / "index.html").is_file():
        raise BuildError(f"frontend_dist_missing:{dist}")
    return dist


def prepare_msvc_redist_source(
    cache_dir: Path, explicit: Path | None, accept_downloads: bool
) -> tuple[Path | None, dict[str, Any]]:
    if explicit is not None:
        if not explicit.is_dir():
            raise BuildError(f"msvc_source_missing:{explicit}")
        return explicit.resolve(), {
            "source": "official_microsoft_vc_redist_payload",
            "artifact_filename": MSVC_REDIST_FILENAME,
            "artifact_sha256": MSVC_REDIST_SHA256,
            "artifact_url": MSVC_REDIST_URL,
        }
    destination = cache_dir / "msvc-redist-14.50.35719"
    required = [destination / name for name in MSVC_DLLS]
    if all(path.is_file() for path in required):
        return destination, {
            "source": "external_cached_official_redist_payload",
            "artifact_filename": MSVC_REDIST_FILENAME,
            "artifact_sha256": MSVC_REDIST_SHA256,
            "artifact_url": MSVC_REDIST_URL,
        }
    redist = download_verified(
        url=MSVC_REDIST_URL,
        filename=MSVC_REDIST_FILENAME,
        expected_sha256=MSVC_REDIST_SHA256,
        cache_dir=cache_dir,
        accept_downloads=accept_downloads,
    )
    payload = redist.read_bytes()
    cab_candidates: list[tuple[int, int]] = []
    offset = payload.find(b"MSCF")
    while offset >= 0:
        if offset + 12 <= len(payload):
            cabinet_size = int.from_bytes(payload[offset + 8 : offset + 12], "little")
            if cabinet_size > 0 and offset + cabinet_size <= len(payload):
                cab_candidates.append((offset, cabinet_size))
        offset = payload.find(b"MSCF", offset + 4)
    if not cab_candidates:
        raise BuildError("msvc_redist_attached_cab_not_found")
    cab_offset, cab_size = max(cab_candidates, key=lambda item: item[1])
    cab_path = cache_dir / "VC_redist.x64-14.50.35719-attached.cab"
    if not cab_path.is_file() or cab_path.stat().st_size != cab_size:
        cab_path.write_bytes(payload[cab_offset : cab_offset + cab_size])
    outer_dir = cache_dir / "msvc-redist-14.50.35719-attached"
    outer_dir.mkdir(parents=True, exist_ok=True)
    expand = shutil.which("expand.exe") or shutil.which("expand")
    if not expand:
        raise BuildError("msvc_redist_extraction_tool_missing:expand.exe")
    outer_result = subprocess.run(
        [expand, "-F:*", str(cab_path), str(outer_dir)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if outer_result.returncode != 0:
        raise BuildError("msvc_redist_attached_cab_extract_failed")
    minimum_cab = outer_dir / "a4"
    additional_cab = outer_dir / "a5"
    if not minimum_cab.is_file() or not additional_cab.is_file():
        raise BuildError("msvc_redist_x64_cabs_missing")
    extracted_minimum = cache_dir / "msvc-redist-14.50.35719-minimum"
    extracted_additional = cache_dir / "msvc-redist-14.50.35719-additional"
    extracted_minimum.mkdir(parents=True, exist_ok=True)
    extracted_additional.mkdir(parents=True, exist_ok=True)
    for cab, directory in (
        (minimum_cab, extracted_minimum),
        (additional_cab, extracted_additional),
    ):
        result = subprocess.run(
            [expand, "-F:*", str(cab), str(directory)],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise BuildError("msvc_redist_runtime_cab_extract_failed")
    destination.mkdir(parents=True, exist_ok=True)
    for extracted in (extracted_minimum, extracted_additional):
        for candidate in extracted.iterdir():
            if not candidate.is_file() or not candidate.name.endswith("_amd64"):
                continue
            name = candidate.name[: -len("_amd64")]
            if name in MSVC_DLLS:
                copy_file(candidate, destination / name)
    if not any((destination / name).is_file() for name in MSVC_DLLS):
        raise BuildError("msvc_redist_runtime_dlls_not_extracted")
    return destination, {
        "source": "official_microsoft_vc_redist_payload",
        "artifact_filename": MSVC_REDIST_FILENAME,
        "artifact_sha256": MSVC_REDIST_SHA256,
        "artifact_url": MSVC_REDIST_URL,
    }


def copy_application(source: Path, package: Path, frontend_dist: Path) -> None:
    app = package / "app"
    copy_tree(source / "apps" / "backend", app / "apps" / "backend")
    copy_tree(source / "apps" / "worker", app / "apps" / "worker")
    copy_file(source / "tools" / "__init__.py", app / "tools" / "__init__.py")
    copy_tree(source / "tools" / "ai_stack", app / "tools" / "ai_stack")
    copy_file(
        source / "tools" / "benchmark" / "__init__.py", app / "tools" / "benchmark" / "__init__.py"
    )
    copy_file(
        source / "tools" / "benchmark" / "runtime.py", app / "tools" / "benchmark" / "runtime.py"
    )
    copy_file(source / "scripts" / "database_backup.py", app / "scripts" / "database_backup.py")
    copy_tree(source / "scripts" / "portable", app / "scripts" / "portable")
    for name in ("model_registry.json", "release.json"):
        copy_file(source / name, app / name)
    copy_tree(frontend_dist, package / "frontend" / "dist")


def parse_metadata(path: Path) -> dict[str, Any]:
    message = email.parser.Parser().parsestr(path.read_text(encoding="utf-8", errors="replace"))
    classifiers = message.get_all("Classifier", [])
    license_classifiers = [value for value in classifiers if value.lower().startswith("license ::")]
    return {
        "name": message.get("Name") or path.parent.name.split("-", 1)[0],
        "version": message.get("Version"),
        "license": message.get("License"),
        "license_expression": message.get("License-Expression"),
        "license_files_metadata": message.get_all("License-File", []),
        "license_classifiers": license_classifiers,
        "home_page": message.get("Home-page"),
        "source_urls": message.get_all("Project-URL", []),
    }


def safe_component_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "unknown"


def is_license_notice_filename(name: str) -> bool:
    lower = name.casefold()
    return any(token in lower for token in ("license", "licence", "copying", "notice"))


def validate_agpl_license_bytes(payload: bytes, source: str = "application") -> dict[str, Any]:
    sha256 = hashlib.sha256(payload).hexdigest()
    if sha256 != AGPL_APPROVED_SHA256:
        raise BuildError(f"application_agpl_license_unapproved_text:{source}")
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BuildError(f"application_agpl_license_encoding_invalid:{source}") from exc
    return {"status": "PASS", "size": len(payload), "sha256": sha256}


def capture_application_license(source_license: Path, package: Path) -> dict[str, Any]:
    payload = source_license.read_bytes()
    evidence = validate_agpl_license_bytes(payload, str(source_license))
    app_license = package / "app" / "LICENSE"
    package_license = package / "LICENSES" / "application" / "LICENSE"
    copy_file(source_license, app_license)
    copy_file(source_license, package_license)
    for target in (app_license, package_license):
        if target.read_bytes() != payload:
            raise BuildError(f"application_agpl_license_copy_mismatch:{target.name}")
    return {
        **evidence,
        "source_url": AGPL_SOURCE_URL,
        "app_license": relpath(app_license, package),
        "package_license": relpath(package_license, package),
    }


def _validated_supplemental_license_record(
    record: Any, lock_item: dict[str, Any], *, packaged: bool = False
) -> dict[str, str]:
    if not isinstance(record, dict):
        raise BuildError("supplemental_license_record_invalid")
    allowed_fields = SUPPLEMENTAL_LICENSE_FIELDS | (
        {"package_license_path"} if packaged else set()
    )
    if set(record) != allowed_fields:
        raise BuildError("supplemental_license_record_schema_invalid")
    if any(
        not isinstance(record.get(key), str) or not record[key].strip()
        for key in SUPPLEMENTAL_LICENSE_FIELDS
    ):
        raise BuildError("supplemental_license_record_schema_invalid")

    name = record["normalized_package_name"]
    if (
        name != normalize_distribution_name(name)
        or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
    ):
        raise BuildError(f"supplemental_license_name_invalid:{name}")
    if (
        name != lock_item.get("normalized_name", name)
        or record["version"] != lock_item.get("version")
        or record["locked_wheel_filename"] != lock_item.get("wheel_filename")
        or record["locked_wheel_sha256"] != lock_item.get("sha256")
    ):
        raise BuildError(f"supplemental_license_locked_distribution_mismatch:{name}")

    license_path = record["license_path"]
    license_rel = PurePosixPath(license_path)
    license_hash = record["license_text_sha256"]
    source_url = record["source_url"]
    source_filename = record["source_distribution_filename"]
    source_member = record["source_distribution_member"]
    source_member_path = PurePosixPath(source_member)
    source_url_full = record["source_distribution_url"]
    source_hash = record["source_distribution_sha256"]
    if (
        "\\" in license_path
        or ":" in license_path
        or license_rel.is_absolute()
        or ".." in license_rel.parts
        or license_rel.as_posix() != license_path
        or not license_path.startswith("packages/portable/licenses/")
    ):
        raise BuildError(f"supplemental_license_path_invalid:{name}")
    if not re.fullmatch(r"[0-9a-f]{64}", license_hash):
        raise BuildError(f"supplemental_license_text_hash_invalid:{name}")
    try:
        source_parts = urlsplit(source_url)
        source_distribution_parts = urlsplit(source_url_full)
        source_distribution_path = source_distribution_parts.path
    except ValueError as exc:
        raise BuildError(f"supplemental_license_source_invalid:{name}") from exc
    if source_parts.scheme != "https" or not source_parts.hostname:
        raise BuildError(f"supplemental_license_source_invalid:{name}")
    if (
        source_filename in {"", ".", ".."}
        or "/" in source_filename
        or "\\" in source_filename
        or ":" in source_filename
        or source_distribution_parts.scheme != "https"
        or not source_distribution_parts.hostname
        or source_distribution_parts.username is not None
        or source_distribution_parts.password is not None
        or source_distribution_parts.query
        or source_distribution_parts.fragment
        or not source_distribution_path.endswith("/" + source_filename)
        or "\\" in source_member
        or ":" in source_member
        or source_member_path.is_absolute()
        or ".." in source_member_path.parts
        or not source_member_path.parts
        or source_member_path.as_posix() != source_member
        or not record["source_text_section"].strip()
    ):
        raise BuildError(f"supplemental_license_source_distribution_invalid:{name}")
    if not re.fullmatch(r"[0-9a-f]{64}", source_hash):
        raise BuildError(f"supplemental_license_source_distribution_hash_invalid:{name}")

    package_license_path = (
        f"LICENSES/python/{safe_component_name(name)}/supplemental/LICENSE.txt"
    )
    if packaged and record.get("package_license_path") != package_license_path:
        raise BuildError(f"supplemental_license_package_path_invalid:{name}")
    return {**record, "package_license_path": package_license_path}


def load_supplemental_license_records(
    source_root: Path, runtime_lock: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    manifest_path = source_root / "packages" / "portable" / "licenses" / "supplemental-licenses.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"supplemental_license_manifest_read_failed:{exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SUPPLEMENTAL_LICENSE_SCHEMA:
        raise BuildError("supplemental_license_manifest_schema_invalid")
    records = manifest.get("records")
    if not isinstance(records, list):
        raise BuildError("supplemental_license_manifest_schema_invalid")
    locked = {
        str(item["normalized_name"]): item for item in runtime_lock.get("distributions", [])
    }
    resolved: dict[str, dict[str, Any]] = {}
    allowed_root = (source_root / "packages" / "portable" / "licenses").resolve()
    for record in records:
        if not isinstance(record, dict):
            raise BuildError("supplemental_license_record_invalid")
        name_value = record.get("normalized_package_name")
        if not isinstance(name_value, str):
            raise BuildError("supplemental_license_record_schema_invalid")
        name = normalize_distribution_name(name_value)
        if not name or name in resolved:
            raise BuildError(f"supplemental_license_name_duplicate_or_invalid:{name}")
        lock_item = locked.get(name)
        if lock_item is None:
            raise BuildError(f"supplemental_license_locked_distribution_mismatch:{name}")
        normalized_record = _validated_supplemental_license_record(record, lock_item)
        license_rel = PurePosixPath(normalized_record["license_path"])
        license_path = (source_root / Path(*license_rel.parts)).resolve()
        if not license_path.is_relative_to(allowed_root) or not license_path.is_file():
            raise BuildError(f"supplemental_license_text_missing:{name}")
        license_hash = sha256_file(license_path)
        if license_hash != normalized_record["license_text_sha256"]:
            raise BuildError(f"supplemental_license_text_hash_mismatch:{name}")
        resolved[name] = {**normalized_record, "_resolved_path": license_path}
    return resolved


def summarize_python_license_evidence(
    components: list[dict[str, Any]], runtime_lock: dict[str, Any]
) -> dict[str, Any]:
    locked = {
        str(item["normalized_name"]): item for item in runtime_lock.get("distributions", [])
    }
    component_by_name: dict[str, dict[str, Any]] = {}
    for component in components:
        name = normalize_distribution_name(str(component.get("name", "")))
        if not name or name in component_by_name or name not in locked:
            raise BuildError(f"runtime_license_component_inventory_invalid:{name}")
        component_by_name[name] = component
    unresolved = []
    for name, lock_item in locked.items():
        component = component_by_name.get(name)
        if component is None or component.get("version") != lock_item["version"]:
            unresolved.append(name)
            continue
        status = component.get("license_evidence_status")
        evidence_files = component.get("license_files")
        from_distribution = component.get("license_file_not_present_in_distribution") is False
        if (
            not isinstance(evidence_files, list)
            or not evidence_files
            or (from_distribution and status != "RESOLVED_FROM_DISTRIBUTION")
            or (not from_distribution and status != "RESOLVED_FROM_SUPPLEMENTAL")
        ):
            unresolved.append(name)
    summary = {
        "status": (
            "PASS"
            if not unresolved
            and len(component_by_name) == len(locked) == EXPECTED_PYTHON_DISTRIBUTIONS
            else "BLOCKED"
        ),
        "locked_distribution_count": len(locked),
        "resolved_license_evidence_count": len(locked) - len(unresolved),
        "unresolved_count": len(unresolved),
        "unresolved_distributions": unresolved,
    }
    if summary["status"] != "PASS":
        raise BuildError(f"runtime_license_evidence_incomplete:{summary}")
    return summary


def capture_distribution_licenses(
    site_packages: Path,
    licenses_root: Path,
    runtime_lock: dict[str, Any] | None = None,
    *,
    source_root: Path | None = None,
) -> list[dict[str, Any]]:
    lock_by_name = {
        str(item["normalized_name"]): item
        for item in (runtime_lock or {}).get("distributions", [])
    }
    supplemental = (
        load_supplemental_license_records(source_root, runtime_lock)
        if runtime_lock is not None and source_root is not None
        else {}
    )
    if runtime_lock is not None and source_root is None:
        raise BuildError("supplemental_license_source_root_required")
    components: list[dict[str, Any]] = []
    for dist_info in sorted(site_packages.glob("*.dist-info")):
        metadata_path = dist_info / "METADATA"
        if not metadata_path.is_file():
            continue
        metadata = parse_metadata(metadata_path)
        name = str(metadata["name"])
        normalized_name = re.sub(r"[-_.]+", "-", name).casefold()
        lock_item = lock_by_name.get(normalized_name)
        if runtime_lock is not None and lock_item is None:
            raise BuildError(f"runtime_license_distribution_unlisted:{name}")
        if lock_item is not None and metadata["version"] != lock_item["version"]:
            raise BuildError(f"runtime_license_distribution_version_mismatch:{name}")
        destination = licenses_root / "python" / safe_component_name(name)
        license_files: list[str] = []
        for candidate in sorted(dist_info.rglob("*")):
            if not candidate.is_file():
                continue
            if not is_license_notice_filename(candidate.name):
                continue
            relative = candidate.relative_to(dist_info)
            output = destination / relative
            copy_file(candidate, output)
            license_files.append(relpath(output, licenses_root.parent))
        license_file_not_present = not bool(license_files)
        supplemental_record = supplemental.get(normalized_name)
        supplemental_license = None
        if license_file_not_present and supplemental_record is not None:
            package_license_path = supplemental_record["package_license_path"]
            output = licenses_root.parent / Path(*PurePosixPath(package_license_path).parts)
            copy_file(supplemental_record["_resolved_path"], output)
            license_files.append(package_license_path)
            supplemental_license = {
                key: value
                for key, value in supplemental_record.items()
                if not key.startswith("_")
            }
        elif not license_file_not_present and supplemental_record is not None:
            raise BuildError(f"supplemental_license_not_needed:{name}")
        license_evidence_status = (
            "RESOLVED_FROM_DISTRIBUTION"
            if not license_file_not_present
            else "RESOLVED_FROM_SUPPLEMENTAL"
            if supplemental_license is not None
            else "UNRESOLVED"
        )
        license_hashes = {
            rel: sha256_file(package_path)
            for rel in license_files
            for package_path in (licenses_root.parent / Path(*PurePosixPath(rel).parts),)
        }
        components.append(
            {
                "component_type": "python_distribution",
                "name": name,
                "version": metadata["version"],
                "license_metadata": metadata["license"],
                "license_expression": metadata["license_expression"],
                "license_classifiers": metadata["license_classifiers"],
                "license_files_metadata": metadata["license_files_metadata"],
                "license_files": license_files,
                "license_file_sha256": license_hashes,
                "license_file_not_present_in_distribution": license_file_not_present,
                "license_evidence_status": license_evidence_status,
                "license_evidence_source": (
                    "supplemental_manifest" if supplemental_license is not None else "distribution"
                ),
                "supplemental_license": supplemental_license,
                "source_urls": metadata["source_urls"],
                "home_page": metadata["home_page"],
                "metadata_sha256": sha256_file(metadata_path),
                "lock": {
                    "wheel_filename": lock_item["wheel_filename"],
                    "wheel_sha256": lock_item["sha256"],
                    "source_index": lock_item["source_index"],
                    "license": lock_item["license"],
                    "license_expression": lock_item["license_expression"],
                    "license_files": lock_item["license_files"],
                }
                if lock_item is not None
                else None,
            }
        )
    if runtime_lock is not None:
        supplemental_manifest = {
            "schema_version": SUPPLEMENTAL_LICENSE_SCHEMA,
            "records": [
                {
                    key: value
                    for key, value in supplemental[name].items()
                    if not key.startswith("_")
                }
                for name in sorted(supplemental)
            ],
        }
        write_json(
            licenses_root.parent / Path(*PurePosixPath(SUPPLEMENTAL_LICENSE_MANIFEST_PATH).parts),
            supplemental_manifest,
        )
        summarize_python_license_evidence(components, runtime_lock)
    return components


def _vite_modulepreload_evidence(
    bundle_assets: list[dict[str, Any]], read_asset: Callable[[str], bytes]
) -> dict[str, Any]:
    for asset in bundle_assets:
        path = asset.get("path")
        if not isinstance(path, str) or Path(path).suffix.casefold() != ".js":
            continue
        payload = read_asset(path)
        if not payload.startswith(b"(function(){"):
            continue
        end = payload.find(b"})();")
        if end < 0:
            continue
        polyfill = payload[: end + len(b"})();")]
        if (
            len(polyfill) != VITE_MODULEPRELOAD_POLYFILL_SIZE
            or hashlib.sha256(polyfill).hexdigest() != VITE_MODULEPRELOAD_POLYFILL_SHA256
        ):
            continue
        return {
            "component_name": "vite",
            "version": FRONTEND_EMITTED_CODE_COMPONENT_VERSIONS["vite"],
            "evidence_type": "modulepreload_polyfill_and_helpers",
            "generator_source": "dist/node/chunks/config.js",
            "generator_helpers": ["getFetchOpts", "processPreload"],
            "asset_path": path,
            "asset_sha256": hashlib.sha256(payload).hexdigest(),
            "code_offset": 0,
            "code_size": len(polyfill),
            "code_sha256": hashlib.sha256(polyfill).hexdigest(),
        }
    raise BuildError("frontend_vite_emitted_code_missing")


def capture_frontend_provenance(source: Path, package: Path, licenses_root: Path) -> dict[str, Any]:
    frontend_root = source / "apps" / "frontend"
    package_json_path = frontend_root / "package.json"
    lock_path = source / "pnpm-lock.yaml"
    try:
        package_json = json.loads(package_json_path.read_text(encoding="utf-8"))
        lock_text = lock_path.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"frontend_runtime_license_inputs_unavailable:{exc}") from exc
    try:
        importers_section = lock_text.split("importers:\n", 1)[1].split(
            "\npackages:\n", 1
        )[0]
        package_section, snapshots_section = lock_text.split("packages:\n", 1)[1].split(
            "\nsnapshots:\n", 1
        )
    except IndexError as exc:
        raise BuildError("frontend_lockfile_package_section_missing") from exc
    importer_lines = importers_section.splitlines()
    importer_headers = [
        index for index, line in enumerate(importer_lines) if line == "  apps/frontend:"
    ]
    if len(importer_headers) != 1:
        raise BuildError("frontend_lockfile_importer_missing")
    importer_start = importer_headers[0] + 1
    importer_end = next(
        (
            index
            for index in range(importer_start, len(importer_lines))
            if importer_lines[index].startswith("  ")
            and not importer_lines[index].startswith("    ")
        ),
        len(importer_lines),
    )
    frontend_importer = "\n".join(importer_lines[importer_start:importer_end])

    direct_dependencies = package_json.get("dependencies", {})
    direct_build_dependencies = {
        key: value
        for key, value in FRONTEND_RUNTIME_PACKAGE_VERSIONS.items()
        if key != "scheduler"
    }
    direct_build_dependencies.update(FRONTEND_EMITTED_CODE_COMPONENT_VERSIONS)
    for name, version in direct_build_dependencies.items():
        if direct_dependencies.get(name) != version:
            raise BuildError(f"frontend_build_dependency_spec_mismatch:{name}")

    def package_root(name: str, version: str) -> Path:
        direct = frontend_root / "node_modules" / name
        if not direct.is_dir():
            raise BuildError(f"frontend_runtime_package_unresolved:{name}@{version}")
        candidate = direct.resolve()
        try:
            metadata = json.loads((candidate / "package.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BuildError(f"frontend_runtime_package_metadata_invalid:{name}") from exc
        if metadata.get("name") != name or metadata.get("version") != version:
            raise BuildError(f"frontend_runtime_package_identity_mismatch:{name}@{version}")
        return candidate

    def lock_package_key(name: str, version: str) -> None:
        pattern = re.compile(
            rf"(?m)^  {re.escape(name)}@{re.escape(version)}(?:\([^\r\n]*\))?:\s*$"
        )
        if not pattern.search(package_section):
            raise BuildError(f"frontend_runtime_package_not_locked:{name}@{version}")

    def lock_importer_dependency(name: str, version: str) -> None:
        pattern = re.compile(
            rf"(?m)^      {re.escape(name)}:\n"
            rf"        specifier: {re.escape(version)}\n"
            rf"        version: {re.escape(version)}(?:\([^\r\n]*\))?$"
        )
        if not pattern.search(frontend_importer):
            raise BuildError(f"frontend_runtime_importer_lock_mismatch:{name}@{version}")

    def react_dom_scheduler_edge() -> tuple[Path, dict[str, Any]]:
        react_dom_version = FRONTEND_RUNTIME_PACKAGE_VERSIONS["react-dom"]
        react_dom_root = package_root("react-dom", react_dom_version)
        react_dom_package_json = react_dom_root / "package.json"
        react_dom = json.loads(react_dom_package_json.read_text(encoding="utf-8"))
        scheduler_version = FRONTEND_RUNTIME_PACKAGE_VERSIONS["scheduler"]
        declared = react_dom.get("dependencies", {}).get("scheduler")
        if declared != "^0.27.0":
            raise BuildError("frontend_scheduler_not_in_runtime_graph")

        snapshot_pattern = re.compile(
            rf"(?m)^  react-dom@{re.escape(react_dom_version)}\([^\r\n]*\):\r?\n"
        )
        snapshots = list(snapshot_pattern.finditer(snapshots_section))
        if len(snapshots) != 1:
            raise BuildError("frontend_scheduler_lock_edge_missing")
        start = snapshots[0].end()
        end = next(
            (
                match.start()
                for match in re.finditer(r"(?m)^  \S", snapshots_section[start:])
            ),
            len(snapshots_section) - start,
        )
        snapshot_key = snapshots[0].group().splitlines()[0].strip().rstrip(":")
        snapshot = snapshots_section[start : start + end]
        lock_edges = re.findall(r"(?m)^      scheduler:\s*([^\s#]+)\s*$", snapshot)
        if lock_edges != [scheduler_version]:
            raise BuildError("frontend_scheduler_lock_edge_mismatch")

        lock_package_key("scheduler", scheduler_version)
        dependency_candidates = (
            react_dom_root / "node_modules" / "scheduler",
            react_dom_root.parent / "scheduler",
        )
        dependency_context_root = next(
            (candidate for candidate in dependency_candidates if candidate.is_dir()), None
        )
        if dependency_context_root is None:
            raise BuildError("frontend_runtime_package_unresolved:scheduler@0.27.0")
        scheduler_root = dependency_context_root.resolve()
        try:
            dependency_context_package_json = dependency_context_root / "package.json"
            scheduler_package_json = scheduler_root / "package.json"
            scheduler = json.loads(dependency_context_package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BuildError("frontend_runtime_package_metadata_invalid:scheduler") from exc
        if scheduler.get("name") != "scheduler" or scheduler.get("version") != scheduler_version:
            raise BuildError("frontend_runtime_package_identity_mismatch:scheduler@0.27.0")
        dependency_context_hash = sha256_file(dependency_context_package_json)
        resolved_package_hash = sha256_file(scheduler_package_json)
        if dependency_context_hash != resolved_package_hash:
            raise BuildError("frontend_scheduler_dependency_context_hash_mismatch")

        return scheduler_root, {
            "importer_name": "react-dom",
            "importer_version": react_dom_version,
            "importer_package_path": relpath(react_dom_root, source),
            "importer_package_json_sha256": sha256_file(react_dom_package_json),
            "declared_dependency": declared,
            "lock_snapshot": snapshot_key,
            "locked_version": scheduler_version,
            "dependency_context_path": lexical_project_relpath(dependency_context_root, source),
            "dependency_context_package_json_sha256": dependency_context_hash,
            "resolved_package_path": relpath(scheduler_root, source),
            "resolved_package_json_sha256": resolved_package_hash,
            "pnpm_lock_sha256": sha256_file(lock_path),
        }

    runtime_components: list[dict[str, Any]] = []
    emitted_code_components: list[dict[str, Any]] = []
    runtime_package_records: list[dict[str, str]] = []
    scheduler_edge: dict[str, Any] | None = None
    pnpm_lock_sha256 = sha256_file(lock_path)
    for name, version in FRONTEND_RUNTIME_PACKAGE_VERSIONS.items():
        lock_package_key(name, version)
        if name != "scheduler":
            lock_importer_dependency(name, version)
        if name == "scheduler":
            installed_root, scheduler_edge = react_dom_scheduler_edge()
        else:
            installed_root = package_root(name, version)
        try:
            metadata = json.loads((installed_root / "package.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BuildError(f"frontend_runtime_package_metadata_invalid:{name}") from exc
        if metadata.get("name") != name or metadata.get("version") != version or not metadata.get("license"):
            raise BuildError(f"frontend_runtime_package_metadata_mismatch:{name}@{version}")
        license_files: list[str] = []
        for candidate in sorted(installed_root.rglob("*")):
            if not candidate.is_file() or not is_license_notice_filename(candidate.name):
                continue
            relative = candidate.relative_to(installed_root)
            output = licenses_root / "frontend" / safe_component_name(name) / relative
            copy_file(candidate, output)
            license_files.append(relpath(output, licenses_root.parent))
        if not license_files:
            raise BuildError(f"frontend_runtime_license_missing:{name}@{version}")
        runtime_components.append(
            {
                "component_type": "frontend_runtime_package",
                "name": name,
                "version": version,
                "license": metadata["license"],
                "license_files": license_files,
                "license_file_sha256": {
                    rel: sha256_file(licenses_root.parent / Path(*PurePosixPath(rel).parts))
                    for rel in license_files
                },
                "license_evidence_status": "RESOLVED_FROM_INSTALLED_PACKAGE",
                "source_package_version": version,
                "source_package_json_sha256": sha256_file(installed_root / "package.json"),
                "source_package_manifest": "apps/frontend/package.json",
                "source_package_manifest_sha256": sha256_file(package_json_path),
                "pnpm_lock": "pnpm-lock.yaml",
                "pnpm_lock_sha256": pnpm_lock_sha256,
                "pnpm_package_key": f"{name}@{version}",
            }
        )
        runtime_package_records.append({"name": name, "version": version})

    vite_name, vite_version = next(iter(FRONTEND_EMITTED_CODE_COMPONENT_VERSIONS.items()))
    lock_package_key(vite_name, vite_version)
    lock_importer_dependency(vite_name, vite_version)
    vite_root = package_root(vite_name, vite_version)
    vite_package_json = vite_root / "package.json"
    vite_metadata = json.loads(vite_package_json.read_text(encoding="utf-8"))
    if not vite_metadata.get("license"):
        raise BuildError(f"frontend_runtime_package_metadata_mismatch:{vite_name}@{vite_version}")
    vite_license_files: list[str] = []
    for candidate in sorted(vite_root.rglob("*")):
        if not candidate.is_file() or not is_license_notice_filename(candidate.name):
            continue
        relative = candidate.relative_to(vite_root)
        output = licenses_root / "frontend" / safe_component_name(vite_name) / relative
        copy_file(candidate, output)
        vite_license_files.append(relpath(output, licenses_root.parent))
    if not vite_license_files:
        raise BuildError(f"frontend_emitted_code_license_missing:{vite_name}@{vite_version}")
    vite_generator_source = vite_root / "dist" / "node" / "chunks" / "config.js"
    if not vite_generator_source.is_file():
        raise BuildError("frontend_vite_generator_source_missing")
    vite_generator_bytes = vite_generator_source.read_bytes()
    if not all(
        marker in vite_generator_bytes
        for marker in (
            b'name: "vite:modulepreload-polyfill"',
            b"function polyfill()",
            b"function getFetchOpts(link)",
            b"function processPreload(link)",
        )
    ):
        raise BuildError("frontend_vite_generator_source_mismatch")
    emitted_code_components.append(
        {
            "component_type": "frontend_emitted_code_component",
            "name": vite_name,
            "version": vite_version,
            "reason_included": (
                "Vite modulepreload polyfill and emitted helpers are present in distributed JS."
            ),
            "license": vite_metadata["license"],
            "license_files": vite_license_files,
            "license_file_sha256": {
                rel: sha256_file(licenses_root.parent / Path(*PurePosixPath(rel).parts))
                for rel in vite_license_files
            },
            "license_evidence_status": "RESOLVED_FROM_INSTALLED_PACKAGE",
            "source_package_name": vite_metadata["name"],
            "source_package_version": vite_version,
            "source_package_json_sha256": sha256_file(vite_package_json),
            "source_package_manifest": "apps/frontend/package.json",
            "source_package_manifest_sha256": sha256_file(package_json_path),
            "pnpm_lock": "pnpm-lock.yaml",
            "pnpm_lock_sha256": pnpm_lock_sha256,
            "pnpm_package_key": f"{vite_name}@{vite_version}",
            "generator_source": "dist/node/chunks/config.js",
            "generator_source_sha256": hashlib.sha256(vite_generator_bytes).hexdigest(),
        }
    )

    dist = package / "frontend" / "dist"
    bundle_assets = [
        {
            "path": relpath(path, package),
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
        }
        for path in sorted(dist.rglob("*"))
        if path.is_file()
    ]
    if not any(Path(item["path"]).suffix.lower() == ".js" for item in bundle_assets):
        raise BuildError("frontend_compiled_runtime_bundle_missing")
    vite_emitted_code = _vite_modulepreload_evidence(
        bundle_assets,
        lambda relative: (package / Path(*PurePosixPath(relative).parts)).read_bytes(),
    )
    provenance = {
        "package_name": package_json.get("name"),
        "source_package_manifest": "apps/frontend/package.json",
        "source_package_manifest_sha256": sha256_file(package_json_path),
        "pnpm_lock": "pnpm-lock.yaml",
        "pnpm_lock_sha256": pnpm_lock_sha256,
        "runtime_mode": "compiled_static_assets_no_node",
        "runtime_packages": runtime_package_records,
        "scheduler_dependency_edge": scheduler_edge,
        "vite_emitted_code": vite_emitted_code,
        "bundle_assets": bundle_assets,
    }
    return {
        "provenance": provenance,
        "components": runtime_components + emitted_code_components,
    }


def nvidia_component_family(filename: str) -> str | None:
    name = filename.casefold()
    for prefix, family in (
        ("cublaslt", "cuBLAS Lt"),
        ("cublas", "cuBLAS"),
        ("cudart", "CUDA runtime / cudart"),
        ("cudnn", "cuDNN"),
        ("cufft", "cuFFT"),
        ("cupti", "CUPTI / Perfworks"),
        ("nvperf", "CUPTI / Perfworks"),
        ("curand", "cuRAND"),
        ("cusolver", "cuSOLVER"),
        ("cusparse", "cuSPARSE"),
        ("nvjitlink", "nvJitLink"),
        ("nvrtc", "NVRTC / NVRTC builtins"),
        ("nvtoolsext", "NVToolsExt"),
        ("nvjpeg", "nvJPEG"),
    ):
        if name.startswith(prefix) and name.endswith(".dll"):
            return family
    return None


def capture_nvidia_runtime_inventory(
    package: Path, runtime_record: dict[str, Any]
) -> dict[str, Any]:
    canonical_by_package_path: dict[str, dict[str, Any]] = {}
    for row in runtime_record.get("files", []):
        canonical_path = row.get("canonical_final_path")
        if not canonical_path:
            continue
        package_path = PurePosixPath("runtime/python", str(canonical_path)).as_posix()
        existing = canonical_by_package_path.get(package_path)
        if existing is not None and any(
            existing.get(key) != row.get(key)
            for key in ("distribution", "wheel_filename", "wheel_sha256", "sha256")
        ):
            raise BuildError(f"nvidia_runtime_canonical_path_conflict:{package_path}")
        canonical_by_package_path[package_path] = row

    for path in package.rglob("*.dll"):
        filename = path.name.casefold()
        if filename == "nvcuda.dll" or (filename.startswith("npp") and filename.endswith(".dll")):
            raise BuildError(f"unexpected_nvidia_driver_or_npp_dll:{path.name}")

    site_packages = package / "runtime" / "python" / "Lib" / "site-packages"
    components: list[dict[str, Any]] = []
    for path in sorted(site_packages.rglob("*.dll")):
        filename = path.name.casefold()
        family = nvidia_component_family(path.name)
        if family is None:
            continue
        package_path = relpath(path, package)
        canonical = canonical_by_package_path.get(package_path)
        if canonical is None:
            raise BuildError(f"nvidia_runtime_dll_missing_canonical_wheel_provenance:{package_path}")
        if canonical.get("distribution") not in {"torch", "torchvision"} and not str(
            canonical.get("distribution", "")
        ).startswith("nvidia-"):
            raise BuildError(f"nvidia_runtime_dll_origin_unexpected:{package_path}")
        digest = sha256_file(path)
        if canonical.get("sha256") != digest or not canonical.get("wheel_filename") or not canonical.get("wheel_sha256"):
            raise BuildError(f"nvidia_runtime_dll_hash_provenance_mismatch:{package_path}")
        components.append(
            {
                "package_path": package_path,
                "dll_filename": path.name,
                "sha256": digest,
                "component_family": family,
                "originating_distribution": canonical["distribution"],
                "originating_locked_wheel": canonical["wheel_filename"],
                "originating_wheel_sha256": canonical["wheel_sha256"],
                "license_terms_reference": NVIDIA_TERMS_REFERENCE,
                "license_terms_urls": [NVIDIA_CUDA_TERMS_URL, NVIDIA_CUDNN_TERMS_URL],
            }
        )
    families = {item["component_family"] for item in components}
    missing_families = sorted(set(NVIDIA_COMPONENT_FAMILIES) - families)
    if missing_families:
        raise BuildError(f"nvidia_runtime_component_families_missing:{missing_families}")
    if not components:
        raise BuildError("nvidia_runtime_component_inventory_empty")
    return {
        "status": NVIDIA_NOTICE_STATUS,
        "dll_count": len(components),
        "component_family_count": len(families),
        "component_families": [name for name in NVIDIA_COMPONENT_FAMILIES if name in families],
        "cuda_toolkit_shipped": False,
        "nvidia_display_driver_shipped": False,
        "nvcuda_shipped": False,
        "npp_shipped": False,
        "license_terms_urls": [NVIDIA_CUDA_TERMS_URL, NVIDIA_CUDNN_TERMS_URL],
        "components": components,
    }


def write_nvidia_terms_notice(package: Path) -> None:
    notice = (
        "NVIDIA CUDA/cuDNN component notice\n"
        "\n"
        "The component DLLs and their exact locked-wheel provenance are itemized in "
        "THIRD_PARTY_LICENSES.json. This package notice does not assert organizational or legal approval. "
        "Human acceptance of applicable NVIDIA terms remains required.\n"
        "\n"
        f"NVIDIA CUDA Toolkit 12.8 EULA and supplement: {NVIDIA_CUDA_TERMS_URL}\n"
        f"NVIDIA cuDNN software license terms: {NVIDIA_CUDNN_TERMS_URL}\n"
        "\n"
        "The packaged inventory excludes the NVIDIA display driver, nvcuda.dll, and NPP.\n"
    )
    target = package / NVIDIA_TERMS_REFERENCE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(notice, encoding="utf-8")


def verify_packaged_supplemental_license_contract(
    package_files: set[str],
    read_file: Callable[[str], bytes],
    third_party: dict[str, Any],
    provenance: dict[str, Any],
    canonical: dict[str, Any],
) -> dict[str, Any]:
    violations: list[str] = []
    manifest_path = SUPPLEMENTAL_LICENSE_MANIFEST_PATH
    if manifest_path not in package_files:
        return {
            "status": "BLOCKED",
            "violations": [f"supplemental_license_manifest_missing:{manifest_path}"],
            "record_count": 0,
        }
    try:
        manifest_bytes = read_file(manifest_path)
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {
            "status": "BLOCKED",
            "violations": [f"supplemental_license_manifest_unreadable:{exc}"],
            "record_count": 0,
        }

    manifest_reference = {
        "path": manifest_path,
        "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }
    if (
        third_party.get("supplemental_license_manifest") != manifest_reference
        or provenance.get("supplemental_license_manifest") != manifest_reference
    ):
        violations.append("supplemental_license_manifest_hash_mismatch")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SUPPLEMENTAL_LICENSE_SCHEMA:
        violations.append("supplemental_license_manifest_schema_invalid")
        records: list[Any] = []
    else:
        records_value = manifest.get("records")
        if not isinstance(records_value, list):
            violations.append("supplemental_license_manifest_schema_invalid")
            records = []
        else:
            records = records_value

    python_rows = [
        row for row in third_party.get("components", [])
        if isinstance(row, dict) and row.get("component_type") == "python_distribution"
    ]
    by_python_name: dict[str, dict[str, Any]] = {}
    for row in python_rows:
        name = normalize_distribution_name(str(row.get("name", "")))
        if not name or name in by_python_name:
            violations.append(f"python_license_component_duplicate:{name}")
        else:
            by_python_name[name] = row

    canonical_distributions: dict[str, dict[str, Any]] = {}
    for item in canonical.get("distributions", []):
        if not isinstance(item, dict):
            continue
        name = normalize_distribution_name(
            str(item.get("normalized_name", item.get("name", "")))
        )
        if not name or name in canonical_distributions:
            violations.append(f"canonical_python_distribution_invalid:{name}")
        else:
            canonical_distributions[name] = item
    wheel_identities: dict[str, set[tuple[str, str]]] = {}
    for item in canonical.get("files", []):
        if not isinstance(item, dict):
            continue
        name = normalize_distribution_name(str(item.get("distribution", "")))
        wheel_filename = item.get("wheel_filename")
        wheel_sha256 = item.get("wheel_sha256")
        if name and isinstance(wheel_filename, str) and isinstance(wheel_sha256, str):
            wheel_identities.setdefault(name, set()).add((wheel_filename, wheel_sha256))

    records_by_name: dict[str, dict[str, str]] = {}
    for raw_record in records:
        if not isinstance(raw_record, dict):
            violations.append("supplemental_license_record_invalid")
            continue
        raw_name = raw_record.get("normalized_package_name")
        name = normalize_distribution_name(raw_name) if isinstance(raw_name, str) else ""
        if not name or name in records_by_name:
            violations.append(f"supplemental_license_duplicate_or_invalid:{name}")
            continue
        distribution = canonical_distributions.get(name)
        identities = wheel_identities.get(name, set())
        if distribution is None or len(identities) != 1:
            violations.append(f"supplemental_license_orphan_or_unlocked:{name}")
            continue
        wheel_filename, wheel_sha256 = next(iter(identities))
        lock_item = {
            "normalized_name": name,
            "version": distribution.get("version"),
            "wheel_filename": wheel_filename,
            "sha256": wheel_sha256,
        }
        try:
            record = _validated_supplemental_license_record(
                raw_record, lock_item, packaged=True
            )
        except BuildError as exc:
            violations.append(str(exc))
            continue
        if record != raw_record:
            violations.append(f"supplemental_license_record_not_normalized:{name}")
        records_by_name[name] = record
        if name not in by_python_name:
            violations.append(f"supplemental_license_orphan_record:{name}")
            continue
        row = by_python_name[name]
        lock = row.get("lock") or {}
        package_license_path = record["package_license_path"]
        if (
            row.get("version") != record["version"]
            or lock.get("wheel_filename") != record["locked_wheel_filename"]
            or lock.get("wheel_sha256") != record["locked_wheel_sha256"]
        ):
            violations.append(f"supplemental_license_component_identity_mismatch:{name}")
        if (
            row.get("license_file_not_present_in_distribution") is not True
            or row.get("license_evidence_status") != "RESOLVED_FROM_SUPPLEMENTAL"
            or row.get("license_evidence_source") != "supplemental_manifest"
            or row.get("supplemental_license") != record
            or row.get("license_files") != [package_license_path]
        ):
            violations.append(f"supplemental_license_component_record_mismatch:{name}")
        if package_license_path not in package_files:
            violations.append(f"supplemental_license_file_missing:{name}:{package_license_path}")
        else:
            actual_hash = hashlib.sha256(read_file(package_license_path)).hexdigest()
            if (
                actual_hash != record["license_text_sha256"]
                or actual_hash
                != (row.get("license_file_sha256") or {}).get(package_license_path)
            ):
                violations.append(f"supplemental_license_file_hash_mismatch:{name}")

    for name, row in by_python_name.items():
        record = records_by_name.get(name)
        if record is not None and (
            row.get("license_file_not_present_in_distribution") is not True
            or row.get("license_evidence_status") != "RESOLVED_FROM_SUPPLEMENTAL"
        ):
            violations.append(f"supplemental_license_not_needed:{name}")
        elif record is None and (
            row.get("license_file_not_present_in_distribution") is True
            or row.get("license_evidence_status") == "RESOLVED_FROM_SUPPLEMENTAL"
        ):
            violations.append(f"supplemental_license_missing_for_unresolved_component:{name}")

    return {
        "status": "PASS" if not violations else "BLOCKED",
        "violations": list(dict.fromkeys(violations)),
        "path": manifest_path,
        "sha256": manifest_reference["sha256"],
        "record_count": len(records_by_name),
    }


def scheduler_dependency_edge_is_valid(
    edge: dict[str, Any],
    importer: dict[str, Any],
    scheduler: dict[str, Any],
    frontend_build: dict[str, Any],
) -> bool:
    path_values = tuple(
        edge.get(key)
        for key in (
            "importer_package_path",
            "dependency_context_path",
            "resolved_package_path",
        )
    )
    if any(not isinstance(value, str) or not value for value in path_values):
        return False
    importer_path, context_path, resolved_path = map(PurePosixPath, path_values)
    if any(
        path.is_absolute()
        or ".." in path.parts
        or ":" in value
        or "\\" in value
        or "\x00" in value
        for value, path in zip(path_values, (importer_path, context_path, resolved_path))
    ):
        return False
    if context_path.parent not in (importer_path.parent, importer_path / "node_modules"):
        return False
    if importer_path.name != "react-dom" or context_path.name != "scheduler" or resolved_path.name != "scheduler":
        return False
    hashes = (
        edge.get("importer_package_json_sha256"),
        edge.get("dependency_context_package_json_sha256"),
        edge.get("resolved_package_json_sha256"),
        edge.get("pnpm_lock_sha256"),
    )
    if any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None for value in hashes):
        return False
    importer_hash, context_hash, resolved_hash, lock_hash = hashes
    return (
        edge.get("importer_name") == "react-dom"
        and edge.get("importer_version") == FRONTEND_RUNTIME_PACKAGE_VERSIONS["react-dom"]
        and edge.get("declared_dependency") == "^0.27.0"
        and edge.get("lock_snapshot") == "react-dom@19.2.0(react@19.2.0)"
        and edge.get("locked_version") == FRONTEND_RUNTIME_PACKAGE_VERSIONS["scheduler"]
        and importer_hash == importer.get("source_package_json_sha256")
        and context_hash == scheduler.get("source_package_json_sha256")
        and resolved_hash == scheduler.get("source_package_json_sha256")
        and context_hash == resolved_hash
        and lock_hash == frontend_build.get("pnpm_lock_sha256")
    )


def verify_license_notice_contract(
    package_files: set[str], read_file: Callable[[str], bytes]
) -> dict[str, Any]:
    violations: list[str] = []
    required_paths = {
        "app/LICENSE",
        "LICENSES/application/LICENSE",
        "LICENSES/nvidia/NVIDIA_COMPONENT_TERMS.txt",
        "LICENSES/ffmpeg/LICENSE.txt",
        "LICENSES/microsoft-vc-redist/README.txt",
        SUPPLEMENTAL_LICENSE_MANIFEST_PATH,
        "THIRD_PARTY_LICENSES.json",
        "PACKAGE_PROVENANCE.json",
        "PORTABLE_RUNTIME_CANONICAL.json",
    }
    for path in sorted(required_paths - package_files):
        violations.append(f"license_required_file_missing:{path}")
    if violations:
        return {"status": "BLOCKED", "violations": violations}

    app_evidence: dict[str, Any] = {}
    try:
        app_license = read_file("app/LICENSE")
        application_license = read_file("LICENSES/application/LICENSE")
        app_evidence = validate_agpl_license_bytes(app_license, "app/LICENSE")
        package_evidence = validate_agpl_license_bytes(application_license, "LICENSES/application/LICENSE")
        if app_license != application_license:
            violations.append("application_license_copy_mismatch")
        if app_evidence["sha256"] != package_evidence["sha256"]:
            violations.append("application_license_hash_mismatch")
    except (OSError, BuildError) as exc:
        violations.append(str(exc))

    third_party = json.loads(read_file("THIRD_PARTY_LICENSES.json").decode("utf-8"))
    provenance = json.loads(read_file("PACKAGE_PROVENANCE.json").decode("utf-8"))
    canonical = json.loads(read_file("PORTABLE_RUNTIME_CANONICAL.json").decode("utf-8"))
    if provenance.get("license_inventory") != "THIRD_PARTY_LICENSES.json":
        violations.append("license_inventory_reference_invalid")
    lock_count = provenance.get("python_runtime_lock", {}).get("locked_distribution_count")
    python_rows = [
        item for item in third_party.get("components", [])
        if item.get("component_type") == "python_distribution"
    ]
    application_record = third_party.get("application_license", {})
    if (
        application_record.get("source_url") != AGPL_SOURCE_URL
        or application_record.get("sha256") != app_evidence.get("sha256")
        or application_record.get("app_license") != "app/LICENSE"
        or application_record.get("package_license") != "LICENSES/application/LICENSE"
        or provenance.get("application_license") != application_record
    ):
        violations.append("application_license_provenance_mismatch")
    by_python_name: dict[str, dict[str, Any]] = {}
    for item in python_rows:
        name = normalize_distribution_name(str(item.get("name", "")))
        if not name or name in by_python_name:
            violations.append(f"python_license_component_duplicate:{name}")
        by_python_name[name] = item
        missing_distribution_file = item.get("license_file_not_present_in_distribution") is True
        expected_status = "RESOLVED_FROM_SUPPLEMENTAL" if missing_distribution_file else "RESOLVED_FROM_DISTRIBUTION"
        if item.get("license_evidence_status") != expected_status or not item.get("license_files"):
            violations.append(f"python_license_evidence_unresolved:{name}")
        for path in item.get("license_files", []):
            if path not in package_files:
                violations.append(f"python_license_file_missing:{name}:{path}")
                continue
            expected_hash = item.get("license_file_sha256", {}).get(path)
            if not expected_hash or hashlib.sha256(read_file(path)).hexdigest() != expected_hash:
                violations.append(f"python_license_file_hash_mismatch:{name}:{path}")
    python_summary = third_party.get("python_license_evidence", {})
    if (
        lock_count != EXPECTED_PYTHON_DISTRIBUTIONS
        or len(python_rows) != lock_count
        or python_summary.get("locked_distribution_count") != lock_count
        or python_summary.get("resolved_license_evidence_count") != lock_count
        or python_summary.get("unresolved_count") != 0
        or python_summary.get("status") != "PASS"
    ):
        violations.append("python_license_completeness_count_mismatch")
    if len(canonical.get("distributions", [])) != lock_count:
        violations.append("canonical_python_distribution_count_mismatch")
    canonical_python: dict[str, dict[str, Any]] = {}
    for item in canonical.get("distributions", []):
        name = normalize_distribution_name(
            str(item.get("normalized_name", item.get("name", "")))
        )
        if not name or name in canonical_python:
            violations.append(f"canonical_python_distribution_invalid:{name}")
        canonical_python[name] = item
    if set(canonical_python) != set(by_python_name):
        violations.append("python_license_runtime_set_mismatch")
    for name, item in canonical_python.items():
        component = by_python_name.get(name)
        if component is None or component.get("version") != item.get("version"):
            violations.append(f"python_license_runtime_identity_mismatch:{name}")
    canonical_wheels: dict[str, set[tuple[str, str]]] = {}
    for item in canonical.get("files", []):
        name = normalize_distribution_name(str(item.get("distribution", "")))
        wheel_filename = item.get("wheel_filename")
        wheel_sha256 = item.get("wheel_sha256")
        if name and wheel_filename and wheel_sha256:
            canonical_wheels.setdefault(name, set()).add((wheel_filename, wheel_sha256))
    for name, component in by_python_name.items():
        identities = canonical_wheels.get(name, set())
        lock = component.get("lock") or {}
        if len(identities) != 1 or next(iter(identities), None) != (
            lock.get("wheel_filename"),
            lock.get("wheel_sha256"),
        ):
            violations.append(f"python_license_wheel_identity_mismatch:{name}")
    supplemental_validation = verify_packaged_supplemental_license_contract(
        package_files, read_file, third_party, provenance, canonical
    )
    violations.extend(supplemental_validation["violations"])
    for name, required_notices in {
        "torch": {"LICENSE", "NOTICE"},
        "torchvision": {"LICENSE"},
    }.items():
        vendor = by_python_name.get(name)
        found_notices = {
            Path(path).name.upper() for path in vendor.get("license_files", [])
        } if vendor else set()
        if not required_notices.issubset(found_notices):
            violations.append(f"pytorch_vendor_license_notice_missing:{name}")
    tqdm = by_python_name.get("tqdm")
    if not tqdm or tqdm.get("version") != "4.69.0" or not any(
        "licence" in Path(path).name.casefold() for path in tqdm.get("license_files", [])
    ):
        violations.append("tqdm_licence_not_packaged")
    frontend_rows = [
        item for item in third_party.get("components", [])
        if item.get("component_type") == "frontend_runtime_package"
    ]
    frontend_by_name = {str(item.get("name")): item for item in frontend_rows}
    if len(frontend_by_name) != len(frontend_rows) or set(frontend_by_name) != set(
        FRONTEND_RUNTIME_PACKAGE_VERSIONS
    ):
        violations.append("frontend_runtime_license_set_mismatch")
    for name, version in FRONTEND_RUNTIME_PACKAGE_VERSIONS.items():
        item = frontend_by_name.get(name)
        if item is None or item.get("version") != version or not item.get("license_files"):
            violations.append(f"frontend_runtime_license_unresolved:{name}")
            continue
        for path in item.get("license_files", []):
            if path not in package_files:
                violations.append(f"frontend_runtime_license_file_missing:{name}:{path}")
                continue
            if hashlib.sha256(read_file(path)).hexdigest() != item.get("license_file_sha256", {}).get(path):
                violations.append(f"frontend_runtime_license_file_hash_mismatch:{name}:{path}")
    frontend_build = next(
        (item for item in third_party.get("components", []) if item.get("component_type") == "frontend_build"),
        {},
    )
    runtime_records = frontend_build.get("runtime_packages", [])
    bundled_frontend_packages = {
        str(item.get("name")): item.get("version") for item in runtime_records
    }
    if (
        len(bundled_frontend_packages) != len(runtime_records)
        or bundled_frontend_packages != FRONTEND_RUNTIME_PACKAGE_VERSIONS
    ):
        violations.append("frontend_runtime_bundle_provenance_mismatch")
    for item in frontend_rows:
        if item.get("pnpm_lock_sha256") != frontend_build.get("pnpm_lock_sha256"):
            violations.append(f"frontend_runtime_lock_identity_mismatch:{item.get('name')}")
    js_bundle_count = 0
    for asset in frontend_build.get("bundle_assets", []):
        path = asset.get("path")
        if not isinstance(path, str) or not path.startswith("frontend/dist/") or path not in package_files:
            violations.append(f"frontend_bundle_asset_missing:{path}")
            continue
        if Path(path).suffix.lower() == ".js":
            js_bundle_count += 1
        if hashlib.sha256(read_file(path)).hexdigest() != asset.get("sha256"):
            violations.append(f"frontend_bundle_asset_hash_mismatch:{path}")
    if not js_bundle_count:
        violations.append("frontend_compiled_runtime_bundle_missing")

    scheduler = frontend_by_name.get("scheduler") or {}
    react_dom = frontend_by_name.get("react-dom") or {}
    edge = frontend_build.get("scheduler_dependency_edge") or {}
    if not scheduler_dependency_edge_is_valid(edge, react_dom, scheduler, frontend_build):
        violations.append("frontend_scheduler_dependency_edge_provenance_mismatch")

    emitted_rows = [
        item for item in third_party.get("components", [])
        if item.get("component_type") == "frontend_emitted_code_component"
    ]
    emitted_by_name = {str(item.get("name")): item for item in emitted_rows}
    if (
        len(emitted_by_name) != len(emitted_rows)
        or set(emitted_by_name) != set(FRONTEND_EMITTED_CODE_COMPONENT_VERSIONS)
    ):
        violations.append("frontend_emitted_code_component_set_mismatch")
    for name, version in FRONTEND_EMITTED_CODE_COMPONENT_VERSIONS.items():
        item = emitted_by_name.get(name)
        if (
            item is None
            or item.get("version") != version
            or item.get("source_package_name") != name
            or item.get("source_package_version") != version
            or item.get("license") != "MIT"
            or item.get("license_evidence_status") != "RESOLVED_FROM_INSTALLED_PACKAGE"
            or item.get("pnpm_package_key") != f"{name}@{version}"
            or item.get("pnpm_lock_sha256") != frontend_build.get("pnpm_lock_sha256")
            or item.get("source_package_manifest_sha256")
            != frontend_build.get("source_package_manifest_sha256")
            or not re.fullmatch(
                r"[0-9a-f]{64}", str(item.get("source_package_json_sha256", ""))
            )
            or item.get("generator_source") != "dist/node/chunks/config.js"
            or not re.fullmatch(
                r"[0-9a-f]{64}", str(item.get("generator_source_sha256", ""))
            )
            or "LICENSE.md" not in {Path(path).name for path in item.get("license_files", [])}
            or not item.get("license_files")
        ):
            violations.append(f"frontend_emitted_code_component_unresolved:{name}")
            continue
        for path in item.get("license_files", []):
            if path not in package_files:
                violations.append(f"frontend_emitted_code_license_file_missing:{name}:{path}")
                continue
            if hashlib.sha256(read_file(path)).hexdigest() != item.get(
                "license_file_sha256", {}
            ).get(path):
                violations.append(f"frontend_emitted_code_license_file_hash_mismatch:{name}:{path}")
    recorded_vite_evidence = frontend_build.get("vite_emitted_code")
    try:
        actual_vite_evidence = _vite_modulepreload_evidence(
            frontend_build.get("bundle_assets", []), read_file
        )
    except (BuildError, OSError) as exc:
        violations.append(str(exc))
    else:
        if recorded_vite_evidence != actual_vite_evidence:
            violations.append("frontend_vite_emitted_code_provenance_mismatch")

    nvidia = third_party.get("nvidia_runtime_inventory", {})
    nvidia_rows = nvidia.get("components", [])
    actual_nvidia_paths: set[str] = set()
    canonical_by_path: dict[str, dict[str, Any]] = {}
    for row in canonical.get("files", []):
        final_path = row.get("canonical_final_path")
        if final_path:
            package_path = PurePosixPath("runtime/python", str(final_path)).as_posix()
            canonical_by_path.setdefault(package_path, row)
    for path in package_files:
        if not path.casefold().endswith(".dll"):
            continue
        filename = Path(path).name.casefold()
        if filename == "nvcuda.dll" or (filename.startswith("npp") and filename.endswith(".dll")):
            violations.append(f"unbundled_nvidia_driver_or_npp:{path}")
        if path.startswith("runtime/python/Lib/site-packages/") and nvidia_component_family(filename):
            actual_nvidia_paths.add(path)
    inventory_paths = {str(item.get("package_path")) for item in nvidia_rows}
    if len(inventory_paths) != len(nvidia_rows) or inventory_paths != actual_nvidia_paths:
        violations.append("nvidia_runtime_inventory_file_set_mismatch")
    for item in nvidia_rows:
        path = str(item.get("package_path", ""))
        canonical_row = canonical_by_path.get(path)
        if path not in package_files or canonical_row is None:
            violations.append(f"nvidia_runtime_provenance_missing:{path}")
            continue
        if item.get("dll_filename") != Path(path).name or hashlib.sha256(read_file(path)).hexdigest() != item.get("sha256"):
            violations.append(f"nvidia_runtime_hash_mismatch:{path}")
        if (
            item.get("originating_distribution") != canonical_row.get("distribution")
            or item.get("originating_locked_wheel") != canonical_row.get("wheel_filename")
            or item.get("originating_wheel_sha256") != canonical_row.get("wheel_sha256")
            or item.get("component_family") != nvidia_component_family(Path(path).name)
            or item.get("license_terms_reference") != NVIDIA_TERMS_REFERENCE
        ):
            violations.append(f"nvidia_runtime_provenance_mismatch:{path}")
    nvidia_terms = read_file(NVIDIA_TERMS_REFERENCE).decode("utf-8", errors="replace")
    if (
        nvidia.get("status") != NVIDIA_NOTICE_STATUS
        or nvidia.get("dll_count") != len(actual_nvidia_paths)
        or nvidia.get("component_family_count") != len(NVIDIA_COMPONENT_FAMILIES)
        or set(nvidia.get("component_families", [])) != set(NVIDIA_COMPONENT_FAMILIES)
        or NVIDIA_CUDA_TERMS_URL not in nvidia_terms
        or NVIDIA_CUDNN_TERMS_URL not in nvidia_terms
    ):
        violations.append("nvidia_runtime_notice_incomplete")

    ffmpeg = provenance.get("ffmpeg", {})
    ffmpeg_component = next(
        (item for item in third_party.get("components", []) if item.get("component_type") == "ffmpeg"),
        {},
    )
    if (
        third_party.get("ffmpeg_status") != FFMPEG_STATUS
        or ffmpeg.get("status") != FFMPEG_STATUS
        or ffmpeg.get("source_bundle_required") is not True
        or ffmpeg_component.get("status") != FFMPEG_STATUS
        or ffmpeg_component.get("source_bundle_required") is not True
        or "source_bundle_url" in ffmpeg
        or "source_bundle_url" in ffmpeg_component
        or ffmpeg.get("asset_filename") != FFMPEG_FILENAME
        or ffmpeg.get("asset_sha256") != FFMPEG_SHA256
        or "--enable-gpl" in str(ffmpeg.get("ffmpeg_buildconf", ""))
        or "--enable-nonfree" in str(ffmpeg.get("ffmpeg_buildconf", ""))
    ):
        violations.append("ffmpeg_source_bundle_status_invalid")

    msvc = provenance.get("microsoft_runtime", {})
    msvc_rows = third_party.get("microsoft_runtime_files", [])
    msvc_notice = read_file("LICENSES/microsoft-vc-redist/README.txt").decode("utf-8", errors="replace")
    if (
        third_party.get("microsoft_runtime_status") != MSVC_STATUS
        or msvc.get("status") != MSVC_STATUS
        or len(msvc_rows) != len(MSVC_DLLS)
        or {item.get("filename") for item in msvc_rows} != set(MSVC_DLLS)
        or "organizational entitlement confirmed" in msvc_notice.casefold()
    ):
        violations.append("msvc_user_confirmation_status_invalid")

    return {
        "status": "PASS" if not violations else "BLOCKED",
        "violations": list(dict.fromkeys(violations)),
        "application_license": app_evidence or None,
        "python_license_evidence": python_summary,
        "supplemental_license_manifest": supplemental_validation,
        "frontend_runtime_package_count": len(frontend_rows),
        "nvidia_runtime_dll_count": len(nvidia_rows),
        "nvidia_component_family_count": nvidia.get("component_family_count"),
        "nvidia_notice_status": nvidia.get("status"),
        "ffmpeg_status": ffmpeg.get("status"),
        "microsoft_runtime_status": msvc.get("status"),
    }


def package_inventory(site_packages: Path) -> list[dict[str, str | None]]:
    inventory: list[dict[str, str | None]] = []
    for metadata_path in sorted(site_packages.glob("*.dist-info/METADATA")):
        metadata = parse_metadata(metadata_path)
        inventory.append({"name": metadata["name"], "version": metadata["version"]})
    return inventory


def verify_ai_inventory(site_packages: Path) -> list[dict[str, Any]]:
    metadata_by_name: dict[str, dict[str, Any]] = {}
    for metadata_path in site_packages.glob("*.dist-info/METADATA"):
        metadata = parse_metadata(metadata_path)
        metadata_by_name[str(metadata["name"]).lower().replace("_", "-")] = metadata
    result: list[dict[str, Any]] = []
    for package_name, required in AI_REQUIREMENTS.items():
        metadata = metadata_by_name.get(package_name.lower().replace("_", "-"))
        actual = metadata.get("version") if metadata else None
        matches = actual == required or (
            package_name not in {"torch", "torchvision"} and actual == required.split("+", 1)[0]
        )
        result.append(
            {
                "package": package_name,
                "required_version": required,
                "installed_version": actual,
                "present": metadata is not None,
                "matches_required": matches,
            }
        )
    blockers = [item for item in result if not item["matches_required"]]
    if blockers:
        raise BuildError(f"ai_package_inventory_mismatch:{blockers}")
    return result


def ffmpeg_metadata(archive: Path, package: Path) -> dict[str, Any]:
    extracted = package / ".ffmpeg-extract"
    extract_zip(archive, extracted)
    ffmpeg_candidates = list(extracted.rglob("ffmpeg.exe"))
    ffprobe_candidates = list(extracted.rglob("ffprobe.exe"))
    license_candidates = list(extracted.rglob("LICENSE.txt"))
    if len(ffmpeg_candidates) != 1 or len(ffprobe_candidates) != 1:
        raise BuildError("ffmpeg_archive_missing_expected_binaries")
    ffmpeg = package / "runtime" / "ffmpeg" / "ffmpeg.exe"
    ffprobe = package / "runtime" / "ffmpeg" / "ffprobe.exe"
    copy_file(ffmpeg_candidates[0], ffmpeg)
    copy_file(ffprobe_candidates[0], ffprobe)
    if license_candidates:
        copy_file(license_candidates[0], package / "LICENSES" / "ffmpeg" / "LICENSE.txt")
    version_result = subprocess.run(
        [str(ffmpeg), "-version"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    buildconf_result = subprocess.run(
        [str(ffmpeg), "-buildconf"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    combined = f"{version_result.stdout}\n{buildconf_result.stdout}"
    if version_result.returncode != 0 or buildconf_result.returncode != 0:
        raise BuildError("ffmpeg_probe_failed")
    if "--enable-gpl" in combined or "--enable-nonfree" in combined:
        raise BuildError("ffmpeg_license_state_rejected_gpl_or_nonfree")
    shutil.rmtree(extracted, ignore_errors=False)
    return {
        "repository": FFMPEG_REPOSITORY,
        "release_tag": FFMPEG_TAG,
        "asset_filename": FFMPEG_FILENAME,
        "asset_url": FFMPEG_URL,
        "asset_sha256": sha256_file(archive),
        "ffmpeg_sha256": sha256_file(ffmpeg),
        "ffprobe_sha256": sha256_file(ffprobe),
        "ffmpeg_version": version_result.stdout.strip(),
        "ffmpeg_buildconf": buildconf_result.stdout.strip(),
        "license_state": "LGPL_candidate_no_enable_gpl_no_enable_nonfree",
        "status": FFMPEG_STATUS,
        "source_bundle_required": True,
        "license_files": ["LICENSES/ffmpeg/LICENSE.txt"] if license_candidates else [],
    }


def msvc_file_version(path: Path) -> str | None:
    if os.name != "nt":
        return None
    query = f"(Get-Item -LiteralPath '{str(path).replace(chr(39), chr(39) * 2)}').VersionInfo.FileVersion"
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", query],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None


def copy_msvc_runtime(
    package: Path,
    source: Path | None,
    source_metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if source is None:
        return []
    if not source.is_dir():
        raise BuildError(f"msvc_source_missing:{source}")
    records: list[dict[str, Any]] = []
    for name in sorted(MSVC_DLLS):
        candidate = source / name
        if not candidate.is_file():
            continue
        expected_sha256 = MSVC_FILE_SHA256.get(name)
        if expected_sha256 is None or sha256_file(candidate) != expected_sha256:
            raise BuildError(f"msvc_runtime_hash_mismatch:{name}")
        destination = package / "runtime" / "python" / name
        copy_file(candidate, destination)
        records.append(
            {
                "filename": name,
                "version": msvc_file_version(destination),
                "sha256": sha256_file(destination),
                "source": (source_metadata or {}).get("source", "official_redist_directory"),
                "source_artifact_filename": (source_metadata or {}).get("artifact_filename"),
                "source_artifact_sha256": (source_metadata or {}).get("artifact_sha256"),
                "source_artifact_url": (source_metadata or {}).get("artifact_url"),
                "terms_location": "LICENSES/microsoft-vc-redist/README.txt",
            }
        )
    if records:
        readme = package / "LICENSES" / "microsoft-vc-redist" / "README.txt"
        readme.parent.mkdir(parents=True, exist_ok=True)
        readme.write_text(
            f"These app-local Microsoft Visual C++ runtime DLLs came from {MSVC_REDIST_FILENAME}.\n"
            f"Official artifact SHA-256: {MSVC_REDIST_SHA256}\n"
            f"Official source: {MSVC_REDIST_URL}\n"
            "Redistribution remains subject to the applicable Microsoft Visual Studio license terms.\n",
            encoding="utf-8",
        )
    return records


def package_file_records(package: Path, excluded: set[str] | None = None) -> list[dict[str, Any]]:
    excluded = excluded or set()
    records: list[dict[str, Any]] = []
    for path in sorted(package.rglob("*")):
        if not path.is_file():
            continue
        relative = relpath(path, package)
        if relative in excluded:
            continue
        records.append({"path": relative, "size": path.stat().st_size, "sha256": sha256_file(path)})
    return records


def privacy_scan(
    package: Path,
    zip_path: Path | None = None,
    *,
    forbidden_markers: Iterable[str] = (),
    forbidden_roots: Iterable[str] = (),
    forbidden_emails: Iterable[str] = (),
) -> dict[str, Any]:
    violations: list[str] = []
    hits: list[dict[str, Any]] = []
    notices: list[dict[str, Any]] = []
    files = list(package.rglob("*"))
    private_path_re = _private_path_pattern()
    private_markers = tuple(dict.fromkeys((*PRIVATE_MARKERS, *forbidden_markers)))
    current_roots = tuple(current_private_roots())
    def package_payloads() -> Iterable[tuple[str, bytes]]:
        for path in files:
            if not path.is_file():
                continue
            relative = relpath(path, package)
            parts = {part.casefold() for part in PurePosixPath(relative).parts}
            if ".git" in parts or "node_modules" in parts or "__pycache__" in parts:
                violations.append(f"forbidden_path:{relative}")
            if path.suffix.lower() in MODEL_EXTENSIONS and relative != "models/yolo11n.pt":
                violations.append(f"unapproved_model:{relative}")
            if path.name.casefold() in {
                "node.exe",
                "npm",
                "npm.cmd",
                "pnpm",
                "pnpm.cmd",
                "vite",
                "git.exe",
            }:
                violations.append(f"forbidden_tool:{relative}")
            if re.search(r"(?i)(^|[\\/])\.env(?:$|[.])", relative):
                violations.append(f"environment_file:{relative}")
            try:
                yield relative, path.read_bytes()
            except OSError as exc:
                violations.append(f"unreadable:{relative}:{type(exc).__name__}")
    payload_violations, payload_hits, payload_notices = scan_payloads(
        package_payloads(),
        private_markers=private_markers,
        private_roots=forbidden_roots,
        current_roots=current_roots,
        private_emails=forbidden_emails,
        private_path_pattern=private_path_re,
        secret_patterns=SECRET_PATTERNS,
    )
    violations.extend(payload_violations)
    hits.extend(payload_hits)
    notices.extend(payload_notices)
    zip_result = None
    if zip_path is not None:
        with zipfile.ZipFile(zip_path) as archive:
            try:
                members = safe_zip_infos(archive)
            except ArchiveMemberError as exc:
                raise BuildError(str(exc)) from exc
            zip_result = {"members": len(members), "violations": [], "hits": [], "notices": []}
            def zip_payloads() -> Iterable[tuple[str, bytes]]:
                for info in members:
                    name = info.filename
                    if any(
                        marker in name.casefold() for marker in (".git", "node_modules", "refs/codex")
                    ):
                        zip_result["violations"].append(f"forbidden_zip_path:{name}")
                    if (
                        PurePosixPath(name).suffix.lower() in MODEL_EXTENSIONS
                        and name != "models/yolo11n.pt"
                    ):
                        zip_result["violations"].append(f"unapproved_zip_model:{name}")
                    yield name, archive.read(info)
            zip_payload_violations, zip_hits, zip_notices = scan_payloads(
                zip_payloads(),
                private_markers=private_markers,
                private_roots=forbidden_roots,
                current_roots=current_roots,
                private_emails=forbidden_emails,
                private_path_pattern=private_path_re,
                secret_patterns=SECRET_PATTERNS,
            )
            zip_result["violations"].extend(zip_payload_violations)
            zip_result["hits"].extend(zip_hits)
            zip_result["notices"].extend(zip_notices)
            violations.extend(zip_result["violations"])
            hits.extend(zip_result["hits"])
            notices.extend(zip_result["notices"])
    return {
        "status": "PASS" if not violations else "BLOCKED",
        "violations": list(dict.fromkeys(violations)),
        "hits": hits,
        "notices": notices,
        "heuristic_hits": notices,
        "zip": zip_result,
    }


def write_deterministic_zip(package: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=True
    ) as archive:
        for path in sorted(package.rglob("*")):
            if not path.is_file():
                continue
            relative = relpath(path, package)
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def verify_manifest(
    package: Path, manifest: dict[str, Any], *, rehash: bool = True
) -> dict[str, Any]:
    expected = {item["path"]: item for item in manifest["files"]}
    if rehash:
        actual = {
            item["path"]: item
            for item in package_file_records(package, set(manifest["excluded_from_manifest"]))
        }
    else:
        actual = {
            relpath(path, package): {"path": relpath(path, package), "size": path.stat().st_size}
            for path in package.rglob("*")
            if path.is_file()
            and relpath(path, package) not in set(manifest["excluded_from_manifest"])
        }
    mismatches: list[str] = []
    if set(expected) != set(actual):
        mismatches.append("file_set_mismatch")
    for path, item in expected.items():
        current = actual.get(path)
        if current is None:
            continue
        if current["size"] != item["size"] or (rehash and current["sha256"] != item["sha256"]):
            mismatches.append(f"file_hash_mismatch:{path}")
    return {"status": "PASS" if not mismatches else "BLOCKED", "mismatches": mismatches}


def _extended_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    value = str(path)
    if value.startswith("\\\\?\\"):
        return path
    return Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)


def create_stage(stage_root: Path) -> Path:
    """Create a short builder-owned stage child without output-name input."""
    stage = Path(tempfile.mkdtemp(prefix=BUILDER_STAGE_PREFIX, dir=stage_root))
    if not BUILDER_STAGE_NAME_RE.fullmatch(stage.name):
        raise BuildError(f"stage_name_invalid:{stage.name}")
    return stage


def cleanup_stage(stage: Path, stage_root: Path) -> None:
    """Remove only a builder-named direct child of its configured stage root."""
    for component in (stage, *stage.parents, stage_root, *stage_root.parents):
        try:
            info = _extended_path(component.absolute()).lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise BuildError(f"stage_cleanup_reparse:{component}")
    root = stage_root.resolve(strict=True)
    target = stage.resolve()
    if target.parent != root or not (
        BUILDER_STAGE_NAME_RE.fullmatch(target.name)
        or LEGACY_STAGE_NAME_RE.fullmatch(target.name)
    ):
        raise BuildError(f"stage_cleanup_target_invalid:{stage}")
    target = _extended_path(target)
    if not target.exists():
        return

    def walk_error(error: OSError) -> None:
        raise error

    # Refuse reparse entries before deleting anything; never follow a junction.
    for directory, dirs, files in os.walk(target, followlinks=False, onerror=walk_error):
        for name in dirs + files:
            entry = Path(directory) / name
            info = entry.lstat()
            if stat.S_ISLNK(info.st_mode) or (
                getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise BuildError(f"stage_cleanup_reparse:{entry}")
    shutil.rmtree(target, ignore_errors=False)


def finish_stage_cleanup(
    stage: Path, stage_root: Path, report_path: Path, primary_error: BaseException | None
) -> None:
    try:
        cleanup_stage(stage, stage_root)
    except Exception as exc:
        diagnostic = f"stage_cleanup_failed:{type(exc).__name__}:{exc}"
        failure = primary_error if primary_error is not None else BuildError(diagnostic)
        failure.add_note(diagnostic)
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report.update(status="BLOCKED", cleanup_error=diagnostic)
            write_json(report_path, report)
        except Exception as report_error:
            failure.add_note(f"stage_cleanup_report_failed:{report_error}")
        if primary_error is None:
            raise failure from exc


def build(args: argparse.Namespace) -> dict[str, Any]:
    source = _canonical_path(args.source)
    state = source_state(
        source,
        getattr(args, "build_mode", "qualification"),
        getattr(args, "expected_source_sha", None),
        getattr(args, "accepted_baseline_sha", None),
        getattr(args, "allow_dirty", False),
    )
    runtime_lock_path = (
        args.runtime_lock.resolve()
        if getattr(args, "runtime_lock", None)
        else source / "packages" / "portable" / "windows-x64-cp312-runtime.lock.json"
    )
    try:
        runtime_lock = load_runtime_lock(runtime_lock_path)
    except RuntimeLockError as exc:
        raise BuildError(str(exc)) from exc
    explicit_roots = getattr(args, "_explicit_build_roots", None)

    def supplied_root(name: str, attribute: str) -> Path | None:
        if explicit_roots is not None and not explicit_roots[name]:
            return None
        return getattr(args, attribute, None)

    paths = resolve_build_paths(
        source,
        getattr(args, "build_mode", "qualification"),
        str(state["head_sha"])[:12],
        output=supplied_root("output", "output"),
        cache_dir=supplied_root("cache", "cache_dir"),
        wheelhouse=supplied_root("wheelhouse", "wheelhouse"),
        stage_root=supplied_root("stage", "stage_root"),
    )
    frontend_dist = build_frontend(source, args.skip_frontend_build, args.pnpm)
    cache_dir = paths.cache_dir
    wheelhouse = paths.wheelhouse
    output_dir = paths.output
    if output_dir.exists() and any(output_dir.iterdir()):
        raise BuildError(f"output_exists:{output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    # Keep the temporary staging prefix short. PyTorch ships a few very deep
    # header paths; a short project-local root preserves Windows path-length
    # handling without granting output authority over staging.
    stage_parent = paths.stage_root
    stage_parent.mkdir(parents=True, exist_ok=True)
    stage = create_stage(stage_parent)
    package = stage / "TrafficVideoAnalytics"
    path_budget: dict[str, Any] | None = None
    native_record: dict[str, Any] | None = None
    try:
        package.mkdir(parents=True, exist_ok=True)
        python_archive = download_verified(
            url=CPYTHON_URL,
            filename=CPYTHON_FILENAME,
            expected_sha256=CPYTHON_SHA256,
            cache_dir=cache_dir,
            accept_downloads=args.accept_downloads,
        )
        ffmpeg_archive = download_verified(
            url=FFMPEG_URL,
            filename=FFMPEG_FILENAME,
            expected_sha256=FFMPEG_SHA256,
            cache_dir=cache_dir,
            accept_downloads=args.accept_downloads,
        )
        msvc_source, msvc_source_metadata = prepare_msvc_redist_source(
            cache_dir,
            args.msvc_redist_dir.resolve() if args.msvc_redist_dir else None,
            args.accept_downloads,
        )
        model_source = (
            args.model.resolve()
            if args.model
            else source / ".local-tools" / "models" / MODEL_FILENAME
        )
        if not model_source.is_file():
            model_source = download_verified(
                url=MODEL_URL,
                filename=MODEL_FILENAME,
                expected_sha256=MODEL_SHA256,
                cache_dir=cache_dir,
                accept_downloads=args.accept_downloads,
            )
        if sha256_file(model_source) != MODEL_SHA256:
            raise BuildError(f"model_hash_mismatch:{model_source}")

        python_root = package / "runtime" / "python"
        extract_zip(python_archive, python_root)
        configure_embedded_python(python_root)
        msvc_records = copy_msvc_runtime(package, msvc_source, msvc_source_metadata)
        site_packages = python_root / "Lib" / "site-packages"
        try:
            path_budget = calculate_runtime_path_budget(
                wheelhouse,
                runtime_lock,
                site_packages,
                stage_root=stage_parent,
            )
            enforce_runtime_path_budget(path_budget)
        except RuntimeLockError as exc:
            raise BuildError(f"P2_RUNTIME_PATH_BUDGET_BLOCKED:{exc}") from exc
        try:
            runtime_install = install_locked_runtime(
                wheelhouse,
                site_packages,
                runtime_lock,
                python_executable=Path(sys.executable),
                managed_root=python_root,
                production_root=source,
                owned_files=python_owned_files(python_archive, python_root),
            )
            runtime_record = runtime_install["record_verification"]
            write_json(output_dir / "PORTABLE_RUNTIME_CANONICAL.json", runtime_record)
            write_json(package / "PORTABLE_RUNTIME_CANONICAL.json", runtime_record)
        except RuntimeLockError as exc:
            raise BuildError(f"P2_LOCKED_RUNTIME_BLOCKED:{exc}") from exc
        copy_application(source, package, frontend_dist)
        copy_file(model_source, package / "models" / MODEL_FILENAME)
        ffmpeg_record = ffmpeg_metadata(ffmpeg_archive, package)
        ai_inventory = verify_ai_inventory(site_packages)
        license_root = package / "LICENSES"
        application_license = capture_application_license(source / "LICENSE", package)
        python_license_components = capture_distribution_licenses(
            site_packages, license_root, runtime_lock, source_root=source
        )
        python_license_evidence = summarize_python_license_evidence(
            python_license_components, runtime_lock
        )
        supplemental_license_manifest_path = (
            package / Path(*PurePosixPath(SUPPLEMENTAL_LICENSE_MANIFEST_PATH).parts)
        )
        supplemental_license_manifest = {
            "path": SUPPLEMENTAL_LICENSE_MANIFEST_PATH,
            "sha256": sha256_file(supplemental_license_manifest_path),
        }
        copy_file(python_root / "LICENSE.txt", license_root / "cpython" / "LICENSE.txt")
        frontend_record = capture_frontend_provenance(source, package, license_root)
        nvidia_runtime_inventory = capture_nvidia_runtime_inventory(package, runtime_record)
        write_nvidia_terms_notice(package)

        native_record = scan_native_closure(package)
        write_json(package / "NATIVE_DLL_CLOSURE.json", native_record)
        if native_record["parse_failures"]:
            raise BuildError(f"native_parse_failures:{native_record['parse_failures'][:20]}")
        if native_record["unknown_architecture"]:
            raise BuildError(
                f"native_unknown_architecture:{native_record['unknown_architecture'][:20]}"
            )
        if native_record["unresolved"]:
            missing = sorted({item["dependency"] for item in native_record["unresolved"]})
            missing_msvc = [name for name in missing if name in MSVC_DLLS]
            if missing_msvc:
                raise BuildError(
                    f"P2_ZERO_INSTALL_BLOCKED_BY_NATIVE_RUNTIME:{','.join(missing_msvc)}"
                )
            raise BuildError(
                f"P2_ZERO_INSTALL_BLOCKED_BY_NATIVE_RUNTIME:unresolved={','.join(missing)}"
            )
        if native_record["non_x64"]:
            raise BuildError(f"native_architecture_mismatch:{native_record['non_x64']}")

        third_party = {
            "schema_version": "third-party-licenses-v1",
            "generated_for": "TrafficVideoAnalytics_Portable_0.1.0-pilot_Win11-x64_FULL",
            "disclaimer": "Engineering provenance inventory; not legal advice or distribution approval.",
            "python_runtime_lock": str(runtime_lock_path.relative_to(source).as_posix())
            if runtime_lock_path.is_relative_to(source)
            else str(runtime_lock_path),
            "application_license": application_license,
            "python_license_evidence": python_license_evidence,
            "supplemental_license_manifest": supplemental_license_manifest,
            "nvidia_runtime_inventory": nvidia_runtime_inventory,
            "nvidia_notice_status": NVIDIA_NOTICE_STATUS,
            "ffmpeg_status": FFMPEG_STATUS,
            "microsoft_runtime_status": MSVC_STATUS,
            "components": [
                {
                    "component_type": "application",
                    "name": "Traffic Video Analytics",
                    "license": "AGPL-3.0-only",
                    "license_files": ["app/LICENSE", "LICENSES/application/LICENSE"],
                    "source": "private repository source tree",
                },
                {
                    "component_type": "cpython",
                    "name": "CPython",
                    "version": "3.12.10",
                    "license": "Python Software Foundation License",
                    "license_files": ["LICENSES/cpython/LICENSE.txt"],
                    "source_url": CPYTHON_SOURCE_PAGE,
                    "artifact_sha256": sha256_file(python_archive),
                },
                *python_license_components,
                {
                    "component_type": "ffmpeg",
                    "name": "BtbN FFmpeg LGPL static build",
                    "license": "LGPL candidate; verify upstream terms before distribution",
                    "license_files": ffmpeg_record["license_files"],
                    "source_url": FFMPEG_URL,
                    "artifact_sha256": FFMPEG_SHA256,
                    "status": FFMPEG_STATUS,
                    "source_bundle_required": True,
                },
                {
                    "component_type": "model_weight",
                    "name": MODEL_FILENAME,
                    "license": "Recorded in model registry; review weight terms before distribution",
                    "source_url": MODEL_URL,
                    "sha256": MODEL_SHA256,
                },
                {
                    "component_type": "frontend_build",
                    **frontend_record["provenance"],
                    "license_files": [],
                    "notice": "Compiled assets only; no Node.js runtime or package manager is shipped.",
                },
                *frontend_record["components"],
            ],
            "microsoft_runtime_files": msvc_records,
        }
        write_json(package / "THIRD_PARTY_LICENSES.json", third_party)

        provenance = {
            "schema_version": "package-provenance-v1",
            "package_name": "Traffic Video Analytics portable engineering candidate",
            "package_version": "0.1.0-pilot",
            "package_filename_pattern": (
                "TrafficVideoAnalytics_Portable_0.1.0-pilot_Win11-x64_FULL_QUALIFICATION_<HEAD-short>.zip"
                if state["qualifiable"]
                else "TrafficVideoAnalytics_Portable_0.1.0-pilot_Win11-x64_FULL_DEVELOPMENT_ONLY_<HEAD-short>.zip"
            ),
            "platform": "Windows 11 x64",
            "source": {
                "private_branch": state["branch"],
                "head_sha": state["head_sha"],
                "base_sha": state["base_sha"],
                "expected_source_sha": state["expected_source_sha"],
                "accepted_baseline_sha": state["accepted_baseline_sha"],
                "baseline_is_ancestor": state["baseline_is_ancestor"],
                "working_tree_dirty_at_build": state["working_tree_dirty"],
                "build_mode": state["build_mode"],
                "qualifiable": state["qualifiable"],
                "public_corresponding_source_sync": "PENDING",
            },
            "runtime_contract": {
                "python": "runtime/python/python.exe",
                "ai_python": "runtime/python/python.exe",
                "ai_site_packages": "runtime/python/Lib/site-packages",
                "model_dir": "models",
                "ffmpeg_dir": "runtime/ffmpeg",
                "frontend_dist": "frontend/dist",
                "source_root": "app",
                "local_data_dir": ".local-data (created on first run; not shipped)",
            },
            "path_budget": portable_path_budget_provenance(path_budget),
            "cpython": {
                "version": "3.12.10",
                "artifact_filename": CPYTHON_FILENAME,
                "artifact_sha256": sha256_file(python_archive),
                "source_url": CPYTHON_URL,
                "source_page": CPYTHON_SOURCE_PAGE,
                "relocatable_pth": "runtime/python/python312._pth points to Lib/site-packages and ../../app",
            },
            "python_runtime_lock": {
                "path": str(runtime_lock_path.relative_to(source).as_posix())
                if runtime_lock_path.is_relative_to(source)
                else str(runtime_lock_path),
                "sha256": sha256_file(runtime_lock_path),
                "target": runtime_lock["target"],
                "policy": runtime_lock["policy"],
                "locked_distribution_count": len(runtime_lock["distributions"]),
                "record_verification": runtime_record,
            },
            "application_license": application_license,
            "python_license_evidence": python_license_evidence,
            "frontend": frontend_record["provenance"],
            "python_runtime_inventory": package_inventory(site_packages),
            "ai_stack": {
                "detector": "detector.ultralytics-yolo11n-coco",
                "tracker": "tracker.trackers-bytetrack",
                "packages": ai_inventory,
                "model_filename": MODEL_FILENAME,
                "model_sha256": MODEL_SHA256,
                "model_source_url": MODEL_URL,
            },
            "ffmpeg": ffmpeg_record,
            "native_closure": {
                "status": native_record["status"],
                "files_scanned": native_record["files_scanned"],
                "category_counts": native_record["category_counts"],
                "unresolved_count": len(native_record["unresolved"]),
                "detail_file": "NATIVE_DLL_CLOSURE.json",
            },
            "microsoft_runtime": {
                "status": MSVC_STATUS,
                "files": msvc_records,
                "basis": "Official app-local redist evidence is required for any files supplied with --msvc-redist-dir.",
                "terms": "https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files",
            },
            "cuda_nvidia": {
                "cuda_toolkit_shipped": False,
                "nvidia_display_driver_shipped": False,
                "pytorch_wheel_runtime": "torch 2.10.0+cu128",
                "runtime_component_inventory_status": NVIDIA_NOTICE_STATUS,
                "runtime_component_inventory": "THIRD_PARTY_LICENSES.json#nvidia_runtime_inventory",
                "runtime_component_dll_count": nvidia_runtime_inventory["dll_count"],
                "runtime_component_family_count": nvidia_runtime_inventory["component_family_count"],
                "gpu_uat": "NOT_RUN",
            },
            "gates": {
                "source_qualification": "PASS"
                if state["qualifiable"]
                else "DEVELOPMENT_ONLY_NON_QUALIFIABLE",
                "locked_runtime": "PASS",
                "native_closure": "PASS",
                "application_license": "PASS",
                "license_completeness": "PASS",
                "frontend_runtime_licenses": "PASS",
                "nvidia_notice_inventory": "PASS",
                "no_node_runtime": "PENDING_PACKAGE_SCAN",
                "privacy": "PENDING_PACKAGE_SCAN",
                "manifest": "PENDING_PACKAGE_SCAN",
                "redistribution_policy": "ENGINEERING_EVIDENCE_ONLY_POLICY_REVIEW_REQUIRED",
                "office_pc_uat": "NOT_RUN",
                "network_disconnect": "NOT_RUN",
            },
            "license_inventory": "THIRD_PARTY_LICENSES.json",
            "supplemental_license_manifest": supplemental_license_manifest,
            "build_artifact_cache": "project-local or explicitly approved external cache; not shipped",
        }
        write_packaged_provenance(package / "PACKAGE_PROVENANCE.json", provenance)
        notice_validation = verify_license_notice_contract(
            {relpath(path, package) for path in package.rglob("*") if path.is_file()},
            lambda relative: (package / Path(*PurePosixPath(relative).parts)).read_bytes(),
        )
        if notice_validation["status"] != "PASS":
            raise BuildError(f"P2_LICENSE_NOTICE_BLOCKED:{notice_validation['violations'][:20]}")
        provenance["license_notice_validation"] = notice_validation
        write_packaged_provenance(package / "PACKAGE_PROVENANCE.json", provenance)
        excluded_manifest = {"PACKAGE_MANIFEST.json"}
        # The six end-user entry points are source-controlled at the repository
        # root and are copied before the single manifest hash pass.
        for name in (
            "START_TRAFFIC_VIDEO_ANALYTICS.bat",
            "STOP_TRAFFIC_VIDEO_ANALYTICS.bat",
            "BACKUP_DATA.bat",
            "RESTORE_DATA.bat",
            "OPEN_GUIDE.bat",
            "คู่มือการใช้งาน.html",
        ):
            copy_file(source / name, package / name)
        privacy_kwargs = {
            "forbidden_markers": getattr(args, "forbidden_markers", []),
            "forbidden_roots": getattr(args, "forbidden_roots", []),
            "forbidden_emails": getattr(args, "forbidden_emails", []),
        }
        package_privacy = privacy_scan(package, **privacy_kwargs)
        if package_privacy["status"] != "PASS":
            raise BuildError(f"P2_PACKAGE_PRIVACY_BLOCKED:{package_privacy['violations'][:20]}")
        provenance["gates"]["no_node_runtime"] = "PASS"
        provenance["gates"]["privacy"] = "PASS"
        write_packaged_provenance(package / "PACKAGE_PROVENANCE.json", provenance)
        manifest = {
            "schema_version": "package-manifest-v1",
            "package_root": "TrafficVideoAnalytics",
            "excluded_from_manifest": sorted(excluded_manifest),
            "files": [],
        }
        manifest["files"] = package_file_records(package, excluded_manifest)
        manifest["file_count"] = len(manifest["files"])
        manifest["uncompressed_bytes"] = sum(item["size"] for item in manifest["files"])
        write_json(package / "PACKAGE_MANIFEST.json", manifest)
        manifest_check = verify_manifest(package, manifest, rehash=False)
        if manifest_check["status"] != "PASS":
            raise BuildError(f"manifest_verification_failed:{manifest_check['mismatches']}")
        provenance["gates"]["manifest"] = "PASS"
        write_packaged_provenance(package / "PACKAGE_PROVENANCE.json", provenance)
        manifest["files"] = package_file_records(package, excluded_manifest)
        manifest["file_count"] = len(manifest["files"])
        manifest["uncompressed_bytes"] = sum(item["size"] for item in manifest["files"])
        write_json(package / "PACKAGE_MANIFEST.json", manifest)

        package_label = "QUALIFICATION" if state["qualifiable"] else "DEVELOPMENT_ONLY"
        package_name = (
            f"TrafficVideoAnalytics_Portable_0.1.0-pilot_Win11-x64_FULL_"
            f"{package_label}_{str(state['head_sha'])[:12]}"
        )
        final_package = output_dir / "TrafficVideoAnalytics"
        shutil.move(str(package), str(final_package))
        zip_path = output_dir / f"{package_name}.zip"
        write_deterministic_zip(final_package, zip_path)
        zip_hash = sha256_file(zip_path)
        (zip_path.with_suffix(zip_path.suffix + ".sha256")).write_text(
            f"{zip_hash}  {zip_path.name}\n", encoding="ascii"
        )
        privacy = privacy_scan(final_package, zip_path, **privacy_kwargs)
        if privacy["status"] != "PASS":
            raise BuildError(f"P2_PACKAGE_PRIVACY_BLOCKED:{privacy['violations'][:20]}")
        manifest_from_disk = json.loads(
            (final_package / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8")
        )
        final_manifest_check = verify_manifest(final_package, manifest_from_disk, rehash=False)
        if final_manifest_check["status"] != "PASS":
            raise BuildError(
                f"manifest_verification_failed_after_move:{final_manifest_check['mismatches']}"
            )
        report = {
            "status": "PASS",
            "package_dir": final_package.name,
            "zip_path": zip_path.name,
            "zip_sha256": zip_hash,
            "zip_bytes": zip_path.stat().st_size,
            "uncompressed_bytes": manifest_from_disk["uncompressed_bytes"],
            "file_count": manifest_from_disk["file_count"],
            "manifest_verification": final_manifest_check,
            "license_notice_validation": notice_validation,
            "application_license": application_license,
            "python_license_evidence": python_license_evidence,
            "frontend_runtime_packages": frontend_record["provenance"]["runtime_packages"],
            "nvidia_runtime_inventory": {
                "status": nvidia_runtime_inventory["status"],
                "dll_count": nvidia_runtime_inventory["dll_count"],
                "component_family_count": nvidia_runtime_inventory["component_family_count"],
            },
            "ffmpeg_status": FFMPEG_STATUS,
            "microsoft_runtime_status": MSVC_STATUS,
            "privacy_scan": privacy,
            "native_closure": {
                "status": native_record["status"],
                "files_scanned": native_record["files_scanned"],
                "category_counts": native_record["category_counts"],
            },
            "source": state,
            "artifacts": {
                "cpython_sha256": sha256_file(python_archive),
                "ffmpeg_archive_sha256": sha256_file(ffmpeg_archive),
                "model_sha256": MODEL_SHA256,
            },
            "path_budget": path_budget,
        }
        write_json(output_dir / "BUILD_REPORT.json", report)
        return report
    except Exception as exc:
        error_report = {
            "status": "BLOCKED",
            "error": str(exc),
            "error_type": type(exc).__name__,
            "traceback": traceback.format_exc(),
            "source": state,
            "stage_dir": stage.name,
            "path_budget": path_budget,
        }
        if native_record is not None:
            error_report["native_closure"] = native_failure_diagnostics(native_record)
        write_json(output_dir / "BUILD_REPORT.json", error_report)
        raise
    finally:
        finish_stage_cleanup(stage, stage_parent, output_dir / "BUILD_REPORT.json", sys.exc_info()[1])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a pinned, self-contained TVA Windows portable candidate."
    )
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--output", type=Path, help="Build output root; omitted uses a project-local ignored path."
    )
    parser.add_argument(
        "--cache-dir", type=Path, help="Artifact cache root; omitted uses a project-local ignored path."
    )
    parser.add_argument("--model", type=Path)
    parser.add_argument("--pnpm", type=Path)
    parser.add_argument("--msvc-redist-dir", type=Path)
    parser.add_argument(
        "--wheelhouse",
        type=Path,
        help="Explicit approved wheelhouse; omitted uses a project-local ignored path.",
    )
    parser.add_argument(
        "--stage-root",
        type=Path,
        help="Explicit temporary staging root; omitted uses a project-local ignored path.",
    )
    parser.add_argument("--runtime-lock", type=Path)
    parser.add_argument(
        "--build-mode",
        choices=("qualification", "development"),
        default="qualification",
        help="Qualification requires exact source/baseline attestations; development is never qualifiable.",
    )
    parser.add_argument("--expected-source-sha")
    parser.add_argument("--accepted-baseline-sha")
    parser.add_argument("--forbidden-marker", dest="forbidden_markers", action="append", default=[])
    parser.add_argument("--forbidden-root", dest="forbidden_roots", action="append", default=[])
    parser.add_argument("--forbidden-email", dest="forbidden_emails", action="append", default=[])
    parser.add_argument("--accept-downloads", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--skip-frontend-build", action="store_true")
    args = parser.parse_args()
    explicit_roots = {
        "output": args.output is not None,
        "cache": args.cache_dir is not None,
        "wheelhouse": args.wheelhouse is not None,
        "stage": args.stage_root is not None,
    }
    source = _canonical_path(args.source)
    provisional_sha = git_output(source, "rev-parse", "HEAD")[:12] or "unknown"
    paths = resolve_build_paths(
        source,
        args.build_mode,
        provisional_sha,
        output=args.output,
        cache_dir=args.cache_dir,
        wheelhouse=args.wheelhouse,
        stage_root=args.stage_root,
    )
    args.source = paths.project_root
    args.output = paths.output
    args.cache_dir = paths.cache_dir
    args.wheelhouse = paths.wheelhouse
    args.stage_root = paths.stage_root
    args._explicit_build_roots = explicit_roots
    args.runtime_lock = (
        args.runtime_lock
        or (source / "packages" / "portable" / "windows-x64-cp312-runtime.lock.json")
    ).resolve()
    return args


def main() -> int:
    args = parse_args()
    try:
        report = build(args)
    except BuildError as exc:
        print(f"PORTABLE_BUILD_BLOCKED: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"PORTABLE_BUILD_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
