from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ai_stack.registry import load_registry  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare approved pilot model weights outside Git.")
    parser.add_argument("--accept-download", action="store_true", help="Required before downloading weights.")
    parser.add_argument("--registry", type=Path, default=Path("model_registry.json"))
    parser.add_argument(
        "--model-id",
        action="append",
        default=["detector.ultralytics-yolo11n-coco"],
        help="Model ID to prepare. Repeat for multiple models.",
    )
    parser.add_argument(
        "--update-registry",
        action="store_true",
        help="Write the verified SHA-256 and file size back to model_registry.json.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    registry = load_registry(args.registry)
    models_dir = root / ".local-tools" / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    wanted = set(args.model_id)
    report = {
        "schema_version": "pilot-model-preparation-v1",
        "accepted_download": args.accept_download,
        "models_dir": str(models_dir),
        "prepared": [],
        "errors": [],
    }
    if not args.accept_download:
        report["errors"].append({"code": "download_acknowledgement_required"})
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 2

    records = [record for record in registry.get("models", []) if record.get("model_id") in wanted]
    for record in records:
        filename = record.get("model_filename")
        url = record.get("source_url")
        if not filename or not url or not str(url).startswith(("https://github.com/", "https://download.openmmlab.com/")):
            report["errors"].append({"model_id": record.get("model_id"), "code": "unsupported_official_download_source"})
            continue
        target = models_dir / str(filename)
        if args.dry_run:
            report["prepared"].append({"model_id": record["model_id"], "target": str(target), "dry_run": True})
            continue
        if not target.exists():
            urllib.request.urlretrieve(str(url), target)
        actual = sha256(target)
        expected = record.get("sha256")
        if expected is not None and actual != expected:
            report["errors"].append(
                {
                    "model_id": record.get("model_id"),
                    "code": "hash_mismatch",
                    "expected": expected,
                    "actual": actual,
                }
            )
            target.unlink(missing_ok=True)
            continue
        report["prepared"].append(
            {
                "model_id": record["model_id"],
                "filename": filename,
                "path": str(target),
                "sha256": actual,
                "file_size_bytes": target.stat().st_size,
                "registry_hash_recorded": expected is not None,
                "registry_updated": bool(args.update_registry),
            }
        )
        if args.update_registry:
            record["sha256"] = actual
            record["file_size_bytes"] = target.stat().st_size
            record["weight_verified_at"] = datetime.now(timezone.utc).isoformat()
    if args.update_registry and not args.dry_run and not report["errors"]:
        args.registry.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        report["registry"] = str(args.registry)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
