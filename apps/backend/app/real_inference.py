from __future__ import annotations

import hashlib
import json
import math
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from tools.ai_stack.registry import load_registry
from tools.ai_stack.runtime import file_sha256

from .domain import TimeContract
from .engineering_outputs import (
    ClassificationPolicy,
    ClassificationStatus,
    RAW_MAPPING_BY_NAME,
    classify_track_evidence,
    compact_track_evidence,
)
from .media import media_runtime_status
from .processing_profiles import (
    CLASSIFICATION_POLICY_REVISION,
    CROSSING_POLICY_REVISION,
    PARAMETER_SCHEMA_REVISION,
    runtime_configuration_hash as canonical_runtime_configuration_hash,
    runtime_configuration_payload as canonical_runtime_configuration_payload,
)
from .synthetic_counting import (
    BoundingBox,
    CountingRunResult,
    CountingScene,
    CountingTolerances,
    Observation,
    Point,
    REAL_TRACK_PROVENANCE,
    REAL_TRACK_SCHEMA_VERSION,
    SyntheticTrack,
    SyntheticTrackSet,
    execute_synthetic_counting,
)


class RealInferenceError(RuntimeError):
    def __init__(self, code: str, category: str, detail: str, *, retryable: bool = False):
        super().__init__(detail)
        self.code = code
        self.category = category
        self.detail = detail
        self.retryable = retryable


class RealInferenceCancelled(RealInferenceError):
    def __init__(self) -> None:
        super().__init__("CANCELLED", "operator_cancelled", "Processing was cancelled by the operator.")


