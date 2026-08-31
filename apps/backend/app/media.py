from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class InspectionResult:
    metadata: dict[str, Any]
    warnings: list[str]
    tool_version: str | None


@dataclass(frozen=True)
class ToolStatus:
    name: str
    available: bool
    path_source: str
    executable: str | None = None
    version: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class RuntimeStatus:
    ffmpeg: ToolStatus
    ffprobe: ToolStatus
    readiness_state: str
    paired_version_consistent: bool
    warnings: list[str]
    metadata_inspection_available: bool
    reference_frame_extraction_available: bool


@dataclass(frozen=True)
class ReferenceFrameResult:
    preview_path: Path
    requested_pts_ms: int
    resolved_pts_ms: int | None
    decoder_seek_pts_ms: int | None
    first_decoded_pts_ms: int | None
    selected_pts_ms: int | None
    preview_width: int | None
    preview_height: int | None
    source_width: int | None
    source_height: int | None
    rotation_degrees: int | None
    warnings: list[str]


class MediaRuntimeError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ffprobe_available() -> bool:
    return _resolve_tool("ffprobe") is not None


def media_runtime_status(timeout_seconds: int = 5) -> RuntimeStatus:
    ffmpeg = _tool_status("ffmpeg", timeout_seconds)
    ffprobe = _tool_status("ffprobe", timeout_seconds)
    pair_ready = ffmpeg.available and ffprobe.available
    paired_version_consistent = _version_signature(ffmpeg.version) == _version_signature(ffprobe.version)
    warnings: list[str] = []
    if not ffmpeg.available and not ffprobe.available:
        readiness_state = "media_runtime_missing"
        warnings.extend(["ffmpeg_missing", "ffprobe_missing"])
    elif not pair_ready:
        readiness_state = "media_runtime_partially_available"
        if not ffmpeg.available:
            warnings.append(ffmpeg.error_code or "ffmpeg_missing")
        if not ffprobe.available:
            warnings.append(ffprobe.error_code or "ffprobe_missing")
    elif not paired_version_consistent:
        readiness_state = "media_runtime_partially_available"
        warnings.append("ffmpeg_ffprobe_version_mismatch")
    else:
        readiness_state = "media_runtime_ready"
    return RuntimeStatus(
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        readiness_state=readiness_state,
        paired_version_consistent=paired_version_consistent,
        warnings=warnings,
        metadata_inspection_available=ffprobe.available,
        reference_frame_extraction_available=pair_ready and paired_version_consistent,
    )


def inspect_media(path: Path, timeout_seconds: int = 20) -> InspectionResult:
    ffprobe = _resolve_tool("ffprobe")
    if ffprobe is None:
        return InspectionResult(
            metadata={},
            warnings=["ffprobe_unavailable"],
            tool_version=None,
        )
    version = subprocess.run(
        [str(ffprobe), "-version"],
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_seconds,
    ).stdout.splitlines()[0]
    result = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_seconds,
    )
    if result.returncode != 0:
        return InspectionResult(
            metadata={},
            warnings=[f"ffprobe_failed:{result.stderr.strip() or result.returncode}"],
            tool_version=version,
        )
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio_present = any(stream.get("codec_type") == "audio" for stream in streams)
    if video is None:
        return InspectionResult(
            metadata={"format": payload.get("format", {})},
            warnings=["no_video_stream"],
            tool_version=version,
        )
    fmt = payload.get("format", {})
    tags = fmt.get("tags", {}) | video.get("tags", {})
    duration_seconds = _float_or_none(video.get("duration")) or _float_or_none(fmt.get("duration"))
    frame_count = _int_or_none(video.get("nb_frames"))
    nominal = video.get("r_frame_rate")
    average = video.get("avg_frame_rate")
    warnings: list[str] = []
    if nominal and average and nominal != average:
        warnings.append("possible_variable_frame_rate")
    return InspectionResult(
        metadata={
            "container_format": fmt.get("format_name"),
            "video_codec": video.get("codec_name"),
            "duration_ms": int(duration_seconds * 1000) if duration_seconds is not None else None,
            "width": _int_or_none(video.get("width")),
            "height": _int_or_none(video.get("height")),
            "sample_aspect_ratio": video.get("sample_aspect_ratio"),
            "display_aspect_ratio": video.get("display_aspect_ratio"),
            "nominal_frame_rate": nominal,
            "average_frame_rate": average,
            "stream_time_base": video.get("time_base"),
            "start_pts": _int_or_none(video.get("start_pts")),
            "first_video_pts": _int_or_none(video.get("start_pts")),
            "frame_count": frame_count,
            "variable_frame_rate": 1 if "possible_variable_frame_rate" in warnings else 0,
            "rotation_degrees": _rotation(tags),
            "metadata_creation_time": tags.get("creation_time"),
            "audio_present": 1 if audio_present else 0,
            "format_start_time": fmt.get("start_time"),
        },
        warnings=warnings,
        tool_version=version,
    )


