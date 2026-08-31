from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apps.backend.app.domain import TimeContract
from apps.backend.app.synthetic_counting import (
    CountingLine,
    CountingScene,
    Observation,
    Point,
    SyntheticTrack,
    SyntheticTrackSet,
    execute_synthetic_counting,
)
from tools.benchmark.adapters import StubDetector, StubTracker, TrackedDetection
from tools.benchmark.contracts import (
    NORMALIZED_TRACK_SCHEMA_VERSION,
    canonical_hash,
    load_json,
    parse_config,
    parse_manifest,
)


def representative_point(detection: TrackedDetection, mode: str) -> Point:
    box = detection.bbox
    if mode == "centroid":
        return Point(box.x + box.width / 2, box.y + box.height / 2)
    return Point(box.x + box.width / 2, box.y + box.height)


def normalized_tracks(detections: list[TrackedDetection], representative_point_mode: str) -> list[SyntheticTrack]:
    by_track: dict[str, list[Observation]] = {}
    labels: dict[str, str] = {}
    confidences: dict[str, list[float]] = {}
    for detection in detections:
        by_track.setdefault(detection.track_id, []).append(
            Observation(
                timestamp_ms=detection.timestamp_ms,
                position=representative_point(detection, representative_point_mode),
                sample_id=f"{detection.track_id}_{detection.timestamp_ms}",
                bbox=detection.bbox,
            )
        )
        labels[detection.track_id] = detection.native_class
        confidences.setdefault(detection.track_id, []).append(detection.confidence)
    tracks: list[SyntheticTrack] = []
    for track_id, observations in sorted(by_track.items()):
        confidence_values = confidences[track_id]
        tracks.append(
            SyntheticTrack(
                track_id=track_id,
                observations=tuple(sorted(observations, key=lambda item: item.timestamp_ms)),
                synthetic_class=labels[track_id],
                confidence=sum(confidence_values) / len(confidence_values),
                provenance="synthetic",
            )
        )
    return tracks


def compare_counts(actual: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    expected_total = int(expected.get("grand_total", 0))
    actual_total = int(actual.get("grand_total", 0))
    absolute_error = abs(actual_total - expected_total)
    percent_error = None if expected_total == 0 else absolute_error / expected_total
    return {
        "expected_total": expected_total,
        "actual_total": actual_total,
        "absolute_error": absolute_error,
        "percent_error": percent_error,
        "matches": actual_total == expected_total,
    }


def fixture_scene(source_fingerprint: str, scene_revision: str) -> CountingScene:
    return CountingScene(
        scene_revision=scene_revision,
        source_fingerprint=source_fingerprint,
        counting_lines=(
            CountingLine("line_main", "Main", Point(0.2, 0.5), Point(0.8, 0.5), "bidirectional"),
        ),
    )


def run_benchmark(manifest_path: Path, config_path: Path) -> dict[str, Any]:
    manifest_payload = load_json(manifest_path)
    config_payload = load_json(config_path)
    manifest = parse_manifest(manifest_payload)
    config = parse_config(config_payload)
    if config.detector != "stub-fixture-detector" or config.tracker != "stub-deterministic-tracker":
        raise ValueError("only the deterministic stub benchmark path is available without optional AI dependencies")

    detector = StubDetector()
    tracker = StubTracker()
    tracked = tracker.track(detector.detect())
    tracks = normalized_tracks(tracked, config.representative_point)
    track_set = SyntheticTrackSet(
        schema_version="synthetic-tracks-v1",
        source_fingerprint=manifest.media_fingerprint,
        fixture_id=manifest.clip_id,
        scene_revision=manifest.scene_revision,
        tracks=tuple(tracks),
        taxonomy_version="tims-provisional-v0.1",
    )
    contract = TimeContract(
        source_started_at=datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc),
        timezone_name="Asia/Bangkok",
        analysis_start_pts_ms=manifest.clip_start_ms,
        analysis_end_pts_ms=manifest.clip_end_ms,
        interval_origin_pts_ms=manifest.clip_start_ms,
    )
    result = execute_synthetic_counting(
        f"benchmark_{canonical_hash({'manifest': manifest_payload, 'config': config_payload})[:12]}",
        track_set,
        fixture_scene(manifest.media_fingerprint, manifest.scene_revision),
        contract,
    )
    normalized_payload = {
        "schema_version": NORMALIZED_TRACK_SCHEMA_VERSION,
        "tracks": [
            {
                "track_id": track.track_id,
                "class": track.synthetic_class,
                "observations": [
                    {"timestamp_ms": obs.timestamp_ms, "x": obs.position.x, "y": obs.position.y}
                    for obs in track.observations
                ],
            }
            for track in tracks
        ],
    }
    return {
        "benchmark_id": manifest.benchmark_id,
        "clip_id": manifest.clip_id,
        "engineering_evaluation_only": True,
        "not_certified_traffic_survey": True,
        "detector": detector.name,
        "tracker": tracker.name,
        "config_hash": canonical_hash(config_payload),
        "manifest_hash": canonical_hash(manifest_payload),
        "normalized_tracks": normalized_payload,
        "counting_engine": result.engine_version,
        "events": [
            {
                "event_id": event.event_id,
                "track_id": event.track_id,
                "line_id": event.line_id,
                "direction": event.direction.value,
                "crossing_timestamp_ms": event.crossing_timestamp_ms,
                "class": event.synthetic_class,
            }
            for event in result.events
        ],
        "aggregates": result.aggregates,
        "count_metrics": compare_counts(result.aggregates, manifest.ground_truth),
        "warnings": list(result.warnings),
        "exclusions": [asdict(item) for item in result.exclusions],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an offline benchmark fixture.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = run_benchmark(args.manifest, args.config)
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
