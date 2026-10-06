"""Hash-locked wheelhouse installation and RECORD verification."""

from __future__ import annotations

import base64
import configparser
import csv
import email.parser
import hashlib
import json
import io
import os
import re
import stat
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .archive_safety import ArchiveMemberError, _normalized_name, safe_zip_infos


LOCK_SCHEMA = "portable-python-runtime-lock-v1"
WHEELHOUSE_SOURCE_NAMES = {"pypi", "pytorch_cu128"}
LOCK_SOURCES = {
    "pypi": "https://pypi.org/simple/",
    "pytorch_cu128": "https://download.pytorch.org/whl/cu128/",
}
REQUIRED_CUDA_WHEELS = {
    "torch": {
        "version": "2.10.0+cu128",
        "source_index": "pytorch_cu128",
        "wheel_filename": "torch-2.10.0+cu128-cp312-cp312-win_amd64.whl",
    },
    "torchvision": {
        "version": "0.25.0+cu128",
        "source_index": "pytorch_cu128",
        "wheel_filename": "torchvision-0.25.0+cu128-cp312-cp312-win_amd64.whl",
    },
}

# The portable builder does not enable Windows long-path support.  Keep the
# legacy MAX_PATH boundary explicit so the locked pip --target materialization
# is rejected before it can create a partial runtime.
WINDOWS_LEGACY_PATH_LIMIT = 260
WINDOWS_PATH_LIMIT_BASIS = (
    "Legacy Windows MAX_PATH boundary for pip --target materialization; "
    "path_units_with_nul is UTF-16-LE code units plus the terminating NUL and "
    "must be <= 260; no registry long-path support is assumed."
)


class RuntimeLockError(RuntimeError):
    """Raised when the locked runtime cannot be reproduced exactly."""


def utf16_code_units(value: str | Path) -> int:
    return len(str(value).encode("utf-16-le")) // 2


