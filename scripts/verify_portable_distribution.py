"""Verify a built TVA portable ZIP without importing the application runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

try:
    from scripts.portable.archive_safety import safe_zip_infos
    from scripts.portable.privacy import (
        PRIVATE_MARKERS,
        SECRET_PATTERNS,
        _private_path_pattern,
        current_private_roots,
        packaged_provenance_absolute_path_violations,
        scan_payloads,
    )
    from scripts.build_portable_distribution import (
        MODEL_EXTENSIONS,
        verify_license_notice_contract,
    )
except ModuleNotFoundError:  # direct execution: Python places scripts/ on sys.path
    from portable.archive_safety import safe_zip_infos  # type: ignore[no-redef]
    from portable.privacy import (  # type: ignore[no-redef]
        PRIVATE_MARKERS,
        SECRET_PATTERNS,
        _private_path_pattern,
        current_private_roots,
        packaged_provenance_absolute_path_violations,
        scan_payloads,
    )
    from build_portable_distribution import (  # type: ignore[no-redef]
        MODEL_EXTENSIONS,
        verify_license_notice_contract,
    )


TEXT_EXTENSIONS = {
    ".bat",
    ".cmd",
    ".json",
    ".txt",
    ".md",
    ".html",
    ".js",
    ".css",
    ".py",
    ".yaml",
    ".yml",
}
FORBIDDEN_TOOLS = {"node.exe", "npm", "npm.cmd", "pnpm", "pnpm.cmd", "vite", "git.exe"}
MANIFEST_NAME = "PACKAGE_MANIFEST.json"
GIT_SHA1_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    return safe_zip_infos(archive)


def _is_git_sha1(value: str) -> bool:
    return isinstance(value, str) and bool(GIT_SHA1_RE.fullmatch(value))


def verify_zip(
    zip_path: Path,
    *,
    expected_source_sha: str,
    accepted_baseline_sha: str,
    forbidden_markers: list[str] | None = None,
    forbidden_roots: list[str] | None = None,
    forbidden_emails: list[str] | None = None,
) -> dict[str, Any]:
    caller_violations = []
    if not _is_git_sha1(expected_source_sha):
        caller_violations.append("caller_expected_source_sha_invalid")
    if not _is_git_sha1(accepted_baseline_sha):
        caller_violations.append("caller_accepted_baseline_sha_invalid")
    if caller_violations:
        return {
            "status": "BLOCKED",
            "zip": str(zip_path),
            "violations": caller_violations,
            "privacy_hits": [],
        }

    violations: list[str] = []
    private_path_re = _private_path_pattern()
    private_markers = tuple(dict.fromkeys((*PRIVATE_MARKERS, *(forbidden_markers or []))))
    current_roots = tuple(current_private_roots())
    hits: list[dict[str, object]] = []
    notices: list[dict[str, object]] = []
    with zipfile.ZipFile(zip_path) as archive:
        members = safe_members(archive)
        by_name = {info.filename: info for info in members}
        if MANIFEST_NAME not in by_name:
            raise ValueError("manifest_missing")
        manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
        if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
            raise ValueError("manifest_schema_invalid")
        expected = {str(item["path"]): item for item in manifest["files"]}
        actual = set(by_name) - {MANIFEST_NAME}
        if actual != set(expected):
            violations.append("manifest_file_set_mismatch")

        def zip_payloads() -> Iterator[tuple[str, bytes]]:
            for info in members:
                name = info.filename
                parts = {part.casefold() for part in PurePosixPath(name).parts}
                lower_name = name.casefold()
                if ".git" in parts or "node_modules" in parts or "refs/codex" in lower_name:
                    violations.append(f"forbidden_path:{name}")
                if (
                    PurePosixPath(name).suffix.lower() in MODEL_EXTENSIONS
                    and name != "models/yolo11n.pt"
                ):
                    violations.append(f"unapproved_model:{name}")
                if Path(name).name.casefold() in FORBIDDEN_TOOLS:
                    violations.append(f"forbidden_tool:{name}")
                payload = archive.read(info)
                if name != MANIFEST_NAME and name in expected:
                    item = expected[name]
                    if int(item.get("size", -1)) != len(payload):
                        violations.append(f"manifest_size_mismatch:{name}")
                    if str(item.get("sha256", "")) != sha256_bytes(payload):
                        violations.append(f"manifest_hash_mismatch:{name}")
                    if ".env" in Path(name).name.casefold():
                        violations.append(f"environment_file:{name}")
                yield name, payload

        payload_violations, payload_hits, payload_notices = scan_payloads(
            zip_payloads(),
            private_markers=private_markers,
            private_roots=forbidden_roots or [],
            current_roots=current_roots,
            private_emails=forbidden_emails or [],
            private_path_pattern=private_path_re,
            secret_patterns=SECRET_PATTERNS,
        )
        violations.extend(payload_violations)
        hits.extend(payload_hits)
        notices.extend(payload_notices)

        provenance = json.loads(archive.read("PACKAGE_PROVENANCE.json").decode("utf-8"))
        if not isinstance(provenance, dict):
            violations.append("provenance_schema_invalid")
            provenance = {}
        violations.extend(packaged_provenance_absolute_path_violations(provenance))
        source = provenance.get("source", {})
        gates = provenance.get("gates", {})
        qualification_package = (
            source.get("build_mode") == "QUALIFICATION"
            or source.get("qualifiable") is True
            or "QUALIFICATION" in zip_path.name.upper()
        )
        required_gates = [
            "source_qualification",
            "locked_runtime",
            "native_closure",
            "no_node_runtime",
            "privacy",
            "manifest",
        ]
        license_notice_validation = None
        if qualification_package:
            if provenance.get("license_inventory") != "THIRD_PARTY_LICENSES.json":
                violations.append("qualification_license_inventory_invalid")
            required_gates.extend(
                (
                    "application_license",
                    "license_completeness",
                    "frontend_runtime_licenses",
                    "nvidia_notice_inventory",
                )
            )
            license_notice_validation = verify_license_notice_contract(
                set(by_name) - {MANIFEST_NAME},
                lambda path: archive.read(by_name[path]),
            )
            violations.extend(license_notice_validation["violations"])
        for gate in required_gates:
            if gates.get(gate) != "PASS":
                violations.append(f"provenance_gate_not_pass:{gate}")
        if source.get("public_corresponding_source_sync") != "PENDING":
            violations.append("public_sync_not_pending")
        if gates.get("office_pc_uat") != "NOT_RUN":
            violations.append("office_uat_status_not_explicit")
        if not source.get("head_sha") or not source.get("base_sha"):
            violations.append("source_sha_missing")
        if source.get("head_sha") != expected_source_sha:
            violations.append("source_head_sha_not_expected")
        if source.get("expected_source_sha") != expected_source_sha:
            violations.append("provenance_expected_source_sha_not_expected")
        if source.get("base_sha") != accepted_baseline_sha:
            violations.append("source_base_sha_not_accepted")
        if source.get("accepted_baseline_sha") != accepted_baseline_sha:
            violations.append("provenance_accepted_baseline_sha_not_accepted")
        if source.get("build_mode") != "QUALIFICATION" or source.get("qualifiable") is not True:
            violations.append("source_qualification_not_auditable")
        if source.get("expected_source_sha") != source.get("head_sha"):
            violations.append("expected_source_sha_mismatch")
        if source.get("accepted_baseline_sha") != source.get("base_sha"):
            violations.append("accepted_baseline_sha_mismatch")
        if source.get("baseline_is_ancestor") is not True:
            violations.append("accepted_baseline_ancestor_unproven")

        return {
            "status": "PASS" if not violations else "BLOCKED",
            "zip": str(zip_path),
            "members": len(members),
            "manifest_file_count": manifest.get("file_count"),
            "manifest_uncompressed_bytes": manifest.get("uncompressed_bytes"),
            "violations": list(dict.fromkeys(violations)),
            "privacy_hits": hits,
            "privacy_notices": notices,
            "privacy_heuristic_hits": notices,
            "license_notice_validation": license_notice_validation,
            "source_head_sha": source.get("head_sha"),
            "source_base_sha": source.get("base_sha"),
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify a TVA portable ZIP manifest and privacy contract."
    )
    parser.add_argument("zip", type=Path)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--accepted-baseline-sha", required=True)
    parser.add_argument("--forbidden-marker", action="append", default=[])
    parser.add_argument("--forbidden-root", action="append", default=[])
    parser.add_argument("--forbidden-email", action="append", default=[])
    args = parser.parse_args()
    try:
        result = verify_zip(
            args.zip.resolve(),
            expected_source_sha=args.expected_source_sha,
            accepted_baseline_sha=args.accepted_baseline_sha,
            forbidden_markers=args.forbidden_marker,
            forbidden_roots=args.forbidden_root,
            forbidden_emails=args.forbidden_email,
        )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
    ) as exc:
        result = {"status": "BLOCKED", "zip": str(args.zip), "violations": [str(exc)]}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
