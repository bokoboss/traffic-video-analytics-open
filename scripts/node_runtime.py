from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
import re
import shutil
import subprocess
from typing import Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_NODE_MIN = (20, 19, 0)
SUPPORTED_NODE_MAX_EXCLUSIVE = (27, 0, 0)
REQUIRED_PNPM_VERSION = "11.9.0"


class RuntimeResolutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class NodeRuntime:
    source: str
    source_label: str
    executable: str
    directory: str
    version: str
    supported: bool
    error_code: str | None = None


@dataclass(frozen=True)
class PnpmRuntime:
    available: bool
    executable: str | None
    version: str | None
    required_version: str
    source: str | None
    error_code: str | None = None


@dataclass(frozen=True)
class FrontendRuntimeStatus:
    node: NodeRuntime | None
    pnpm: PnpmRuntime
    setup_complete: bool
    normal_launch_supported: bool
    child_path_preview: list[str]
    warnings: list[str]


Runner = Callable[[Sequence[str]], tuple[int, str, str]]


def _quote_cmd_argument(value: object) -> str:
    return '"' + str(value).replace('"', r"\"") + '"'


def _subprocess_command(command: Sequence[str]) -> list[str] | str:
    executable = Path(command[0])
    if os.name == "nt" and executable.suffix.lower() in {".bat", ".cmd"}:
        return " ".join(_quote_cmd_argument(part) for part in [executable, *command[1:]])
    return [str(part) for part in command]


def default_runner(command: Sequence[str]) -> tuple[int, str, str]:
    prepared = _subprocess_command(command)
    try:
        result = subprocess.run(
            prepared,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
            shell=isinstance(prepared, str),
        )
    except OSError as exc:
        return 1, "", str(exc)
    return result.returncode, result.stdout, result.stderr


def parse_node_version(version: str) -> tuple[int, int, int] | None:
    match = re.search(r"v?(\d+)\.(\d+)\.(\d+)", version)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def node_version_supported(version: str) -> bool:
    parsed = parse_node_version(version)
    if parsed is None:
        return False
    return SUPPORTED_NODE_MIN <= parsed < SUPPORTED_NODE_MAX_EXCLUSIVE


def _run_version(executable: Path, runner: Runner) -> str:
    code, stdout, stderr = runner([str(executable), "--version"])
    if code != 0:
        raise RuntimeResolutionError("node_version_failed", stderr.strip() or "node --version failed")
    version = stdout.splitlines()[0].strip() if stdout.strip() else ""
    if not version:
        raise RuntimeResolutionError("node_version_empty", "node --version returned no version")
    if not node_version_supported(version):
        raise RuntimeResolutionError("node_unsupported_version", f"Unsupported Node.js version {version}")
    return version


def _node_from_dir(directory: Path, source: str, label: str, runner: Runner) -> NodeRuntime:
    executable = directory / "node.exe"
    if not executable.exists():
        raise RuntimeResolutionError("node_executable_missing", f"Node executable not found: {executable}")
    version = _run_version(executable, runner)
    return NodeRuntime(
        source=source,
        source_label=label,
        executable=str(executable),
        directory=str(directory),
        version=version,
        supported=True,
    )


def _which(command: str, path: str | None) -> str | None:
    return shutil.which(command, path=path)


def _pnpm_managed_node(path_value: str | None) -> Path | None:
    pnpm = _which("pnpm", path_value)
    if pnpm is None:
        return None
    pnpm_path = Path(pnpm).resolve()
    try:
        candidate = (pnpm_path.parent / ".." / ".." / "node" / "bin" / "node.exe").resolve()
    except RuntimeError:
        return None
    pnpm_text = str(pnpm_path).lower()
    candidate_text = str(candidate).lower()
    if "\\dependencies\\bin\\fallback\\" not in pnpm_text:
        return None
    if "\\dependencies\\node\\bin\\node.exe" not in candidate_text:
        return None
    return candidate if candidate.exists() else None