@dataclass(frozen=True)
class RealProcessingConfig:
    detector_id: str = "detector.ultralytics-yolo11n-coco"
    tracker_id: str = "tracker.trackers-bytetrack"
    device_mode: str = "AUTO"
    # The value is populated only after the worker resolves AUTO/CPU/CUDA.
    resolved_device: str = "AUTO"
    confidence_threshold: float = 0.25
    iou_threshold: float = 0.70
    image_size: int = 640
    frame_stride: int = 1
    track_activation_threshold: float = 0.25
    lost_track_buffer: int = 30
    minimum_iou_threshold: float = 0.10
    minimum_consecutive_frames: int = 1
    class_allowlist: tuple[str, ...] = (
        "bicycle",
        "bus",
        "car",
        "motorcycle",
        "other",
        "person",
        "truck",
    )
    minimum_track_duration_ms: int = 0
    minimum_track_observations: int = 2
    crossing_tolerance: float = 1e-9
    crossing_hysteresis: float = 0.002
    minimum_movement_distance: float = 0.002
    minimum_side_stability_frames: int = 1
    duplicate_crossing_cooldown_ms: int = 250
    classification_min_observations: int = 2
    classification_min_winning_vote_share: float = 0.60
    classification_min_weighted_share: float = 0.60
    classification_near_tie_margin: float = 0.10
    anchor: str = "bottom_center"
    sampling: str = "every_decoded_frame"

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "RealProcessingConfig":
        source = dict(payload or {})
        # Persisted job configurations also contain immutable run metadata. Those
        # fields are not operator-configurable and are intentionally ignored when
        # reconstructing the adapter configuration for a retry/worker claim.
        for metadata_key in (
            "mode",
            "fixture_id",
            "validation_only",
            "scene_schema_version",
            "resolved_device",
            "config_revision",
            "processing_configuration_revision_id",
            "profile_code",
            "profile_revision",
            "guided_settings",
            "requested_expert_overrides",
            "resolved_parameters",
            "configuration_schema_revision",
            "parameter_schema_revision",
            "configuration_hash",
            "runtime_configuration_hash",
            "request_provenance_hash",
            "runtime_provenance_status",
            "runtime_provenance",
            "model_revision",
            "weight_sha256",
            "tracker_revision",
            "crossing_policy_revision",
            "classification_policy_revision",
            "device_request",
            "requested_guided_settings",
            "normalizations",
            "warnings",
            "errors",
            "valid",
            "estimated_resource_impact",
            "adapter_support",
        ):
            source.pop(metadata_key, None)
        device = str(source.pop("device_mode", source.pop("device", "AUTO"))).upper()
        if device not in {"AUTO", "CPU", "CUDA"}:
            raise RealInferenceError("invalid_device_mode", "configuration", f"Unsupported device mode: {device}")
        detector_id = str(source.pop("detector_id", cls.detector_id))
        tracker_id = str(source.pop("tracker_id", cls.tracker_id))
        if detector_id != cls.detector_id:
            raise RealInferenceError("unsupported_detector", "configuration", detector_id)
        if tracker_id != cls.tracker_id:
            raise RealInferenceError("unsupported_tracker", "configuration", tracker_id)
        aliases = {
            "confidence": "confidence_threshold",
            "conf": "confidence_threshold",
            "iou": "iou_threshold",
            "imgsz": "image_size",
            "track_buffer": "lost_track_buffer",
        }
        for old_key, new_key in aliases.items():
            if old_key in source and new_key not in source:
                source[new_key] = source.pop(old_key)
        allowed = {
            "confidence_threshold",
            "iou_threshold",
            "image_size",
            "frame_stride",
            "track_activation_threshold",
            "lost_track_buffer",
            "minimum_iou_threshold",
            "minimum_consecutive_frames",
            "class_allowlist",
            "minimum_track_duration_ms",
            "minimum_track_observations",
            "crossing_anchor",
            "crossing_tolerance",
            "crossing_hysteresis",
            "minimum_movement_distance",
            "minimum_side_stability_frames",
            "duplicate_crossing_cooldown_ms",
            "classification_min_observations",
            "classification_min_winning_vote_share",
            "classification_min_weighted_share",
            "classification_near_tie_margin",
            "anchor",
            "sampling",
        }
        unknown = sorted(set(source) - allowed)
        if unknown:
            raise RealInferenceError(
                "unsupported_configuration_key",
                "configuration",
                f"Unsupported real-video configuration key(s): {', '.join(unknown)}",
            )
        try:
            values = {
                "confidence_threshold": float(source.get("confidence_threshold", cls.confidence_threshold)),
                "iou_threshold": float(source.get("iou_threshold", cls.iou_threshold)),
                "image_size": int(source.get("image_size", cls.image_size)),
                "frame_stride": int(source.get("frame_stride", cls.frame_stride)),
                "track_activation_threshold": float(
                    source.get("track_activation_threshold", cls.track_activation_threshold)
                ),
                "lost_track_buffer": int(source.get("lost_track_buffer", cls.lost_track_buffer)),
                "minimum_iou_threshold": float(
                    source.get("minimum_iou_threshold", cls.minimum_iou_threshold)
                ),
                "minimum_consecutive_frames": int(
                    source.get("minimum_consecutive_frames", cls.minimum_consecutive_frames)
                ),
                "class_allowlist": tuple(
                    sorted({str(value).strip().lower() for value in source.get("class_allowlist", cls.class_allowlist)})
                ),
                "minimum_track_duration_ms": int(
                    source.get("minimum_track_duration_ms", cls.minimum_track_duration_ms)
                ),
                "minimum_track_observations": int(
                    source.get("minimum_track_observations", cls.minimum_track_observations)
                ),
                "crossing_tolerance": float(source.get("crossing_tolerance", cls.crossing_tolerance)),
                "crossing_hysteresis": float(source.get("crossing_hysteresis", cls.crossing_hysteresis)),
                "minimum_movement_distance": float(
                    source.get("minimum_movement_distance", cls.minimum_movement_distance)
                ),
                "minimum_side_stability_frames": int(
                    source.get("minimum_side_stability_frames", cls.minimum_side_stability_frames)
                ),
                "duplicate_crossing_cooldown_ms": int(
                    source.get("duplicate_crossing_cooldown_ms", cls.duplicate_crossing_cooldown_ms)
                ),
                "classification_min_observations": int(
                    source.get("classification_min_observations", cls.classification_min_observations)
                ),
                "classification_min_winning_vote_share": float(
                    source.get(
                        "classification_min_winning_vote_share", cls.classification_min_winning_vote_share
                    )
                ),
                "classification_min_weighted_share": float(
                    source.get("classification_min_weighted_share", cls.classification_min_weighted_share)
                ),
                "classification_near_tie_margin": float(
                    source.get("classification_near_tie_margin", cls.classification_near_tie_margin)
                ),
                "anchor": str(source.get("crossing_anchor", source.get("anchor", cls.anchor))),
                "sampling": str(source.get("sampling", cls.sampling)),
            }
        except (TypeError, ValueError) as exc:
            raise RealInferenceError("invalid_configuration", "configuration", str(exc)) from exc
        if not 0 <= values["confidence_threshold"] <= 1:
            raise RealInferenceError("invalid_confidence_threshold", "configuration", "confidence_threshold must be in [0, 1].")
        if not 0 <= values["iou_threshold"] <= 1:
            raise RealInferenceError("invalid_iou_threshold", "configuration", "iou_threshold must be in [0, 1].")
        if values["image_size"] < 64 or values["image_size"] > 4096:
            raise RealInferenceError("invalid_image_size", "configuration", "image_size must be between 64 and 4096.")
        if values["frame_stride"] < 1:
            raise RealInferenceError("invalid_frame_stride", "configuration", "frame_stride must be at least 1.")
        if values["lost_track_buffer"] < 1 or values["minimum_consecutive_frames"] < 1:
            raise RealInferenceError("invalid_tracker_configuration", "configuration", "tracker buffer values must be positive.")
        if not values["class_allowlist"]:
            raise RealInferenceError("invalid_class_allowlist", "configuration", "class_allowlist must not be empty.")
        unsupported_classes = sorted(set(values["class_allowlist"]) - set(RAW_MAPPING_BY_NAME))
        if unsupported_classes:
            raise RealInferenceError("unsupported_class_allowlist", "configuration", ", ".join(unsupported_classes))
        numeric_ranges = (
            ("crossing_tolerance", 0.0, 0.2),
            ("crossing_hysteresis", 0.0, 0.2),
            ("minimum_movement_distance", 0.0, 1.0),
            ("classification_min_winning_vote_share", 0.5, 1.0),
            ("classification_min_weighted_share", 0.5, 1.0),
            ("classification_near_tie_margin", 0.0, 0.5),
        )
        for name, minimum, maximum in numeric_ranges:
            if not minimum <= values[name] <= maximum:
                raise RealInferenceError("invalid_configuration_range", "configuration", name)
        if values["minimum_track_duration_ms"] < 0 or values["minimum_track_observations"] < 1:
            raise RealInferenceError("invalid_track_filter", "configuration", "track duration and observations are invalid.")
        if values["minimum_side_stability_frames"] < 1 or values["duplicate_crossing_cooldown_ms"] < 0:
            raise RealInferenceError("invalid_crossing_policy", "configuration", "crossing policy values are invalid.")
        if values["anchor"] != "bottom_center":
            raise RealInferenceError("unsupported_anchor", "configuration", "Only the bottom_center anchor is supported.")
        if values["sampling"] != "every_decoded_frame":
            raise RealInferenceError("unsupported_sampling", "configuration", "Only deterministic every_decoded_frame sampling is supported.")
        return cls(detector_id=detector_id, tracker_id=tracker_id, device_mode=device, **values)

    def public_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["class_allowlist"] = list(self.class_allowlist)
        payload["crossing_anchor"] = self.anchor
        payload["anchor"] = self.anchor
        payload["config_revision"] = configuration_revision(payload)
        return payload

    @property
    def crossing_anchor(self) -> str:
        return self.anchor


@dataclass(frozen=True)
class DecodedFrame:
    frame_index: int
    pts_ms: int
    image: Any


@dataclass(frozen=True)
class Detection:
    xyxy: tuple[float, float, float, float]
    confidence: float
    raw_class_id: int
    raw_class_name: str


@dataclass(frozen=True)
class RealInferenceResult:
    counting_result: CountingRunResult
    config: RealProcessingConfig
    stats: dict[str, Any]
    tracks: tuple[dict[str, Any], ...]


