from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ai_stack.artifacts import artifact_hash, observable_class, validate_normalized_track_artifact  # noqa: E402
from tools.ai_stack.registry import load_registry  # noqa: E402
from tools.ai_stack.runtime import ai_python, file_sha256, torch_status  # noqa: E402


def reexec_in_ai_venv() -> int | None:
    target = ai_python(ROOT)
    if os.environ.get("TVA_AI_SMOKE_REEXEC") == "1":
        return None
    if Path(sys.executable).resolve() == target.resolve():
        return None
    if not target.exists():
        return None
    env = {**os.environ, "TVA_AI_SMOKE_REEXEC": "1"}
    completed = subprocess.run([str(target), *sys.argv], cwd=ROOT, env=env, check=False)
    return completed.returncode


def sha256(path: Path) -> str:
    return file_sha256(path)


def ffmpeg_tool(name: str) -> str:
    local = ROOT / ".local-tools" / "ffmpeg" / "bin" / f"{name}.exe"
    if local.exists():
        return str(local)
    discovered = shutil.which(name)
    return discovered or name


def ffprobe_media(media_path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            ffmpeg_tool("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=index,codec_type,width,height,r_frame_rate,avg_frame_rate,nb_frames",
            "-of",
            "json",
            str(media_path),
        ],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:])
    return json.loads(result.stdout)


