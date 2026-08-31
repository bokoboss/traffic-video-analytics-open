from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.backend.app.main import create_app  # noqa: E402
from apps.backend.app.synthetic_counting import (  # noqa: E402
    CountingLine,
    CountingScene,
    Point,
    SyntheticTrack,
    SyntheticTrackSet,
    Observation,
    execute_synthetic_counting,
)
from apps.backend.app.domain import TimeContract  # noqa: E402
from scripts.runtime_readiness import readiness  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def measure(name: str, func: Callable[[], Any], samples: int = 5) -> dict[str, Any]:
    timings: list[float] = []
    status = "passed"
    error: str | None = None
    last_result: Any = None
    for _ in range(samples):
        started = time.perf_counter()
        try:
            last_result = func()
        except Exception as exc:  # pragma: no cover - reported by harness
            status = "failed"
            error = type(exc).__name__
            timings.append((time.perf_counter() - started) * 1000)
            break
        timings.append((time.perf_counter() - started) * 1000)
    return {
        "operation": name,
        "status": status,
        "samples": len(timings),
        "mean_ms": round(statistics.fmean(timings), 3) if timings else None,
        "median_ms": round(statistics.median(timings), 3) if timings else None,
        "min_ms": round(min(timings), 3) if timings else None,
        "max_ms": round(max(timings), 3) if timings else None,
        "error": error,
        "result_size": _result_size(last_result),
    }


def unavailable(name: str, reason: str) -> dict[str, Any]:
    return {
        "operation": name,
        "status": "unavailable",
        "samples": 0,
        "mean_ms": None,
        "median_ms": None,
        "min_ms": None,
        "max_ms": None,
        "error": reason,
        "result_size": None,
    }


def _result_size(value: Any) -> int | None:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(json.dumps(value, sort_keys=True, default=str))
    return None


def seed_project(client: TestClient) -> dict[str, Any]:
    project = client.post(
        "/api/v1/projects",
        json={"name": "5.3A synthetic benchmark", "location": "Bangkok", "study_type": "intersection", "language": "en"},
    ).json()
    client.post(
        "/api/v1/sources",
        json={
            "project_id": project["id"],
            "file_name": "benchmark-mock.mp4",
            "fingerprint_sha256": "benchmark-safe-fixture",
            "source_started_at": datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc).isoformat(),
            "timezone_name": "Asia/Bangkok",
            "analysis_start_pts_ms": 0,
            "analysis_end_pts_ms": 3_600_000,
        },
    ).raise_for_status()
    service = client.app.state.foundation_service
    source = service.connection.execute(
        "SELECT * FROM video_sources WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
        (project["id"],),
    ).fetchone()
    frame_id = "frm_benchmark"
    service.connection.execute(
        """
        INSERT INTO reference_frames(
          id, project_id, source_id, source_fingerprint_sha256, requested_pts_ms,
          resolved_pts_ms, decoder_seek_pts_ms, first_decoded_pts_ms, selected_pts_ms,
          extraction_mode, preview_path, preview_width, preview_height, source_width,
          source_height, rotation_degrees, warnings_json, created_at
        ) VALUES (?, ?, ?, ?, 0, 0, 0, 0, 0, 'synthetic', 'benchmark.png', 640, 360, 640, 360, 0, '[]', ?)
        """,
        (frame_id, project["id"], source["id"], source["fingerprint_sha256"], now_iso()),
    )
    service.connection.commit()
    geometry = {
        "schema_version": "scene-geometry-v2",
        "coordinate_system": {"origin": "top_left", "units": "normalized_source_display"},
        "counting_lines": [
            {
                "id": "line_main",
                "name": "Benchmark line",
                "active": True,
                "start": {"x": 0.2, "y": 0.5},
                "end": {"x": 0.8, "y": 0.5},
                "direction_mode": "BIDIRECTIONAL",
                "side_a_name": "Side A",
                "side_b_name": "Side B",
                "approach_name": "",
                "movement_name": "through",
                "analyst_note": "",
            }
        ],
        "rois": [],
    }
    client.put(
        f"/api/v1/projects/{project['id']}/scene-configuration",
        json={"reference_frame_id": frame_id, "geometry": geometry},
    ).raise_for_status()
    return project


