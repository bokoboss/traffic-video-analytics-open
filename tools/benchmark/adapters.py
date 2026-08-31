from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from apps.backend.app.synthetic_counting import BoundingBox


@dataclass(frozen=True)
class Detection:
    timestamp_ms: int
    bbox: BoundingBox
    native_class: str
    confidence: float


@dataclass(frozen=True)
class TrackedDetection(Detection):
    track_id: str


class DetectorAdapter(Protocol):
    name: str

    def detect(self) -> list[Detection]:
        ...


class TrackerAdapter(Protocol):
    name: str

    def track(self, detections: list[Detection]) -> list[TrackedDetection]:
        ...


class StubDetector:
    name = "stub-fixture-detector"

    def detect(self) -> list[Detection]:
        return [
            Detection(0, BoundingBox(0.45, 0.30, 0.10, 0.10), "car", 0.91),
            Detection(1_000, BoundingBox(0.45, 0.55, 0.10, 0.10), "car", 0.92),
            Detection(0, BoundingBox(0.62, 0.62, 0.08, 0.08), "motorcycle", 0.77),
            Detection(1_000, BoundingBox(0.62, 0.35, 0.08, 0.08), "motorcycle", 0.74),
        ]


class StubTracker:
    name = "stub-deterministic-tracker"

    def track(self, detections: list[Detection]) -> list[TrackedDetection]:
        tracked: list[TrackedDetection] = []
        counters: dict[str, str] = {}
        for detection in detections:
            if detection.native_class not in counters:
                counters[detection.native_class] = f"track_{len(counters) + 1}"
            tracked.append(
                TrackedDetection(
                    timestamp_ms=detection.timestamp_ms,
                    bbox=detection.bbox,
                    native_class=detection.native_class,
                    confidence=detection.confidence,
                    track_id=counters[detection.native_class],
                )
            )
        return tracked
