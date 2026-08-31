"""Fail-closed validation for the public corresponding-source tree.

This validator intentionally has no third-party dependencies. It checks the
working tree, not Git object metadata; commit identity and history are checked
separately by the release procedure. Use ``--tracked`` for a bounded final
candidate check that enumerates exactly the paths tracked by Git.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 1_000_000

ALLOWED_ROOT_FILES = frozenset(
    {
        ".editorconfig",
        ".gitattributes",
        ".gitignore",
        ".github/workflows/ci.yml",
        "backup_app.bat",
        "CONTRIBUTING.md",
        "DESIGN.md",
        "LICENSE",
        "model_registry.json",
        "package.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "PRODUCT.md",
        "pyproject.toml",
        "release.json",
        "requirements-ai.txt",
        "requirements-benchmark.txt",
        "requirements-dev.txt",
        "restore_app.bat",
        "run_app.bat",
        "setup_app.bat",
        "stop_app.bat",
        "THIRD_PARTY_NOTICES.md",
        "README.md",
    }
)

ALLOWED_PREFIXES = (
    "apps/",
    "design/",
    "local-data.example/",
    "packages/",
    "references/",
    "sample-data/",
    "scripts/",
    "tests/",
    "tools/",
    "docs/api/",
    "docs/architecture/",
    "docs/ux/",
)

ALLOWED_DOC_FILES = frozenset(
    {
        "docs/BUILDING.md",
        "docs/PORTABLE_BUILD.md",
        "docs/PRIVACY.md",
        "docs/SOURCE_SNAPSHOT.md",
        "docs/glossary.md",
        "docs/development/advanced_parameter_guide.md",
        "docs/development/backup_restore.md",
        "docs/development/benchmark_annotation_guide.md",
        "docs/development/benchmark_cli_guide.md",
        "docs/development/dependency_license_register.md",
        "docs/development/export_schema_guide.md",
        "docs/development/local_development.md",
        "docs/development/local_runtime_launcher.md",
        "docs/development/processing_profile_guide.md",
        "docs/development/real_validation_guide.md",
        "docs/development/real_video_inference_troubleshooting.md",
        "docs/development/review_operator_guide.md",
        "docs/development/support_diagnostics.md",
        "docs/development/windows_launcher.md",
        "docs/development/windows_setup.md",
    }
)

FORBIDDEN_EXTENSIONS = frozenset(
    {
        ".7z",
        ".aac",
        ".avi",
        ".db",
        ".db-shm",
        ".db-wal",
        ".dll",
        ".engine",
        ".exe",
        ".flac",
        ".iso",
        ".key",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".mpeg",
        ".onnx",
        ".p12",
        ".pem",
        ".pfx",
        ".pt",
        ".pth",
        ".rar",
        ".safetensors",
        ".sqlite",
        ".sqlite-shm",
        ".sqlite-wal",
        ".sqlite3",
        ".tflite",
        ".wav",
        ".webm",
        ".weights",
        ".zip",
    }
)

FORBIDDEN_DIRECTORY_NAMES = frozenset(
    {
        ".local-data",
        ".local-tools",
        ".venv",
        ".venv-ai",
        "build",
        "cache",
        "caches",
        "coverage",
        "datasets",
        "dist",
        "evidence",
        "exports",
        "node_modules",
        "output",
        "outputs",
        "runs",
        "temporary",
        "tmp",
        "weights",
    }
)

EMAIL_PATTERN = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
UNIX_PRIVATE_ROOTS = ("Users", "home", "private")
ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"(?i)(?:[A-Z]:[\\/]Users(?:[\\/]|$))"),
    re.compile(r"(?i)(?:[A-Z]:[\\/]R&D(?:[\\/]|$))"),
) + tuple(
    re.compile(r"(?i)(?:^|[\s(\"'`])/" + root_name + r"/") for root_name in UNIX_PRIVATE_ROOTS
)
SECRET_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN [A-Z0-9 ]+ PRIVATE KEY-----")),
    ("github_token", re.compile(r"\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    (
        "credential_assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*"
            r"[\"']?(?!<redacted>|<placeholder>|\$\{|None\b|null\b)[A-Za-z0-9_./+=-]{12,}"
        ),
    ),
)


@dataclass(frozen=True)
class Finding:
    category: str
    path: str
    detail: str


def relative_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def is_allowed(path: str) -> bool:
    return path in ALLOWED_ROOT_FILES or path in ALLOWED_DOC_FILES or any(path.startswith(prefix) for prefix in ALLOWED_PREFIXES)


def tracked_candidate_files(root: Path) -> list[Path]:
    completed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    names = completed.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    return sorted(root / Path(name) for name in names if name)


def candidate_files(root: Path, *, tracked: bool = False) -> list[Path]:
    if tracked:
        return tracked_candidate_files(root)
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    )


def scan(root: Path, *, tracked: bool = False) -> list[Finding]:
    findings: list[Finding] = []
    try:
        files = candidate_files(root, tracked=tracked)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", b"")
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace").strip()
        detail = detail or str(exc)
        findings.append(Finding("tracked_enumeration", ".", f"could not enumerate Git-tracked files: {detail}"))
        files = []
    tracked_paths = {relative_path(path, root) for path in files} if tracked else set()
    for path in files:
        rel = relative_path(path, root)
        if tracked and not path.is_file() and not path.is_symlink():
            findings.append(Finding("missing_file", rel, "tracked file is missing from the working tree"))
            continue
        lower_parts = {part.lower() for part in path.relative_to(root).parts[:-1]}
        name = path.name.lower()
        if not is_allowed(rel):
            findings.append(Finding("allowlist", rel, "path is not in the public release allowlist"))
        if any(part in FORBIDDEN_DIRECTORY_NAMES for part in lower_parts):
            findings.append(Finding("local_state_or_private_data", rel, "forbidden local-state/data directory"))
        if path.suffix.lower() in FORBIDDEN_EXTENSIONS:
            findings.append(Finding("forbidden_extension", rel, f"extension {path.suffix.lower()} is not publishable"))
        if name == ".env" or name.startswith(".env.") or path.suffix.lower() in {".key", ".pem", ".p12", ".pfx", ".secret"}:
            findings.append(Finding("secret_file", rel, "secret-like file name"))
        if path.is_symlink():
            findings.append(Finding("symlink", rel, "symlinks are not part of the source snapshot"))
        try:
            size = path.stat().st_size
        except OSError as exc:
            findings.append(Finding("read_error", rel, str(exc)))
            continue
        if size > MAX_FILE_BYTES:
            findings.append(Finding("large_file", rel, f"{size} bytes exceeds {MAX_FILE_BYTES}-byte review limit"))
        try:
            raw = path.read_bytes()
        except OSError as exc:
            findings.append(Finding("read_error", rel, str(exc)))
            continue
        if b"\x00" in raw:
            findings.append(Finding("binary_file", rel, "NUL byte found in candidate file"))
            continue
        text = raw.decode("utf-8", errors="replace")
        for pattern in ABSOLUTE_PATH_PATTERNS:
            if pattern.search(text):
                findings.append(Finding("machine_path", rel, f"forbidden absolute path pattern: {pattern.pattern}"))
        if EMAIL_PATTERN.search(text):
            findings.append(Finding("email", rel, "email address found in candidate content"))
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(text):
                findings.append(Finding("secret", rel, f"secret-like pattern: {label}"))

    for required in ("LICENSE", "THIRD_PARTY_NOTICES.md", "model_registry.json"):
        path = root / required
        if (tracked and required not in tracked_paths) or not path.is_file():
            findings.append(Finding("required_file", required, "required public-release file is missing"))

    license_path = root / "LICENSE"
    if license_path.is_file():
        license_text = license_path.read_text(encoding="utf-8", errors="replace")
        required_license_markers = (
            "GNU AFFERO GENERAL PUBLIC LICENSE",
            "Version 3, 19 November 2007",
            "How to Apply These Terms to Your New Programs",
        )
        for marker in required_license_markers:
            if marker not in license_text:
                findings.append(Finding("license", "LICENSE", f"missing official AGPL marker: {marker}"))
        if len(license_text) < 10_000:
            findings.append(Finding("license", "LICENSE", "license text is shorter than the complete AGPL-3.0 text review threshold"))

    registry_path = root / "model_registry.json"
    if registry_path.is_file():
        try:
            payload = json.loads(registry_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
                findings.append(Finding("provenance", "model_registry.json", "registry must contain a models list"))
        except (OSError, json.JSONDecodeError) as exc:
            findings.append(Finding("provenance", "model_registry.json", f"invalid JSON: {exc}"))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a sanitized public source tree.")
    parser.add_argument("--root", type=Path, default=ROOT, help="Candidate repository root.")
    parser.add_argument(
        "--tracked",
        action="store_true",
        help="Validate exactly the paths returned by git ls-files; ignore untracked local runtime/cache files.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output.")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        print(json.dumps({"valid": False, "error": f"root does not exist: {root}"}))
        return 2
    findings = scan(root, tracked=args.tracked)
    try:
        files_scanned = len(candidate_files(root, tracked=args.tracked))
    except (OSError, subprocess.CalledProcessError):
        files_scanned = 0
    result = {
        "valid": not findings,
        "validation_scope": "tracked" if args.tracked else "worktree",
        "files_scanned": files_scanned,
        "finding_count": len(findings),
        "findings": [asdict(item) for item in findings],
    }
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif findings:
        print("PUBLIC_RELEASE_VALIDATION: FAIL")
        for finding in findings:
            print(f"[{finding.category}] {finding.path}: {finding.detail}")
    else:
        print(f"PUBLIC_RELEASE_VALIDATION: PASS ({result['files_scanned']} files scanned)")
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
