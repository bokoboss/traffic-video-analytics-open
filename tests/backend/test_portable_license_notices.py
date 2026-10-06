from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from scripts.build_portable_distribution import (
    BuildError,
    capture_application_license,
    capture_distribution_licenses,
    capture_frontend_provenance,
    capture_nvidia_runtime_inventory,
    load_supplemental_license_records,
    nvidia_component_family,
    summarize_python_license_evidence,
    scheduler_dependency_edge_is_valid,
    verify_packaged_supplemental_license_contract,
)
from scripts import build_portable_distribution as portable_builder
from scripts.verify_portable_distribution import verify_zip
from scripts.portable.runtime_lock import load_runtime_lock


ROOT = Path(__file__).parents[2]
LOCK_PATH = ROOT / "packages" / "portable" / "windows-x64-cp312-runtime.lock.json"
FAMILIES = {
    "cudart64_12.dll": "CUDA runtime / cudart",
    "cublas64_12.dll": "cuBLAS",
    "cublasLt64_12.dll": "cuBLAS Lt",
    "cudnn64_9.dll": "cuDNN",
    "cufft64_11.dll": "cuFFT",
    "cupti64_2025.1.1.dll": "CUPTI / Perfworks",
    "nvperf_host.dll": "CUPTI / Perfworks",
    "curand64_10.dll": "cuRAND",
    "cusolver64_11.dll": "cuSOLVER",
    "cusparse64_12.dll": "cuSPARSE",
    "nvJitLink_120_0.dll": "nvJitLink",
    "nvrtc64_120_0.dll": "NVRTC / NVRTC builtins",
    "nvrtc-builtins64_124.dll": "NVRTC / NVRTC builtins",
    "nvToolsExt64_1.dll": "NVToolsExt",
    "nvjpeg64_12.dll": "nvJPEG",
}


