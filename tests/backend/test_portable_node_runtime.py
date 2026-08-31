from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.node_runtime import (
    REQUIRED_PNPM_VERSION,
    RuntimeResolutionError,
    _subprocess_command,
    frontend_runtime_status,
    node_version_supported,
    resolve_node_runtime,
    resolve_pnpm_runtime,
)


def runner_for(versions: dict[str, str]):
    def run(command: list[str] | tuple[str, ...]) -> tuple[int, str, str]:
        executable = Path(command[0]).name.lower()
        if "--version" not in command:
            return 1, "", "unexpected command"
        if executable.startswith("node"):
            return 0, versions.get("node", "v24.14.0") + "\n", ""
        if executable.startswith("pnpm"):
            return 0, versions.get("pnpm", REQUIRED_PNPM_VERSION) + "\n", ""
        return 1, "", "unknown executable"

    return run


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def test_repository_local_node_resolution(tmp_path: Path) -> None:
    touch(tmp_path / ".local-tools" / "node" / "node.exe")
    runtime = resolve_node_runtime(tmp_path, env={"PATH": ""}, runner=runner_for({}))
    assert runtime.source_label == "node_repository_local"
    assert runtime.version == "v24.14.0"


def test_configured_node_dir_wins_over_repository_local(tmp_path: Path) -> None:
    configured = tmp_path / "Approved Runtime"
    touch(configured / "node.exe")
    touch(tmp_path / ".local-tools" / "node" / "node.exe")
    runtime = resolve_node_runtime(tmp_path, env={"TVA_NODE_DIR": str(configured), "PATH": ""}, runner=runner_for({}))
    assert runtime.source_label == "node_configured_path"
    assert runtime.directory == str(configured)


def test_invalid_configured_node_dir_does_not_fallback(tmp_path: Path) -> None:
    touch(tmp_path / ".local-tools" / "node" / "node.exe")
    with pytest.raises(RuntimeResolutionError, match="TVA_NODE_DIR"):
        resolve_node_runtime(tmp_path, env={"TVA_NODE_DIR": str(tmp_path / "missing"), "PATH": ""}, runner=runner_for({}))


def test_missing_node_executable_is_controlled(tmp_path: Path) -> None:
    (tmp_path / ".local-tools" / "node").mkdir(parents=True)
    with pytest.raises(RuntimeResolutionError) as excinfo:
        resolve_node_runtime(tmp_path, env={"PATH": ""}, runner=runner_for({}))
    assert excinfo.value.code == "node_executable_missing"


def test_no_runtime_found_reports_node_missing(tmp_path: Path) -> None:
    with pytest.raises(RuntimeResolutionError) as excinfo:
        resolve_node_runtime(tmp_path, env={"PATH": ""}, path_value="", runner=runner_for({}))
    assert excinfo.value.code == "node_missing"


def test_unsupported_node_version_is_rejected(tmp_path: Path) -> None:
    touch(tmp_path / ".local-tools" / "node" / "node.exe")
    with pytest.raises(RuntimeResolutionError) as excinfo:
        resolve_node_runtime(tmp_path, env={"PATH": ""}, runner=runner_for({"node": "v18.19.0"}))
    assert excinfo.value.code == "node_unsupported_version"


def test_node_v26_is_supported_after_candidate_evaluation() -> None:
    assert node_version_supported("v26.5.0")


def test_node_v27_requires_separate_evaluation() -> None:
    assert not node_version_supported("v27.0.0")


def test_path_resolution_supports_spaces_and_ampersand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path_dir = tmp_path / "IT Approved & Portable" / "node bin"
    touch(path_dir / "node.exe")
    monkeypatch.setenv("PATH", str(path_dir))
    runtime = resolve_node_runtime(tmp_path, env={}, path_value=str(path_dir), runner=runner_for({}))
    assert runtime.source_label == "node_system_path"
    assert "IT Approved & Portable" in runtime.executable


