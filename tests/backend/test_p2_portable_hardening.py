from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import stat
import struct
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.build_portable_distribution as portable_distribution
from scripts.build_portable_distribution import BuildError, extract_zip, privacy_scan
from scripts.portable import launcher
from scripts.portable.native_closure import scan_native_closure
from scripts.portable.runtime_lock import (
    RuntimeLockError,
    install_locked_runtime,
    load_runtime_lock,
    verify_installed_records,
    verify_wheelhouse,
)
from scripts.portable.process_ownership import ProcessIdentity
from scripts.verify_portable_distribution import safe_members, verify_zip


def _minimal_pe(
    *,
    machine: int = 0x8664,
    imports: str | None = None,
    import_rva: int = 0x1100,
    optional_size: int = 0xF0,
) -> bytes:
    """Create a small PE32+ image with enough structure for closure tests."""

    data = bytearray(0x800)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80 : 0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x84, machine)
    struct.pack_into("<H", data, 0x86, 1)
    struct.pack_into("<H", data, 0x94, optional_size)
    optional_offset = 0x98
    if optional_size >= 2:
        struct.pack_into("<H", data, optional_offset, 0x20B)
    if optional_size >= 32:
        struct.pack_into("<Q", data, optional_offset + 24, 0x140000000)
    if optional_size >= 112:
        struct.pack_into("<I", data, optional_offset + 108, 16)
        if imports is not None:
            struct.pack_into("<II", data, optional_offset + 120, import_rva, 40)
    section_offset = optional_offset + optional_size
    if section_offset + 40 <= len(data):
        data[section_offset : section_offset + 8] = b".rdata\0\0"
        struct.pack_into("<I", data, section_offset + 8, 0x600)
        struct.pack_into("<I", data, section_offset + 12, 0x1000)
        struct.pack_into("<I", data, section_offset + 16, 0x600)
        struct.pack_into("<I", data, section_offset + 20, 0x200)
    if imports is not None and optional_size >= 112 and import_rva == 0x1100:
        descriptor_offset = 0x200 + 0x100
        struct.pack_into("<IIII", data, descriptor_offset, 0, 0, 0, 0x1140)
        struct.pack_into("<I", data, descriptor_offset + 16, 0x1160)
        data[0x200 + 0x140 : 0x200 + 0x140 + len(imports) + 1] = imports.encode() + b"\0"
        # A valid x64 FirstThunk must at least map one eight-byte entry.
        struct.pack_into("<Q", data, 0x200 + 0x160, 0)
    return bytes(data)


def _write_pe(path: Path, **kwargs: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_minimal_pe(**kwargs))


def test_native_closure_blocks_malformed_pe_and_exposes_parse_failure(tmp_path: Path) -> None:
    malformed = tmp_path / "app.exe"
    malformed.write_bytes(b"MZ\x00\x00truncated")

    result = scan_native_closure(tmp_path)

    assert result["status"] == "BLOCKED"
    assert result["parse_failures"] == [
        {
            "path": "app.exe",
            "status": "PARSE_FAILED",
            "error_code": "dos_header_truncated",
            "detail": None,
        }
    ]
    assert result["no_imports"] == []


def test_native_closure_blocks_truncated_optional_header(tmp_path: Path) -> None:
    (tmp_path / "truncated.dll").write_bytes(
        _minimal_pe(optional_size=100)[: 0x98 + 100]
    )

    result = scan_native_closure(tmp_path)

    assert result["status"] == "BLOCKED"
    assert result["parse_failures"][0]["error_code"] == "optional_header_truncated"


def test_native_closure_blocks_invalid_import_rva(tmp_path: Path) -> None:
    _write_pe(tmp_path / "invalid.exe", imports="dependency.dll", import_rva=0x9000)

    result = scan_native_closure(tmp_path)

    assert result["status"] == "BLOCKED"
    assert result["parse_failures"][0]["error_code"] == "import_directory_rva_invalid"


def test_native_closure_blocks_invalid_delay_import_mapping(tmp_path: Path) -> None:
    data = bytearray(_minimal_pe())
    optional_offset = 0x98
    delay_directory_offset = optional_offset + 112 + (13 * 8)
    struct.pack_into("<II", data, delay_directory_offset, 0x1100, 32)
    struct.pack_into("<I", data, 0x200 + 0x100, 2)
    (tmp_path / "delay.dll").write_bytes(data)

    result = scan_native_closure(tmp_path)

    assert result["status"] == "BLOCKED"
    assert result["parse_failures"][0]["error_code"] == "delay_import_descriptor_attributes_invalid"


@pytest.mark.parametrize("machine", [0, 0x014C])
def test_native_closure_blocks_unknown_or_non_x64_machine(tmp_path: Path, machine: int) -> None:
    _write_pe(tmp_path / "machine.dll", machine=machine)

    result = scan_native_closure(tmp_path)

    assert result["status"] == "BLOCKED"
    assert "machine.dll" in result["non_x64"]
    if machine == 0:
        assert "machine.dll" in result["unknown_architecture"]


def test_valid_pe_without_import_directory_is_not_a_parse_failure(tmp_path: Path) -> None:
    _write_pe(tmp_path / "no-imports.dll")

    result = scan_native_closure(tmp_path)

    assert result["status"] == "PASS"
    assert result["parse_failures"] == []
    assert result["no_imports"] == ["no-imports.dll"]


def test_package_global_matching_cannot_resolve_unreachable_dependency(tmp_path: Path) -> None:
    _write_pe(tmp_path / "app" / "importer.exe", imports="dependency.dll")
    _write_pe(tmp_path / "unreachable" / "dependency.dll")

    result = scan_native_closure(tmp_path)

    assert result["status"] == "BLOCKED"
    dependency = result["unresolved"][0]
    assert dependency["state"] == "missing"
    assert dependency["candidate_paths"] == []


