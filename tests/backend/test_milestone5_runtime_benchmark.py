from __future__ import annotations

from pathlib import Path

import pytest

from scripts.runtime_readiness import readiness
from tools.benchmark.contracts import BenchmarkValidationError, parse_config, parse_manifest
from tools.benchmark.harness import representative_point, run_benchmark
from tools.benchmark.adapters import TrackedDetection
from apps.backend.app.synthetic_counting import BoundingBox


ROOT = Path(__file__).resolve().parents[2]


def test_runtime_readiness_reports_required_fields() -> None:
    status = readiness()
    assert status.python
    assert status.backend_import_ready
    assert status.cpu_architecture
    assert status.support_level in {
        "app_review_ready",
        "media_runtime_ready",
        "unsupported_or_incomplete",
    }


def test_benchmark_manifest_validation_rejects_bad_window() -> None:
    with pytest.raises(BenchmarkValidationError):
        parse_manifest(
            {
                "manifest_version": "benchmark-manifest-v1",
                "benchmark_id": "bad",
                "clip_id": "bad",
                "media_fingerprint": "src",
                "usage_status": "synthetic_fixture",
                "clip_start_ms": 10,
                "clip_end_ms": 10,
                "scene_revision": "scene",
                "ground_truth": {},
            }
        )


def test_benchmark_config_validation_rejects_bad_threshold() -> None:
    with pytest.raises(BenchmarkValidationError):
        parse_config(
            {
                "config_version": "benchmark-config-v1",
                "detector": "stub-fixture-detector",
                "tracker": "stub-deterministic-tracker",
                "confidence_threshold": 2,
            }
        )


def test_representative_point_bottom_center() -> None:
    detection = TrackedDetection(
        timestamp_ms=0,
        bbox=BoundingBox(0.1, 0.2, 0.4, 0.6),
        native_class="car",
        confidence=0.9,
        track_id="t1",
    )
    point = representative_point(detection, "bottom_center")
    assert point.x == pytest.approx(0.3)
    assert point.y == pytest.approx(0.8)


def test_benchmark_smoke_reconciles_with_counting_engine() -> None:
    result = run_benchmark(
        ROOT / "tools/benchmark/fixtures/stub_manifest.json",
        ROOT / "tools/benchmark/fixtures/stub_config.json",
    )
    assert result["engineering_evaluation_only"] is True
    assert result["not_certified_traffic_survey"] is True
    assert result["counting_engine"] == "synthetic-crossing-engine-v1"
    assert result["count_metrics"]["matches"] is True
    assert len(result["events"]) == 2
