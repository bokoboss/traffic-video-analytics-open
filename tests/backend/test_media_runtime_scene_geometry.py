from __future__ import annotations

from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient

from apps.backend.app.main import create_app
import apps.backend.app.media as media_module
from apps.backend.app.media import ReferenceFrameResult
import apps.backend.app.services as services_module


def _valid_mp4_bytes() -> bytes:
    return b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 32


def _valid_png_header(width: int = 640, height: int = 360) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _stub_inspection(monkeypatch: pytest.MonkeyPatch, warnings: list[str] | None = None) -> None:
    monkeypatch.setattr(
        services_module,
        "inspect_media",
        lambda path: media_module.InspectionResult(
            metadata={
                "container_format": "mov,mp4,m4a,3gp,3g2,mj2",
                "video_codec": "h264",
                "duration_ms": 1000,
                "width": 640,
                "height": 360,
                "sample_aspect_ratio": "1:1",
                "display_aspect_ratio": "16:9",
                "nominal_frame_rate": "10/1",
                "average_frame_rate": "10/1",
                "stream_time_base": "1/1000",
                "start_pts": 0,
                "first_video_pts": 0,
                "frame_count": 10,
                "variable_frame_rate": 0,
                "rotation_degrees": 0,
                "metadata_creation_time": None,
                "audio_present": 0,
            },
            warnings=warnings or [],
            tool_version="ffprobe test",
        ),
    )


def _project_with_upload(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    _stub_inspection(monkeypatch)
    project = client.post(
        "/api/v1/projects",
        json={"name": "Scene", "location": "Bangkok", "study_type": "intersection", "language": "en"},
    ).json()
    response = client.post(
        f"/api/v1/projects/{project['id']}/sources/upload",
        files={"file": ("synthetic source.mp4", _valid_mp4_bytes(), "video/mp4")},
    )
    response.raise_for_status()
    return project


def _geometry(line_name: str = "Main crossing", note: str = "") -> dict:
    return {
        "schema_version": "scene-geometry-v2",
        "coordinate_system": {"origin": "top_left", "units": "normalized_source_display"},
        "counting_lines": [
            {
                "id": "line_main",
                "name": line_name,
                "active": True,
                "start": {"x": 0.2, "y": 0.7},
                "end": {"x": 0.8, "y": 0.25},
                "direction_mode": "A_TO_B",
                "side_a_name": "Inbound",
                "side_b_name": "Outbound",
                "approach_name": "Inbound",
                "movement_name": "Through",
                "analyst_note": note,
            }
        ],
        "rois": [
            {
                "id": "roi_approach",
                "name": "Working area",
                "active": True,
                "vertices": [{"x": 0.1, "y": 0.1}, {"x": 0.9, "y": 0.1}, {"x": 0.8, "y": 0.9}, {"x": 0.15, "y": 0.8}],
            }
        ],
    }


def test_media_runtime_status_is_controlled() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/media-runtime")
    response.raise_for_status()
    payload = response.json()
    assert payload["ffmpeg"]["name"] == "ffmpeg"
    assert payload["ffprobe"]["name"] == "ffprobe"
    assert payload["readiness_state"] in {
        "media_runtime_missing",
        "media_runtime_partially_available",
        "media_runtime_ready",
    }
    assert "reference_frame_extraction_available" in payload


def test_media_runtime_resolution_prefers_configured_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "Configured Runtime" / "bin"
    configured.mkdir(parents=True)
    (configured / "ffmpeg.exe").write_text("", encoding="utf-8")
    (configured / "ffprobe.exe").write_text("", encoding="utf-8")
    monkeypatch.setenv("TVA_FFMPEG_DIR", str(configured.parent))
    monkeypatch.setattr(media_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        media_module.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout=f"{Path(command[0]).stem} version 7.1.1-full_build\n"),
    )

    status = media_module.media_runtime_status()

    assert status.readiness_state == "media_runtime_ready"
    assert status.ffmpeg.path_source == "TVA_FFMPEG_DIR"
    assert status.ffprobe.path_source == "TVA_FFMPEG_DIR"
    assert "Configured Runtime" in (status.ffmpeg.executable or "")