def test_same_name_dll_collision_is_ambiguous_and_blocked(tmp_path: Path) -> None:
    _write_pe(tmp_path / "app" / "importer.exe", imports="dependency.dll")
    _write_pe(tmp_path / "app" / "dependency.dll")
    _write_pe(tmp_path / "runtime" / "python" / "dependency.dll")

    result = scan_native_closure(tmp_path)

    assert result["status"] == "BLOCKED"
    dependency = result["unresolved"][0]
    assert dependency["state"] == "ambiguous"
    assert set(dependency["candidate_paths"]) == {
        "app/dependency.dll",
        "runtime/python/dependency.dll",
    }


def test_python_extension_uses_preloaded_embedded_runtime_root(tmp_path: Path) -> None:
    extension = tmp_path / "runtime" / "python" / "Lib" / "site-packages" / "extension"
    _write_pe(extension / "importer.pyd", imports="dependency.dll")
    _write_pe(extension / "dependency.dll")
    _write_pe(tmp_path / "runtime" / "python" / "dependency.dll")

    result = scan_native_closure(tmp_path)

    assert result["status"] == "PASS"
    dependency = result["imports"][0]
    assert dependency["state"] == "package_local"
    assert dependency["resolved_path"] == "runtime/python/dependency.dll"
    assert dependency["resolution_kind"] == "preloaded_application_runtime_directory"
    assert set(dependency["candidate_paths"]) == {
        "runtime/python/Lib/site-packages/extension/dependency.dll",
        "runtime/python/dependency.dll",
    }


def test_python_extension_uses_preloaded_pytorch_runtime_root(tmp_path: Path) -> None:
    extension = tmp_path / "runtime" / "python" / "Lib" / "site-packages" / "torchvision"
    torch_lib = tmp_path / "runtime" / "python" / "Lib" / "site-packages" / "torch" / "lib"
    _write_pe(extension / "importer.pyd", imports="dependency.dll")
    _write_pe(extension / "dependency.dll")
    _write_pe(torch_lib / "dependency.dll")

    result = scan_native_closure(tmp_path)

    assert result["status"] == "PASS"
    dependency = result["imports"][0]
    assert dependency["state"] == "package_local"
    assert dependency["resolved_path"] == (
        "runtime/python/Lib/site-packages/torch/lib/dependency.dll"
    )
    assert dependency["resolution_kind"] == "preloaded_pytorch_runtime_dll_directory"


def test_unmodeled_python_extension_collision_remains_ambiguous(tmp_path: Path) -> None:
    extension = tmp_path / "runtime" / "python" / "Lib" / "site-packages" / "extension"
    extra = tmp_path / "runtime" / "python" / "extra"
    _write_pe(extension / "importer.pyd", imports="dependency.dll")
    _write_pe(extension / "dependency.dll")
    _write_pe(extra / "dependency.dll")

    result = scan_native_closure(tmp_path, dll_search_roots=[extra])

    assert result["status"] == "BLOCKED"
    dependency = result["unresolved"][0]
    assert dependency["state"] == "ambiguous"


def test_native_failure_report_retains_resolution_evidence() -> None:
    unresolved = {
        "importer": "runtime/python/Lib/site-packages/extension/_C.pyd",
        "dependency": "dependency.dll",
        "state": "ambiguous",
        "candidate_paths": ["one/dependency.dll", "two/dependency.dll"],
        "resolution_kind": None,
    }
    details = portable_distribution.native_failure_diagnostics(
        {
            "schema_version": "native-closure-v2",
            "status": "BLOCKED",
            "files_scanned": 2,
            "unresolved": [unresolved],
            "parse_failures": [],
            "unknown_architecture": [],
            "non_x64": [],
            "reachability_model": {"global_filename_matching": False},
        }
    )

    assert details["unresolved"] == [unresolved]
    assert details["reachability_model"]["global_filename_matching"] is False


def test_host_optional_nvcuda_does_not_require_a_package_file(tmp_path: Path) -> None:
    _write_pe(tmp_path / "runtime" / "python" / "importer.exe", imports="nvcuda.dll")

    result = scan_native_closure(tmp_path)

    assert result["status"] == "PASS"
    assert result["imports"][0]["state"] == "host_optional_driver"


def _archive(tmp_path: Path, names: list[str], *, symlink: bool = False) -> Path:
    archive_path = tmp_path / "malicious.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for index, name in enumerate(names):
            info = zipfile.ZipInfo(name)
            if symlink and index == 0:
                info.create_system = 3
                info.external_attr = 0o120777 << 16
            archive.writestr(info, b"payload")
    return archive_path


def _fake_lock_entry(name: str = "fixture-runtime", version: str = "1.0.0") -> dict[str, object]:
    normalized = name.replace("_", "-").casefold()
    return {
        "name": name,
        "normalized_name": normalized,
        "version": version,
        "wheel_filename": f"{name.replace('-', '_')}-{version}-py3-none-any.whl",
        "sha256": "0" * 64,
        "source_index": "pypi",
        "license": "MIT",
        "license_expression": "MIT",
        "license_files": ["LICENSE"],
        "license_metadata_source": "wheel METADATA",
    }


def _write_fake_wheel(
    wheelhouse: Path, entry: dict[str, object], *, body: bytes = b"value = 1\n"
) -> Path:
    wheel_path = wheelhouse / str(entry["wheel_filename"])
    dist_info = f"{entry['normalized_name'].replace('-', '_')}-{entry['version']}.dist-info"
    package_path = f"{entry['normalized_name'].replace('-', '_')}.py"
    metadata = (
        f"Metadata-Version: 2.3\nName: {entry['name']}\nVersion: {entry['version']}\n"
        "License: MIT\nLicense-Expression: MIT\nLicense-File: LICENSE\n"
    ).encode()
    wheel_metadata = (
        b"Wheel-Version: 1.0\nGenerator: p2-test\nRoot-Is-Purelib: true\n"
        b"Tag: py3-none-any\n"
    )
    files = {
        package_path: body,
        f"{dist_info}/METADATA": metadata,
        f"{dist_info}/WHEEL": wheel_metadata,
        f"{dist_info}/LICENSE": b"MIT\n",
    }
    rows: list[list[str]] = []
    for filename, payload in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode().rstrip("=")
        rows.append([filename, f"sha256={digest}", str(len(payload))])
    rows.append([f"{dist_info}/RECORD", "", ""])
    record = io.StringIO()
    csv.writer(record, lineterminator="\n").writerows(rows)
    files[f"{dist_info}/RECORD"] = record.getvalue().encode()
    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename, payload in files.items():
            archive.writestr(filename, payload)
    entry["sha256"] = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
    return wheel_path


