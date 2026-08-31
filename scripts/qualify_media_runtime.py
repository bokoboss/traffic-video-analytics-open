from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.backend.app.media import MediaRuntimeError, extract_reference_frame, inspect_media, media_runtime_status  # noqa: E402


def run(command: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def main() -> int:
    status = media_runtime_status()
    payload: dict[str, object] = {
        "schema_version": "media-runtime-qualification-v1",
        "readiness_state": status.readiness_state,
        "ffmpeg": status.ffmpeg.__dict__,
        "ffprobe": status.ffprobe.__dict__,
        "checks": [],
    }
    checks: list[dict[str, object]] = payload["checks"]  # type: ignore[assignment]
    if not status.reference_frame_extraction_available or status.ffmpeg.executable is None or status.ffprobe.executable is None:
        checks.append({"name": "runtime_pair", "status": "blocked", "warnings": status.warnings})
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 2

    output_dir = ROOT / ".local-data" / "media-runtime-qualification" / "Paths with spaces & Thai ทดสอบ"
    output_dir.mkdir(parents=True, exist_ok=True)
    cfr_video = output_dir / "synthetic cfr ทดสอบ.mp4"
    no_video = output_dir / "audio-only.wav"
    corrupt = output_dir / "corrupt.mp4"
    preview = output_dir / "preview.png"

    cfr = run(
        [
            status.ffmpeg.executable,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x90:rate=10:duration=1",
            "-pix_fmt",
            "yuv420p",
            str(cfr_video),
        ]
    )
    checks.append({"name": "create_cfr_unicode_path", "status": "passed" if cfr.returncode == 0 else "failed"})
    if cfr.returncode == 0:
        inspection = inspect_media(cfr_video)
        checks.append(
            {
                "name": "probe_cfr",
                "status": "passed" if inspection.metadata.get("video_codec") else "failed",
                "metadata": inspection.metadata,
                "warnings": inspection.warnings,
            }
        )
        try:
            frame = extract_reference_frame(cfr_video, preview, 0)
            checks.append(
                {
                    "name": "extract_preview",
                    "status": "passed" if preview.exists() else "failed",
                    "preview_width": frame.preview_width,
                    "preview_height": frame.preview_height,
                    "warnings": frame.warnings,
                }
            )
        except MediaRuntimeError as exc:
            checks.append({"name": "extract_preview", "status": "failed", "error_code": exc.code})

    audio = run([status.ffmpeg.executable, "-y", "-f", "lavfi", "-i", "sine=frequency=1000:duration=0.25", str(no_video)])
    if audio.returncode == 0:
        inspection = inspect_media(no_video)
        checks.append(
            {
                "name": "no_video_stream_controlled_failure",
                "status": "passed" if "no_video_stream" in inspection.warnings else "failed",
                "warnings": inspection.warnings,
            }
        )
    else:
        checks.append({"name": "create_no_video_fixture", "status": "failed"})

    corrupt.write_bytes(b"not a valid video")
    inspection = inspect_media(corrupt)
    checks.append(
        {
            "name": "corrupt_media_controlled_failure",
            "status": "passed" if any(warning.startswith("ffprobe_failed") for warning in inspection.warnings) else "failed",
            "warnings": inspection.warnings,
        }
    )

    failures = [check for check in checks if check.get("status") in {"failed", "blocked"}]
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