def test_media_runtime_resolution_uses_repository_local_before_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo with spaces & ampersand"
    local_bin = repo / ".local-tools" / "ffmpeg" / "bin"
    local_bin.mkdir(parents=True)
    (local_bin / "ffmpeg.exe").write_text("", encoding="utf-8")
    (local_bin / "ffprobe.exe").write_text("", encoding="utf-8")
    monkeypatch.delenv("TVA_FFMPEG_DIR", raising=False)
    monkeypatch.setattr(media_module, "ROOT", repo)
    monkeypatch.setattr(media_module.shutil, "which", lambda name: f"C:/system/{name}.exe")
    monkeypatch.setattr(
        media_module.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout=f"{Path(command[0]).stem} version 7.1.1-full_build\n"),
    )

    status = media_module.media_runtime_status()

    assert status.readiness_state == "media_runtime_ready"
    assert status.ffmpeg.path_source == "repository_local"
    assert status.ffprobe.path_source == "repository_local"
    assert "& ampersand" in (status.ffmpeg.executable or "")


def test_media_runtime_reports_partial_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configured = tmp_path / "ffmpeg" / "bin"
    configured.mkdir(parents=True)
    (configured / "ffmpeg.exe").write_text("", encoding="utf-8")
    monkeypatch.setenv("TVA_FFMPEG_DIR", str(configured.parent))
    monkeypatch.setattr(media_module, "ROOT", tmp_path / "empty-repo")
    monkeypatch.setattr(media_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        media_module.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="ffmpeg version 7.1.1-full_build\n"),
    )

    status = media_module.media_runtime_status()

    assert status.readiness_state == "media_runtime_partially_available"
    assert status.ffmpeg.available is True
    assert status.ffprobe.available is False
    assert "ffprobe_missing" in status.warnings


def test_media_runtime_reports_version_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configured = tmp_path / "ffmpeg" / "bin"
    configured.mkdir(parents=True)
    (configured / "ffmpeg.exe").write_text("", encoding="utf-8")
    (configured / "ffprobe.exe").write_text("", encoding="utf-8")
    monkeypatch.setenv("TVA_FFMPEG_DIR", str(configured.parent))
    monkeypatch.setattr(media_module.shutil, "which", lambda name: None)

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        stem = Path(command[0]).stem
        version = "7.1.1-full_build" if stem == "ffmpeg" else "6.1.2-full_build"
        return subprocess.CompletedProcess(command, 0, stdout=f"{stem} version {version}\n")

    monkeypatch.setattr(media_module.subprocess, "run", fake_run)

    status = media_module.media_runtime_status()

    assert status.readiness_state == "media_runtime_partially_available"
    assert status.reference_frame_extraction_available is False
    assert "ffmpeg_ffprobe_version_mismatch" in status.warnings


def test_reference_frame_extraction_cache_and_project_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TVA_LOCAL_DATA_DIR", str(tmp_path / "local-data"))
    extraction_count = 0

    def fake_extract(source_path: Path, output_path: Path, requested_pts_ms: int) -> ReferenceFrameResult:
        nonlocal extraction_count
        extraction_count += 1
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(_valid_png_header())
        return ReferenceFrameResult(
            preview_path=output_path,
            requested_pts_ms=requested_pts_ms,
            resolved_pts_ms=requested_pts_ms,
            decoder_seek_pts_ms=requested_pts_ms,
            first_decoded_pts_ms=requested_pts_ms,
            selected_pts_ms=requested_pts_ms,
            preview_width=640,
            preview_height=360,
            source_width=640,
            source_height=360,
            rotation_degrees=0,
            warnings=[],
        )

    monkeypatch.setattr(services_module, "extract_reference_frame", fake_extract)
    client = TestClient(create_app())
    project = _project_with_upload(client, tmp_path, monkeypatch)
    first = client.post(f"/api/v1/projects/{project['id']}/reference-frames", json={"mode": "beginning"}).json()
    second = client.post(f"/api/v1/projects/{project['id']}/reference-frames", json={"mode": "beginning"}).json()
    assert first["id"] == second["id"]
    assert extraction_count == 1
    regenerated = client.post(
        f"/api/v1/projects/{project['id']}/reference-frames",
        json={"mode": "beginning", "force_regenerate": True},
    ).json()
    assert regenerated["id"] == first["id"]
    assert extraction_count == 2
    assert client.get(f"/api/v1/projects/{project['id']}/reference-frames/{first['id']}/preview").status_code == 200

    other = client.post(
        "/api/v1/projects",
        json={"name": "Other", "location": "Bangkok", "study_type": "intersection", "language": "en"},
    ).json()
    assert client.get(f"/api/v1/projects/{other['id']}/reference-frames/{first['id']}/preview").status_code == 404