def extract_reference_frame(
    source_path: Path,
    output_path: Path,
    requested_pts_ms: int,
    timeout_seconds: int = 20,
    max_preview_width: int = 1280,
) -> ReferenceFrameResult:
    runtime = media_runtime_status()
    ffmpeg = _resolve_tool("ffmpeg")
    if not runtime.ffprobe.available:
        raise MediaRuntimeError("ffprobe_missing", "ffprobe is required for reference-frame extraction")
    if not runtime.ffmpeg.available:
        raise MediaRuntimeError("ffmpeg_missing", "ffmpeg is required for reference-frame extraction")
    if not runtime.paired_version_consistent:
        raise MediaRuntimeError("ffmpeg_ffprobe_version_mismatch", "ffmpeg and ffprobe must come from a consistent build")
    if ffmpeg is None:
        raise MediaRuntimeError("ffmpeg_missing", "ffmpeg is required for reference-frame extraction")
    if not source_path.exists():
        raise MediaRuntimeError("source_missing", "source media is unavailable")

    inspection = inspect_media(source_path, timeout_seconds=timeout_seconds)
    if "no_video_stream" in inspection.warnings:
        raise MediaRuntimeError("no_video_stream", "source contains no video stream")
    if inspection.metadata == {} and inspection.warnings:
        raise MediaRuntimeError("unsupported_source", "source metadata could not be inspected")

    duration_ms = inspection.metadata.get("duration_ms")
    warnings = list(inspection.warnings)
    if requested_pts_ms < 0:
        warnings.append("requested_before_media_start")
        requested_pts_ms = 0
    if isinstance(duration_ms, int) and requested_pts_ms > duration_ms:
        warnings.append("requested_after_media_end")
        requested_pts_ms = max(duration_ms - 1, 0)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp_seconds = requested_pts_ms / 1000
    result = subprocess.run(
        [
            str(ffmpeg),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp_seconds:.3f}",
            "-i",
            str(source_path),
            "-frames:v",
            "1",
            "-vf",
            f"scale='min({max_preview_width},iw)':-2",
            str(output_path),
        ],
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_seconds,
    )
    if result.returncode != 0 or not output_path.exists():
        message = (result.stderr or "").lower()
        code = "reference_frame_failed"
        if "permission" in message:
            code = "permission_failure"
        elif "invalid data" in message or "moov atom not found" in message:
            code = "corrupted_source"
        raise MediaRuntimeError(code, "reference frame extraction failed")

    dimensions = _image_dimensions(output_path)
    return ReferenceFrameResult(
        preview_path=output_path,
        requested_pts_ms=requested_pts_ms,
        resolved_pts_ms=requested_pts_ms,
        decoder_seek_pts_ms=requested_pts_ms,
        first_decoded_pts_ms=requested_pts_ms,
        selected_pts_ms=requested_pts_ms,
        preview_width=dimensions[0],
        preview_height=dimensions[1],
        source_width=inspection.metadata.get("width"),
        source_height=inspection.metadata.get("height"),
        rotation_degrees=inspection.metadata.get("rotation_degrees"),
        warnings=warnings,
    )


def _tool_status(name: str, timeout_seconds: int) -> ToolStatus:
    resolved = _resolve_tool_with_source(name)
    if resolved is None:
        return ToolStatus(name=name, available=False, path_source="unresolved", error_code=f"{name}_missing")
    path, path_source = resolved
    if not _is_allowed_executable(path, name):
        return ToolStatus(
            name=name,
            available=False,
            path_source=path_source,
            executable=str(path),
            error_code="invalid_executable",
        )
    try:
        version = subprocess.run(
            [str(path), "-version"],
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
        )
    except PermissionError:
        return ToolStatus(name=name, available=False, path_source=path_source, executable=str(path), error_code="permission_failure")
    except subprocess.TimeoutExpired:
        return ToolStatus(name=name, available=False, path_source=path_source, executable=str(path), error_code="timeout")
    except OSError:
        return ToolStatus(name=name, available=False, path_source=path_source, executable=str(path), error_code="invocation_failure")
    if version.returncode != 0:
        return ToolStatus(name=name, available=False, path_source=path_source, executable=str(path), error_code="invocation_failure")
    return ToolStatus(
        name=name,
        available=True,
        path_source=path_source,
        executable=str(path),
        version=version.stdout.splitlines()[0] if version.stdout else None,
    )


def _resolve_tool(name: str) -> Path | None:
    resolved = _resolve_tool_with_source(name)
    if resolved is None:
        return None
    return resolved[0]


def _resolve_tool_with_source(name: str) -> tuple[Path, str] | None:
    for candidate in _configured_ffmpeg_candidates(name):
        if candidate.exists():
            return candidate, "TVA_FFMPEG_DIR"
    repo_candidate = ROOT / ".local-tools" / "ffmpeg" / "bin" / _exe_name(name)
    if repo_candidate.exists():
        return repo_candidate, "repository_local"
    path = shutil.which(name)
    if path is not None:
        return Path(path), "PATH"
    return None


def _configured_ffmpeg_candidates(name: str) -> list[Path]:
    configured = os.getenv("TVA_FFMPEG_DIR")
    if not configured:
        return []
    root = Path(configured).expanduser()
    return [root / _exe_name(name), root / "bin" / _exe_name(name)]


def _exe_name(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def _is_allowed_executable(path: Path, name: str) -> bool:
    return path.is_file() and path.name.lower() in {name.lower(), f"{name.lower()}.exe"}


def _version_signature(version: str | None) -> str | None:
    if not version:
        return None
    parts = version.split()
    return parts[2] if len(parts) >= 3 and parts[0].lower() in {"ffmpeg", "ffprobe"} else version


def _image_dimensions(path: Path) -> tuple[int | None, int | None]:
    # PNG stores width/height as big-endian integers at bytes 16..24.
    try:
        data = path.read_bytes()[:24]
    except OSError:
        return None, None
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    return None, None


def png_dimensions(path: Path) -> tuple[int | None, int | None]:
    return _image_dimensions(path)


def _float_or_none(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _int_or_none(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _rotation(tags: dict[str, Any]) -> int | None:
    for key in ("rotate", "rotation"):
        value = _int_or_none(tags.get(key))
        if value is not None:
            return value
    return None