def _write_verifier_probe_zip(
    tmp_path: Path,
    *,
    include_inventory: bool = False,
    inventory: str | None = None,
    extra_files: dict[str, bytes] | None = None,
    provenance_updates: dict[str, object] | None = None,
) -> Path:
    provenance: dict[str, object] = {
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
            "application_license": "PASS",
            "license_completeness": "PASS",
            "frontend_runtime_licenses": "PASS",
            "nvidia_notice_inventory": "PASS",
            "office_pc_uat": "NOT_RUN",
        },
    }
    if include_inventory:
        provenance["license_inventory"] = inventory
    if provenance_updates:
        provenance.update(provenance_updates)
    files = {"PACKAGE_PROVENANCE.json": json.dumps(provenance).encode("utf-8")}
    files.update(extra_files or {})
    manifest = {
        "excluded_from_manifest": ["PACKAGE_MANIFEST.json"],
        "files": [
            {
                "path": name,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            for name, payload in sorted(files.items())
        ],
        "file_count": len(files),
        "uncompressed_bytes": sum(len(payload) for payload in files.values()),
    }
    path = tmp_path / "qualification-probe.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
        archive.writestr("PACKAGE_MANIFEST.json", json.dumps(manifest).encode("utf-8"))
    return path


def test_application_agpl_is_copied_complete_and_short_text_is_rejected(tmp_path: Path) -> None:
    source_license = ROOT / "LICENSE"
    package = tmp_path / "package"
    evidence = capture_application_license(source_license, package)
    original = source_license.read_bytes()

    assert evidence["status"] == "PASS"
    assert evidence["source_url"] == "https://www.gnu.org/licenses/agpl-3.0.txt"
    assert evidence["size"] >= 30_000
    assert evidence["sha256"] == portable_builder.AGPL_APPROVED_SHA256
    assert (package / "app" / "LICENSE").read_bytes() == original
    assert (package / "LICENSES" / "application" / "LICENSE").read_bytes() == original

    short = tmp_path / "short-license.txt"
    short.write_text("GNU AFFERO GENERAL PUBLIC LICENSE", encoding="utf-8")
    with pytest.raises(BuildError, match="application_agpl_license_unapproved_text"):
        capture_application_license(short, tmp_path / "blocked")


def test_agpl_markers_and_thirty_kilobytes_of_filler_are_rejected(tmp_path: Path) -> None:
    fake = b"\n".join(
        (
            b"GNU AFFERO GENERAL PUBLIC LICENSE",
            b"Version 3, 19 November 2007",
            b"How to Apply These Terms to Your New Programs",
        )
    ) + b"\n" + b"X" * 30000
    source = tmp_path / "fake-license.txt"
    source.write_bytes(fake)

    with pytest.raises(BuildError, match="application_agpl_license_unapproved_text"):
        capture_application_license(source, tmp_path / "blocked")


def test_one_byte_mutation_of_approved_agpl_text_is_rejected(tmp_path: Path) -> None:
    mutated = bytearray((ROOT / "LICENSE").read_bytes())
    mutated[0] ^= 1
    source = tmp_path / "mutated-license.txt"
    source.write_bytes(mutated)

    with pytest.raises(BuildError, match="application_agpl_license_unapproved_text"):
        capture_application_license(source, tmp_path / "blocked")


def test_all_locked_python_distributions_capture_license_evidence(tmp_path: Path) -> None:
    runtime_lock = load_runtime_lock(LOCK_PATH)
    site_packages = tmp_path / "site-packages"
    licenses_root = tmp_path / "package" / "LICENSES"
    for item in runtime_lock["distributions"]:
        dist_info = site_packages / f"{item['normalized_name']}-{item['version']}.dist-info"
        dist_info.mkdir(parents=True)
        (dist_info / "METADATA").write_text(
            f"Name: {item['name']}\nVersion: {item['version']}\nLicense: BSD\n",
            encoding="utf-8",
        )
        if item["normalized_name"] == "nvidia-ml-py":
            continue
        license_name = "LICENCE" if item["normalized_name"] == "tqdm" else "LICENSE"
        (dist_info / license_name).write_text(
            f"license evidence for {item['name']} {item['version']}\n", encoding="utf-8"
        )

    components = capture_distribution_licenses(
        site_packages, licenses_root, runtime_lock, source_root=ROOT
    )
    summary = summarize_python_license_evidence(components, runtime_lock)
    by_name = {item["name"].casefold().replace("_", "-"): item for item in components}
    tqdm = by_name["tqdm"]
    nvidia_ml = by_name["nvidia-ml-py"]

    assert summary == {
        "status": "PASS",
        "locked_distribution_count": 61,
        "resolved_license_evidence_count": 61,
        "unresolved_count": 0,
        "unresolved_distributions": [],
    }
    assert tqdm["license_evidence_status"] == "RESOLVED_FROM_DISTRIBUTION"
    assert any(
        Path(path).name == "LICENCE" and (tmp_path / "package" / path).is_file()
        for path in tqdm["license_files"]
    )
    assert nvidia_ml["license_file_not_present_in_distribution"] is True
    assert nvidia_ml["license_evidence_status"] == "RESOLVED_FROM_SUPPLEMENTAL"
    assert nvidia_ml["license_evidence_source"] == "supplemental_manifest"
    assert nvidia_ml["license_files"] == [
        "LICENSES/python/nvidia-ml-py/supplemental/LICENSE.txt"
    ]
    supplemental = nvidia_ml["supplemental_license"]
    packaged_manifest_path = (
        tmp_path / "package" / "LICENSES" / "python" / "SUPPLEMENTAL_LICENSES.json"
    )
    packaged_manifest = json.loads(packaged_manifest_path.read_text(encoding="utf-8"))
    assert packaged_manifest["schema_version"] == "supplemental-python-licenses-v1"
    assert packaged_manifest["records"] == [
        {
            **supplemental,
            "package_license_path": nvidia_ml["license_files"][0],
        }
    ]
    assert supplemental["version"] == "13.610.43"
    assert supplemental["locked_wheel_sha256"] == (
        "f13c72698edef492f985cc225f14faafe68ae065a2e407f45bdf6f4b9b43fde8"
    )
    assert supplemental["locked_wheel_filename"] == "nvidia_ml_py-13.610.43-py3-none-any.whl"
    assert supplemental["source_url"] == "https://pypi.org/project/nvidia-ml-py/13.610.43/"
    assert supplemental["source_distribution_member"] == "nvidia_ml_py-13.610.43/README.txt"
    assert supplemental["source_text_section"] == "LICENSE (trailing README blank separator omitted)"
    assert supplemental["source_distribution_sha256"] == (
        "65437eb73d68d0c62c931ca4d45038472faff03bd0b8729abba4b899f70d60f2"
    )
    license_path = tmp_path / "package" / nvidia_ml["license_files"][0]
    assert hashlib.sha256(license_path.read_bytes()).hexdigest() == supplemental[
        "license_text_sha256"
    ]


@pytest.mark.parametrize("field,value", [("version", "13.610.44"), ("sha256", "0" * 64)])
def test_supplemental_license_rejects_wrong_locked_identity(field: str, value: str) -> None:
    runtime_lock = load_runtime_lock(LOCK_PATH)
    nvidia_ml = next(
        item for item in runtime_lock["distributions"] if item["normalized_name"] == "nvidia-ml-py"
    )
    nvidia_ml[field] = value

    with pytest.raises(BuildError, match="supplemental_license_locked_distribution_mismatch"):
        load_supplemental_license_records(ROOT, runtime_lock)


def _supplemental_fixture(
    tmp_path: Path, *, include_record: bool = True, include_license_file: bool = True
) -> tuple[Path, Path]:
    source_root = tmp_path / "source"
    original = json.loads(
        (ROOT / "packages/portable/licenses/supplemental-licenses.json").read_text(
            encoding="utf-8"
        )
    )
    if not include_record:
        original["records"] = []
    manifest_path = source_root / "packages/portable/licenses/supplemental-licenses.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(original), encoding="utf-8")
    if include_license_file and original["records"]:
        license_path = source_root / original["records"][0]["license_path"]
        license_path.parent.mkdir(parents=True)
        license_path.write_bytes(
            (ROOT / original["records"][0]["license_path"]).read_bytes()
        )
    return source_root, manifest_path


def test_unresolved_distribution_without_supplemental_record_blocks(tmp_path: Path) -> None:
    source_root, _ = _supplemental_fixture(tmp_path, include_record=False)
    site_packages = tmp_path / "site-packages"
    dist_info = site_packages / "nvidia-ml-py-13.610.43.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        "Name: nvidia-ml-py\nVersion: 13.610.43\n", encoding="utf-8"
    )

    with pytest.raises(BuildError, match="runtime_license_evidence_incomplete"):
        capture_distribution_licenses(
            site_packages, tmp_path / "package" / "LICENSES", load_runtime_lock(LOCK_PATH),
            source_root=source_root,
        )


def test_supplemental_license_missing_file_blocks(tmp_path: Path) -> None:
    source_root, _ = _supplemental_fixture(tmp_path, include_license_file=False)

    with pytest.raises(BuildError, match="supplemental_license_text_missing:nvidia-ml-py"):
        load_supplemental_license_records(source_root, load_runtime_lock(LOCK_PATH))


