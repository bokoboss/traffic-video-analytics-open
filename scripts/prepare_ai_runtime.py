from __future__ import annotations

import argparse
import json
import platform
import subprocess
import venv
from pathlib import Path


def venv_python(root: Path) -> Path:
    return root / ".venv-ai" / "Scripts" / "python.exe"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare the ignored optional AI runtime.")
    parser.add_argument("--accept-install", action="store_true", help="Required before package installation.")
    parser.add_argument("--requirements", type=Path, default=Path("requirements-ai.txt"))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(".local-data/ai-artifacts/runtime/ai_runtime_manifest.json"),
        help="Ignored manifest recording the installed AI runtime.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    env_dir = root / ".venv-ai"
    result = {
        "schema_version": "ai-runtime-preparation-v1",
        "environment": str(env_dir),
        "requirements": str(args.requirements),
        "accepted_install": args.accept_install,
        "dry_run": args.dry_run,
        "actions": [],
    }
    if not args.accept_install:
        result["actions"].append("blocked: --accept-install is required")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 2
    if args.dry_run:
        result["actions"].append("would create .venv-ai and install requirements-ai.txt")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if not env_dir.exists():
        venv.EnvBuilder(with_pip=True).create(env_dir)
        result["actions"].append("created .venv-ai")
    else:
        result["actions"].append("reused .venv-ai")

    pip_cmd = [
        str(venv_python(root)),
        "-m",
        "pip",
        "install",
        "--requirement",
        str(root / args.requirements),
    ]
    completed = subprocess.run(pip_cmd, cwd=root, check=False)
    result["actions"].append(f"pip install exit code {completed.returncode}")
    if completed.returncode == 0:
        freeze = subprocess.run(
            [str(venv_python(root)), "-m", "pip", "freeze"],
            cwd=root,
            check=False,
            text=True,
            capture_output=True,
        )
        manifest_path = root / args.manifest
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": "ai-runtime-install-manifest-v1",
            "python": platform.python_version(),
            "python_executable": str(venv_python(root)),
            "requirements": str(root / args.requirements),
            "pip_freeze": freeze.stdout.splitlines() if freeze.returncode == 0 else [],
            "pip_freeze_exit_code": freeze.returncode,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        result["manifest"] = str(manifest_path)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
