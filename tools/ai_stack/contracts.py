from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

BoxFormat = Literal["xyxy_normalized", "xywh_normalized"]


@dataclass(frozen=True)
class AdapterManifest:
    schema_version: str
    implementation_id: str
    model_family: str
    package_name: str
    package_version: str
    model_version: str
    weight_sha256: str | None
    license_classification: str
    runtime_provider: str
    device: str
    configuration: dict[str, Any]
    warnings: tuple[str, ...] = ()
    supported_classes: tuple[str, ...] = ()
    unsupported_distinctions: tuple[str, ...] = ()


@dataclass(frozen=True)
class FrameRef:
    frame_index: int
    pts_ms: int
    width: int
    height: int
    source_fingerprint: str


@dataclass(frozen=True)
class DetectorBox:
    x1: float
    y1: float
    x2: float
    y2: float
    native_class: str
    confidence: float
    source: str

    def normalized_xywh(self) -> tuple[float, float, float, float]:
        return (self.x1, self.y1, self.x2 - self.x1, self.y2 - self.y1)


@dataclass(frozen=True)
class DetectionFrame:
    frame: FrameRef
    detections: tuple[DetectorBox, ...]
    inference_time_ms: float
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TrackObservation:
    pts_ms: int
    frame_index: int
    bbox_xywh: tuple[float, float, float, float]
    representative_point: tuple[float, float]
    native_class: str
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedTrack:
    schema_version: str
    track_id: str
    source_fingerprint: str
    detector_manifest: AdapterManifest
    tracker_manifest: AdapterManifest
    observations: tuple[TrackObservation, ...]
    track_class: str
    track_confidence: float
    review_state: Literal["accepted", "unknown", "ambiguous", "needs_review"]
    provenance: dict[str, Any]


class DetectorAdapter(Protocol):
    manifest: AdapterManifest

    def detect(self, frames: tuple[FrameRef, ...]) -> tuple[DetectionFrame, ...]:
        ...


class TrackerAdapter(Protocol):
    manifest: AdapterManifest

    def track(self, detections: tuple[DetectionFrame, ...]) -> tuple[NormalizedTrack, ...]:
        ...


def representative_point(
    bbox_xywh: tuple[float, float, float, float], mode: Literal["bottom_center", "centroid"]
) -> tuple[float, float]:
    x, y, width, height = bbox_xywh
    if mode == "centroid":
        return (x + width / 2, y + height / 2)
    return (x + width / 2, y + height)


def validate_normalized_box(box: DetectorBox) -> tuple[str, ...]:
    errors: list[str] = []
    if not 0 <= box.x1 <= 1 or not 0 <= box.x2 <= 1 or not 0 <= box.y1 <= 1 or not 0 <= box.y2 <= 1:
        errors.append("box_coordinates_out_of_normalized_range")
    if box.x2 <= box.x1 or box.y2 <= box.y1:
        errors.append("box_has_non_positive_area")
    if not 0 <= box.confidence <= 1:
        errors.append("confidence_out_of_range")
    return tuple(errors)