def normalize_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def load_runtime_lock(path: Path) -> dict[str, Any]:
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeLockError(f"runtime_lock_read_failed:{path}:{exc}") from exc
    if not isinstance(lock, dict) or lock.get("schema_version") != LOCK_SCHEMA:
        raise RuntimeLockError("runtime_lock_schema_invalid")
    target = lock.get("target")
    policy = lock.get("policy")
    distributions = lock.get("distributions")
    if target != {
        "implementation": "CPython",
        "python_version": "3.12",
        "platform": "win_amd64",
        "abi": "cp312",
    }:
        raise RuntimeLockError("runtime_lock_target_invalid")
    required_policy = {
        "require_hashes": True,
        "only_binary": True,
        "no_index": True,
        "no_source_distributions": True,
        "no_unlisted_distributions": True,
    }
    if not isinstance(policy, dict) or any(policy.get(key) != value for key, value in required_policy.items()):
        raise RuntimeLockError("runtime_lock_policy_invalid")
    if not isinstance(distributions, list) or not distributions:
        raise RuntimeLockError("runtime_lock_distributions_missing")
    seen: set[str] = set()
    for item in distributions:
        if not isinstance(item, dict):
            raise RuntimeLockError("runtime_lock_distribution_invalid")
        required = (
            "name",
            "normalized_name",
            "version",
            "wheel_filename",
            "sha256",
            "source_index",
            "license",
            "license_expression",
            "license_files",
            "license_metadata_source",
        )
        if any(key not in item for key in required):
            raise RuntimeLockError(f"runtime_lock_distribution_fields_missing:{item!r}")
        if not all(isinstance(item[key], str) for key in ("name", "normalized_name", "version")):
            raise RuntimeLockError(f"runtime_lock_distribution_metadata_invalid:{item.get('name')}")
        wheel_filename = str(item["wheel_filename"])
        wheel_path = PureWindowsPath(wheel_filename)
        if (
            not wheel_filename.lower().endswith(".whl")
            or "/" in wheel_filename
            or "\\" in wheel_filename
            or ":" in wheel_filename
            or wheel_path.drive
            or wheel_path.anchor
            or ".." in PurePosixPath(wheel_filename).parts
        ):
            raise RuntimeLockError(f"runtime_lock_wheel_filename_invalid:{item.get('name')}")
        normalized = normalize_distribution_name(str(item["name"]))
        if normalized != item["normalized_name"] or normalized in seen:
            raise RuntimeLockError(f"runtime_lock_distribution_name_invalid:{item.get('name')}")
        seen.add(normalized)
        if item["source_index"] not in WHEELHOUSE_SOURCE_NAMES:
            raise RuntimeLockError(f"runtime_lock_source_invalid:{item['name']}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(item["sha256"])):
            raise RuntimeLockError(f"runtime_lock_hash_invalid:{item['name']}")
        if not isinstance(item["license_files"], list) or not all(
            isinstance(value, str) for value in item["license_files"]
        ):
            raise RuntimeLockError(f"runtime_lock_license_metadata_invalid:{item['name']}")
        if not all(
            isinstance(item[key], str)
            for key in ("license", "license_expression", "license_metadata_source")
        ):
            raise RuntimeLockError(f"runtime_lock_license_metadata_invalid:{item['name']}")
    sources = lock.get("sources")
    if sources != LOCK_SOURCES:
        raise RuntimeLockError("runtime_lock_sources_invalid")
    if policy.get("record_validation") != "every installed file must be covered by its wheel RECORD":
        raise RuntimeLockError("runtime_lock_record_policy_invalid")
    by_name = {item["normalized_name"]: item for item in distributions}
    for name, expected in REQUIRED_CUDA_WHEELS.items():
        actual = by_name.get(name)
        if actual is None or any(actual.get(key) != value for key, value in expected.items()):
            raise RuntimeLockError(f"runtime_lock_cuda_wheel_invalid:{name}")
    return lock


def _metadata_from_wheel(archive: zipfile.ZipFile) -> tuple[dict[str, Any], str]:
    metadata_names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
    if len(metadata_names) != 1:
        raise RuntimeLockError("wheel_metadata_count_invalid")
    metadata_name = metadata_names[0]
    message = email.parser.Parser().parsestr(
        archive.read(metadata_name).decode("utf-8", errors="replace")
    )
    return (
        {
            "name": message.get("Name"),
            "version": message.get("Version"),
            "license": message.get("License") or "",
            "license_expression": message.get("License-Expression") or "",
            "license_files": message.get_all("License-File", []),
            "project_urls": message.get_all("Project-URL", []),
        },
        metadata_name,
    )


def verify_wheelhouse(wheelhouse: Path, lock: dict[str, Any]) -> dict[str, Any]:
    if not wheelhouse.is_dir():
        raise RuntimeLockError(f"wheelhouse_missing:{wheelhouse}")
    _record_target(wheelhouse, wheelhouse.resolve())
    expected_filenames = {str(item["wheel_filename"]) for item in lock["distributions"]}
    unexpected_entries = sorted(
        path.name
        for path in wheelhouse.iterdir()
        if path.name not in expected_filenames or not path.is_file() or path.is_symlink()
    )
    if unexpected_entries:
        raise RuntimeLockError(f"wheelhouse_unlisted_or_unsafe_entries:{unexpected_entries[:20]}")
    records: list[dict[str, Any]] = []
    for item in lock["distributions"]:
        wheel = wheelhouse / str(item["wheel_filename"])
        if not wheel.is_file():
            raise RuntimeLockError(f"locked_wheel_missing:{item['wheel_filename']}")
        _record_target(wheel, wheelhouse.resolve())
        actual_hash = _hash_file(wheel)
        if actual_hash != item["sha256"]:
            raise RuntimeLockError(
                f"locked_wheel_hash_mismatch:{item['wheel_filename']}:{actual_hash}"
            )
        try:
            with zipfile.ZipFile(wheel) as archive:
                try:
                    safe_zip_infos(archive)
                except ArchiveMemberError as exc:
                    raise RuntimeLockError(f"wheel_archive_unsafe:{item['wheel_filename']}:{exc}") from exc
                metadata, metadata_name = _metadata_from_wheel(archive)
                member_names = set(archive.namelist())
                if not member_names or not any(name.endswith(".dist-info/RECORD") for name in member_names):
                    raise RuntimeLockError(f"wheel_record_missing:{item['wheel_filename']}")
        except (OSError, zipfile.BadZipFile) as exc:
            raise RuntimeLockError(f"wheel_invalid:{item['wheel_filename']}:{exc}") from exc
        actual_name = normalize_distribution_name(str(metadata.get("name") or ""))
        if actual_name != item["normalized_name"] or metadata.get("version") != item["version"]:
            raise RuntimeLockError(f"wheel_metadata_mismatch:{item['wheel_filename']}")
        expected_expression = str(item["license_expression"])
        if expected_expression and metadata["license_expression"] != expected_expression:
            raise RuntimeLockError(f"wheel_license_expression_mismatch:{item['wheel_filename']}")
        expected_license = str(item["license"])
        if expected_license and not str(metadata["license"]).startswith(expected_license):
            raise RuntimeLockError(f"wheel_license_metadata_mismatch:{item['wheel_filename']}")
        expected_license_files = set(item["license_files"])
        if not expected_license_files.issubset(set(metadata["license_files"])):
            raise RuntimeLockError(f"wheel_license_files_mismatch:{item['wheel_filename']}")
        dist_info_prefix = metadata_name.rsplit("/", 1)[0]
        with zipfile.ZipFile(wheel) as archive:
            archive_names = set(archive.namelist())
            missing_license_files = [
                license_file
                for license_file in expected_license_files
                if f"{dist_info_prefix}/{license_file}" not in archive_names
                and f"{dist_info_prefix}/licenses/{license_file}" not in archive_names
            ]
        if missing_license_files:
            raise RuntimeLockError(
                f"wheel_license_payload_missing:{item['wheel_filename']}:{missing_license_files}"
            )
        records.append(
            {
                "name": metadata["name"],
                "normalized_name": actual_name,
                "version": metadata["version"],
                "wheel_filename": wheel.name,
                "wheel_sha256": actual_hash,
                "source_index": item["source_index"],
                "metadata_path": metadata_name,
                "license": metadata["license"],
                "license_expression": metadata["license_expression"],
                "license_files": metadata["license_files"],
                "project_urls": metadata["project_urls"],
            }
        )
    return {
        "status": "PASS",
        "wheelhouse": str(wheelhouse),
        "locked_distribution_count": len(records),
        "distributions": records,
    }


def _requirements_text(lock: dict[str, Any]) -> str:
    lines = []
    for item in lock["distributions"]:
        lines.append(
            f"{item['name']}=={item['version']} "
            f"--hash=sha256:{item['sha256']}"
        )
    return "\n".join(lines) + "\n"


def install_locked_runtime(
    wheelhouse: Path,
    destination: Path,
    lock: dict[str, Any],
    *,
    python_executable: Path | None = None,
    managed_root: Path | None = None,
    production_root: Path | None = None,
    owned_files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    wheelhouse_report = verify_wheelhouse(wheelhouse, lock)
    current = destination
    while True:
        if current.exists() or current.is_symlink():
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or (
                getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise RuntimeLockError(f"runtime_destination_reparse:{current}")
        if current.parent == current:
            break
        current = current.parent
    if destination.exists() and any(destination.iterdir()):
        raise RuntimeLockError(f"runtime_destination_not_empty:{destination}")
    if destination.exists() and not destination.is_dir():
        raise RuntimeLockError(f"runtime_destination_not_directory:{destination}")
    destination.mkdir(parents=True, exist_ok=True)
    requirement_path = destination.parent / f".runtime-lock-{uuid.uuid4().hex}.txt"
    requirement_path.write_text(_requirements_text(lock), encoding="ascii")
    executable = python_executable or Path(sys.executable)
    command = [
        str(executable),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-cache-dir",
        "--no-index",
        "--only-binary=:all:",
        "--require-hashes",
        "--no-deps",
        "--no-compile",
        "--find-links",
        str(wheelhouse),
        "--target",
        str(destination),
        "--requirement",
        str(requirement_path),
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeLockError(f"locked_runtime_install_failed:{exc}") from exc
    finally:
        requirement_path.unlink(missing_ok=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise RuntimeLockError(f"locked_runtime_install_failed:{detail}")
    record_report = verify_installed_records(
        destination, lock, managed_root=managed_root, wheelhouse=wheelhouse,
        production_root=production_root, owned_files=owned_files, prune=True,
    )
    return {
        "status": "PASS",
        "pip_command_policy": [
            "--no-index",
            "--no-cache-dir",
            "--only-binary=:all:",
            "--require-hashes",
            "--no-deps",
            "--no-compile",
        ],
        "wheelhouse": wheelhouse_report,
        "record_verification": record_report,
    }


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_target(path: Path, managed_root: Path) -> Path:
    # Inspect the lexical path before resolving: even an in-root link is not
    # part of the managed installation contract. lstat also catches junctions.
    for component in (path, *path.parents):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise RuntimeLockError(f"installed_runtime_reparse:{component}")
    target = path.resolve(strict=True)
    if not target.is_relative_to(managed_root):
        raise RuntimeLockError(f"installed_record_path_escape:{path}")
    return target


def _record_name(value: str) -> str:
    """Normalize Windows separators, permitting only leading scheme parents."""
    slash = value.replace("\\", "/")
    parents = ""
    while slash.startswith("../"):
        parents += "../"
        slash = slash[3:]
    try:
        return parents + _normalized_name(slash)
    except ArchiveMemberError as exc:
        raise RuntimeLockError(f"installed_record_path_invalid:{value}") from exc


def _constraints(rows: list[list[str]], digest: str, size: int, label: str) -> None:
    hashes = {row[1] for row in rows if row[1]}
    sizes = {row[2] for row in rows if row[2]}
    if len(hashes) > 1 or len(sizes) > 1:
        raise RuntimeLockError(f"installed_record_alias_conflict:{label}")
    encoded = "sha256=" + base64.urlsafe_b64encode(bytes.fromhex(digest)).decode().rstrip("=")
    if hashes and hashes != {encoded}:
        raise RuntimeLockError(f"installed_record_hash_mismatch:{label}")
    if sizes and sizes != {str(size)}:
        raise RuntimeLockError(f"installed_record_size_mismatch:{label}")


def _wheel_source(wheelhouse: Path, item: dict[str, Any]) -> dict[str, Any]:
    """Read evidence only after hashing the same open locked archive handle."""
    wheel = wheelhouse / item["wheel_filename"]
    with wheel.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != item["sha256"]:
            raise RuntimeLockError(f"locked_wheel_hash_mismatch:{wheel.name}")
        handle.seek(0)
        with zipfile.ZipFile(handle) as archive:
            infos = safe_zip_infos(archive)
            metadata, metadata_name = _metadata_from_wheel(archive)
            dist = metadata_name.rsplit("/", 1)[0]
            data_prefix = dist.removesuffix(".dist-info") + ".data/"
            wheel_metadata = email.parser.Parser().parsestr(
                archive.read(f"{dist}/WHEEL").decode("utf-8")
            )
            root_scheme = "purelib" if wheel_metadata["Root-Is-Purelib"] == "true" else "platlib"
            members = {}
            for info in infos:
                name = _normalized_name(info.filename)
                with archive.open(info) as member:
                    member_hash = hashlib.file_digest(member, "sha256").hexdigest()
                scheme, target = root_scheme, name
                if name.startswith(data_prefix):
                    scheme, separator, target = name[len(data_prefix):].partition("/")
                    if not separator or scheme not in {"purelib", "platlib", "scripts", "data", "headers"}:
                        raise RuntimeLockError(f"wheel_scheme_invalid:{name}")
                    if scheme == "scripts":
                        target = "bin/" + target
                    elif scheme == "headers":
                        # pip --target calls get_scheme("", home=...), whose
                        # Windows sysconfig header directory is UNKNOWN.
                        target = "include/python/UNKNOWN/" + target
                record_name = target if scheme in {"purelib", "platlib"} else "../../" + target
                key = target.casefold()
                if key in members:
                    raise RuntimeLockError(f"wheel_materialization_collision:{target}")
                members[key] = {
                    "member": info.filename, "scheme": scheme, "target": target,
                    "record_name": record_name, "sha256": member_hash, "size": info.file_size,
                }
            wheel_rows = list(csv.reader(io.StringIO(archive.read(f"{dist}/RECORD").decode("utf-8"))))
            by_member = {_normalized_name(value["member"]).casefold(): value for value in members.values()}
            seen = set()
            for row in wheel_rows:
                if len(row) != 3:
                    raise RuntimeLockError("wheel_record_row_invalid")
                key = _record_name(row[0]).casefold()
                source = by_member.get(key)
                if source is None:
                    raise RuntimeLockError(f"wheel_record_member_missing:{row[0]}")
                if key != f"{dist}/RECORD".casefold():
                    _constraints([row], source["sha256"], source["size"], row[0])
                seen.add(key)
            if seen != set(by_member):
                raise RuntimeLockError("wheel_members_unrecorded")
            entrypoints = {}
            entry_path = f"{dist}/entry_points.txt"
            if entry_path.casefold() in by_member:
                parser = configparser.ConfigParser(interpolation=None)
                parser.optionxform = str
                parser.read_string(archive.read(by_member[entry_path.casefold()]["member"]).decode("utf-8"))
                for group in ("console_scripts", "gui_scripts"):
                    if parser.has_section(group):
                        for name, value in parser.items(group):
                            _record_name(name)
                            if "/" in name or "\\" in name:
                                raise RuntimeLockError("wheel_entrypoint_name_invalid")
                            target = "bin/" + name + ".exe"
                            if target.casefold() in members or target.casefold() in entrypoints:
                                raise RuntimeLockError(f"wheel_materialization_collision:{target}")
                            entrypoints[target.casefold()] = {
                                "member": entry_path, "scheme": "scripts", "target": target,
                                "record_name": "../../" + target, "entrypoint": value,
                            }
            if normalize_distribution_name(metadata["name"]) != item["normalized_name"] or metadata["version"] != item["version"]:
                raise RuntimeLockError("wheel_metadata_mismatch")
    return {"dist": dist, "members": members, "entrypoints": entrypoints, "wheel_rows": wheel_rows}


def calculate_runtime_path_budget(
    wheelhouse: Path,
    lock: dict[str, Any],
    site_packages: Path,
    *,
    stage_root: Path,
) -> dict[str, Any]:
    """Project every locked pip target before starting materialization."""
    verify_wheelhouse(wheelhouse, lock)
    site_root = site_packages.resolve(strict=False)
    stage_root_path = stage_root.resolve(strict=False)
    projected: list[dict[str, Any]] = []
    for item in lock["distributions"]:
        source = _wheel_source(wheelhouse, item)
        for materialization, values in (
            ("wheel_member", source["members"].values()),
            ("generated_entrypoint", source["entrypoints"].values()),
        ):
            for value in values:
                target = str(value["target"])
                projected_path = site_root / target
                path_units = utf16_code_units(projected_path)
                projected.append(
                    {
                        "distribution": str(item["normalized_name"]),
                        "wheel_filename": str(item["wheel_filename"]),
                        "wheel_member": str(value["member"]),
                        "materialization": materialization,
                        "install_scheme": str(value["scheme"]),
                        "materialized_target": target.replace("\\", "/"),
                        "projected_path": str(projected_path),
                        "path_utf16_code_units": path_units,
                        "path_units_with_nul": path_units + 1,
                        # Compatibility alias retained for existing report readers.
                        "path_length": path_units + 1,
                    }
                )
    if not projected:
        raise RuntimeLockError("runtime_path_budget_empty")
    deepest = max(
        projected,
        key=lambda value: (int(value["path_units_with_nul"]), str(value["projected_path"])),
    )
    maximum_units = int(deepest["path_utf16_code_units"])
    maximum_with_nul = int(deepest["path_units_with_nul"])
    stage_root_units = utf16_code_units(stage_root_path)
    site_root_units = utf16_code_units(site_root)
    return {
        "status": "PASS" if maximum_with_nul <= WINDOWS_LEGACY_PATH_LIMIT else "BLOCKED",
        "path_limit": WINDOWS_LEGACY_PATH_LIMIT,
        "path_limit_basis": WINDOWS_PATH_LIMIT_BASIS,
        "path_utf16_code_units": maximum_units,
        "path_units_with_nul": maximum_with_nul,
        # Compatibility alias retained for existing qualification readers; it
        # now reports the gated UTF-16-unit value including the terminating NUL.
        "max_projected_path_length": maximum_with_nul,
        "deepest_member": deepest,
        "stage_root": str(stage_root_path),
        "stage_root_utf16_code_units": stage_root_units,
        "stage_root_length": stage_root_units,
        "effective_site_packages": str(site_root),
        "effective_site_packages_utf16_code_units": site_root_units,
        "effective_site_packages_length": site_root_units,
        "projected_member_count": len(projected),
    }


def enforce_runtime_path_budget(report: dict[str, Any]) -> dict[str, Any]:
    """Fail closed when the projected target exceeds legacy Windows MAX_PATH."""
    if report.get("status") == "PASS":
        return report
    deepest = report.get("deepest_member") or {}
    raise RuntimeLockError(
        "runtime_path_budget_exceeded:"
        f"max={report.get('path_units_with_nul', report.get('max_projected_path_length'))}:"
        f"limit={report.get('path_limit')}:"
        f"member={deepest.get('wheel_member')}"
    )


def _pruning_sources(production_root: Path) -> list[tuple[str, str]]:
    # These are the production trees copied by copy_application plus entry points.
    roots = [production_root / name for name in (
        "apps/backend", "apps/worker", "scripts/portable", "tools/ai_stack",
        "tools/__init__.py", "tools/benchmark/__init__.py",
        "tools/benchmark/runtime.py", "scripts/database_backup.py",
    )]
    roots.extend(sorted(production_root.glob("*.bat")))
    sources = []
    for root in roots:
        if not root.exists():
            raise RuntimeLockError(f"runtime_pruning_source_missing:{root.name}")
        for path in sorted(root.rglob("*")) if root.is_dir() else [root]:
            if path.suffix in {".py", ".bat", ".json", ".ps1"} and path.is_file():
                sources.append((path.relative_to(production_root).as_posix(), path.read_text(encoding="utf-8")))
    return sources


def _prove_unused(target: str, sources: list[tuple[str, str]]) -> None:
    basename = PurePosixPath(target).name.casefold()
    command = re.escape(PurePosixPath(target).stem)
    # Exact paths/names and bare argv[0]/shell command literals. Module imports
    # and Python -m entry points remain intact. See the production-callsite audit.
    command_pattern = re.compile(
        rf'''(?i)(?:\[\s*["']{command}["']|(?:run|Popen|system)\(\s*["']{command}(?:\s|["']))'''
    )
    for path, text in sources:
        if basename in text.casefold() or target.casefold() in text.replace("\\", "/").casefold() or command_pattern.search(text):
            raise RuntimeLockError(f"runtime_pruning_artifact_used:{target}:{path}")


def _verify_wrapper(path: Path, entrypoint: str) -> None:
    match = re.fullmatch(r"([\w.]+)\s*:\s*([\w.]+)(?:\s*\[[^]]*\])?", entrypoint)
    if not match:
        raise RuntimeLockError("runtime_wrapper_entrypoint_invalid")
    module, function = match.groups()
    expected = (
        "# -*- coding: utf-8 -*-\nimport re\nimport sys\n"
        f"from {module} import {function.split('.')[0]}\n"
        "if __name__ == '__main__':\n"
        "    sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])\n"
        f"    sys.exit({function}())\n"
    ).encode()
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.namelist() != ["__main__.py"] or archive.read("__main__.py") != expected:
                raise RuntimeLockError("runtime_wrapper_generation_mismatch")
    except zipfile.BadZipFile as exc:
        raise RuntimeLockError("runtime_wrapper_generation_mismatch") from exc


def verify_installed_records(
    site_packages: Path, lock: dict[str, Any], *, wheelhouse: Path,
    managed_root: Path | None = None, production_root: Path | None = None,
    owned_files: list[dict[str, Any]] | None = None, prune: bool = False,
) -> dict[str, Any]:
    """Canonical pip --target inventory; vendor RECORD is retained as evidence.

    Non-wheel owned_files is an explicit builder contract for hash-locked
    CPython/MSVC and the deterministic embedded path configuration, never a
    snapshot of unknown files. All wheel members and all actual files must agree.
    """
    root = (managed_root or site_packages).resolve(strict=True)
    _record_target(managed_root or site_packages, root)
    site_packages = _record_target(site_packages, root)
    verify_wheelhouse(wheelhouse, lock)
    expected = {item["normalized_name"]: item for item in lock["distributions"]}
    sources = _pruning_sources(production_root) if production_root else []
    counts = dict.fromkeys(("relocations", "pruned_artifacts", "alias_groups",
        "empty_hash_resolved_by_alias", "empty_hash_resolved_by_wheel",
        "generated_metadata_rows", "unresolved_rows"), 0)
    installed = {}
    inventory = []
    recorded = {}
    pruned = []
    empty_hash_entries = []
    for dist_info in sorted(site_packages.glob("*.dist-info")):
        metadata_path, record_path = dist_info / "METADATA", dist_info / "RECORD"
        for path in (dist_info, metadata_path, record_path):
            try:
                _record_target(path, root)
            except FileNotFoundError as exc:
                raise RuntimeLockError(f"installed_record_files_missing:{dist_info.name}") from exc
        message = email.parser.Parser().parsestr(metadata_path.read_text(encoding="utf-8"))
        name = normalize_distribution_name(message.get("Name", ""))
        if name not in expected or message.get("Version") != expected[name]["version"]:
            raise RuntimeLockError(f"installed_distribution_unlisted_or_mismatched:{name}")
        if name in installed:
            raise RuntimeLockError(f"installed_distribution_duplicate:{name}")
        item = expected[name]
        source = _wheel_source(wheelhouse, item)
        if source["dist"] != dist_info.name:
            raise RuntimeLockError(f"installed_distribution_metadata_path_mismatch:{name}")
        installed[name] = {"name": message["Name"], "normalized_name": name,
            "version": message["Version"], "metadata_sha256": _hash_file(metadata_path),
            "dist_info": dist_info.name}
        source_targets = {**source["members"], **source["entrypoints"]}
        # Only these two installer metadata artifacts have fixed generated bytes.
        for filename, payload in (("INSTALLER", b"pip\n"), ("REQUESTED", b"")):
            target = f"{dist_info.name}/{filename}"
            if target.casefold() not in source_targets:
                source_targets[target.casefold()] = {
                    "member": None, "scheme": "metadata", "target": target,
                    "record_name": target, "sha256": hashlib.sha256(payload).hexdigest(),
                    "size": len(payload),
                }
        record_lookup = {value["record_name"].casefold(): key for key, value in source_targets.items()}
        groups = {}
        with record_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.reader(handle):
                if len(row) != 3:
                    raise RuntimeLockError(f"installed_record_row_invalid:{name}")
                relative = _record_name(row[0])
                if not (site_packages / relative).resolve().is_relative_to(root):
                    raise RuntimeLockError(f"installed_record_path_escape:{row[0]}")
                key = record_lookup.get(relative.casefold())
                if key is None:
                    raise RuntimeLockError(f"installed_runtime_integrity_unprovable:{row[0]}")
                groups.setdefault(key, []).append(row)
        missing = set(source["members"]) | set(source["entrypoints"])
        missing -= set(groups)
        if missing:
            raise RuntimeLockError(f"installed_record_source_targets_missing:{sorted(missing)[:20]}")
        for key, rows in sorted(groups.items()):
            evidence = source_targets[key]
            target = site_packages / evidence["target"]
            try:
                target = _record_target(target, root)
            except FileNotFoundError as exc:
                raise RuntimeLockError(f"installed_record_file_missing:{evidence['target']}") from exc
            final = target.relative_to(root).as_posix()
            if final.casefold() in recorded:
                raise RuntimeLockError(f"installed_record_cross_distribution_collision:{final}")
            digest, size = _hash_file(target), target.stat().st_size
            is_self = target == record_path.resolve()
            _constraints(rows, digest, size, final)
            is_wrapper = "entrypoint" in evidence
            if is_wrapper:
                if not any(row[1] and row[2] for row in rows):
                    raise RuntimeLockError(f"installed_runtime_integrity_unprovable:{final}")
                _verify_wrapper(target, evidence["entrypoint"])
            elif not is_self and (digest != evidence["sha256"] or size != evidence["size"]):
                raise RuntimeLockError(f"installed_record_locked_source_mismatch:{final}")
            non_runtime = is_wrapper or (
                evidence["scheme"] == "data"
                and re.fullmatch(r"share/man/man[1-9]/[^/]+\.[1-9](?:\.gz)?", evidence["target"])
            )
            # Script-scheme members are never imported; only byte-identical
            # wheel scripts qualify here. Rewritten shebangs fail source integrity.
            non_runtime = non_runtime or evidence["scheme"] == "scripts"
            if non_runtime:
                if not prune or not sources:
                    raise RuntimeLockError(f"runtime_non_runtime_pruning_required:{final}")
                _prove_unused(evidence["target"], sources)
                pruned.append(target)
                counts["pruned_artifacts"] += 1
            if evidence["record_name"].startswith("../../"):
                counts["relocations"] += 1
            if len(rows) > 1:
                counts["alias_groups"] += 1
            for index, row in enumerate(rows):
                integrity = "installed_record"
                if not row[1] or not row[2]:
                    if is_self:
                        integrity = "generated_metadata"
                        empty_hash_entries.append(row[0])
                    elif any(other[1] for other in rows if other is not row) or any(
                        other[2] for other in rows if other is not row
                    ):
                        integrity = "alias_record"
                        counts["empty_hash_resolved_by_alias"] += int(not row[1])
                    elif evidence["member"] is not None:
                        integrity = "locked_wheel_member"
                        counts["empty_hash_resolved_by_wheel"] += int(not row[1])
                    else:
                        integrity = "generated_metadata"
                if is_self or evidence["scheme"] == "metadata":
                    integrity = "generated_metadata"
                    counts["generated_metadata_rows"] += 1
                inventory.append({
                    "distribution": name, "wheel_filename": item["wheel_filename"],
                    "wheel_sha256": item["sha256"], "wheel_member": evidence["member"],
                    "installed_record_path": row[0], "install_scheme": evidence["scheme"],
                    "canonical_final_path": final,
                    "integrity_source": "generated_metadata" if is_wrapper else integrity,
                    "action": "pruned_non_runtime" if non_runtime else (
                        "alias_collapsed" if index else "retained"),
                    "sha256": digest, "size": size, "status": "PASS",
                })
            recorded[final.casefold()] = name
    if set(installed) != set(expected):
        raise RuntimeLockError("installed_distribution_set_mismatch")
    for item in owned_files or []:
        relative = _record_name(item["canonical_final_path"])
        target = _record_target(root / relative, root)
        key = target.relative_to(root).as_posix().casefold()
        if key in recorded:
            raise RuntimeLockError(f"installed_record_cross_distribution_collision:{relative}")
        if _hash_file(target) != item["sha256"] or target.stat().st_size != item["size"]:
            raise RuntimeLockError(f"installed_owned_file_mismatch:{relative}")
        recorded[key] = "builder_owned"
        inventory.append(item)
    # Inspect directories before descending, and never suppress walk errors.
    def walk_error(error: OSError) -> None:
        raise error

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        for filename in sorted(dirs + files):
            target = _record_target(Path(directory) / filename, root)
            if target.is_file() and target.relative_to(root).as_posix().casefold() not in recorded:
                raise RuntimeLockError(f"installed_runtime_unrecorded_files:{target.relative_to(root)}")
            if not target.is_file() and not target.is_dir():
                raise RuntimeLockError("installed_runtime_non_regular_file")
    for path in pruned:
        _record_target(path, root).unlink()
    retained = set(recorded) - {path.relative_to(root).as_posix().casefold() for path in pruned}
    actual = {
        _record_target(path, root).relative_to(root).as_posix().casefold()
        for path in root.rglob("*") if path.is_file()
    }
    if actual != retained:
        raise RuntimeLockError("installed_runtime_final_inventory_mismatch")
    return {
        "schema_version": "portable-runtime-canonical-v1", "status": "PASS",
        "site_packages": site_packages.relative_to(root).as_posix(),
        "record_policy": "vendor RECORD preserved; this inventory is authoritative for exclusions",
        "locked_distribution_count": len(expected), "installed_distribution_count": len(installed),
        "empty_hash_entries": empty_hash_entries, "counts": counts,
        "distributions": [installed[name] for name in sorted(installed)],
        "pruning_source_files": [{"path": path, "sha256": hashlib.sha256(text.encode()).hexdigest()}
                                 for path, text in sources],
        "files": inventory,
    }
