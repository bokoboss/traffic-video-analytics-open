from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
PNPM_VERSION = "11.9.0"
METADATA_URL = f"https://registry.npmjs.org/pnpm/{PNPM_VERSION}"


def fetch_bytes(url: str) -> bytes:
    with urlopen(url, timeout=60) as response:
        return response.read()


def write_wrapper(root: Path) -> Path:
    wrapper = root / ".local-tools" / "pnpm" / "pnpm.cmd"
    text = """@echo off
setlocal

set "ROOT=%~dp0..\\.."
set "NODE_EXE=%ROOT%\\.local-tools\\node\\node.exe"
set "PNPM_CJS=%ROOT%\\.local-tools\\pnpm\\node_modules\\pnpm\\bin\\pnpm.cjs"

if not exist "%NODE_EXE%" (
  echo Portable Node.js executable is missing: "%NODE_EXE%" 1>&2
  exit /b 2
)

if not exist "%PNPM_CJS%" (
  echo Portable pnpm entry point is missing: "%PNPM_CJS%" 1>&2
  exit /b 2
)

set "PATH=%ROOT%\\.local-tools\\node;%PATH%"
"%NODE_EXE%" "%PNPM_CJS%" %*
exit /b %ERRORLEVEL%
"""
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(text, encoding="utf-8", newline="\r\n")
    return wrapper


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare repository-local portable pnpm.")
    parser.add_argument(
        "--accept-download",
        action="store_true",
        help="Required. Downloads pnpm metadata and tarball from the official npm registry.",
    )
    args = parser.parse_args()
    if not args.accept_download:
        print("Refusing to download pnpm without --accept-download.", file=sys.stderr)
        return 2

    node = ROOT / ".local-tools" / "node" / "node.exe"
    npm_cli = ROOT / ".local-tools" / "node" / "node_modules" / "npm" / "bin" / "npm-cli.js"
    if not node.exists():
        print(f"Portable Node.js executable is missing: {node}", file=sys.stderr)
        return 2
    if not npm_cli.exists():
        print(f"Portable npm CLI is missing: {npm_cli}", file=sys.stderr)
        return 2

    print(f"Fetching pnpm metadata: {METADATA_URL}")
    metadata = json.loads(fetch_bytes(METADATA_URL).decode("utf-8"))
    if metadata.get("name") != "pnpm" or metadata.get("version") != PNPM_VERSION:
        print("Official metadata did not describe pnpm 11.9.0.", file=sys.stderr)
        return 2

    dist = metadata["dist"]
    tarball_url = dist["tarball"]
    integrity = dist["integrity"]
    shasum = dist["shasum"]
    print(f"Fetching pnpm tarball: {tarball_url}")
    tarball = fetch_bytes(tarball_url)

    sha512_base64 = base64.b64encode(hashlib.sha512(tarball).digest()).decode("ascii")
    expected_sha512 = integrity.removeprefix("sha512-")
    sha1 = hashlib.sha1(tarball).hexdigest()
    sha256 = hashlib.sha256(tarball).hexdigest()
    if sha512_base64 != expected_sha512:
        print("Downloaded tarball failed SHA-512 integrity verification.", file=sys.stderr)
        return 2
    if sha1 != shasum:
        print("Downloaded tarball failed npm shasum verification.", file=sys.stderr)
        return 2

    evidence_dir = ROOT / ".local-data" / "pnpm-11.9.0-preparation"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "pnpm-11.9.0.metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (evidence_dir / "pnpm-11.9.0.tgz").write_bytes(tarball)

    pnpm_dir = ROOT / ".local-tools" / "pnpm"
    env = os.environ.copy()
    env["npm_config_update_notifier"] = "false"
    env["npm_config_cache"] = str(ROOT / ".local-data" / "npm-cache")
    command = [
        str(node),
        str(npm_cli),
        "install",
        "--prefix",
        str(pnpm_dir),
        "--no-save",
        "--ignore-scripts=false",
        "--fund=false",
        "--audit=false",
        f"pnpm@{PNPM_VERSION}",
    ]
    subprocess.run(command, cwd=ROOT, env=env, check=True)
    wrapper = write_wrapper(ROOT)

    version = subprocess.run(
        f'"{wrapper}" --version',
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
        shell=True,
    )
    if version.stdout.strip() != PNPM_VERSION:
        print(f"Prepared pnpm reported unexpected version: {version.stdout.strip()}", file=sys.stderr)
        return 2

    print(json.dumps({
        "package": "pnpm",
        "version": PNPM_VERSION,
        "tarball": tarball_url,
        "integrity": integrity,
        "shasum": shasum,
        "computed_sha256": sha256,
        "pnpm_command": str(wrapper),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