def _fake_lock(entry: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "portable-python-runtime-lock-v1",
        "target": {
            "implementation": "CPython",
            "python_version": "3.12",
            "platform": "win_amd64",
            "abi": "cp312",
        },
        "policy": {
            "require_hashes": True,
            "only_binary": True,
            "no_index": True,
            "no_source_distributions": True,
            "no_unlisted_distributions": True,
            "record_validation": "every installed file must be covered by its wheel RECORD",
        },
        "sources": {
            "pypi": "https://pypi.org/simple/",
            "pytorch_cu128": "https://download.pytorch.org/whl/cu128/",
        },
        "distributions": [entry],
    }


@pytest.mark.parametrize(
    "member",
    [
        "../outside",
        r"..\outside",
        r"C:\outside",
        r"\\server\share",
        "C:ads",
        "CON",
        "con.txt",
        r"nested\NUL",
        "COM1",
        "normal.",
    ],
)
def test_windows_archive_members_reject_escape_forms(tmp_path: Path, member: str) -> None:
    archive = _archive(tmp_path, [member])

    with pytest.raises(BuildError):
        extract_zip(archive, tmp_path / "extract")
    with zipfile.ZipFile(archive) as handle, pytest.raises(ValueError):
        safe_members(handle)


@pytest.mark.parametrize(
    "members",
    [
        ["one/file.txt", r"one\file.txt"],
        ["Folder/File.txt", "folder/file.txt"],
        ["duplicate.txt", "duplicate.txt"],
    ],
)
def test_windows_archive_members_reject_normalized_duplicates(
    tmp_path: Path, members: list[str]
) -> None:
    archive = _archive(tmp_path, members)

    with pytest.raises(BuildError):
        extract_zip(archive, tmp_path / "extract")
    with zipfile.ZipFile(archive) as handle, pytest.raises(ValueError):
        safe_members(handle)


def test_windows_archive_members_reject_symlink(tmp_path: Path) -> None:
    archive = _archive(tmp_path, ["link"], symlink=True)

    with pytest.raises(BuildError):
        extract_zip(archive, tmp_path / "extract")


def test_privacy_scan_inspects_binary_payloads_and_utf16_markers(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "native.pyd").write_bytes(b"prefix\x00github_pat_binary_secret\x00")
    (package / "wide.dll").write_bytes("builder-private-marker".encode("utf-16le"))

    result = privacy_scan(
        package,
        forbidden_markers=["builder-private-marker", "github_pat_binary_secret"],
    )

    assert result["status"] == "BLOCKED"
    assert {hit["classification"] for hit in result["hits"]} == {
        "private_marker",
        "private_marker",
    }
    assert {hit["path"] for hit in result["hits"]} == {"native.pyd", "wide.dll"}


def test_privacy_scan_does_not_block_generic_credentials_in_binary_payloads(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "ascii.bin").write_bytes((b'api_key=' + b'fixture-key-0123456789'))
    (package / "wide.bin").write_bytes("token:fixture-token-0123456789".encode("utf-16le"))

    result = privacy_scan(package)

    assert result["status"] == "PASS"
    assert result["hits"] == []


def test_privacy_policy_contract_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USERNAME", "current-user")
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.setenv("USERPROFILE", ('C:\\Users\\c' + 'urrent-user'))

    def scan(relative: str, payload: bytes, **kwargs: object) -> dict:
        package = tmp_path / relative.replace("/", "_")
        package.mkdir(parents=True)
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return privacy_scan(package, **kwargs)

    assert scan("private.txt", (b'C:\\Users\\current' + b'-user\\private.txt'))["status"] == "BLOCKED"
    assert scan("private-wide.txt", ('C:\\Users\\current' + '-user\\private.txt').encode("utf-16le"))["status"] == "BLOCKED"
    assert scan("native.bin", (b'prefix\x00C:\\Users\\c' + b'urrent-user\x00suffix'))["status"] == "BLOCKED"
    assert scan("markers.txt", b"refs/codex")["status"] == "BLOCKED"
    assert scan("ghp.bin", b"ghp_" + b"a" * 36)["status"] == "BLOCKED"
    assert scan("private.pem", b"-----BEGIN PRIVATE KEY-----")["status"] == "BLOCKED"
    assert scan(
        "private.bin",
        b"MZ\x00-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\x00",
    )["status"] == "BLOCKED"
    assert scan("explicit.bin", b"caller-marker", forbidden_markers=["caller-marker"])["status"] == "BLOCKED"
    assert scan("explicit-root.bin", b"D:\\private-build\\x", forbidden_roots=[r"D:\private-build"])["status"] == "BLOCKED"
    assert scan("explicit-email.txt", (b'operator@' + b'example.invalid'), forbidden_emails=[('operator@' + 'example.invalid')])["status"] == "BLOCKED"
    assert scan("incidental.dll", b"\x00current-user\x00")["status"] == "PASS"
    assert scan("runtime/python/Lib/site-packages/vendor.py", b"name = 'current-user'\n")["status"] == "PASS"
    assert scan("token.dll", b"MZ\x00token=abcdefghijklmnop\x00")["status"] == "PASS"
    assert scan("app/config.py", b"token=abcdefghijklmnop\n")["status"] == "BLOCKED"
    vendor = scan(
        "runtime/python/Lib/site-packages/vendor-secret.py",
        b"token=abcdefghijklmnop\n",
    )
    assert vendor["status"] == "PASS"
    assert vendor["heuristic_hits"]
    assert scan("cuda-like.dll", b"MZ\x00secret=abcdefghijklmnop\x00token=qrstuvwxyzabcdef\x00")["status"] == "PASS"