def resolve_node_runtime(
    root: Path = ROOT,
    env: Mapping[str, str] | None = None,
    path_value: str | None = None,
    runner: Runner = default_runner,
) -> NodeRuntime:
    environment = dict(os.environ if env is None else env)
    search_path = environment.get("PATH") if path_value is None else path_value

    configured = environment.get("TVA_NODE_DIR")
    if configured:
        directory = Path(configured).expanduser()
        if not directory.exists():
            raise RuntimeResolutionError("configured_node_dir_missing", f"TVA_NODE_DIR does not exist: {directory}")
        return _node_from_dir(directory, "configured", "node_configured_path", runner)

    local = root / ".local-tools" / "node"
    if local.exists():
        return _node_from_dir(local, "repository-local", "node_repository_local", runner)

    path_node = _which("node", search_path)
    if path_node is not None:
        executable = Path(path_node).resolve()
        version = _run_version(executable, runner)
        return NodeRuntime(
            source="system PATH",
            source_label="node_system_path",
            executable=str(executable),
            directory=str(executable.parent),
            version=version,
            supported=True,
        )

    fallback = _pnpm_managed_node(search_path)
    if fallback is not None:
        version = _run_version(fallback, runner)
        return NodeRuntime(
            source="pnpm runtime fallback",
            source_label="node_pnpm_fallback",
            executable=str(fallback),
            directory=str(fallback.parent),
            version=version,
            supported=True,
        )

    raise RuntimeResolutionError("node_missing", "No approved Node.js runtime was found")


def resolve_pnpm_runtime(
    node_runtime: NodeRuntime | None,
    root: Path = ROOT,
    env: Mapping[str, str] | None = None,
    path_value: str | None = None,
    runner: Runner = default_runner,
) -> PnpmRuntime:
    environment = dict(os.environ if env is None else env)
    path_parts: list[str] = []
    if node_runtime is not None:
        path_parts.append(node_runtime.directory)
    if path_value is not None:
        path_parts.append(path_value)
    else:
        path_parts.append(environment.get("PATH", ""))
    child_path = os.pathsep.join(part for part in path_parts if part)

    candidates: list[tuple[str, Path]] = []
    configured = environment.get("TVA_PNPM_CMD")
    if configured:
        configured_path = Path(configured).expanduser()
        if not configured_path.exists():
            return PnpmRuntime(False, str(configured_path), None, REQUIRED_PNPM_VERSION, "configured", "configured_pnpm_cmd_missing")
        candidates.append(("configured", configured_path))
    local = root / ".local-tools" / "pnpm" / "pnpm.cmd"
    if local.exists():
        candidates.append(("repository-local", local))
    path_pnpm = _which("pnpm", child_path)
    if path_pnpm is not None:
        candidates.append(("PATH", Path(path_pnpm)))

    for source, executable in candidates:
        if not executable.exists():
            continue
        code, stdout, _stderr = runner([str(executable), "--version"])
        version = stdout.splitlines()[0].strip() if code == 0 and stdout.strip() else None
        if version == REQUIRED_PNPM_VERSION:
            return PnpmRuntime(True, str(executable), version, REQUIRED_PNPM_VERSION, source)
        if version is None and source in {"configured", "repository-local"}:
            return PnpmRuntime(False, str(executable), None, REQUIRED_PNPM_VERSION, source, "pnpm_version_failed")
        if version is not None:
            return PnpmRuntime(False, str(executable), version, REQUIRED_PNPM_VERSION, source, "pnpm_version_mismatch")

    return PnpmRuntime(False, None, None, REQUIRED_PNPM_VERSION, None, "pnpm_missing")


def frontend_runtime_status(
    root: Path = ROOT,
    env: Mapping[str, str] | None = None,
    path_value: str | None = None,
    runner: Runner = default_runner,
) -> FrontendRuntimeStatus:
    warnings: list[str] = []
    try:
        node = resolve_node_runtime(root, env, path_value, runner)
    except RuntimeResolutionError as exc:
        node = None
        warnings.append(exc.code)
    pnpm = resolve_pnpm_runtime(node, root, env, path_value, runner)
    if pnpm.error_code:
        warnings.append(pnpm.error_code)
    setup_complete = (root / ".local-data" / "runtime" / "setup-complete.json").exists()
    child_path_preview = []
    if node is not None:
        child_path_preview.append(node.directory)
    if pnpm.executable is not None:
        child_path_preview.append(str(Path(pnpm.executable).parent))
    return FrontendRuntimeStatus(
        node=node,
        pnpm=pnpm,
        setup_complete=setup_complete,
        normal_launch_supported=node is not None and pnpm.available and setup_complete,
        child_path_preview=child_path_preview,
        warnings=warnings,
    )


def frontend_runtime_status_dict() -> dict[str, object]:
    return asdict(frontend_runtime_status())