def test_reference_frame_cache_rejects_corrupt_preview_and_sets_cache_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TVA_LOCAL_DATA_DIR", str(tmp_path / "local-data"))
    extraction_count = 0

    def fake_extract(source_path: Path, output_path: Path, requested_pts_ms: int) -> ReferenceFrameResult:
        nonlocal extraction_count
        extraction_count += 1
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(_valid_png_header(2, 2))
        return ReferenceFrameResult(
            preview_path=output_path,
            requested_pts_ms=requested_pts_ms,
            resolved_pts_ms=requested_pts_ms,
            decoder_seek_pts_ms=requested_pts_ms,
            first_decoded_pts_ms=requested_pts_ms,
            selected_pts_ms=requested_pts_ms,
            preview_width=2,
            preview_height=2,
            source_width=640,
            source_height=360,
            rotation_degrees=0,
            warnings=[],
        )

    monkeypatch.setattr(services_module, "extract_reference_frame", fake_extract)
    client = TestClient(create_app())
    project = _project_with_upload(client, tmp_path, monkeypatch)
    first = client.post(f"/api/v1/projects/{project['id']}/reference-frames", json={"mode": "beginning"}).json()
    service = client.app.state.foundation_service
    preview_path = Path(
        service.connection.execute("SELECT preview_path FROM reference_frames WHERE id = ?", (first["id"],)).fetchone()[
            "preview_path"
        ]
    )
    preview_path.write_bytes(b"not a valid png")

    second = client.post(f"/api/v1/projects/{project['id']}/reference-frames", json={"mode": "beginning"}).json()

    assert second["id"] == first["id"]
    assert extraction_count == 2
    response = client.get(f"/api/v1/projects/{project['id']}/reference-frames/{first['id']}/preview")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert response.headers["etag"] == f'"{first["id"]}"'


def test_project_source_video_supports_range_requests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TVA_LOCAL_DATA_DIR", str(tmp_path / "local-data"))
    client = TestClient(create_app())
    project = _project_with_upload(client, tmp_path, monkeypatch)

    partial = client.get(f"/api/v1/projects/{project['id']}/source-video", headers={"Range": "bytes=0-7"})

    assert partial.status_code == 206
    assert partial.headers["accept-ranges"] == "bytes"
    assert partial.headers["content-range"].startswith("bytes 0-7/")
    assert partial.content == _valid_mp4_bytes()[:8]