def test_high_confidence_credential_signatures_block_in_binary_payloads(tmp_path: Path) -> None:
    signatures = {
        "aws.bin": b"AKIA" + b"A" * 16,
        "stripe.bin": b"sk-live-" + b"A" * 16,
        "slack.bin": b"xoxb-" + b"A" * 16,
        "github-pat.bin": b"github_pat_" + b"A" * 22,
    }
    for name, payload in signatures.items():
        package = tmp_path / name
        package.mkdir()
        (package / "native.bin").write_bytes(b"MZ\x00" + payload + b"\x00")
        assert privacy_scan(package)["status"] == "BLOCKED"


def test_builder_and_zip_verifier_share_privacy_disposition(tmp_path: Path) -> None:
    package = tmp_path / "TrafficVideoAnalytics"
    vendor = package / "runtime" / "python" / "Lib" / "site-packages" / "vendor.py"
    vendor.parent.mkdir(parents=True)
    vendor.write_text("token=abcdefghijklmnop\n", encoding="utf-8")
    provenance = {
        "source": {
            "head_sha": "a" * 40,
            "base_sha": "b" * 40,
            "expected_source_sha": "a" * 40,
            "accepted_baseline_sha": "b" * 40,
            "baseline_is_ancestor": True,
            "build_mode": "QUALIFICATION",
            "qualifiable": True,
            "public_corresponding_source_sync": "PENDING",
        },
        "gates": {
            "source_qualification": "PASS",
            "locked_runtime": "PASS",
            "native_closure": "PASS",
            "no_node_runtime": "PASS",
            "privacy": "PASS",
            "manifest": "PASS",
            "office_pc_uat": "NOT_RUN",
        },
    }
    (package / "PACKAGE_PROVENANCE.json").write_text(json.dumps(provenance), encoding="utf-8")
    manifest = {
        "excluded_from_manifest": ["PACKAGE_MANIFEST.json"],
        "files": portable_distribution.package_file_records(package, {"PACKAGE_MANIFEST.json"}),
    }
    (package / "PACKAGE_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    archive_path = tmp_path / "package.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in package.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(package).as_posix())

    builder_result = privacy_scan(package)
    verifier_result = verify_zip(
        archive_path,
        expected_source_sha="a" * 40,
        accepted_baseline_sha="b" * 40,
    )
    assert builder_result["status"] == "PASS"
    assert verifier_result["status"] == "BLOCKED"
    assert verifier_result["license_notice_validation"] is not None
    assert not verifier_result["privacy_hits"]
    assert len(builder_result["heuristic_hits"]) == len(verifier_result["privacy_heuristic_hits"])


def test_privacy_scan_uses_supplied_roots_and_emails_without_generic_user_rejection(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "vendor.dll").write_bytes((b'C:\\Users\\upstre' + b'am\\ci\\build.pdb'))
    assert privacy_scan(package)["status"] == "PASS"
    (package / "private.bin").write_bytes(
        ('D:\\private-build\\source\noperator@' + 'example.invalid').encode("utf-16le")
    )

    result = privacy_scan(
        package,
        forbidden_roots=[r"D:\private-build"],
        forbidden_emails=[('operator@' + 'example.invalid')],
    )

    assert result["status"] == "BLOCKED"
    assert {hit["classification"] for hit in result["hits"]} == {
        "private_marker",
        "private_email",
    }


def test_spawn_child_cleans_direct_process_when_identity_recording_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeProcess:
        pid = 1234

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            terminated.append(self.pid)

        def wait(self, timeout: float) -> int:
            return 0

    fake_process = FakeProcess()
    terminated: list[int] = []
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *args, **kwargs: fake_process)
    monkeypatch.setattr(launcher, "logs_dir", lambda root: tmp_path / "logs")
    monkeypatch.setattr(launcher, "process_identity", lambda pid: None)

    with pytest.raises(launcher.LauncherError, match="process_identity_unavailable"):
        launcher.spawn_child(
            root=tmp_path,
            role="backend",
            command=["python.exe", "-m", "app"],
            port=8000,
            environment={},
            owner_token="token",
        )

    assert terminated == [1234]


def test_spawn_child_cleans_direct_process_when_record_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeProcess:
        pid = 1235

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            terminated.append(self.pid)

        def wait(self, timeout: float) -> int:
            return 0

    terminated: list[int] = []
    identity = ProcessIdentity(1235, tmp_path / "python.exe", 42)
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(launcher, "process_identity", lambda pid: identity)
    monkeypatch.setattr(launcher, "process_command_line", lambda pid: "python.exe -m app")
    monkeypatch.setattr(
        launcher,
        "write_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("record disk failure")),
    )

    with pytest.raises(OSError, match="record disk failure"):
        launcher.spawn_child(
            root=tmp_path,
            role="backend",
            command=["python.exe", "-m", "app"],
            port=8000,
            environment={},
            owner_token="token",
        )

    assert terminated == [1235]


def test_startup_blocks_when_owned_cleanup_cannot_be_proven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "TrafficVideoAnalytics"
    (package / "runtime" / "python").mkdir(parents=True)
    (package / "runtime" / "python" / "python.exe").write_bytes(b"python")
    (package / "app").mkdir()
    (package / "frontend" / "dist").mkdir(parents=True)
    (package / "frontend" / "dist" / "index.html").write_text("<html />", encoding="utf-8")
    monkeypatch.setattr(launcher, "duplicate_instance", lambda root: False)
    monkeypatch.setattr(launcher, "port_open", lambda port: False)
    monkeypatch.setattr(launcher, "build_environment", lambda root, token=None: {"TVA_OWNER_TOKEN": "token"})
    monkeypatch.setattr(
        launcher,
        "spawn_child",
        lambda **kwargs: (_ for _ in ()).throw(launcher.LauncherError("spawn failed")),
    )
    monkeypatch.setattr(launcher, "stop_owned", lambda root: False)

    with pytest.raises(launcher.LauncherError, match="startup_cleanup_unsafe"):
        launcher.run_instance(package, no_browser=True)