def test_supplemental_license_text_hash_mismatch_blocks(tmp_path: Path) -> None:
    source_root, manifest_path = _supplemental_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["records"][0]["license_text_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BuildError, match="supplemental_license_text_hash_mismatch:nvidia-ml-py"):
        load_supplemental_license_records(source_root, load_runtime_lock(LOCK_PATH))


def test_supplemental_license_non_https_source_url_blocks(tmp_path: Path) -> None:
    source_root, manifest_path = _supplemental_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["records"][0]["source_url"] = "http://example.com/unrelated"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BuildError, match="supplemental_license_source_invalid"):
        load_supplemental_license_records(source_root, load_runtime_lock(LOCK_PATH))


def _second_supplemental_record() -> tuple[dict[str, str], bytes, dict[str, str]]:
    payload = b"Synthetic supplemental license for a different locked component.\n"
    record = {
        "normalized_package_name": "fixture-analytics",
        "version": "7.8.9",
        "locked_wheel_filename": "fixture_analytics-7.8.9-py3-none-any.whl",
        "locked_wheel_sha256": "1" * 64,
        "license_path": "packages/portable/licenses/python/fixture-analytics/LICENSE.txt",
        "license_text_sha256": hashlib.sha256(payload).hexdigest(),
        "source_url": "https://example.test/fixture-analytics/7.8.9/",
        "source_distribution_filename": "fixture_analytics-7.8.9.tar.gz",
        "source_distribution_member": "fixture_analytics-7.8.9/LICENSE.txt",
        "source_text_section": "LICENSE file",
        "source_distribution_url": (
            "https://example.test/files/fixture_analytics-7.8.9.tar.gz"
        ),
        "source_distribution_sha256": "2" * 64,
    }
    lock_item = {
        "normalized_name": "fixture-analytics",
        "version": "7.8.9",
        "wheel_filename": "fixture_analytics-7.8.9-py3-none-any.whl",
        "sha256": "1" * 64,
    }
    return record, payload, lock_item


def test_generic_supplemental_loader_accepts_second_exact_locked_record(
    tmp_path: Path,
) -> None:
    source_root, manifest_path = _supplemental_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record, payload, lock_item = _second_supplemental_record()
    manifest["records"].append(record)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    license_path = source_root / record["license_path"]
    license_path.parent.mkdir(parents=True)
    license_path.write_bytes(payload)
    runtime_lock = load_runtime_lock(LOCK_PATH)
    runtime_lock["distributions"].append(lock_item)

    resolved = load_supplemental_license_records(source_root, runtime_lock)

    assert set(resolved) == {"nvidia-ml-py", "fixture-analytics"}
    assert resolved["fixture-analytics"]["locked_wheel_filename"] == lock_item[
        "wheel_filename"
    ]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        ("unknown_package", "supplemental_license_locked_distribution_mismatch"),
        ("version", "supplemental_license_locked_distribution_mismatch"),
        ("wheel_filename", "supplemental_license_locked_distribution_mismatch"),
        ("wheel_sha", "supplemental_license_locked_distribution_mismatch"),
        ("license_path", "supplemental_license_path_invalid"),
        ("source_url", "supplemental_license_source_invalid"),
        ("malformed_source_url", "supplemental_license_source_invalid"),
        ("source_distribution_url", "supplemental_license_source_distribution_invalid"),
        ("source_filename", "supplemental_license_source_distribution_invalid"),
        (
            "source_member",
            "supplemental_license_source_distribution_invalid",
        ),
        (
            "source_hash",
            "supplemental_license_source_distribution_hash_invalid",
        ),
    ],
)
def test_generic_supplemental_loader_rejects_invalid_record_fields(
    tmp_path: Path, mutate: str, message: str
) -> None:
    source_root, manifest_path = _supplemental_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest["records"][0]
    if mutate == "unknown_package":
        record["normalized_package_name"] = "not-locked"
    elif mutate == "version":
        record["version"] = "99.0"
    elif mutate == "wheel_filename":
        record["locked_wheel_filename"] = "other.whl"
    elif mutate == "wheel_sha":
        record["locked_wheel_sha256"] = "0" * 64
    elif mutate == "license_path":
        record["license_path"] = "packages/portable/licenses/../../outside/LICENSE.txt"
    elif mutate == "source_url":
        record["source_url"] = "file:///local/source"
    elif mutate == "malformed_source_url":
        record["source_url"] = "https://[malformed"
    elif mutate == "source_distribution_url":
        record["source_distribution_url"] = "http://example.test/archive.tar.gz"
    elif mutate == "source_filename":
        record["source_distribution_filename"] = "../archive.tar.gz"
    elif mutate == "source_member":
        record["source_distribution_member"] = "../LICENSE.txt"
    else:
        record["source_distribution_sha256"] = "A" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BuildError, match=message):
        load_supplemental_license_records(source_root, load_runtime_lock(LOCK_PATH))


def test_generic_supplemental_loader_rejects_duplicate_package_records(
    tmp_path: Path,
) -> None:
    source_root, manifest_path = _supplemental_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["records"].append(manifest["records"][0])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BuildError, match="supplemental_license_name_duplicate_or_invalid"):
        load_supplemental_license_records(source_root, load_runtime_lock(LOCK_PATH))