def decode_frames(media_path: Path, frames_dir: Path) -> list[dict[str, Any]]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    for stale in frames_dir.glob("frame_*.jpg"):
        stale.unlink()
    probe = subprocess.run(
        [
            ffmpeg_tool("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "frame=best_effort_timestamp_time,pkt_pts_time",
            "-of",
            "json",
            str(media_path),
        ],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if probe.returncode != 0:
        raise RuntimeError(probe.stderr[-2000:])
    frame_pts = []
    for index, frame in enumerate(json.loads(probe.stdout or "{}").get("frames", [])):
        pts_s = frame.get("best_effort_timestamp_time") or frame.get("pkt_pts_time")
        frame_pts.append({"frame_index": index, "pts_ms": round(float(pts_s or 0) * 1000)})

    decode = subprocess.run(
        [
            ffmpeg_tool("ffmpeg"),
            "-y",
            "-i",
            str(media_path),
            "-vsync",
            "0",
            str(frames_dir / "frame_%04d.jpg"),
        ],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if decode.returncode != 0:
        raise RuntimeError(decode.stderr[-2000:])
    decoded = sorted(frames_dir.glob("frame_*.jpg"))
    if len(frame_pts) < len(decoded):
        frame_pts.extend({"frame_index": index, "pts_ms": index * 200} for index in range(len(frame_pts), len(decoded)))
    return [
        {"path": frame_path, "frame_index": frame_pts[index]["frame_index"], "pts_ms": frame_pts[index]["pts_ms"]}
        for index, frame_path in enumerate(decoded)
    ]


def normalize_xyxy(xyxy: list[float], width: int, height: int) -> tuple[list[float], list[float]]:
    x1, y1, x2, y2 = xyxy
    x = max(0.0, min(1.0, x1 / width))
    y = max(0.0, min(1.0, y1 / height))
    w = max(0.0, min(1.0, (x2 - x1) / width))
    h = max(0.0, min(1.0, (y2 - y1) / height))
    return [x, y, w, h], [max(0.0, min(1.0, x + (w / 2))), max(0.0, min(1.0, y + h))]


def tracker_update(tracker: Any, detections: Any, frame: Any) -> Any:
    try:
        return tracker.update(detections, frame=frame)
    except TypeError:
        return tracker.update(detections)


def detections_to_tracks(updated: Any, fallback_names: list[str]) -> list[dict[str, Any]]:
    import numpy as np

    xyxy = np.asarray(getattr(updated, "xyxy", []), dtype=float)
    confidence = np.asarray(getattr(updated, "confidence", []), dtype=float)
    class_id = np.asarray(getattr(updated, "class_id", []), dtype=int)
    tracker_id = getattr(updated, "tracker_id", None)
    tracker_id = np.asarray(tracker_id, dtype=int) if tracker_id is not None else np.arange(len(xyxy), dtype=int)
    names = fallback_names
    data = getattr(updated, "data", {}) or {}
    if "class_name" in data:
        names = [str(value) for value in data["class_name"]]
    tracks = []
    for index, box in enumerate(xyxy):
        if index < len(tracker_id) and int(tracker_id[index]) < 0:
            continue
        name = names[index] if index < len(names) else str(class_id[index]) if index < len(class_id) else "unknown"
        tracks.append(
            {
                "track_id": str(int(tracker_id[index])) if index < len(tracker_id) else str(index),
                "native_class": name,
                "confidence": float(confidence[index]) if index < len(confidence) else None,
                "xyxy": [float(value) for value in box.tolist()],
            }
        )
    return tracks


def run_ultralytics(candidate: str, root: Path, registry: dict, args: argparse.Namespace) -> dict[str, Any]:
    os.environ.setdefault("YOLO_CONFIG_DIR", str(root / ".local-data" / "ai-artifacts" / "ultralytics-config"))
    if args.offline:
        os.environ["ULTRALYTICS_OFFLINE"] = "1"
    warnings.filterwarnings("ignore", category=FutureWarning, module="trackers")
    try:
        import numpy as np
        import supervision as sv
        import torchvision
        import ultralytics
        from PIL import Image
        from trackers import ByteTrackTracker
        from ultralytics import YOLO
    except Exception as exc:
        return {"candidate": candidate, "status": "blocked", "error_code": "ai_package_missing", "error": str(exc)}

    record = next(item for item in registry["models"] if item["model_id"] == candidate)
    tracker_record = next(item for item in registry["models"] if item["model_id"] == "tracker.trackers-bytetrack")
    model_path = root / ".local-tools" / "models" / record["model_filename"]
    if not model_path.exists():
        return {"candidate": candidate, "status": "blocked", "error_code": "weight_missing", "path": str(model_path)}
    actual_hash = sha256(model_path)
    expected_hash = record.get("sha256")
    if expected_hash and actual_hash != expected_hash:
        return {
            "candidate": candidate,
            "status": "blocked",
            "error_code": "weight_hash_mismatch",
            "expected": expected_hash,
            "actual": actual_hash,
        }

    media_path = root / args.media
    if not media_path.exists():
        return {"candidate": candidate, "status": "blocked", "error_code": "smoke_media_missing", "path": str(media_path)}

    torch_info = torch_status(Path(sys.executable))
    if args.device == "gpu" and not torch_info.get("cuda_available"):
        return {"candidate": candidate, "status": "blocked", "error_code": "gpu_provider_unavailable", "torch": torch_info}
    device = "cuda:0" if args.device == "gpu" else "cpu"

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"_{args.device}{'_offline' if args.offline else ''}"
    run_dir = root / ".local-data" / "ai-artifacts" / "smoke" / "runs" / run_id
    frames_dir = run_dir / "frames"
    try:
        media_probe = ffprobe_media(media_path)
        frames = decode_frames(media_path, frames_dir)
    except Exception as exc:
        return {"candidate": candidate, "status": "blocked", "error_code": "media_decode_failed", "error": str(exc)}
    if not frames:
        return {"candidate": candidate, "status": "blocked", "error_code": "no_decoded_frames"}

    first_frame = Image.open(frames[0]["path"])
    width, height = first_frame.size
    first_frame.close()

    load_start = time.perf_counter()
    model = YOLO(str(model_path))
    load_ms = (time.perf_counter() - load_start) * 1000
    tracker_config = {
        "track_activation_threshold": args.conf,
        "lost_track_buffer": 30,
        "minimum_iou_threshold": 0.1,
        "frame_rate": args.fps,
        "minimum_consecutive_frames": 1,
    }
    try:
        tracker = ByteTrackTracker(**tracker_config)
    except TypeError:
        tracker = ByteTrackTracker()

    tracks_by_id: dict[str, dict[str, Any]] = {}
    frame_timings: list[dict[str, Any]] = []
    detection_count = 0
    allowed_classes = set(record.get("supported_observable_classes", []))
    for frame_ref in frames:
        infer_start = time.perf_counter()
        results = model.predict(
            str(frame_ref["path"]),
            device=device,
            conf=args.conf,
            iou=args.iou,
            imgsz=args.imgsz,
            verbose=False,
        )
        infer_ms = (time.perf_counter() - infer_start) * 1000
        result = results[0]
        names = result.names
        boxes = []
        confidences = []
        class_ids = []
        class_names = []
        for box in result.boxes:
            cls = int(box.cls[0].item())
            native = str(names.get(cls, cls))
            if allowed_classes and native not in allowed_classes:
                continue
            boxes.append([float(value) for value in box.xyxy[0].tolist()])
            confidences.append(float(box.conf[0].item()))
            class_ids.append(cls)
            class_names.append(native)
        detection_count += len(boxes)
        detections = sv.Detections(
            xyxy=np.asarray(boxes, dtype=float).reshape((-1, 4)),
            confidence=np.asarray(confidences, dtype=float),
            class_id=np.asarray(class_ids, dtype=int),
            data={"class_name": np.asarray(class_names, dtype=object)},
        )
        updated = tracker_update(tracker, detections, None)
        for tracked in detections_to_tracks(updated, class_names):
            bbox_xywh, point = normalize_xyxy(tracked["xyxy"], width, height)
            track = tracks_by_id.setdefault(
                tracked["track_id"],
                {
                    "track_id": tracked["track_id"],
                    "track_class": tracked["native_class"],
                    "observable_class": observable_class(tracked["native_class"]),
                    "track_confidence": tracked["confidence"],
                    "review_state": "needs_review",
                    "observations": [],
                },
            )
            if tracked["confidence"] is not None:
                previous = track.get("track_confidence")
                track["track_confidence"] = tracked["confidence"] if previous is None else max(previous, tracked["confidence"])
            track["observations"].append(
                {
                    "pts_ms": frame_ref["pts_ms"],
                    "frame_index": frame_ref["frame_index"],
                    "bbox_xywh": bbox_xywh,
                    "representative_point": point,
                    "native_class": tracked["native_class"],
                    "observable_class": observable_class(tracked["native_class"]),
                    "confidence": tracked["confidence"],
                    "evidence": {"decoded_frame": frame_ref["path"].name, "source": "yolo11n_bytetrack_smoke"},
                }
            )
        frame_timings.append(
            {
                "frame_index": frame_ref["frame_index"],
                "pts_ms": frame_ref["pts_ms"],
                "inference_ms": round(infer_ms, 3),
                "detections": len(boxes),
            }
        )

    tracks = list(tracks_by_id.values())
    for track in tracks:
        track["observations"].sort(key=lambda item: (item["pts_ms"], item["frame_index"]))

    config = {
        "device_request": args.device,
        "runtime_device": device,
        "imgsz": args.imgsz,
        "confidence": args.conf,
        "iou": args.iou,
        "offline": args.offline,
        "tracker": tracker_config,
    }
    payload = {
        "schema_version": "normalized-track-artifact-v1",
        "candidate": candidate,
        "status": "succeeded",
        "engineering_evaluation_only": True,
        "not_certified_traffic_survey": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_media": {
            "path": str(media_path),
            "sha256": sha256(media_path),
            "ffprobe": media_probe,
            "rights_note": "local smoke media prepared from Ultralytics public example image; not committed",
        },
        "runtime": {
            "python": platform.python_version(),
            "python_executable": sys.executable,
            "platform": platform.platform(),
            "torch": torch_info,
            "torchvision": torchvision.__version__,
            "ultralytics": ultralytics.__version__,
        },
        "detector_manifest": {
            "implementation_id": candidate,
            "package_name": "ultralytics",
            "package_version": ultralytics.__version__,
            "model_filename": record["model_filename"],
            "weight_sha256": actual_hash,
            "code_license": record["code_license"],
            "package_license": record["package_license"],
            "weight_license": record["weight_license"],
        },
        "tracker_manifest": {
            "implementation_id": tracker_record["model_id"],
            "package_name": "trackers",
            "package_version": tracker_record["exact_version"],
            "model_version": "none",
            "code_license": tracker_record["code_license"],
            "package_license": tracker_record["package_license"],
            "weight_license": tracker_record["weight_license"],
        },
        "configuration": config,
        "configuration_hash": hashlib.sha256(json.dumps(config, sort_keys=True).encode("utf-8")).hexdigest(),
        "frames_processed": len(frames),
        "frame_timings": frame_timings,
        "model_load_ms": round(load_ms, 3),
        "inference_total_ms": round(sum(item["inference_ms"] for item in frame_timings), 3),
        "detections_summary": {
            "detections": detection_count,
            "tracks": len(tracks),
            "multi_frame_tracks": sum(1 for track in tracks if len(track["observations"]) > 1),
        },
        "tracks": tracks,
        "warnings": [
            "smoke fixture only; not a representative traffic-count benchmark",
            "COCO classes are mapped only to observable coarse domains, never to TIMS fine classes",
        ],
    }
    payload["artifact_hash"] = artifact_hash(payload)
    errors = validate_normalized_track_artifact(payload)
    if errors:
        return {"candidate": candidate, "status": "blocked", "error_code": "normalized_artifact_invalid", "errors": errors}
    output = run_dir / f"normalized_tracks_{args.device}{'_offline' if args.offline else ''}.json"
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    payload["artifact"] = str(output)
    return payload


def main() -> int:
    reexec_code = reexec_in_ai_venv()
    if reexec_code is not None:
        return reexec_code
    parser = argparse.ArgumentParser(description="Run real YOLO11n + ByteTrack smoke qualification.")
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--registry", type=Path, default=Path("model_registry.json"))
    parser.add_argument("--device", choices=("cpu", "gpu"), default="cpu")
    parser.add_argument("--media", type=Path, default=Path(".local-data/ai-artifacts/smoke/smoke_yolo_bus.mp4"))
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.7)
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--verbose-artifact", action="store_true", help="Print the full normalized artifact payload.")
    args = parser.parse_args()

    registry = load_registry(args.registry)
    candidates = {record["model_id"] for record in registry.get("models", [])}
    if args.candidate not in candidates:
        print(json.dumps({"status": "blocked", "error_code": "unknown_candidate"}, indent=2))
        return 2
    if args.candidate.startswith("detector.ultralytics-"):
        payload = run_ultralytics(args.candidate, ROOT, registry, args)
    else:
        payload = {
            "candidate": args.candidate,
            "status": "blocked",
            "error_code": "adapter_not_implemented",
            "reason": "candidate remains fallback/design-approved until exact runtime checkout and weights are pinned",
        }
    output_payload = payload
    if payload.get("status") == "succeeded" and not args.verbose_artifact:
        output_payload = {
            "schema_version": payload["schema_version"],
            "candidate": payload["candidate"],
            "status": payload["status"],
            "device": payload["configuration"]["device_request"],
            "runtime_device": payload["configuration"]["runtime_device"],
            "offline": payload["configuration"]["offline"],
            "frames_processed": payload["frames_processed"],
            "detections_summary": payload["detections_summary"],
            "model_load_ms": payload["model_load_ms"],
            "inference_total_ms": payload["inference_total_ms"],
            "artifact_hash": payload["artifact_hash"],
            "artifact": payload["artifact"],
            "warnings": payload["warnings"],
        }
    print(json.dumps(output_payload, indent=2, ensure_ascii=False))
    return 0 if payload.get("status") == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