def _gpu_memory_mb(device: str) -> dict[str, float] | None:
    if not str(device).startswith("cuda"):
        return None
    try:
        import torch  # type: ignore

        return {
            "allocated": round(float(torch.cuda.memory_allocated()) / 1_048_576, 3),
            "reserved": round(float(torch.cuda.memory_reserved()) / 1_048_576, 3),
        }
    except Exception:
        return None


def eligible_real_tracks_for_counting(
    tracks: Iterable[SyntheticTrack],
    *,
    minimum_observations: int = 2,
    minimum_duration_ms: int = 0,
) -> tuple[SyntheticTrack, ...]:
    """Keep short-lived detector tracks from invalidating otherwise valid crossings."""
    return tuple(
        track
        for track in tracks
        if len(track.observations) >= minimum_observations
        and (
            not track.observations
            or track.observations[-1].timestamp_ms - track.observations[0].timestamp_ms >= minimum_duration_ms
        )
    )


def configuration_revision(config: dict[str, Any]) -> str:
    stable = {key: value for key, value in config.items() if key not in {"resolved_device", "config_revision"}}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def add_ai_site_packages(root: Path) -> None:
    candidates = [root / ".venv-ai" / "Lib" / "site-packages"]
    candidates.extend((root / ".venv-ai" / "lib").glob("python*/site-packages"))
    for candidate in reversed(candidates):
        if candidate.exists() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))


def resolve_real_runtime(root: Path, config: RealProcessingConfig) -> RealProcessingConfig:
    registry_path = root / "model_registry.json"
    if not registry_path.exists():
        raise RealInferenceError("model_registry_missing", "runtime", str(registry_path))
    registry = load_registry(registry_path)
    detector_record = next(
        (item for item in registry.get("models", []) if item.get("model_id") == config.detector_id), None
    )
    tracker_record = next(
        (item for item in registry.get("models", []) if item.get("model_id") == config.tracker_id), None
    )
    if detector_record is None or tracker_record is None:
        raise RealInferenceError("model_registry_entry_missing", "runtime", "Primary detector or tracker is not registered.")
    weight_path = root / ".local-tools" / "models" / str(detector_record.get("model_filename", ""))
    if not weight_path.is_file() or weight_path.is_symlink():
        raise RealInferenceError("weight_missing", "runtime", str(weight_path), retryable=True)
    expected_hash = detector_record.get("sha256")
    actual_hash = file_sha256(weight_path)
    if expected_hash and actual_hash != expected_hash:
        raise RealInferenceError("weight_hash_mismatch", "runtime", f"expected={expected_hash} actual={actual_hash}")
    add_ai_site_packages(root)
    try:
        import torch  # type: ignore
    except Exception as exc:
        raise RealInferenceError("torch_unavailable", "runtime", str(exc), retryable=True) from exc
    cuda_available = bool(torch.cuda.is_available())
    if config.device_mode == "CUDA" and not cuda_available:
        raise RealInferenceError("cuda_unavailable", "device", "CUDA was requested but torch reports no CUDA device.")
    resolved = "cuda:0" if config.device_mode == "CUDA" or (config.device_mode == "AUTO" and cuda_available) else "cpu"
    return replace(config, resolved_device=resolved)


def _runtime_provenance(root: Path, config: RealProcessingConfig) -> dict[str, str | None]:
    registry = load_registry(root / "model_registry.json")
    detector = next(item for item in registry["models"] if item["model_id"] == config.detector_id)
    tracker = next(item for item in registry["models"] if item["model_id"] == config.tracker_id)
    weight_path = root / ".local-tools" / "models" / str(detector["model_filename"])
    return {
        "detector_id": str(detector["model_id"]),
        "detector_version": str(detector.get("exact_version") or "unknown"),
        "detector_revision": str(detector.get("commit_or_tag") or detector.get("exact_version") or "unknown"),
        "model_revision": str(detector.get("commit_or_tag") or detector.get("exact_version") or "unknown"),
        "tracker_id": str(tracker["model_id"]),
        "tracker_version": str(tracker.get("exact_version") or "unknown"),
        "tracker_revision": str(tracker.get("commit_or_tag") or tracker.get("exact_version") or "unknown"),
        "weight_identifier": str(detector.get("model_filename") or "unknown"),
        "weight_sha256": file_sha256(weight_path) if weight_path.is_file() else None,
    }


def _payload_differences(configured: Any, actual: Any, path: str = "") -> list[dict[str, Any]]:
    if isinstance(configured, Mapping) and isinstance(actual, Mapping):
        differences: list[dict[str, Any]] = []
        for key in sorted(set(configured) | set(actual)):
            field = f"{path}.{key}" if path else str(key)
            if key not in configured:
                differences.append({"field": field, "configured": "<MISSING>", "actual": actual[key]})
            elif key not in actual:
                differences.append({"field": field, "configured": configured[key], "actual": "<MISSING>"})
            else:
                differences.extend(_payload_differences(configured[key], actual[key], field))
        return differences
    if configured != actual:
        return [{"field": path, "configured": configured, "actual": actual}]
    return []


