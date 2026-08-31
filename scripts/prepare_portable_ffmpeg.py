from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.request import urlopen
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
SHA256_URL = f"{ARCHIVE_URL}.sha256"
SOURCE_PAGE = "https://www.gyan.dev/ffmpeg/builds/"
LICENSE_PAGE = "https://ffmpeg.org/legal.html"


def fetch_bytes(url: str) -> bytes:
    with urlopen(url, timeout=120) as response:
        return response.read()


def expected_sha256(document: bytes) -> str:
    text = document.decode("utf-8").strip()
    digest = text.split()[0].lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError("SHA-256 document did not contain a valid digest")
    return digest


def extract_binary(archive_path: Path, binary_name: str, target_dir: Path) -> Path:
    with ZipFile(archive_path) as archive:
        members = [
            member
            for member in archive.infolist()
            if not member.is_dir()
            and Path(member.filename).name.lower() == binary_name.lower()
            and "/bin/" in member.filename.replace("\\", "/")
        ]
        if len(members) != 1:
            raise ValueError(f"Expected exactly one {binary_name} under the archive bin directory.")
        member = members[0]
        target = target_dir / binary_name
        target_dir.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination)
        return target


def version_details(executable: Path) -> dict[str, str | list[str] | None]:
    result = subprocess.run(
        [str(executable), "-version"],
        check=True,
        text=True,
        capture_output=True,
        timeout=20,
    )
    lines = result.stdout.splitlines()
    return {
        "version": lines[0] if lines else None,
        "configuration": next((line.removeprefix("configuration: ") for line in lines if line.startswith("configuration: ")), None),
        "libraries": [line for line in lines if line.startswith("lib")],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare repository-local portable FFmpeg/ffprobe.")
    parser.add_argument(
        "--accept-download",
        action="store_true",
        help="Required. Downloads a Windows FFmpeg build archive and checksum from gyan.dev.",
    )
    args = parser.parse_args()
    if not args.accept_download:
        print("Refusing to download FFmpeg without --accept-download.", file=sys.stderr)
        return 2

    print(f"Fetching FFmpeg checksum: {SHA256_URL}")
    expected = expected_sha256(fetch_bytes(SHA256_URL))
    print(f"Fetching FFmpeg archive: {ARCHIVE_URL}")
    archive = fetch_bytes(ARCHIVE_URL)
    actual = hashlib.sha256(archive).hexdigest()
    if actual != expected:
        print("Downloaded FFmpeg archive failed SHA-256 verification.", file=sys.stderr)
        return 2

    evidence_dir = ROOT / ".local-data" / "ffmpeg-preparation"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    archive_path = evidence_dir / "ffmpeg-release-essentials.zip"
    archive_path.write_bytes(archive)

    target_dir = ROOT / ".local-tools" / "ffmpeg" / "bin"
    ffmpeg = extract_binary(archive_path, "ffmpeg.exe", target_dir)
    ffprobe = extract_binary(archive_path, "ffprobe.exe", target_dir)
    ffmpeg_details = version_details(ffmpeg)
    ffprobe_details = version_details(ffprobe)

    provenance = {
        "runtime": "ffmpeg",
        "acquisition_date_utc": datetime.now(timezone.utc).isoformat(),
        "distribution": "gyan.dev release essentials Windows build",
        "source_page": SOURCE_PAGE,
        "archive_url": ARCHIVE_URL,
        "sha256_url": SHA256_URL,
        "archive_sha256": actual,
        "license_page": LICENSE_PAGE,
        "license_summary": (
            "FFmpeg contains LGPL and optional GPL components depending on build configuration; "
            "the gyan.dev essentials build is documented as GPL enabled and must remain an "
            "ignored local runtime unless distribution approval is recorded."
        ),
        "ffmpeg": ffmpeg_details,
        "ffprobe": ffprobe_details,
        "installed_files": [str(ffmpeg.relative_to(ROOT)), str(ffprobe.relative_to(ROOT))],
    }
    (evidence_dir / "portable_ffmpeg_provenance.json").write_text(
        json.dumps(provenance, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(provenance, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
