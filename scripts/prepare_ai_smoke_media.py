from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://ultralytics.com/images/bus.jpg"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ffmpeg_exe(root: Path) -> Path | str:
    local = root / ".local-tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    return local if local.exists() else "ffmpeg"


def ffprobe_exe(root: Path) -> Path | str:
    local = root / ".local-tools" / "ffmpeg" / "bin" / "ffprobe.exe"
    return local if local.exists() else "ffprobe"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare ignored deterministic media for real AI smoke tests.")
    parser.add_argument("--accept-download", action="store_true", help="Required before downloading source media.")
    parser.add_argument("--frames", type=int, default=10)
    parser.add_argument("--fps", type=int, default=5)
    parser.add_argument("--output", type=Path, default=Path(".local-data/ai-artifacts/smoke/smoke_yolo_bus.mp4"))
    args = parser.parse_args()

    report = {
        "schema_version": "ai-smoke-media-preparation-v1",
        "accepted_download": args.accept_download,
        "source_url": SOURCE_URL,
        "source_license_note": "Ultralytics public example image; local smoke fixture only, not committed.",
        "errors": [],
        "artifacts": {},
    }
    if not args.accept_download:
        report["errors"].append({"code": "download_acknowledgement_required"})
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 2

    try:
        from PIL import Image
    except Exception as exc:
        report["errors"].append({"code": "pillow_missing", "error": str(exc)})
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 1

    artifacts_dir = ROOT / ".local-data" / "ai-artifacts" / "smoke"
    source_dir = artifacts_dir / "source"
    frames_dir = artifacts_dir / "frames"
    source_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)
    source_path = source_dir / "ultralytics_bus.jpg"
    if not source_path.exists():
        urllib.request.urlretrieve(SOURCE_URL, source_path)

    with Image.open(source_path) as image:
        image = image.convert("RGB")
        width, height = 640, 384
        scaled_height = height
        scaled_width = round(image.width * (scaled_height / image.height))
        resized = image.resize((scaled_width, scaled_height))
        for index in range(args.frames):
            frame = Image.new("RGB", (width, height), (32, 32, 32))
            offset = round(-40 + (80 * index / max(args.frames - 1, 1)))
            frame.paste(resized, (offset, 0))
            frame.save(frames_dir / f"frame_{index:04d}.jpg", quality=95)

    output_path = ROOT / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(ffmpeg_exe(ROOT)),
        "-y",
        "-framerate",
        str(args.fps),
        "-i",
        str(frames_dir / "frame_%04d.jpg"),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    completed = subprocess.run(command, cwd=ROOT, check=False, text=True, capture_output=True)
    if completed.returncode != 0:
        report["errors"].append({"code": "ffmpeg_encode_failed", "stderr": completed.stderr[-2000:]})
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 1

    probe = subprocess.run(
        [
            str(ffprobe_exe(ROOT)),
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=width,height,r_frame_rate,nb_frames",
            "-of",
            "json",
            str(output_path),
        ],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    report["artifacts"] = {
        "source_image": str(source_path),
        "source_sha256": sha256(source_path),
        "frames_dir": str(frames_dir),
        "video": str(output_path),
        "video_sha256": sha256(output_path),
        "frames": args.frames,
        "fps": args.fps,
        "ffprobe": json.loads(probe.stdout or "{}") if probe.returncode == 0 else {"error": probe.stderr[-1000:]},
    }
    manifest_path = artifacts_dir / "smoke_media_manifest.json"
    manifest_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    report["manifest"] = str(manifest_path)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