def test_source_runtime_lock_is_hash_locked_and_complete() -> None:
    lock_path = Path("packages/portable/windows-x64-cp312-runtime.lock.json")
    lock = load_runtime_lock(lock_path)

    assert len(lock["distributions"]) == 61
    assert lock["policy"]["require_hashes"] is True
    assert {item["source_index"] for item in lock["distributions"]} == {
        "pypi",
        "pytorch_cu128",
    }
    torch = next(item for item in lock["distributions"] if item["name"] == "torch")
    vision = next(item for item in lock["distributions"] if item["name"] == "torchvision")
    assert torch["version"] == "2.10.0+cu128"
    assert vision["version"] == "0.25.0+cu128"
    assert torch["source_index"] == vision["source_index"] == "pytorch_cu128"


def test_source_runtime_lock_rejects_non_cu128_torch_source(tmp_path: Path) -> None:
    lock_path = Path("packages/portable/windows-x64-cp312-runtime.lock.json")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    next(item for item in lock["distributions"] if item["name"] == "torch")["source_index"] = "pypi"
    tampered = tmp_path / lock_path.name
    tampered.write_text(json.dumps(lock), encoding="utf-8")

    with pytest.raises(RuntimeLockError, match="cuda_wheel_invalid:torch"):
        load_runtime_lock(tampered)


def test_qualification_source_state_requires_exact_attestations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    head = "a" * 40
    baseline = "b" * 40
    values = {
        ("rev-parse", "HEAD"): head,
        ("status", "--porcelain=v1", "--untracked-files=all"): "",
        ("rev-parse", "--verify", f"{baseline}^{{commit}}"): baseline,
        ("branch", "--show-current"): "codex/p2",
    }

    def fake_git_output(source: Path, *args: str) -> str:
        return values.get(tuple(args), "")

    monkeypatch.setattr(portable_distribution, "git_output", fake_git_output)
    monkeypatch.setattr(portable_distribution, "_git_is_ancestor", lambda *args: True)

    with pytest.raises(BuildError, match="expected_source_sha_invalid"):
        portable_distribution.source_state(tmp_path, "qualification")
    with pytest.raises(BuildError, match="source_sha_mismatch"):
        portable_distribution.source_state(tmp_path, "qualification", "c" * 40, baseline)

    state = portable_distribution.source_state(tmp_path, "qualification", head, baseline)
    assert state["build_mode"] == "QUALIFICATION"
    assert state["qualifiable"] is True
    assert state["base_sha"] == baseline

    values[("status", "--porcelain=v1", "--untracked-files=all")] = " M source.py"
    with pytest.raises(BuildError, match="qualification_worktree_dirty"):
        portable_distribution.source_state(tmp_path, "qualification", head, baseline)

    values[("status", "--porcelain=v1", "--untracked-files=all")] = ""
    monkeypatch.setattr(portable_distribution, "_git_is_ancestor", lambda *args: False)
    with pytest.raises(BuildError, match="accepted_baseline_not_ancestor"):
        portable_distribution.source_state(tmp_path, "qualification", head, baseline)


def test_development_source_state_is_explicitly_non_qualifiable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    head = "c" * 40

    def fake_git_output(source: Path, *args: str) -> str:
        if tuple(args) == ("rev-parse", "HEAD"):
            return head
        if tuple(args) == ("status", "--porcelain=v1", "--untracked-files=all"):
            return " M dirty.py"
        if tuple(args) == ("branch", "--show-current"):
            return "codex/p2"
        return ""

    monkeypatch.setattr(portable_distribution, "git_output", fake_git_output)
    state = portable_distribution.source_state(tmp_path, "development")

    assert state["build_mode"] == "DEVELOPMENT_ONLY"
    assert state["qualifiable"] is False
    assert state["working_tree_dirty"] is True
    assert state["base_sha"] is None


def test_locked_runtime_installs_only_wheelhouse_files_and_detects_record_tampering(
    tmp_path: Path,
) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    entry = _fake_lock_entry()
    _write_fake_wheel(wheelhouse, entry)
    lock = _fake_lock(entry)
    site_packages = tmp_path / "site-packages"

    report = install_locked_runtime(
        wheelhouse,
        site_packages,
        lock,
        python_executable=Path(sys.executable),
    )

    assert report["status"] == "PASS"
    assert report["record_verification"]["status"] == "PASS"
    assert (site_packages / "fixture_runtime.py").is_file()
    assert not (site_packages / "source_only.py").exists()

    (site_packages / "source_only.py").write_text("injected = True\n", encoding="ascii")
    with pytest.raises(RuntimeLockError, match="unrecorded_files"):
        verify_installed_records(site_packages, lock, wheelhouse=wheelhouse)