def evaluate_runtime_provenance(
    *,
    config: RealProcessingConfig,
    configured_runtime_provenance: Mapping[str, Any] | None,
    actual_runtime_provenance: Mapping[str, Any],
    legacy_configured_runtime_hash: str | None = None,
) -> dict[str, Any]:
    """Compare post-resolution expected inputs with observed executable inputs."""
    configured = dict(configured_runtime_provenance or {})
    required = (
        "parameter_schema_revision",
        "model_revision",
        "weight_sha256",
        "tracker_revision",
        "crossing_policy_revision",
        "classification_policy_revision",
    )
    actual_parameters = config.public_payload()
    actual_parameters["detector_id"] = actual_runtime_provenance.get("detector_id") or config.detector_id
    actual_parameters["tracker_id"] = actual_runtime_provenance.get("tracker_id") or config.tracker_id
    actual_payload = canonical_runtime_configuration_payload(
        parameter_schema_revision=PARAMETER_SCHEMA_REVISION,
        resolved_parameters=actual_parameters,
        model_revision=str(actual_runtime_provenance.get("model_revision") or actual_runtime_provenance.get("detector_revision") or ""),
        weight_sha256=actual_runtime_provenance.get("weight_sha256"),
        tracker_revision=str(actual_runtime_provenance.get("tracker_revision") or ""),
        crossing_policy_revision=CROSSING_POLICY_REVISION,
        classification_policy_revision=CLASSIFICATION_POLICY_REVISION,
        resolved_device=config.resolved_device,
    )
    actual_hash = canonical_runtime_configuration_hash(
        parameter_schema_revision=PARAMETER_SCHEMA_REVISION,
        resolved_parameters=actual_parameters,
        model_revision=str(actual_runtime_provenance.get("model_revision") or actual_runtime_provenance.get("detector_revision") or ""),
        weight_sha256=actual_runtime_provenance.get("weight_sha256"),
        tracker_revision=str(actual_runtime_provenance.get("tracker_revision") or ""),
        crossing_policy_revision=CROSSING_POLICY_REVISION,
        classification_policy_revision=CLASSIFICATION_POLICY_REVISION,
        resolved_device=config.resolved_device,
    )
    missing = [field for field in required if not configured.get(field)]
    if missing:
        return {
            "runtime_configuration_hash": None,
            "actual_runtime_configuration_hash": actual_hash,
            "provenance_status": "UNRESOLVED_CONFIGURED_RUNTIME_PROVENANCE",
            "provenance_mismatches": [
                {"field": field, "configured": "<MISSING>", "actual": actual_payload.get(field)} for field in missing
            ],
            "configured_runtime_payload": None,
            "actual_runtime_payload": actual_payload,
        }
    configured_payload = canonical_runtime_configuration_payload(
        parameter_schema_revision=str(configured["parameter_schema_revision"]),
        resolved_parameters=config.public_payload(),
        model_revision=str(configured["model_revision"]),
        weight_sha256=str(configured["weight_sha256"]),
        tracker_revision=str(configured["tracker_revision"]),
        crossing_policy_revision=str(configured["crossing_policy_revision"]),
        classification_policy_revision=str(configured["classification_policy_revision"]),
        resolved_device=config.resolved_device,
    )
    configured_hash = canonical_runtime_configuration_hash(
        parameter_schema_revision=str(configured["parameter_schema_revision"]),
        resolved_parameters=config.public_payload(),
        model_revision=str(configured["model_revision"]),
        weight_sha256=str(configured["weight_sha256"]),
        tracker_revision=str(configured["tracker_revision"]),
        crossing_policy_revision=str(configured["crossing_policy_revision"]),
        classification_policy_revision=str(configured["classification_policy_revision"]),
        resolved_device=config.resolved_device,
    )
    differences = _payload_differences(configured_payload, actual_payload)
    if legacy_configured_runtime_hash and legacy_configured_runtime_hash != configured_hash:
        differences.append(
            {
                "field": "legacy_pre_execution_runtime_configuration_hash",
                "configured": legacy_configured_runtime_hash,
                "actual": configured_hash,
            }
        )
    return {
        "runtime_configuration_hash": configured_hash,
        "actual_runtime_configuration_hash": actual_hash,
        "provenance_status": "VERIFIED_RUNTIME_PROVENANCE" if not differences else "RUNTIME_PROVENANCE_MISMATCH",
        "provenance_mismatches": differences,
        "configured_runtime_payload": configured_payload,
        "actual_runtime_payload": actual_payload,
    }