def test_scene_configuration_validation_stale_write_and_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TVA_LOCAL_DATA_DIR", str(tmp_path / "local-data"))
    monkeypatch.setattr(
        services_module,
        "extract_reference_frame",
        lambda source_path, output_path, requested_pts_ms: ReferenceFrameResult(
            preview_path=output_path,
            requested_pts_ms=requested_pts_ms,
            resolved_pts_ms=requested_pts_ms,
            decoder_seek_pts_ms=requested_pts_ms,
            first_decoded_pts_ms=requested_pts_ms,
            selected_pts_ms=requested_pts_ms,
            preview_width=640,
            preview_height=360,
            source_width=640,
            source_height=360,
            rotation_degrees=0,
            warnings=[],
        ),
    )
    db_path = tmp_path / "scene.sqlite"
    client = TestClient(create_app(str(db_path)))
    project = _project_with_upload(client, tmp_path, monkeypatch)
    frame = client.post(f"/api/v1/projects/{project['id']}/reference-frames", json={"mode": "analysis_start"}).json()

    invalid = _geometry()
    invalid["counting_lines"][0]["end"] = {"x": 0.2, "y": 0.7}
    bad = client.post(
        f"/api/v1/projects/{project['id']}/scene-configuration/validate",
        json={"reference_frame_id": frame["id"], "geometry": invalid},
    ).json()
    assert bad["valid"] is False
    assert "counting_lines[0]" in bad["errors"][0]

    saved = client.put(
        f"/api/v1/projects/{project['id']}/scene-configuration",
        json={"reference_frame_id": frame["id"], "geometry": _geometry()},
    ).json()
    assert saved["schema_version"] == "scene-geometry-v2"
    assert saved["version"] == 1
    ready_snapshot = client.get(f"/api/v1/projects/{project['id']}").json()
    assert ready_snapshot["project"]["stale"] == 0
    assert ready_snapshot["readiness"] == {"state": "scene_ready_for_ai_trial", "blockers": []}
    conflict = client.put(
        f"/api/v1/projects/{project['id']}/scene-configuration",
        json={"expected_version": 0, "reference_frame_id": frame["id"], "geometry": _geometry("Changed")},
    )
    assert conflict.status_code == 422

    stale = client.put(
        f"/api/v1/projects/{project['id']}/scene-configuration",
        json={"expected_version": 1, "reference_frame_id": frame["id"], "geometry": _geometry("Changed")},
    )
    assert stale.status_code == 200
    stale_again = client.put(
        f"/api/v1/projects/{project['id']}/scene-configuration",
        json={"expected_version": 1, "reference_frame_id": frame["id"], "geometry": _geometry("Changed again")},
    )
    assert stale_again.status_code == 409

    restarted = TestClient(create_app(str(db_path)))
    snapshot = restarted.get(f"/api/v1/projects/{project['id']}").json()
    assert snapshot["scene"]["version"] == 2
    assert "line_main" in snapshot["scene"]["geometry_json"]


def test_material_scene_change_invalidates_existing_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TVA_LOCAL_DATA_DIR", str(tmp_path / "local-data"))
    monkeypatch.setattr(
        services_module,
        "extract_reference_frame",
        lambda source_path, output_path, requested_pts_ms: ReferenceFrameResult(
            preview_path=output_path,
            requested_pts_ms=requested_pts_ms,
            resolved_pts_ms=requested_pts_ms,
            decoder_seek_pts_ms=requested_pts_ms,
            first_decoded_pts_ms=requested_pts_ms,
            selected_pts_ms=requested_pts_ms,
            preview_width=640,
            preview_height=360,
            source_width=640,
            source_height=360,
            rotation_degrees=0,
            warnings=[],
        ),
    )
    client = TestClient(create_app())
    project = _project_with_upload(client, tmp_path, monkeypatch)
    frame = client.post(f"/api/v1/projects/{project['id']}/reference-frames", json={"mode": "beginning"}).json()
    client.put(
        f"/api/v1/projects/{project['id']}/scene-configuration",
        json={"reference_frame_id": frame["id"], "geometry": _geometry()},
    ).raise_for_status()
    ready_snapshot = client.get(f"/api/v1/projects/{project['id']}").json()
    assert ready_snapshot["readiness"]["state"] == "scene_ready_for_ai_trial"
    run = client.post(f"/api/v1/projects/{project['id']}/mock-analysis").json()
    client.put(
        f"/api/v1/projects/{project['id']}/scene-configuration",
        json={"expected_version": 1, "reference_frame_id": frame["id"], "geometry": _geometry("Semantic change")},
    ).raise_for_status()
    stale_snapshot = client.get(f"/api/v1/projects/{project['id']}").json()
    assert stale_snapshot["project"]["stale"] == 1
    assert stale_snapshot["readiness"]["state"] == "stale_scene"
    service = client.app.state.foundation_service
    row = service.connection.execute("SELECT stale FROM aggregate_snapshots WHERE run_id = ?", (run["id"],)).fetchone()
    assert row["stale"] == 1


def test_real_ffmpeg_fixture_path_when_installed(tmp_path: Path) -> None:
    pytest.importorskip("shutil")
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are not installed in this environment")
    video = tmp_path / "thai source ทดสอบ.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x90:rate=10:duration=1",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    assert video.exists()
