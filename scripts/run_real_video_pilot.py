from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.backend.app.main import create_app  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local YOLO11n + ByteTrack real-video pilot.")
    parser.add_argument(
        "--media",
        type=Path,
        default=ROOT / ".local-data" / "ai-artifacts" / "smoke" / "smoke_yolo_bus.mp4",
    )
    parser.add_argument("--keep-data", action="store_true")
    args = parser.parse_args()
    media_path = args.media.resolve()
    if not media_path.is_file() or media_path.is_symlink():
        print(json.dumps({"status": "blocked", "error_code": "media_missing", "path": str(media_path)}))
        return 2
    temp_dir = Path(tempfile.mkdtemp(prefix="tva-real-pilot-", dir=ROOT / ".local-data"))
    db_path = temp_dir / "pilot.sqlite3"
    previous_data_dir = os.environ.get("TVA_LOCAL_DATA_DIR")
    os.environ["TVA_LOCAL_DATA_DIR"] = str(temp_dir)
    try:
        with TestClient(create_app(str(db_path))) as client:
            project = client.post(
                "/api/v1/projects",
                json={"name": "Real video pilot", "location": "local", "study_type": "intersection", "language": "en"},
            )
            project.raise_for_status()
            project_id = project.json()["id"]
            with media_path.open("rb") as handle:
                upload = client.post(
                    f"/api/v1/projects/{project_id}/sources/upload",
                    files={"file": (media_path.name, handle, "video/mp4")},
                )
            upload.raise_for_status()
            source = upload.json()
            duration_ms = int(source.get("duration_ms") or 2000)
            time_response = client.put(
                f"/api/v1/projects/{project_id}/time-configuration",
                json={
                    "source_started_at": "2026-01-01T00:00:00+00:00",
                    "timezone_name": "UTC",
                    "analysis_start_pts_ms": 0,
                    "analysis_end_pts_ms": max(1, duration_ms),
                    "source_offset_ms": 0,
                },
            )
            if time_response.status_code >= 400:
                print(json.dumps({"status": "blocked", "stage": "time_configuration", "detail": time_response.json(), "source": source}))
            time_response.raise_for_status()
            scene = client.post(
                "/api/v1/scenes",
                json={"project_id": project_id, "template": "intersection"},
            )
            scene.raise_for_status()
            scene_payload = scene.json()
            job_response = client.post(
                f"/api/v1/projects/{project_id}/processing-jobs",
                json={
                    "mode": "REAL_VIDEO",
                    "configuration": {"device_mode": "AUTO", "frame_stride": 1},
                    "expected_scene_version": scene_payload["version"],
                    "idempotency_key": "real-video-pilot",
                    "auto_start": False,
                },
            )
            job_response.raise_for_status()
            job = job_response.json()
        worker = subprocess.run(
            [
                sys.executable,
                "-m",
                "apps.worker.processing_worker",
                "--db",
                str(db_path),
                "--worker-id",
                "real-pilot-worker",
                "--once",
            ],
            cwd=ROOT,
            env={**os.environ, "TVA_LOCAL_DATA_DIR": str(temp_dir)},
            text=True,
            capture_output=True,
            check=False,
            timeout=600,
        )
        with TestClient(create_app(str(db_path))) as client:
            final_job = client.get(f"/api/v1/processing-jobs/{job['id']}")
            final_job.raise_for_status()
            final_events = client.get(f"/api/v1/runs/{job['id']}/crossing-events")
            final_events.raise_for_status()
            with sqlite3.connect(db_path) as connection:
                track_summaries = [
                    json.loads(row[0])
                    for row in connection.execute(
                        "SELECT summary_json FROM track_summaries WHERE run_id = ? ORDER BY track_id",
                        (job["id"],),
                    )
                ]
            payload = {
                "status": "passed" if final_job.json().get("job_state") == "COMPLETED" else "failed",
                "job": final_job.json(),
                "event_count": len(final_events.json()),
                "first_event": final_events.json()[0] if final_events.json() else None,
                "track_summaries": track_summaries,
                "worker_stdout": worker.stdout,
                "worker_stderr": worker.stderr,
                "worker_returncode": worker.returncode,
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0 if payload["status"] == "passed" and worker.returncode == 0 else 1
    finally:
        if previous_data_dir is None:
            os.environ.pop("TVA_LOCAL_DATA_DIR", None)
        else:
            os.environ["TVA_LOCAL_DATA_DIR"] = previous_data_dir
        if not args.keep_data:
            import shutil

            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