class FfmpegFrameDecoder:
    _PTS_PATTERN = re.compile(r"pts_time:(?P<pts>[-+0-9.eE]+)")

    def __init__(
        self,
        *,
        executable: str,
        source_path: Path,
        width: int,
        height: int,
        first_source_pts_ms: int | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        heartbeat_callback: Callable[[], None] | None = None,
    ) -> None:
        self.executable = executable
        self.source_path = source_path
        self.width = int(width)
        self.height = int(height)
        self.first_source_pts_ms = first_source_pts_ms
        self.cancel_requested = cancel_requested or (lambda: False)
        self.heartbeat_callback = heartbeat_callback or (lambda: None)
        self.process: subprocess.Popen[bytes] | None = None
        self._pts_queue: queue.Queue[float | None] = queue.Queue()
        self._stderr_tail: list[str] = []
        self._stderr_thread: threading.Thread | None = None
        self._stop_stderr = threading.Event()
        self._cancel_thread: threading.Thread | None = None
        self._stop_cancel_watch = threading.Event()
        self._cancelled_event = threading.Event()
        self._cancel_watch_error: BaseException | None = None

    def __iter__(self) -> Iterable[DecodedFrame]:
        return self.frames()

    def frames(self) -> Iterable[DecodedFrame]:
        if self.width <= 0 or self.height <= 0:
            raise RealInferenceError("invalid_media_dimensions", "media", "Decoded frame dimensions are invalid.")
        command = [
            self.executable,
            "-hide_banner",
            "-loglevel",
            "info",
            "-copyts",
            "-i",
            str(self.source_path),
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            "showinfo",
            "-fps_mode",
            "passthrough",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "pipe:1",
        ]
        try:
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except OSError as exc:
            raise RealInferenceError("ffmpeg_start_failed", "media", str(exc), retryable=True) from exc
        self._stderr_thread = threading.Thread(target=self._read_stderr, name="tva-ffmpeg-stderr", daemon=True)
        self._stderr_thread.start()
        self._stop_cancel_watch.clear()
        self._cancel_thread = threading.Thread(target=self._watch_cancel, name="tva-ffmpeg-cancel", daemon=True)
        self._cancel_thread.start()
        frame_bytes = self.width * self.height * 3
        first_pts_ms = self.first_source_pts_ms
        frame_index = 0
        last_heartbeat = 0.0
        try:
            while True:
                if self.cancel_requested():
                    raise RealInferenceCancelled()
                current_time = time.monotonic()
                if current_time - last_heartbeat >= 5.0:
                    self.heartbeat_callback()
                    last_heartbeat = current_time
                raw = self._read_exact(frame_bytes)
                if self._cancel_watch_error is not None:
                    raise self._cancel_watch_error
                if self._cancelled_event.is_set() or self.cancel_requested():
                    raise RealInferenceCancelled()
                if not raw:
                    break
                if len(raw) != frame_bytes:
                    raise RealInferenceError("short_decoded_frame", "media", "FFmpeg ended with a partial raw frame.")
                deadline = time.monotonic() + 10
                while True:
                    if self._cancel_watch_error is not None:
                        raise self._cancel_watch_error
                    if self._cancelled_event.is_set() or self.cancel_requested():
                        raise RealInferenceCancelled()
                    try:
                        pts_seconds = self._pts_queue.get(timeout=0.25)
                        break
                    except queue.Empty:
                        if time.monotonic() >= deadline:
                            raise RealInferenceError("ffmpeg_pts_timeout", "media", "FFmpeg did not provide a frame PTS.", retryable=True)
                if pts_seconds is None:
                    raise RealInferenceError("missing_frame_pts", "media", "FFmpeg did not provide a PTS for a decoded frame.")
                pts_ms_absolute = round(float(pts_seconds) * 1000)
                if first_pts_ms is None:
                    first_pts_ms = pts_ms_absolute
                pts_ms = pts_ms_absolute - first_pts_ms
                try:
                    import numpy as np  # type: ignore

                    image = np.frombuffer(raw, dtype=np.uint8).reshape((self.height, self.width, 3)).copy()
                except Exception as exc:
                    raise RealInferenceError("numpy_decode_failed", "runtime", str(exc)) from exc
                yield DecodedFrame(frame_index=frame_index, pts_ms=max(0, int(pts_ms)), image=image)
                frame_index += 1
            return_code = self.process.wait()
            if return_code != 0:
                raise RealInferenceError(
                    "ffmpeg_decode_failed",
                    "media",
                    " ".join(self._stderr_tail[-8:]) or f"ffmpeg exited with {return_code}",
                    retryable=True,
                )
        except subprocess.TimeoutExpired as exc:
            raise RealInferenceError("ffmpeg_pts_timeout", "media", str(exc), retryable=True) from exc
        finally:
            self.close()

    def _read_exact(self, size: int) -> bytes:
        assert self.process is not None and self.process.stdout is not None
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = self.process.stdout.read(remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        try:
            for raw_line in iter(self.process.stderr.readline, b""):
                if self._stop_stderr.is_set():
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if line:
                    self._stderr_tail.append(line)
                    del self._stderr_tail[:-32]
                    match = self._PTS_PATTERN.search(line)
                    if match:
                        try:
                            self._pts_queue.put(float(match.group("pts")))
                        except ValueError:
                            pass
        finally:
            self._pts_queue.put(None)

    def _watch_cancel(self) -> None:
        while not self._stop_cancel_watch.wait(0.05):
            try:
                if not self.cancel_requested():
                    continue
            except BaseException as exc:
                # The owner check in the inference loop will surface the typed
                # fencing error; the watcher only needs to unblock FFmpeg.
                self._cancel_watch_error = exc
                self._cancelled_event.set()
            else:
                self._cancelled_event.set()
            process = self.process
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
            return

    def close(self) -> None:
        process = self.process
        self._stop_stderr.set()
        self._stop_cancel_watch.set()
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            if self._stderr_thread is not None:
                self._stderr_thread.join(timeout=2)
            if self._cancel_thread is not None and self._cancel_thread is not threading.current_thread():
                self._cancel_thread.join(timeout=2)
            self.process = None


class UltralyticsDetectorAdapter:
    def __init__(self, root: Path, config: RealProcessingConfig) -> None:
        add_ai_site_packages(root)
        try:
            from ultralytics import YOLO  # type: ignore
        except Exception as exc:
            raise RealInferenceError("detector_import_failed", "runtime", str(exc), retryable=True) from exc
        registry = load_registry(root / "model_registry.json")
        record = next(item for item in registry["models"] if item["model_id"] == config.detector_id)
        weight_path = root / ".local-tools" / "models" / str(record["model_filename"])
        started = time.perf_counter()
        try:
            self.model = YOLO(str(weight_path))
        except Exception as exc:
            raise RealInferenceError("model_load_failed", "runtime", str(exc), retryable=True) from exc
        self.load_ms = round((time.perf_counter() - started) * 1000, 3)
        self.config = config
        self.model_id = config.detector_id
        self.filtered_class_allowlist_count = 0
        self._names: dict[int, str] = {}
        model_names = getattr(self.model, "names", {})
        if isinstance(model_names, dict):
            self._names = {int(key): str(value) for key, value in model_names.items()}
        elif isinstance(model_names, list):
            self._names = {index: str(value) for index, value in enumerate(model_names)}

    def detect(self, image: Any) -> tuple[Detection, ...]:
        predict_kwargs: dict[str, Any] = {
            "device": self.config.resolved_device,
            "conf": self.config.confidence_threshold,
            "iou": self.config.iou_threshold,
            "imgsz": self.config.image_size,
            "verbose": False,
        }
        allowed_class_ids = sorted(
            class_id for class_id, class_name in self._names.items() if class_name.strip().lower() in self.config.class_allowlist
        )
        if allowed_class_ids:
            predict_kwargs["classes"] = allowed_class_ids
        try:
            predictions = self.model.predict(image, **predict_kwargs)
        except Exception as exc:
            raise RealInferenceError("detector_inference_failed", "inference", str(exc), retryable=True) from exc
        if not predictions:
            return ()
        result = predictions[0]
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return ()
        try:
            xyxy = boxes.xyxy.detach().cpu().numpy()
            confidences = boxes.conf.detach().cpu().numpy()
            class_ids = boxes.cls.detach().cpu().numpy()
        except Exception as exc:
            raise RealInferenceError("detector_output_invalid", "inference", str(exc)) from exc
        height, width = image.shape[:2]
        detections: list[Detection] = []
        for index, box in enumerate(xyxy):
            raw_class_id = int(class_ids[index])
            confidence = float(confidences[index])
            raw_class_name = self._names.get(raw_class_id, str(raw_class_id)).strip().lower()
            if self.config.class_allowlist and raw_class_name not in self.config.class_allowlist:
                self.filtered_class_allowlist_count += 1
                continue
            x1, y1, x2, y2 = (float(value) for value in box[:4])
            if not all(math.isfinite(value) for value in (x1, y1, x2, y2, confidence)):
                continue
            x1 = max(0.0, min(float(width), x1))
            x2 = max(0.0, min(float(width), x2))
            y1 = max(0.0, min(float(height), y1))
            y2 = max(0.0, min(float(height), y2))
            if x2 <= x1 or y2 <= y1:
                continue
            detections.append(
                Detection(
                    xyxy=(x1, y1, x2, y2),
                    confidence=max(0.0, min(1.0, confidence)),
                    raw_class_id=raw_class_id,
                    raw_class_name=raw_class_name,
                )
            )
        return tuple(detections)


class ByteTrackTrackerAdapter:
    def __init__(self, root: Path, config: RealProcessingConfig) -> None:
        add_ai_site_packages(root)
        try:
            import numpy as np  # type: ignore
            import supervision as sv  # type: ignore
            from trackers import ByteTrackTracker  # type: ignore
        except Exception as exc:
            raise RealInferenceError("tracker_import_failed", "runtime", str(exc), retryable=True) from exc
        self._np = np
        self._sv = sv
        tracker_config = {
            "track_activation_threshold": config.track_activation_threshold,
            "lost_track_buffer": config.lost_track_buffer,
            "minimum_iou_threshold": config.minimum_iou_threshold,
            "frame_rate": 30,
            "minimum_consecutive_frames": config.minimum_consecutive_frames,
        }
        try:
            import inspect

            signature = inspect.signature(ByteTrackTracker)
            accepts_kwargs = any(item.kind is inspect.Parameter.VAR_KEYWORD for item in signature.parameters.values())
            unsupported = sorted(
                key for key in tracker_config if not accepts_kwargs and key not in signature.parameters
            )
            if unsupported:
                raise RealInferenceError(
                    "tracker_parameter_unsupported",
                    "configuration",
                    ", ".join(unsupported),
                )
            self.tracker = ByteTrackTracker(**tracker_config)
        except RealInferenceError:
            raise
        except (TypeError, ValueError) as exc:
            raise RealInferenceError("tracker_configuration_failed", "configuration", str(exc)) from exc
        self.tracker_id = config.tracker_id

    def update(self, detections: tuple[Detection, ...]) -> tuple[dict[str, Any], ...]:
        xyxy = self._np.asarray([item.xyxy for item in detections], dtype=float).reshape((-1, 4))
        confidence = self._np.asarray([item.confidence for item in detections], dtype=float)
        class_id = self._np.asarray([item.raw_class_id for item in detections], dtype=int)
        class_names = self._np.asarray([item.raw_class_name for item in detections], dtype=object)
        wrapped = self._sv.Detections(
            xyxy=xyxy,
            confidence=confidence,
            class_id=class_id,
            data={"class_name": class_names},
        )
        try:
            updated = self.tracker.update(wrapped, frame=None)
        except TypeError:
            updated = self.tracker.update(wrapped)
        updated_xyxy = self._np.asarray(getattr(updated, "xyxy", []), dtype=float)
        updated_confidence = self._np.asarray(getattr(updated, "confidence", []), dtype=float)
        updated_class_ids = self._np.asarray(getattr(updated, "class_id", []), dtype=int)
        tracker_ids = getattr(updated, "tracker_id", None)
        tracker_ids = self._np.asarray(tracker_ids, dtype=int) if tracker_ids is not None else self._np.arange(len(updated_xyxy), dtype=int)
        data = getattr(updated, "data", {}) or {}
        names = [str(value) for value in data.get("class_name", [])] if "class_name" in data else []
        tracks: list[dict[str, Any]] = []
        for index, box in enumerate(updated_xyxy):
            if index < len(tracker_ids) and int(tracker_ids[index]) < 0:
                continue
            raw_id = int(updated_class_ids[index]) if index < len(updated_class_ids) else -1
            name = names[index] if index < len(names) else str(raw_id)
            tracks.append(
                {
                    "track_id": str(int(tracker_ids[index])) if index < len(tracker_ids) else str(index),
                    "xyxy": tuple(float(value) for value in box[:4]),
                    "raw_class_id": raw_id,
                    "raw_class_name": name,
                    "confidence": float(updated_confidence[index]) if index < len(updated_confidence) else None,
                }
            )
        return tuple(tracks)


def run_real_video_inference(
    *,
    root: Path,
    run_id: str,
    source_path: Path,
    source_fingerprint: str,
    source_width: int,
    source_height: int,
    source_first_pts_ms: int | None,
    source_frame_count: int | None,
    source_duration_ms: int | None,
    scene: CountingScene,
    time_contract: TimeContract,
    source_time_configured: bool,
    config: RealProcessingConfig,
    ffmpeg_executable: str | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    status_callback: Callable[[str, dict[str, Any]], None] | None = None,
    heartbeat_callback: Callable[[], None] | None = None,
    configured_runtime_configuration_hash: str | None = None,
    configured_runtime_provenance: Mapping[str, Any] | None = None,
    configuration_hash: str | None = None,
    request_provenance_hash: str | None = None,
) -> RealInferenceResult:
    cancel_requested = cancel_requested or (lambda: False)
    status_callback = status_callback or (lambda _phase, _detail: None)
    heartbeat_callback = heartbeat_callback or (lambda: None)

    def checkpoint(*, renew: bool = False) -> None:
        if cancel_requested():
            raise RealInferenceCancelled()
        if renew:
            heartbeat_callback()

    checkpoint(renew=True)
    runtime_config = resolve_real_runtime(root, config)
    checkpoint(renew=True)
    status_callback("INITIALIZING_RUNTIME", {"resolved_device": runtime_config.resolved_device})
    status_callback("LOADING_MODEL", {"detector_id": runtime_config.detector_id})
    checkpoint(renew=True)
    detector = UltralyticsDetectorAdapter(root, runtime_config)
    checkpoint(renew=True)
    tracker = ByteTrackTrackerAdapter(root, runtime_config)
    ffmpeg = ffmpeg_executable or media_runtime_status().ffmpeg.executable
    if not ffmpeg:
        raise RealInferenceError("ffmpeg_unavailable", "media", "FFmpeg is not available.", retryable=True)
    decoder = FfmpegFrameDecoder(
        executable=ffmpeg,
        source_path=source_path,
        width=source_width,
        height=source_height,
        first_source_pts_ms=source_first_pts_ms,
        cancel_requested=cancel_requested,
        heartbeat_callback=heartbeat_callback,
    )
    tracks: dict[str, dict[str, Any]] = {}
    decoded_frames = 0
    processed_frames = 0
    detection_count = 0
    unsupported_raw_class_detections = 0
    inference_times: list[float] = []
    tracker_times: list[float] = []
    first_inference_ms: float | None = None
    decode_startup_ms: float | None = None
    decode_started = time.perf_counter()
    started = time.perf_counter()
    status_callback("DECODING", {"total_units": source_frame_count, "indeterminate": source_frame_count is None})
    try:
        for frame in decoder.frames():
            checkpoint()
            if decode_startup_ms is None:
                decode_startup_ms = round((time.perf_counter() - decode_started) * 1000, 3)
            decoded_frames += 1
            if frame.pts_ms < time_contract.analysis_start_pts_ms:
                continue
            if frame.pts_ms >= time_contract.analysis_end_pts_ms:
                break
            if frame.frame_index % runtime_config.frame_stride != 0:
                continue
            checkpoint()
            inference_started = time.perf_counter()
            detections = detector.detect(frame.image)
            checkpoint()
            elapsed_ms = round((time.perf_counter() - inference_started) * 1000, 3)
            inference_times.append(elapsed_ms)
            if first_inference_ms is None:
                first_inference_ms = elapsed_ms
            allowed = []
            for detection in detections:
                if str(detection.raw_class_name).strip().lower() not in RAW_MAPPING_BY_NAME:
                    unsupported_raw_class_detections += 1
                # Unsupported raw evidence remains in the tracker input so it
                # can be audited as UNKNOWN/UNSUPPORTED_RAW_CLASS later.
                allowed.append(detection)
            detection_count += len(allowed)
            checkpoint()
            tracker_started = time.perf_counter()
            tracked = tracker.update(tuple(allowed))
            checkpoint()
            tracker_times.append(round((time.perf_counter() - tracker_started) * 1000, 3))
            processed_frames += 1
            for item in tracked:
                x1, y1, x2, y2 = item["xyxy"]
                width = max(1.0, float(source_width))
                height = max(1.0, float(source_height))
                bbox = BoundingBox(x1 / width, y1 / height, (x2 - x1) / width, (y2 - y1) / height)
                position = Point((x1 + x2) / 2 / width, y2 / height)
                track = tracks.setdefault(
                    str(item["track_id"]),
                    {
                        "track_id": str(item["track_id"]),
                        "observations": [],
                        "evidence": [],
                        "class_ids": [],
                        "native_classes": [],
                        "confidences": [],
                    },
                )
                track["observations"].append(
                    Observation(
                        timestamp_ms=frame.pts_ms,
                        position=position,
                        sample_id=f"frame:{frame.frame_index}",
                        bbox=bbox,
                        source_frame_index=frame.frame_index,
                        raw_class_id=int(item["raw_class_id"]),
                        raw_class_name=str(item["raw_class_name"]),
                        detector_confidence=item.get("confidence"),
                    )
                )
                confidence = item.get("confidence")
                if confidence is not None:
                    try:
                        numeric_confidence = float(confidence)
                    except (TypeError, ValueError):
                        numeric_confidence = None
                    if numeric_confidence is not None and math.isfinite(numeric_confidence) and 0.0 <= numeric_confidence <= 1.0:
                        track["confidences"].append(numeric_confidence)
                track["evidence"].append(
                    {
                        "pts_ms": frame.pts_ms,
                        "raw_class_id": int(item["raw_class_id"]),
                        "native_class": str(item["raw_class_name"]),
                        "confidence": confidence,
                    }
                )
                track["class_ids"].append(int(item["raw_class_id"]))
                track["native_classes"].append(str(item["raw_class_name"]))
            if processed_frames == 1 or processed_frames % 5 == 0:
                checkpoint(renew=True)
                status_callback(
                    "PROCESSING",
                    {
                        "completed_units": decoded_frames,
                        "total_units": source_frame_count,
                        "decoded_frames": decoded_frames,
                        "processed_frames": processed_frames,
                        "detections": detection_count,
                    },
                )
    finally:
        decoder.close()
    if decoded_frames == 0:
        raise RealInferenceError("no_decoded_frames", "media", "No video frames were decoded.")
    checkpoint(renew=True)
    status_callback("FINALIZING_EVENTS", {"track_count": len(tracks)})
    real_tracks: list[SyntheticTrack] = []
    track_metadata: list[dict[str, Any]] = []
    classification_policy = ClassificationPolicy(
        min_observations=runtime_config.classification_min_observations,
        min_winning_vote_share=runtime_config.classification_min_winning_vote_share,
        min_winning_weighted_share=runtime_config.classification_min_weighted_share,
        near_tie_margin=runtime_config.classification_near_tie_margin,
    )
    for track_id, payload in sorted(tracks.items()):
        checkpoint()
        evidence = tuple(payload["evidence"])
        decision = classify_track_evidence(evidence, policy=classification_policy)
        checkpoint()
        compact = compact_track_evidence(
            payload["observations"],
            decision,
            provenance={
                "detector_id": runtime_config.detector_id,
                "tracker_id": runtime_config.tracker_id,
                "configuration": runtime_config.public_payload(),
            },
        )
        provisional = decision.provisional_class
        raw_class_id = decision.track_voted_raw_class_id
        raw_class_name = decision.track_voted_raw_class_name
        confidence = max(payload["confidences"]) if payload["confidences"] else 0.0
        review_state = (
            "ambiguous"
            if decision.classification_status == ClassificationStatus.AMBIGUOUS.value
            else "unknown"
            if decision.classification_status in {
                ClassificationStatus.UNKNOWN.value,
                ClassificationStatus.UNSUPPORTED_RAW_CLASS.value,
                ClassificationStatus.INSUFFICIENT_EVIDENCE.value,
            }
            else "accepted"
        )
        real_tracks.append(
            SyntheticTrack(
                track_id=track_id,
                observations=tuple(payload["observations"]),
                synthetic_class=provisional,
                confidence=confidence,
                provenance=REAL_TRACK_PROVENANCE,
                raw_class_id=raw_class_id,
                raw_class_name=raw_class_name,
                provisional_class=provisional,
                classification_review_state=review_state,
                classification_evidence=(compact,),
                engineering_class=decision.engineering_class,
                classification_status=decision.classification_status,
                classification_reason=decision.classification_reason,
                taxonomy_revision="pilot-observable-taxonomy-v1",
                mapping_revision="pilot-observable-mapping-v1",
                classification_policy_revision=decision.policy_revision,
            )
        )
        track_metadata.append(
            {
                "track_id": track_id,
                "raw_class_id": raw_class_id,
                "raw_class_name": raw_class_name,
                "provisional_class": provisional,
                "confidence": round(confidence, 4),
                "review_state": review_state,
                "observation_count": len(payload["observations"]),
                "engineering_class": decision.engineering_class,
                "classification_status": decision.classification_status,
                "classification_reason": decision.classification_reason,
                "classification_evidence": compact,
                "warnings": list(decision.warnings),
            }
        )
    counting_tracks = eligible_real_tracks_for_counting(
        real_tracks,
        minimum_observations=runtime_config.minimum_track_observations,
        minimum_duration_ms=runtime_config.minimum_track_duration_ms,
    )
    track_set = SyntheticTrackSet(
        schema_version=REAL_TRACK_SCHEMA_VERSION,
        source_fingerprint=source_fingerprint,
        fixture_id="real-video",
        tracks=counting_tracks,
        scene_revision=scene.scene_revision,
        taxonomy_version="observable-class-v1",
    )
    counting_started = time.perf_counter()
    checkpoint(renew=True)
    counting_result = execute_synthetic_counting(
        run_id,
        track_set,
        scene,
        time_contract,
        tolerances=CountingTolerances(
            point_on_line=runtime_config.crossing_tolerance,
            duplicate_crossing_time_ms=runtime_config.duplicate_crossing_cooldown_ms,
            spatial_hysteresis=runtime_config.crossing_hysteresis,
            minimum_side_distance=runtime_config.minimum_movement_distance,
            minimum_side_stability_frames=runtime_config.minimum_side_stability_frames,
        ),
        time_configured=source_time_configured,
        classification_policy=classification_policy,
    )
    checkpoint(renew=True)
    warm_inference_times = inference_times[1:]
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    provenance = _runtime_provenance(root, runtime_config)
    runtime_identity = evaluate_runtime_provenance(
        config=runtime_config,
        configured_runtime_provenance=configured_runtime_provenance,
        actual_runtime_provenance=provenance,
        legacy_configured_runtime_hash=str(configured_runtime_configuration_hash or "") or None,
    )
    stats = {
        "decoded_frames": decoded_frames,
        "processed_frames": processed_frames,
        "detection_count": detection_count,
        "ignored_unsupported_detections": 0,
        "unsupported_raw_class_detections": unsupported_raw_class_detections,
        "ignored_class_allowlist_detections": detector.filtered_class_allowlist_count,
        "track_count": len(real_tracks),
        "counting_track_count": len(counting_tracks),
        "short_tracks_excluded_from_counting": len(real_tracks) - len(counting_tracks),
        "event_count": len(counting_result.events),
        "exclusion_count": len(counting_result.exclusions),
        "model_load_ms": detector.load_ms,
        "decode_startup_ms": decode_startup_ms,
        "first_inference_ms": first_inference_ms,
        "warm_inference_mean_ms": round(sum(warm_inference_times) / len(warm_inference_times), 3) if warm_inference_times else None,
        "warm_inference_min_ms": min(warm_inference_times) if warm_inference_times else None,
        "warm_inference_max_ms": max(warm_inference_times) if warm_inference_times else None,
        "tracker_update_mean_ms": round(sum(tracker_times) / len(tracker_times), 3) if tracker_times else None,
        "crossing_engine_ms": round((time.perf_counter() - counting_started) * 1000, 3),
        "elapsed_ms": elapsed_ms,
        "processed_fps": round(processed_frames / (elapsed_ms / 1000), 3) if elapsed_ms > 0 else None,
        "processing_ratio": (
            round(((time_contract.analysis_end_pts_ms - time_contract.analysis_start_pts_ms) / 1000) / (elapsed_ms / 1000), 3)
            if elapsed_ms > 0
            else None
        ),
        "real_time_status": "NOT_ESTABLISHED",
        "gpu_memory_mb": _gpu_memory_mb(runtime_config.resolved_device),
        "device": runtime_config.resolved_device,
        "source_duration_ms": source_duration_ms,
        "source_time_configured": source_time_configured,
        "pts_semantics": "source_relative_from_first_video_pts",
        "anchor": runtime_config.anchor,
        "sampling": runtime_config.sampling,
        "configuration_hash": configuration_hash,
        "request_provenance_hash": request_provenance_hash,
        **runtime_identity,
        "configured_runtime_provenance": dict(configured_runtime_provenance or {}),
        "actual_runtime_provenance": provenance,
        **provenance,
    }
    return RealInferenceResult(counting_result, runtime_config, stats, tuple(track_metadata))