def test_locked_wheel_hash_mismatch_blocks_before_install(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    entry = _fake_lock_entry()
    wheel = _write_fake_wheel(wheelhouse, entry)
    lock = _fake_lock(entry)
    wheel.write_bytes(wheel.read_bytes() + b"tampered")

    with pytest.raises(RuntimeLockError, match="hash_mismatch"):
        verify_wheelhouse(wheelhouse, lock)


def test_locked_wheelhouse_rejects_unlisted_entries(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    entry = _fake_lock_entry()
    _write_fake_wheel(wheelhouse, entry)
    lock = _fake_lock(entry)
    (wheelhouse / "unlisted-1.0.0-py3-none-any.whl").write_bytes(b"unlisted")

    with pytest.raises(RuntimeLockError, match="unlisted_or_unsafe_entries"):
        verify_wheelhouse(wheelhouse, lock)


def _canonical_fixture(
    tmp_path: Path, *, extras: dict[str, bytes] | None = None,
    entrypoint: bool = False, empty_wheel_hash: bool = False,
) -> dict:
    root = tmp_path / "runtime" / "python"
    site = root / "Lib" / "site-packages"
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    entry = _fake_lock_entry()
    wheel = _write_fake_wheel(wheelhouse, entry)
    dist = "fixture_runtime-1.0.0.dist-info"
    with zipfile.ZipFile(wheel) as archive:
        files = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/RECORD")}
    files.update(extras or {})
    if entrypoint:
        files[f"{dist}/entry_points.txt"] = b"[console_scripts]\nfixture-cli = fixture_runtime:main\n"
    wheel_rows, installed_rows = [], []
    for name, payload in files.items():
        digest = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode().rstrip("=")
        digest = "" if empty_wheel_hash and name == "fixture_runtime.py" else digest
        wheel_rows.append([name, digest, str(len(payload))])
        target, record_name = name, name
        if ".data/" in name:
            scheme, target = name.split(".data/", 1)[1].split("/", 1)
            if scheme == "scripts":
                target = "bin/" + target
            elif scheme == "headers":
                target = "include/python/UNKNOWN/" + target
            record_name = target if scheme in {"purelib", "platlib"} else "../../" + target
        path = site / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        installed_rows.append([record_name, digest, str(len(payload))])
    wheel_rows.append([f"{dist}/RECORD", "", ""])
    buffer = io.StringIO()
    csv.writer(buffer).writerows(wheel_rows)
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
        archive.writestr(f"{dist}/RECORD", buffer.getvalue())
    entry["sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if entrypoint:
        from scripts.portable.runtime_lock import _verify_wrapper

        path = site / "bin/fixture-cli.exe"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"MZ synthetic installer launcher")
        main = (
            "# -*- coding: utf-8 -*-\nimport re\nimport sys\n"
            "from fixture_runtime import main\n"
            "if __name__ == '__main__':\n"
            "    sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])\n"
            "    sys.exit(main())\n"
        )
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr("__main__.py", main)
        _verify_wrapper(path, "fixture_runtime:main")
        payload = path.read_bytes()
        digest = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode().rstrip("=")
        installed_rows.append(["../../bin/fixture-cli.exe", digest, str(len(payload))])
    for name, payload in (("INSTALLER", b"pip\n"), ("REQUESTED", b"")):
        (site / dist / name).write_bytes(payload)
        installed_rows.append([f"{dist}/{name}", "", ""])
    installed_rows.append([f"{dist}/RECORD", "", ""])
    record = site / dist / "RECORD"
    with record.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(installed_rows)
    production = tmp_path / "production"
    for name in ("apps/backend", "apps/worker", "scripts/portable", "tools/ai_stack", "tools/benchmark"):
        (production / name).mkdir(parents=True, exist_ok=True)
    (production / "tools/__init__.py").write_text("# synthetic production source\n")
    (production / "tools/benchmark/__init__.py").write_text("# synthetic production source\n")
    for name in ("tools/benchmark/runtime.py", "scripts/database_backup.py"):
        (production / name).write_text("# synthetic production source\n")
    return {"site_packages": site, "lock": _fake_lock(entry), "wheelhouse": wheelhouse,
            "managed_root": root, "production_root": production, "prune": True}


def _edit_record(fixture: dict, edit: object) -> None:
    record = next(fixture["site_packages"].glob("*.dist-info/RECORD"))
    rows = list(csv.reader(record.read_text(encoding="utf-8").splitlines()))
    edit(rows)
    with record.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)


def test_canonical_normal_member_and_record_self(tmp_path: Path) -> None:
    fixture = _canonical_fixture(tmp_path)
    report = verify_installed_records(**fixture)
    assert report["status"] == "PASS"
    assert report["counts"]["unresolved_rows"] == 0
    assert report["counts"]["generated_metadata_rows"] == 3
    assert len(report["empty_hash_entries"]) == 1


@pytest.mark.parametrize("used", [False, True])
@pytest.mark.parametrize("artifact", ["wrapper", "manpage", "script"])
def test_canonical_prunes_only_proven_unused_schemes(tmp_path: Path, used: bool, artifact: str) -> None:
    member = {"manpage": "data/share/man/man1/fixture.1", "script": "scripts/fixture-tool"}
    extras = {"fixture_runtime-1.0.0.data/" + member[artifact]: b"non-runtime"} if artifact != "wrapper" else {}
    fixture = _canonical_fixture(tmp_path, extras=extras, entrypoint=artifact == "wrapper")
    target = {"wrapper": "bin/fixture-cli.exe", "manpage": "share/man/man1/fixture.1", "script": "bin/fixture-tool"}[artifact]
    record = next(fixture["site_packages"].glob("*.dist-info/RECORD"))
    original = record.read_bytes()
    if used:
        (fixture["production_root"] / "apps/backend/use.py").write_text(f'open("{target}")')
        with pytest.raises(RuntimeLockError, match="artifact_used"):
            verify_installed_records(**fixture)
        assert (fixture["site_packages"] / target).exists()
    else:
        report = verify_installed_records(**fixture)
        assert report["counts"]["pruned_artifacts"] == report["counts"]["relocations"] == 1
        assert not (fixture["site_packages"] / target).exists()
        assert record.read_bytes() == original
        assert any(row["action"] == "pruned_non_runtime" for row in report["files"])


@pytest.mark.parametrize("scheme", ["purelib", "platlib", "data", "headers"])
def test_canonical_materializes_scheme_members(tmp_path: Path, scheme: str) -> None:
    fixture = _canonical_fixture(tmp_path, extras={f"fixture_runtime-1.0.0.data/{scheme}/payload.txt": b"payload"})
    assert verify_installed_records(**fixture)["status"] == "PASS"
    target = "include/python/UNKNOWN/payload.txt" if scheme == "headers" else "payload.txt"
    assert (fixture["site_packages"] / target).read_bytes() == b"payload"


@pytest.mark.parametrize("alias", ["identical", "empty", "conflict", "size_conflict", "self"])
def test_canonical_alias_constraints(tmp_path: Path, alias: str) -> None:
    fixture = _canonical_fixture(tmp_path)
    def edit(rows: list) -> None:
        row = list(rows[-1] if alias == "self" else rows[0])
        row[0] = row[0].replace("/", "\\")
        if alias == "empty":
            row[1:] = ["", ""]
        if alias == "conflict":
            row[1] = "sha256=conflict"
        if alias == "size_conflict":
            row[2] = "999"
        rows.append(row)
    _edit_record(fixture, edit)
    if "conflict" in alias:
        with pytest.raises(RuntimeLockError, match="alias_conflict"):
            verify_installed_records(**fixture)
    else:
        report = verify_installed_records(**fixture)
        assert report["counts"]["alias_groups"] == 1
        assert report["counts"]["empty_hash_resolved_by_alias"] == int(alias == "empty")


@pytest.mark.parametrize("modified", [False, True])
def test_canonical_empty_hash_requires_locked_bytes(tmp_path: Path, modified: bool) -> None:
    fixture = _canonical_fixture(tmp_path, empty_wheel_hash=True)
    if modified:
        (fixture["site_packages"] / "fixture_runtime.py").write_bytes(b"value = 2\n")
        with pytest.raises(RuntimeLockError, match="locked_source_mismatch"):
            verify_installed_records(**fixture)
    else:
        report = verify_installed_records(**fixture)
        assert report["counts"]["empty_hash_resolved_by_wheel"] == 1


@pytest.mark.parametrize("member", ["fixture_runtime.py", "native.dll"])
def test_canonical_missing_runtime_member_fails(tmp_path: Path, member: str) -> None:
    fixture = _canonical_fixture(tmp_path, extras={"native.dll": b"synthetic DLL"})
    (fixture["site_packages"] / member).unlink()
    with pytest.raises(RuntimeLockError, match="file_missing"):
        verify_installed_records(**fixture)


@pytest.mark.parametrize("recorded", [False, True])
@pytest.mark.parametrize("filename", ["injected.exe", "injected.py"])
def test_canonical_unproven_actual_file_fails(tmp_path: Path, recorded: bool, filename: str) -> None:
    fixture = _canonical_fixture(tmp_path)
    (fixture["site_packages"] / filename).write_bytes(b"injected")
    if recorded:
        _edit_record(fixture, lambda rows: rows.append([filename, "", ""]))
    with pytest.raises(RuntimeLockError, match="integrity_unprovable|unrecorded_files"):
        verify_installed_records(**fixture)


@pytest.mark.parametrize("name", [
    "../../../outside.exe", "/absolute.exe", "C:/absolute.exe", "C:relative.exe",
    "\\\\server\\share\\file.exe", "//server/share/file.exe", "file.exe:stream", "NUL",
    "nested/CON/file", "trailing./file", "../bin/../file", "file\x00.exe",
])
def test_canonical_rejects_unsafe_paths(tmp_path: Path, name: str) -> None:
    fixture = _canonical_fixture(tmp_path)
    _edit_record(fixture, lambda rows: rows.append([name, "", ""]))
    with pytest.raises(RuntimeLockError, match="path_escape|path_invalid"):
        verify_installed_records(**fixture)


def test_canonical_covers_complete_managed_root(tmp_path: Path) -> None:
    fixture = _canonical_fixture(tmp_path)
    (fixture["managed_root"] / "injected.dll").write_bytes(b"native")
    with pytest.raises(RuntimeLockError, match="unrecorded_files"):
        verify_installed_records(**fixture)


def test_canonical_reparse_parent_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _canonical_fixture(tmp_path)
    original = Path.lstat
    def reparse(path: Path, *args: object, **kwargs: object) -> object:
        if path.name == "Lib":
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(RuntimeLockError, match="reparse"):
        verify_installed_records(**fixture)


def test_canonical_cross_distribution_collision_fails(tmp_path: Path) -> None:
    fixture = _canonical_fixture(tmp_path)
    entry = _fake_lock_entry("second")
    wheel = _write_fake_wheel(fixture["wheelhouse"], entry)
    with zipfile.ZipFile(wheel) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    files["second-1.0.0.data/purelib/fixture_runtime.py"] = b"value = 1\n"
    rows = list(csv.reader(files["second-1.0.0.dist-info/RECORD"].decode().splitlines()))
    rows.append(["second-1.0.0.data/purelib/fixture_runtime.py", "", ""])
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    files["second-1.0.0.dist-info/RECORD"] = buffer.getvalue().encode()
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
            if ".data/" not in name:
                target = fixture["site_packages"] / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload.replace(b"second-1.0.0.data/purelib/", b"") if name.endswith("/RECORD") else payload)
    entry["sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    fixture["lock"]["distributions"].append(entry)
    with pytest.raises(RuntimeLockError, match="cross_distribution_collision"):
        verify_installed_records(**fixture)


def test_canonical_verifies_whole_wheel_before_member_fallback(tmp_path: Path) -> None:
    fixture = _canonical_fixture(tmp_path, empty_wheel_hash=True)
    wheel = next(fixture["wheelhouse"].glob("*.whl"))
    wheel.write_bytes(wheel.read_bytes() + b"tampered")
    with pytest.raises(RuntimeLockError, match="locked_wheel_hash_mismatch"):
        verify_installed_records(**fixture)


@pytest.mark.parametrize("mutation", ["size_only_missing", "both_missing", "forged_hash", "metadata", "missing_row"])
def test_canonical_integrity_fallback_has_no_record_bypass(tmp_path: Path, mutation: str) -> None:
    fixture = _canonical_fixture(tmp_path)
    def edit(rows: list) -> None:
        if mutation == "missing_row":
            rows.pop(0)
        elif mutation == "both_missing":
            rows[0][1:] = ["", ""]
        elif mutation == "size_only_missing":
            rows[0][2] = ""
        elif mutation == "forged_hash":
            payload = b"modified\n"
            (fixture["site_packages"] / rows[0][0]).write_bytes(payload)
            rows[0][1:] = ["sha256=" + base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode().rstrip("="), str(len(payload))]
    _edit_record(fixture, edit)
    if mutation == "metadata":
        next(fixture["site_packages"].glob("*.dist-info/INSTALLER")).write_bytes(b"forged\n")
    if mutation in {"size_only_missing", "both_missing"}:
        report = verify_installed_records(**fixture)
        assert next(row for row in report["files"] if row["wheel_member"] == "fixture_runtime.py")["integrity_source"] == "locked_wheel_member"
    else:
        with pytest.raises(RuntimeLockError, match="locked_source_mismatch|source_targets_missing"):
            verify_installed_records(**fixture)


def test_canonical_bare_command_use_blocks_pruning(tmp_path: Path) -> None:
    fixture = _canonical_fixture(tmp_path, entrypoint=True)
    (fixture["production_root"] / "apps/backend/use.py").write_text('subprocess.run(["fixture-cli"])')
    with pytest.raises(RuntimeLockError, match="artifact_used"):
        verify_installed_records(**fixture)


def test_canonical_owned_file_contract_is_verified(tmp_path: Path) -> None:
    fixture = _canonical_fixture(tmp_path)
    payload = b"synthetic CPython"
    target = fixture["managed_root"] / "python.exe"
    target.write_bytes(payload)
    fixture["owned_files"] = [{"canonical_final_path": "python.exe",
                               "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}]
    assert verify_installed_records(**fixture)["status"] == "PASS"
    target.write_bytes(b"tampered")
    with pytest.raises(RuntimeLockError, match="owned_file_mismatch"):
        verify_installed_records(**fixture)


@pytest.mark.parametrize("deep", [False, True])
def test_stage_cleanup_removes_normal_and_long_paths(tmp_path: Path, deep: bool) -> None:
    stage = tmp_path / ".fixture.stage-owned"
    target = stage / "file.txt"
    if deep:
        target = stage.joinpath(*(["deep_directory_" + "x" * 35] * 6), "file.txt")
        assert len(str(target)) > 260
    extended = portable_distribution._extended_path(target.absolute())
    extended.parent.mkdir(parents=True)
    extended.write_bytes(b"synthetic header")
    portable_distribution.cleanup_stage(stage, tmp_path)
    assert not stage.exists()


@pytest.mark.parametrize("name", ["outside/.fixture.stage-owned", "stages", "stages/not-owned"])
def test_stage_cleanup_refuses_unowned_targets(tmp_path: Path, name: str) -> None:
    root = tmp_path / "stages"
    root.mkdir()
    target = tmp_path / name
    target.mkdir(parents=True, exist_ok=True)
    marker = target / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(BuildError, match="target_invalid"):
        portable_distribution.cleanup_stage(target, root)
    assert marker.read_text(encoding="utf-8") == "keep"


def test_stage_cleanup_refuses_reparse_before_deleting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = tmp_path / ".fixture.stage-owned"
    stage.mkdir()
    marker = stage / "keep.txt"
    marker.write_bytes(b"keep")
    original = Path.lstat

    def reparse(path: Path, *args: object, **kwargs: object) -> object:
        if path.name == marker.name:
            return SimpleNamespace(st_mode=stat.S_IFREG,
                                   st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(BuildError, match="reparse"):
        portable_distribution.cleanup_stage(stage, tmp_path)
    assert marker.read_bytes() == b"keep"


@pytest.mark.skipif(os.name != "nt", reason="Windows extended path spelling")
def test_stage_extended_path_drive_and_unc() -> None:
    for raw, expected in [
        ("D:\\stage\\file", "\\\\?\\D:\\stage\\file"),
        ("\\\\server\\share\\stage", "\\\\?\\UNC\\server\\share\\stage"),
    ]:
        extended = portable_distribution._extended_path(Path(raw))
        assert str(extended) == expected
        assert portable_distribution._extended_path(extended) == extended


@pytest.mark.parametrize("cleanup_fails", [False, True])
@pytest.mark.parametrize("build_fails", [False, True])
def test_cleanup_preserves_primary_failure_and_reports_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup_fails: bool, build_fails: bool
) -> None:
    report_path = tmp_path / "BUILD_REPORT.json"
    portable_distribution.write_json(report_path, {
        "status": "BLOCKED" if build_fails else "PASS", "error": "original" if build_fails else None,
    })

    def cleanup(*args: object) -> None:
        if cleanup_fails:
            raise OSError("cleanup repro")

    monkeypatch.setattr(portable_distribution, "cleanup_stage", cleanup)
    original = BuildError("original")

    def operation() -> None:
        try:
            if build_fails:
                raise original
        finally:
            portable_distribution.finish_stage_cleanup(
                tmp_path / ".fixture.stage-owned", tmp_path, report_path, sys.exc_info()[1]
            )

    if build_fails or cleanup_fails:
        with pytest.raises(BuildError) as caught:
            operation()
        if build_fails:
            assert caught.value is original
        if cleanup_fails:
            assert any("cleanup repro" in note for note in caught.value.__notes__)
    else:
        operation()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if cleanup_fails:
        assert report["status"] == "BLOCKED"
        assert "cleanup repro" in report["cleanup_error"]
    if build_fails:
        assert report["error"] == "original"


def test_cleanup_report_failure_cannot_mask_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cleanup(*args: object) -> None:
        raise OSError("cleanup repro")

    monkeypatch.setattr(portable_distribution, "cleanup_stage", cleanup)
    original = BuildError("original")
    with pytest.raises(BuildError) as caught:
        try:
            raise original
        finally:
            portable_distribution.finish_stage_cleanup(
                tmp_path / ".fixture.stage-owned", tmp_path, tmp_path / "missing-report", original
            )
    assert caught.value is original
    assert any("cleanup repro" in note for note in original.__notes__)
    assert any("stage_cleanup_report_failed" in note for note in original.__notes__)
