"""Windows-safe ZIP member validation and extraction helpers."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path, PureWindowsPath
from typing import Iterable


class ArchiveMemberError(ValueError):
    """Raised when a ZIP member cannot be safely represented on Windows."""


_WINDOWS_DEVICE_NAMES = {
    "aux",
    "clock$",
    "con",
    "conin$",
    "conout$",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
    *(f"com{suffix}" for suffix in ("¹", "²", "³")),
    *(f"lpt{suffix}" for suffix in ("¹", "²", "³")),
}


def _is_windows_device_name(part: str) -> bool:
    stem = part.split(".", 1)[0].rstrip(" .").casefold()
    return stem in _WINDOWS_DEVICE_NAMES


def _normalized_name(filename: str) -> str:
    if not filename or "\x00" in filename:
        raise ArchiveMemberError(f"unsafe_archive_member:{filename!r}")
    if filename.startswith(("/", "\\")):
        raise ArchiveMemberError(f"unsafe_archive_member:{filename}")

    windows_path = PureWindowsPath(filename)
    if windows_path.drive or windows_path.anchor:
        raise ArchiveMemberError(f"unsafe_archive_member:{filename}")

    slash_name = filename.replace("\\", "/")
    parts = slash_name.split("/")
    if parts and parts[-1] == "" and filename.endswith(("/", "\\")):
        parts.pop()
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ArchiveMemberError(f"unsafe_archive_member:{filename}")
    if any(part.endswith((".", " ")) or _is_windows_device_name(part) for part in parts):
        raise ArchiveMemberError(f"unsafe_archive_member:{filename}")
    if any(":" in part for part in parts):
        # This rejects drive-relative paths and NTFS alternate data streams.
        raise ArchiveMemberError(f"unsafe_archive_member:{filename}")
    return "/".join(parts)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return bool((info.external_attr >> 16) & 0o170000 == 0o120000)


def safe_zip_infos(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Validate every member and return non-directory entries in archive order."""

    members: list[zipfile.ZipInfo] = []
    seen: set[str] = set()
    for info in archive.infolist():
        normalized = _normalized_name(info.filename)
        key = normalized.casefold()
        if key in seen:
            raise ArchiveMemberError(f"duplicate_archive_member:{info.filename}")
        seen.add(key)
        if _is_symlink(info):
            raise ArchiveMemberError(f"symlink_archive_member:{info.filename}")
        if not info.is_dir():
            members.append(info)
    return members


def safe_zip_members(archive: Path) -> Iterable[zipfile.ZipInfo]:
    """Validate a ZIP path and yield its non-directory members."""

    with zipfile.ZipFile(archive) as handle:
        yield from safe_zip_infos(handle)


def _safe_destination(root: Path, normalized_name: str) -> Path:
    resolved_root = root.resolve()
    target = root.joinpath(*normalized_name.split("/"))
    resolved_target = target.resolve(strict=False)
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError as exc:
        raise ArchiveMemberError(f"unsafe_archive_destination:{normalized_name}") from exc

    current = resolved_root
    for part in normalized_name.split("/")[:-1]:
        current = current / part
        if current.is_symlink():
            raise ArchiveMemberError(f"symlink_archive_destination:{normalized_name}")
    if target.exists() and target.is_symlink():
        raise ArchiveMemberError(f"symlink_archive_destination:{normalized_name}")
    return target


def extract_zip(archive: Path, destination: Path) -> None:
    """Extract a validated archive without following an escaping path."""

    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as handle:
        for info in safe_zip_infos(handle):
            normalized = _normalized_name(info.filename)
            target = _safe_destination(destination, normalized)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.is_symlink():
                raise ArchiveMemberError(f"symlink_archive_destination:{normalized}")
            with handle.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