def seed_large_run(connection: sqlite3.Connection, project_id: str, event_count: int) -> str:
    source_id = connection.execute("SELECT id FROM video_sources WHERE project_id = ?", (project_id,)).fetchone()["id"]
    scene_id = connection.execute("SELECT id FROM scene_versions WHERE project_id = ?", (project_id,)).fetchone()["id"]
    run_id = "run_benchmark_large"
    connection.execute(
        """
        INSERT INTO analysis_runs(id, project_id, source_id, scene_version_id, state, progress_percent, result_version, created_at, completed_at)
        VALUES (?, ?, ?, ?, 'processing_complete', 100, 'benchmark-large-v1', ?, ?)
        """,
        (run_id, project_id, source_id, scene_id, now_iso(), now_iso()),
    )
    rows = [
        (
            f"evt_bench_{index:06d}",
            run_id,
            f"technical_{index:06d}",
            index * 100,
            f"track_{index:06d}",
            "line_main",
            "vehicle",
            "synthetic_vehicle",
            "A_TO_B" if index % 2 == 0 else "B_TO_A",
            1.0,
            "ok",
            now_iso(),
        )
        for index in range(event_count)
    ]
    connection.executemany(
        """
        INSERT INTO auto_count_events(
          id, run_id, technical_key, pts_ms, track_id, rule_id, object_domain,
          classification, movement, confidence, qc_state, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    connection.execute(
        """
        INSERT INTO aggregate_snapshots(id, run_id, result_version, fifteen_minute_counts_json, hourly_total, phf, stale, created_at)
        VALUES ('agg_benchmark_large', ?, 'benchmark-large-v1', ?, ?, 1.0, 0, ?)
        """,
        (run_id, json.dumps([event_count // 4] * 4), event_count, now_iso()),
    )
    connection.commit()
    return run_id


def synthetic_crossing_throughput(track_count: int) -> dict[str, Any]:
    line = CountingLine("line_main", "Benchmark line", Point(0.2, 0.5), Point(0.8, 0.5), "BIDIRECTIONAL")
    scene = CountingScene("scene_benchmark", "benchmark-safe-fixture", (line,), ())
    tracks = tuple(
        SyntheticTrack(
            track_id=f"track_{index}",
            observations=(
                Observation(index * 1000, Point(0.5, 0.6), "before"),
                Observation(index * 1000 + 500, Point(0.5, 0.4), "after"),
            ),
            synthetic_class="synthetic_vehicle",
        )
        for index in range(track_count)
    )
    track_set = SyntheticTrackSet("synthetic-tracks-v1", "benchmark-safe-fixture", "benchmark", tracks, "scene_benchmark")
    contract = TimeContract(datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc), "Asia/Bangkok", 0, max(track_count * 1000, 1), 0)
    result = execute_synthetic_counting("run_throughput", track_set, scene, contract)
    return {"events": len(result.events), "exclusions": len(result.exclusions)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Milestone 5.3A local performance baseline harness.")
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--events", type=int, default=5000)
    parser.add_argument("--tracks", type=int, default=1000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    app = create_app()
    client = TestClient(app)
    service = app.state.foundation_service
    project = seed_project(client)
    run_id = seed_large_run(service.connection, project["id"], args.events)
    runtime = readiness()

    measurements = [
        measure("api.health", lambda: client.get("/api/v1/health").json(), args.samples),
        measure("api.media_runtime", lambda: client.get("/api/v1/media-runtime").json(), args.samples),
        measure("api.project_list", lambda: client.get("/api/v1/projects").json(), args.samples),
        measure("api.project_open", lambda: client.get(f"/api/v1/projects/{project['id']}").json(), args.samples),
        measure("api.scene_load", lambda: client.get(f"/api/v1/projects/{project['id']}/scene-configuration").json(), args.samples),
        measure(
            "api.scene_validate",
            lambda: client.post(
                f"/api/v1/projects/{project['id']}/scene-configuration/validate",
                json={
                    "reference_frame_id": "frm_benchmark",
                    "geometry": json.loads(client.get(f"/api/v1/projects/{project['id']}/scene-configuration").json()["geometry_json"]),
                },
            ).json(),
            args.samples,
        ),
        measure("api.events_page_100", lambda: client.get(f"/api/v1/runs/{run_id}/events?limit=100&offset=0").json(), args.samples),
        measure("api.events_page_1000", lambda: client.get(f"/api/v1/runs/{run_id}/events?limit=1000&offset=0").json(), args.samples),
        measure("api.aggregates", lambda: client.get(f"/api/v1/runs/{run_id}/aggregates").json(), args.samples),
        measure("api.reviewed_totals", lambda: client.get(f"/api/v1/runs/{run_id}/reviewed-totals").json(), args.samples),
        measure("domain.synthetic_crossing_throughput", lambda: synthetic_crossing_throughput(args.tracks), args.samples),
        unavailable("api.reference_frame_ffmpeg", "requires approved local FFmpeg and safe media fixture"),
        unavailable("browser.workflow_timing", "run Playwright/browser smoke separately; this script is API/domain only"),
        unavailable("worker.model_inference", "no committed model weights or approved benchmark clips"),
    ]
    payload = {
        "schema_version": "milestone-5.3a-benchmark-v1",
        "generated_at": now_iso(),
        "environment": {
            "os": platform.platform(),
            "python": platform.python_version(),
            "cpu_architecture": platform.machine(),
            "node": runtime.node,
            "pnpm": runtime.pnpm,
            "gpu_available": runtime.gpu.get("available"),
            "ffmpeg_available": runtime.ffmpeg.get("available") if isinstance(runtime.ffmpeg, dict) else None,
            "ffprobe_available": runtime.ffprobe.get("available") if isinstance(runtime.ffprobe, dict) else None,
        },
        "parameters": {"samples": args.samples, "events": args.events, "tracks": args.tracks},
        "measurements": measurements,
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if all(item["status"] != "failed" for item in measurements) else 2


if __name__ == "__main__":
    raise SystemExit(main())