def test_pnpm_managed_runtime_fallback_requires_verified_layout(tmp_path: Path) -> None:
    base = tmp_path / "managed-runtimes" / "primary-runtime" / "dependencies"
    pnpm_dir = base / "bin" / "fallback"
    node_dir = base / "node" / "bin"
    touch(pnpm_dir / "pnpm.cmd")
    touch(node_dir / "node.exe")
    runtime = resolve_node_runtime(tmp_path, env={}, path_value=str(pnpm_dir), runner=runner_for({}))
    assert runtime.source_label == "node_pnpm_fallback"


def test_pnpm_missing_reports_unavailable(tmp_path: Path) -> None:
    touch(tmp_path / ".local-tools" / "node" / "node.exe")
    node = resolve_node_runtime(tmp_path, env={"PATH": ""}, runner=runner_for({}))
    pnpm = resolve_pnpm_runtime(node, tmp_path, env={"PATH": ""}, path_value="", runner=runner_for({}))
    assert pnpm.available is False
    assert pnpm.error_code == "pnpm_missing"


def test_missing_configured_pnpm_command_blocks_fallback(tmp_path: Path) -> None:
    touch(tmp_path / ".local-tools" / "node" / "node.exe")
    touch(tmp_path / ".local-tools" / "pnpm" / "pnpm.cmd")
    node = resolve_node_runtime(tmp_path, env={"PATH": ""}, runner=runner_for({}))
    pnpm = resolve_pnpm_runtime(
        node,
        tmp_path,
        env={"TVA_PNPM_CMD": str(tmp_path / "missing-pnpm.cmd"), "PATH": ""},
        path_value="",
        runner=runner_for({}),
    )
    assert pnpm.available is False
    assert pnpm.source == "configured"
    assert pnpm.error_code == "configured_pnpm_cmd_missing"


def test_pnpm_version_mismatch_blocks_setup(tmp_path: Path) -> None:
    touch(tmp_path / ".local-tools" / "node" / "node.exe")
    touch(tmp_path / ".local-tools" / "pnpm" / "pnpm.cmd")
    node = resolve_node_runtime(tmp_path, env={"PATH": ""}, runner=runner_for({}))
    pnpm = resolve_pnpm_runtime(node, tmp_path, env={"PATH": ""}, path_value="", runner=runner_for({"pnpm": "10.0.0"}))
    assert pnpm.available is False
    assert pnpm.error_code == "pnpm_version_mismatch"


def test_damaged_repository_local_pnpm_blocks_path_fallback(tmp_path: Path) -> None:
    touch(tmp_path / ".local-tools" / "node" / "node.exe")
    touch(tmp_path / ".local-tools" / "pnpm" / "pnpm.cmd")
    path_pnpm = tmp_path / "path-tools" / "pnpm.cmd"
    touch(path_pnpm)
    node = resolve_node_runtime(tmp_path, env={"PATH": ""}, runner=runner_for({}))

    def runner(command: list[str] | tuple[str, ...]) -> tuple[int, str, str]:
        if Path(command[0]).parent.name == "pnpm":
            return 1, "", "damaged pnpm"
        return runner_for({})(command)

    pnpm = resolve_pnpm_runtime(node, tmp_path, env={"PATH": str(path_pnpm.parent)}, runner=runner)
    assert pnpm.available is False
    assert pnpm.source == "repository-local"
    assert pnpm.error_code == "pnpm_version_failed"


@pytest.mark.skipif(os.name != "nt", reason="Windows command wrapping only applies to .cmd launchers")
def test_windows_cmd_wrapping_quotes_paths_with_ampersand() -> None:
    command = _subprocess_command([r"C:\project & data\.local-tools\pnpm\pnpm.cmd", "--version"])
    assert isinstance(command, str)
    assert command.startswith('"C:\\project & data\\.local-tools\\pnpm\\pnpm.cmd"')
    assert '"--version"' in command


def test_frontend_status_does_not_modify_process_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    touch(tmp_path / ".local-tools" / "node" / "node.exe")
    before = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", before)
    status = frontend_runtime_status(tmp_path, env={"PATH": before}, path_value="", runner=runner_for({}))
    assert status.node is not None
    assert os.environ.get("PATH", "") == before
    assert status.normal_launch_supported is False