def _packaged_supplemental_fixture() -> tuple[
    set[str], dict[str, bytes], dict[str, object], dict[str, object], dict[str, object]
]:
    source_record = json.loads(
        (ROOT / "packages/portable/licenses/supplemental-licenses.json").read_text(
            encoding="utf-8"
        )
    )["records"][0]
    synthetic_record, synthetic_payload, synthetic_lock = _second_supplemental_record()
    source_payload = (ROOT / source_record["license_path"]).read_bytes()
    records = [source_record, synthetic_record]
    payloads: dict[str, bytes] = {}
    components: list[dict[str, object]] = []
    distributions: list[dict[str, str]] = []
    canonical_files: list[dict[str, str]] = []
    for source in records:
        name = source["normalized_package_name"]
        package_license_path = (
            f"LICENSES/python/{name}/supplemental/LICENSE.txt"
        )
        packaged = {**source, "package_license_path": package_license_path}
        license_payload = source_payload if name == "nvidia-ml-py" else synthetic_payload
        payloads[package_license_path] = license_payload
        wheel_filename = source["locked_wheel_filename"]
        wheel_sha = source["locked_wheel_sha256"]
        components.append(
            {
                "component_type": "python_distribution",
                "name": name,
                "version": source["version"],
                "license_file_not_present_in_distribution": True,
                "license_evidence_status": "RESOLVED_FROM_SUPPLEMENTAL",
                "license_evidence_source": "supplemental_manifest",
                "license_files": [package_license_path],
                "license_file_sha256": {
                    package_license_path: source["license_text_sha256"]
                },
                "supplemental_license": packaged,
                "lock": {
                    "wheel_filename": wheel_filename,
                    "wheel_sha256": wheel_sha,
                },
            }
        )
        distributions.append(
            {"normalized_name": name, "version": source["version"]}
        )
        canonical_files.append(
            {
                "distribution": name,
                "wheel_filename": wheel_filename,
                "wheel_sha256": wheel_sha,
            }
        )
    manifest = {
        "schema_version": "supplemental-python-licenses-v1",
        "records": [
            {**record, "package_license_path": f"LICENSES/python/{record['normalized_package_name']}/supplemental/LICENSE.txt"}
            for record in records
        ],
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
    manifest_path = "LICENSES/python/SUPPLEMENTAL_LICENSES.json"
    payloads[manifest_path] = manifest_bytes
    manifest_reference = {
        "path": manifest_path,
        "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }
    third_party: dict[str, object] = {
        "components": components,
        "supplemental_license_manifest": manifest_reference,
    }
    provenance: dict[str, object] = {
        "supplemental_license_manifest": manifest_reference,
    }
    canonical: dict[str, object] = {
        "distributions": distributions,
        "files": canonical_files,
    }
    return set(payloads), payloads, third_party, provenance, canonical


def test_generic_standalone_contract_accepts_two_supplemental_records() -> None:
    files, payloads, third_party, provenance, canonical = (
        _packaged_supplemental_fixture()
    )

    result = verify_packaged_supplemental_license_contract(
        files, payloads.__getitem__, third_party, provenance, canonical
    )

    assert result["status"] == "PASS"
    assert result["record_count"] == 2


@pytest.mark.parametrize(
    "failure",
    [
        "missing_manifest",
        "manifest_hash",
        "duplicate_record",
        "orphan_record",
        "component_mismatch",
        "third_party_reference",
        "provenance_reference",
        "license_hash",
        "unneeded_record",
        "unresolved_component",
    ],
)
def test_generic_standalone_contract_fails_closed(
    failure: str,
) -> None:
    files, payloads, third_party, provenance, canonical = (
        _packaged_supplemental_fixture()
    )
    manifest_path = "LICENSES/python/SUPPLEMENTAL_LICENSES.json"
    manifest = json.loads(payloads[manifest_path].decode("utf-8"))
    rows = third_party["components"]
    if failure == "missing_manifest":
        files.remove(manifest_path)
        payloads.pop(manifest_path)
    elif failure == "manifest_hash":
        payloads[manifest_path] += b" "
    elif failure == "duplicate_record":
        manifest["records"].append(manifest["records"][0])
        payloads[manifest_path] = json.dumps(manifest, sort_keys=True).encode("utf-8")
    elif failure == "orphan_record":
        manifest["records"][0]["normalized_package_name"] = "orphan-component"
        payloads[manifest_path] = json.dumps(manifest, sort_keys=True).encode("utf-8")
    elif failure == "component_mismatch":
        rows[0]["supplemental_license"]["source_distribution_sha256"] = "3" * 64
    elif failure == "third_party_reference":
        third_party["supplemental_license_manifest"]["sha256"] = "0" * 64
    elif failure == "provenance_reference":
        provenance["supplemental_license_manifest"]["sha256"] = "0" * 64
    elif failure == "license_hash":
        payloads[rows[0]["license_files"][0]] += b"tampered"
    elif failure == "unneeded_record":
        rows[0]["license_file_not_present_in_distribution"] = False
        rows[0]["license_evidence_status"] = "RESOLVED_FROM_DISTRIBUTION"
    else:
        rows[0]["license_evidence_status"] = "UNRESOLVED"
        rows[0]["license_evidence_source"] = "distribution"
        rows[0]["license_file_not_present_in_distribution"] = True
        rows[0]["license_files"] = []

    result = verify_packaged_supplemental_license_contract(
        files, payloads.__getitem__, third_party, provenance, canonical
    )

    assert result["status"] == "BLOCKED"
    assert result["violations"]


@pytest.mark.parametrize(
    ("include_inventory", "inventory"),
    [(False, None), (True, None), (True, "RENAMED_LICENSES.json")],
)
def test_qualification_requires_exact_license_inventory_reference(
    tmp_path: Path, include_inventory: bool, inventory: str | None
) -> None:
    archive = _write_verifier_probe_zip(
        tmp_path, include_inventory=include_inventory, inventory=inventory
    )

    result = verify_zip(
        archive,
        expected_source_sha="a" * 40,
        accepted_baseline_sha="b" * 40,
    )

    assert result["status"] == "BLOCKED"
    assert "qualification_license_inventory_invalid" in result["violations"]
    assert result["license_notice_validation"]["status"] == "BLOCKED"
    assert "license_required_file_missing:THIRD_PARTY_LICENSES.json" in result[
        "license_notice_validation"
    ]["violations"]


def test_declared_license_inventory_without_file_fails_standalone_verifier(
    tmp_path: Path,
) -> None:
    archive = _write_verifier_probe_zip(
        tmp_path, include_inventory=True, inventory="THIRD_PARTY_LICENSES.json"
    )

    result = verify_zip(
        archive,
        expected_source_sha="a" * 40,
        accepted_baseline_sha="b" * 40,
    )

    assert result["status"] == "BLOCKED"
    assert result["license_notice_validation"]["status"] == "BLOCKED"
    assert "license_required_file_missing:THIRD_PARTY_LICENSES.json" in result[
        "license_notice_validation"
    ]["violations"]


@pytest.mark.parametrize(
    ("local_path", "field_path"),
    [
        (
            r"D:\A&B\traffic-video-analytics\.local-data\s\.s-example"
            r"\TrafficVideoAnalytics\runtime\python\Lib\site-packages",
            "path_budget.stage_root",
        ),
        (('C:\\Users\\builder\\AppData\\Loc' + 'al\\Temp\\TrafficVideoAnalytics'), "path_budget.stage_root"),
        (r"\\build-server\share\TrafficVideoAnalytics", "path_budget.deepest_member.projected_path"),
        (('/' + 'home/builder/work/TrafficVideoAnalytics'), "path_budget.effective_site_packages"),
        (('/' + 'Users/builder/work/TrafficVideoAnalytics'), "path_budget.effective_site_packages"),
        (('/' + 'private/build/TrafficVideoAnalytics'), "path_budget.effective_site_packages"),
        ("/tmp/build/TrafficVideoAnalytics", "path_budget.effective_site_packages"),
    ],
)
def test_standalone_verifier_blocks_local_absolute_paths_in_provenance(
    tmp_path: Path, local_path: str, field_path: str
) -> None:
    archive = _write_verifier_probe_zip(
        tmp_path,
        provenance_updates={
            "path_budget": {
                "stage_root": local_path,
                "effective_site_packages": local_path,
                "deepest_member": {"projected_path": local_path},
            }
        },
    )

    result = verify_zip(
        archive,
        expected_source_sha="a" * 40,
        accepted_baseline_sha="b" * 40,
    )

    assert (
        f"packaged_provenance_absolute_path:{field_path}" in result["violations"]
    )
    assert local_path not in json.dumps(result)


def test_standalone_verifier_path_guard_allows_urls_and_package_relative_paths(
    tmp_path: Path,
) -> None:
    archive = _write_verifier_probe_zip(
        tmp_path,
        provenance_updates={
            "source_url": "https://example.com/builds/output",
            "runtime_contract": {
                "python": "runtime/python/python.exe",
                "route": "/api/v1/projects",
            },
        },
    )

    result = verify_zip(
        archive,
        expected_source_sha="a" * 40,
        accepted_baseline_sha="b" * 40,
    )

    assert not any(
        item.startswith("packaged_provenance_absolute_path:")
        for item in result["violations"]
    )


def test_standalone_verifier_rejects_filler_agpl_even_with_pass_flags(
    tmp_path: Path,
) -> None:
    fake_agpl = b"\n".join(
        (
            b"GNU AFFERO GENERAL PUBLIC LICENSE",
            b"Version 3, 19 November 2007",
            b"How to Apply These Terms to Your New Programs",
        )
    ) + b"\n" + b"X" * 30000
    application_record = {
        "source_url": "https://www.gnu.org/licenses/agpl-3.0.txt",
        "sha256": hashlib.sha256(fake_agpl).hexdigest(),
        "app_license": "app/LICENSE",
        "package_license": "LICENSES/application/LICENSE",
    }
    terms = (
        "https://docs.nvidia.com/cuda/archive/12.8.0/eula/index.html\n"
        "https://docs.nvidia.com/deeplearning/cudnn/backend/latest/reference/eula.html\n"
    ).encode("utf-8")
    third_party = {
        "components": [],
        "application_license": application_record,
        "python_license_evidence": {},
        "nvidia_runtime_inventory": {},
        "microsoft_runtime_files": [],
    }
    supplemental_manifest = json.dumps(
        {"schema_version": "supplemental-python-licenses-v1", "records": []},
        sort_keys=True,
    ).encode("utf-8")
    supplemental_manifest_reference = {
        "path": "LICENSES/python/SUPPLEMENTAL_LICENSES.json",
        "sha256": hashlib.sha256(supplemental_manifest).hexdigest(),
    }
    third_party["supplemental_license_manifest"] = supplemental_manifest_reference
    extra_files = {
        "app/LICENSE": fake_agpl,
        "LICENSES/application/LICENSE": fake_agpl,
        "LICENSES/nvidia/NVIDIA_COMPONENT_TERMS.txt": terms,
        "LICENSES/ffmpeg/LICENSE.txt": b"FFmpeg source bundle required\n",
        "LICENSES/microsoft-vc-redist/README.txt": b"Microsoft runtime terms\n",
        "LICENSES/python/SUPPLEMENTAL_LICENSES.json": supplemental_manifest,
        "THIRD_PARTY_LICENSES.json": json.dumps(third_party).encode("utf-8"),
        "PORTABLE_RUNTIME_CANONICAL.json": json.dumps(
            {"distributions": [], "files": []}
        ).encode("utf-8"),
    }
    archive = _write_verifier_probe_zip(
        tmp_path,
        include_inventory=True,
        inventory="THIRD_PARTY_LICENSES.json",
        extra_files=extra_files,
        provenance_updates={
            "application_license": application_record,
            "python_runtime_lock": {"locked_distribution_count": 61},
            "ffmpeg": {},
            "microsoft_runtime": {},
                "supplemental_license_manifest": supplemental_manifest_reference,
        },
    )

    result = verify_zip(
        archive,
        expected_source_sha="a" * 40,
        accepted_baseline_sha="b" * 40,
    )

    assert result["status"] == "BLOCKED"
    assert result["license_notice_validation"] is not None
    assert any(
        item.startswith("application_agpl_license_unapproved_text:app/LICENSE")
        for item in result["license_notice_validation"]["violations"]
    )


def _write_npm_package(
    root: Path,
    name: str,
    version: str,
    *,
    license_file: bool = True,
    license_file_name: str = "LICENSE",
) -> None:
    root.mkdir(parents=True)
    metadata: dict[str, object] = {"name": name, "version": version, "license": "MIT"}
    if name == "react-dom":
        metadata["dependencies"] = {"scheduler": "^0.27.0"}
    (root / "package.json").write_text(json.dumps(metadata), encoding="utf-8")
    if license_file:
        (root / license_file_name).write_text(f"{name} license\n", encoding="utf-8")


def test_vite_emitted_code_contract_is_pinned_to_distributed_bundle() -> None:
    assert portable_builder.FRONTEND_EMITTED_CODE_COMPONENT_VERSIONS == {
        "vite": "7.1.12"
    }
    assert portable_builder.VITE_MODULEPRELOAD_POLYFILL_SIZE == 714
    assert portable_builder.VITE_MODULEPRELOAD_POLYFILL_SHA256 == (
        "ba4f9e90492bda90f86b7630f8b7a654d0c749f678bd92034c281f924f5e96d5"
    )


def _vite_test_bundle() -> bytes:
    return b"(function(){/* synthetic Vite emitted-code fixture */})();"


def _pin_test_vite_bundle(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> None:
    end = payload.find(b"})();")
    code = payload[: end + len(b"})();")]
    monkeypatch.setattr(portable_builder, "VITE_MODULEPRELOAD_POLYFILL_SIZE", len(code))
    monkeypatch.setattr(
        portable_builder,
        "VITE_MODULEPRELOAD_POLYFILL_SHA256",
        hashlib.sha256(code).hexdigest(),
    )


def _frontend_fixture(
    source: Path,
    package: Path,
    *,
    missing_license: str | None = None,
    scheduler_context_present: bool = True,
    bundle_payload: bytes = b"compiled frontend runtime\n",
) -> None:
    frontend = source / "apps" / "frontend"
    frontend.mkdir(parents=True)
    (frontend / "package.json").write_text(
        json.dumps(
            {
                "name": "traffic-video-analytics-frontend",
                "dependencies": {
                    "react": "19.2.0",
                    "react-dom": "19.2.0",
                    "lucide-react": "0.468.0",
                    "vite": "7.1.12",
                },
                "devDependencies": {"vitest": "3.2.4"},
            }
        ),
        encoding="utf-8",
    )
    (source / "pnpm-lock.yaml").write_text(
        "importers:\n"
        "  apps/frontend:\n"
        "    dependencies:\n"
        "      lucide-react:\n"
        "        specifier: 0.468.0\n"
        "        version: 0.468.0(react@19.2.0)\n"
        "      react:\n"
        "        specifier: 19.2.0\n"
        "        version: 19.2.0\n"
        "      react-dom:\n"
        "        specifier: 19.2.0\n"
        "        version: 19.2.0(react@19.2.0)\n"
        "      vite:\n"
        "        specifier: 7.1.12\n"
        "        version: 7.1.12(lightningcss@1.32.0)\n"
        "packages:\n"
        "  react@19.2.0:\n"
        "  react-dom@19.2.0:\n"
        "  lucide-react@0.468.0:\n"
        "  scheduler@0.27.0:\n"
        "  vite@7.1.12:\n"
        "  vitest@3.2.4:\n"
        "snapshots:\n"
        "  react-dom@19.2.0(react@19.2.0):\n"
        "    dependencies:\n"
        "      react: 19.2.0\n"
        "      scheduler: 0.27.0\n"
        "  scheduler@0.27.0: {}\n",
        encoding="utf-8",
    )
    modules = frontend / "node_modules"
    for name, version in (
        ("react", "19.2.0"),
        ("react-dom", "19.2.0"),
        ("lucide-react", "0.468.0"),
    ):
        _write_npm_package(
            modules / name,
            name,
            version,
            license_file=name != missing_license,
        )
    if scheduler_context_present:
        _write_npm_package(
            modules / "scheduler",
            "scheduler",
            "0.27.0",
            license_file=missing_license != "scheduler",
        )
    vite_root = modules / "vite"
    _write_npm_package(
        vite_root,
        "vite",
        "7.1.12",
        license_file=missing_license != "vite",
        license_file_name="LICENSE.md",
    )
    vite_source = vite_root / "dist" / "node" / "chunks" / "config.js"
    vite_source.parent.mkdir(parents=True)
    vite_source.write_text(
        'name: "vite:modulepreload-polyfill"; function polyfill() {} '
        'function getFetchOpts(link) {} function processPreload(link) {}',
        encoding="utf-8",
    )
    dist = package / "frontend" / "dist" / "assets"
    dist.mkdir(parents=True)
    (dist / "index.js").write_bytes(bundle_payload)


def test_frontend_runtime_license_set_uses_lock_and_excludes_dev_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    package = tmp_path / "package"
    bundle = _vite_test_bundle()
    _frontend_fixture(source, package, bundle_payload=bundle)
    _pin_test_vite_bundle(monkeypatch, bundle)

    result = capture_frontend_provenance(source, package, package / "LICENSES")

    runtime_components = [
        item for item in result["components"]
        if item["component_type"] == "frontend_runtime_package"
    ]
    assert {item["name"]: item["version"] for item in runtime_components} == {
        "react": "19.2.0",
        "react-dom": "19.2.0",
        "lucide-react": "0.468.0",
        "scheduler": "0.27.0",
    }
    assert {item["name"] for item in result["provenance"]["runtime_packages"]} == {
        "react",
        "react-dom",
        "lucide-react",
        "scheduler",
    }
    assert {item["path"] for item in result["provenance"]["bundle_assets"]} == {
        "frontend/dist/assets/index.js"
    }
    assert all(item["license_files"] for item in runtime_components)
    emitted = next(
        item for item in result["components"]
        if item["component_type"] == "frontend_emitted_code_component"
    )
    assert emitted["name"] == "vite"
    assert emitted["version"] == "7.1.12"
    assert emitted["license_files"] == ["LICENSES/frontend/vite/LICENSE.md"]
    assert (package / emitted["license_files"][0]).is_file()
    assert result["provenance"]["vite_emitted_code"]["evidence_type"] == (
        "modulepreload_polyfill_and_helpers"
    )
    assert result["provenance"]["vite_emitted_code"]["generator_helpers"] == [
        "getFetchOpts",
        "processPreload",
    ]
    assert "vitest" not in {item["name"] for item in result["components"]}


@pytest.mark.parametrize(
    ("field", "value"),
    [("name", "unrelated-package"), ("version", "7.1.11")],
)
def test_vite_package_requires_exact_name_and_version(
    tmp_path: Path, field: str, value: str
) -> None:
    source = tmp_path / "source"
    package = tmp_path / "package"
    _frontend_fixture(source, package)
    vite_metadata_path = source / "apps" / "frontend" / "node_modules" / "vite" / "package.json"
    vite_metadata = json.loads(vite_metadata_path.read_text(encoding="utf-8"))
    vite_metadata[field] = value
    vite_metadata_path.write_text(json.dumps(vite_metadata), encoding="utf-8")

    with pytest.raises(BuildError, match="frontend_runtime_package_identity_mismatch:vite@7.1.12"):
        capture_frontend_provenance(source, package, package / "LICENSES")


def test_vite_emitted_code_requires_a_separate_license_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    package = tmp_path / "package"
    bundle = _vite_test_bundle()
    _frontend_fixture(source, package, missing_license="vite", bundle_payload=bundle)
    _pin_test_vite_bundle(monkeypatch, bundle)

    with pytest.raises(BuildError, match="frontend_emitted_code_license_missing:vite@7.1.12"):
        capture_frontend_provenance(source, package, package / "LICENSES")


def test_vite_signature_rejects_unrelated_bundle_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    package = tmp_path / "package"
    _frontend_fixture(source, package, bundle_payload=b"application mentions modulepreload")

    with pytest.raises(BuildError, match="frontend_vite_emitted_code_missing"):
        capture_frontend_provenance(source, package, package / "LICENSES")


def test_scheduler_uses_react_dom_install_and_pnpm_dependency_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    package = tmp_path / "package"
    bundle = _vite_test_bundle()
    _frontend_fixture(source, package, bundle_payload=bundle)
    _pin_test_vite_bundle(monkeypatch, bundle)
    decoy = (
        source
        / "node_modules"
        / ".pnpm"
        / "scheduler@0.27.0_irrelevant@1.0.0"
        / "node_modules"
        / "scheduler"
    )
    _write_npm_package(decoy, "scheduler", "0.27.0")
    (decoy / "LICENSE").write_text("store decoy license\n", encoding="utf-8")

    result = capture_frontend_provenance(source, package, package / "LICENSES")

    edge = result["provenance"]["scheduler_dependency_edge"]
    assert edge["importer_name"] == "react-dom"
    assert edge["declared_dependency"] == "^0.27.0"
    assert edge["lock_snapshot"] == "react-dom@19.2.0(react@19.2.0)"
    assert edge["locked_version"] == "0.27.0"
    assert edge["dependency_context_path"] == "apps/frontend/node_modules/scheduler"
    assert edge["dependency_context_package_json_sha256"] == edge[
        "resolved_package_json_sha256"
    ]
    components = {row["name"]: row for row in result["components"]}
    assert scheduler_dependency_edge_is_valid(
        edge,
        components["react-dom"],
        components["scheduler"],
        {"pnpm_lock_sha256": result["provenance"]["pnpm_lock_sha256"]},
    )
    assert (package / "LICENSES/frontend/scheduler/LICENSE").read_text(
        encoding="utf-8"
    ) == "scheduler license\n"


def _pnpm_virtual_store_scheduler_edge() -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, str]]:
    importer_path = "node_modules/.pnpm/react-dom@19.2.0_react@19.2.0/node_modules/react-dom"
    context_path = "node_modules/.pnpm/react-dom@19.2.0_react@19.2.0/node_modules/scheduler"
    resolved_path = "node_modules/.pnpm/scheduler@0.27.0/node_modules/scheduler"
    edge: dict[str, object] = {
        "importer_name": "react-dom",
        "importer_version": "19.2.0",
        "importer_package_path": importer_path,
        "importer_package_json_sha256": "a" * 64,
        "declared_dependency": "^0.27.0",
        "lock_snapshot": "react-dom@19.2.0(react@19.2.0)",
        "locked_version": "0.27.0",
        "dependency_context_path": context_path,
        "dependency_context_package_json_sha256": "b" * 64,
        "resolved_package_path": resolved_path,
        "resolved_package_json_sha256": "b" * 64,
        "pnpm_lock_sha256": "c" * 64,
    }
    react_dom = {"source_package_json_sha256": "a" * 64}
    scheduler = {"source_package_json_sha256": "b" * 64}
    frontend_build = {"pnpm_lock_sha256": "c" * 64}
    return edge, react_dom, scheduler, frontend_build


def test_pnpm_virtual_store_edge_uses_dependency_context_not_resolved_parent() -> None:
    edge, react_dom, scheduler, frontend_build = _pnpm_virtual_store_scheduler_edge()
    importer = Path(str(edge["importer_package_path"]))
    context = Path(str(edge["dependency_context_path"]))
    resolved = Path(str(edge["resolved_package_path"]))

    assert importer.parent != resolved.parent
    assert context.parent == importer.parent
    assert not (
        resolved.parent == importer.parent
        or resolved.parent == importer / "node_modules"
    )
    assert scheduler_dependency_edge_is_valid(edge, react_dom, scheduler, frontend_build)


@pytest.mark.parametrize(
    ("field", "value", "scheduler_hash"),
    [
        ("dependency_context_path", "node_modules/unrelated/scheduler", "b" * 64),
        ("dependency_context_package_json_sha256", "d" * 64, "b" * 64),
        ("resolved_package_json_sha256", "d" * 64, "b" * 64),
        ("resolved_package_json_sha256", "d" * 64, "d" * 64),
        ("dependency_context_path", "../scheduler", "b" * 64),
        ("dependency_context_path", None, "b" * 64),
    ],
)
def test_pnpm_scheduler_edge_rejects_invalid_context_or_hash(
    field: str, value: object, scheduler_hash: str
) -> None:
    edge, react_dom, scheduler, frontend_build = _pnpm_virtual_store_scheduler_edge()
    edge[field] = value
    scheduler["source_package_json_sha256"] = scheduler_hash

    assert not scheduler_dependency_edge_is_valid(edge, react_dom, scheduler, frontend_build)


def test_arbitrary_pnpm_store_scheduler_cannot_satisfy_react_dom_edge(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    package = tmp_path / "package"
    _frontend_fixture(source, package, scheduler_context_present=False)
    decoy = (
        source
        / "node_modules"
        / ".pnpm"
        / "scheduler@0.27.0_irrelevant@1.0.0"
        / "node_modules"
        / "scheduler"
    )
    _write_npm_package(decoy, "scheduler", "0.27.0")

    with pytest.raises(BuildError, match="frontend_runtime_package_unresolved:scheduler@0.27.0"):
        capture_frontend_provenance(source, package, package / "LICENSES")


def test_unrelated_package_with_scheduler_version_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    package = tmp_path / "package"
    _frontend_fixture(source, package)
    scheduler_metadata_path = (
        source / "apps" / "frontend" / "node_modules" / "scheduler" / "package.json"
    )
    scheduler_metadata = json.loads(scheduler_metadata_path.read_text(encoding="utf-8"))
    scheduler_metadata["name"] = "unrelated-package"
    scheduler_metadata_path.write_text(json.dumps(scheduler_metadata), encoding="utf-8")

    with pytest.raises(BuildError, match="frontend_runtime_package_identity_mismatch:scheduler@0.27.0"):
        capture_frontend_provenance(source, package, package / "LICENSES")


def test_missing_frontend_runtime_license_blocks_capture(tmp_path: Path) -> None:
    source = tmp_path / "source"
    package = tmp_path / "package"
    _frontend_fixture(source, package, missing_license="scheduler")

    with pytest.raises(BuildError, match="frontend_runtime_license_missing:scheduler@0.27.0"):
        capture_frontend_provenance(source, package, package / "LICENSES")


def test_nvidia_inventory_maps_actual_locked_wheel_dlls_without_changing_them(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    rows = []
    original_hashes = {}
    for index, (filename, family) in enumerate(FAMILIES.items()):
        distribution = "torch" if index % 2 == 0 else "torchvision"
        relative = Path("Lib") / "site-packages" / distribution / "lib" / filename
        path = package / "runtime" / "python" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"locked dll {index}".encode("ascii"))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        original_hashes[path] = digest
        rows.append(
            {
                "canonical_final_path": relative.as_posix(),
                "distribution": distribution,
                "wheel_filename": f"{distribution}-2.10.0-cp312-win_amd64.whl",
                "wheel_sha256": ("a" if distribution == "torch" else "b") * 64,
                "sha256": digest,
            }
        )

    result = capture_nvidia_runtime_inventory(package, {"files": rows})

    assert result["status"] == "READY_WITH_HUMAN_APPROVAL"
    assert result["component_family_count"] == 13
    assert len(result["components"]) == len(FAMILIES)
    assert {item["dll_filename"]: item["component_family"] for item in result["components"]} == FAMILIES
    assert all(item["originating_wheel_sha256"] in {"a" * 64, "b" * 64} for item in result["components"])
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in original_hashes.items())
    assert nvidia_component_family("nvcuda.dll") is None
    assert nvidia_component_family("nppc64_12.dll") is None

    forbidden = package / "runtime" / "python" / "Lib" / "site-packages" / "nvcuda.dll"
    forbidden.write_bytes(b"must not be bundled")
    with pytest.raises(BuildError, match="unexpected_nvidia_driver_or_npp_dll:nvcuda.dll"):
        capture_nvidia_runtime_inventory(package, {"files": rows})
    forbidden.unlink()
    forbidden = forbidden.with_name("nppc64_12.dll")
    forbidden.write_bytes(b"must not be bundled")
    with pytest.raises(BuildError, match="unexpected_nvidia_driver_or_npp_dll:nppc64_12.dll"):
        capture_nvidia_runtime_inventory(package, {"files": rows})
