from __future__ import annotations

import base64
import ctypes
import json
import os
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

import apps.backend.app.runtime_paths as runtime_paths
import scripts.portable.launcher as portable_launcher
import scripts.build_portable_distribution as portable_distribution
import scripts.portable.process_ownership as process_ownership
from scripts.build_portable_distribution import package_file_records, privacy_scan, verify_manifest
from scripts.portable import runtime_lock
from scripts.verify_portable_distribution import verify_zip
from scripts.portable.process_ownership import (
    ProcessIdentity,
    ProcessStopResult,
    command_signature,
    is_owned_record,
    normalize_path,
    process_command_line,
    record_process,
    stop_owned_process,
)
from scripts.portable.runtime_lock import (
    RuntimeLockError,
    calculate_runtime_path_budget,
    enforce_runtime_path_budget,
    utf16_code_units,
)
from scripts.portable.privacy import packaged_provenance_absolute_path_violations
from scripts.portable.static_server import StaticServer


def test_launcher_direct_execution_bootstraps_local_import_path() -> None:
    launcher = Path(__file__).parents[2] / "scripts" / "portable" / "launcher.py"
    probe = f"""
import runpy
import sys

sys.path = [entry for entry in sys.path if entry not in {{"", r"{launcher.parent}"}}]
sys.argv = [r"{launcher}", "--help"]
runpy.run_path(r"{launcher}", run_name="__main__")
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "portable lifecycle launcher" in result.stdout


def test_benchmark_package_import_does_not_require_optional_harness() -> None:
    probe = """
import sys
import tools.benchmark

print("tools.benchmark.harness" in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


def test_portable_launcher_passes_worker_identity_to_backend_and_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package = tmp_path / "TrafficVideoAnalytics"
    (package / "runtime" / "python").mkdir(parents=True)
    (package / "runtime" / "python" / "python.exe").write_bytes(b"python")
    (package / "app").mkdir()
    (package / "frontend" / "dist").mkdir(parents=True)
    (package / "frontend" / "dist" / "index.html").write_text("<html />", encoding="utf-8")
    captured: list[dict[str, object]] = []

    monkeypatch.setattr(portable_launcher, "duplicate_instance", lambda root: False)
    monkeypatch.setattr(portable_launcher, "port_open", lambda port: False)
    monkeypatch.setattr(
        portable_launcher,
        "spawn_child",
        lambda **kwargs: captured.append(kwargs),
    )
    monkeypatch.setattr(
        portable_launcher,
        "wait_until",
        lambda predicate, timeout_seconds, label: None,
    )

    assert portable_launcher.run_instance(package, no_browser=True) == 0

    backend = next(item for item in captured if item["role"] == "backend")
    worker = next(item for item in captured if item["role"] == "worker")
    environment = backend["environment"]
    worker_environment = worker["environment"]
    worker_command = worker["command"]
    assert isinstance(environment, dict)
    assert isinstance(worker_environment, dict)
    assert environment["TVA_WORKER_ID"]
    assert environment["TVA_WORKER_INSTANCE_TOKEN"]
    assert worker_environment["TVA_WORKER_ID"] == environment["TVA_WORKER_ID"]
    assert worker_environment["TVA_WORKER_INSTANCE_TOKEN"] == environment["TVA_WORKER_INSTANCE_TOKEN"]
    assert worker_command[-1] == environment["TVA_WORKER_INSTANCE_TOKEN"]


def test_portable_runtime_paths_take_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package = tmp_path / "TVA Portable With Spaces"
    app = package / "app"
    python = package / "runtime" / "python" / "python.exe"
    site_packages = package / "runtime" / "python" / "Lib" / "site-packages"
    model_dir = package / "models"
    ffmpeg_dir = package / "runtime" / "ffmpeg"
    for path in (site_packages, model_dir, ffmpeg_dir):
        path.mkdir(parents=True, exist_ok=True)
    for name, value in {
        "TVA_SOURCE_ROOT": app,
        "TVA_PACKAGE_ROOT": package,
        "TVA_PYTHON": python,
        "TVA_AI_PYTHON": python,
        "TVA_AI_SITE_PACKAGES": site_packages,
        "TVA_MODEL_DIR": model_dir,
        "TVA_FFMPEG_DIR": ffmpeg_dir,
    }.items():
        monkeypatch.setenv(name, str(value))

    assert runtime_paths.source_root() == app.resolve()
    assert runtime_paths.package_root() == package.resolve()
    assert runtime_paths.ai_python() == python.resolve()
    assert runtime_paths.ai_site_packages() == (site_packages.resolve(),)
    assert runtime_paths.model_dir() == model_dir.resolve()


def test_legacy_runtime_fallback_is_preserved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in (
        "TVA_SOURCE_ROOT",
        "TVA_APP_ROOT",
        "TVA_PACKAGE_ROOT",
        "TVA_PYTHON",
        "TVA_AI_PYTHON",
        "TVA_AI_SITE_PACKAGES",
        "TVA_MODEL_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(runtime_paths, "SOURCE_ROOT", tmp_path)

    assert runtime_paths.source_root() == tmp_path
    assert runtime_paths.ai_python() == (tmp_path / ".venv-ai" / "Scripts" / "python.exe").resolve()
    assert (
        runtime_paths.ai_site_packages()[0]
        == (tmp_path / ".venv-ai" / "Lib" / "site-packages").resolve()
    )
    assert runtime_paths.model_dir() == (tmp_path / ".local-tools" / "models").resolve()


def test_static_server_serves_assets_and_spa_routes_without_directory_listing(
    tmp_path: Path,
) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>TVA</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    server = StaticServer("127.0.0.1", 0, dist)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/", timeout=2) as response:
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("text/html")
            assert b"TVA" in response.read()
        with urlopen(base + "/assets/app.js", timeout=2) as response:
            assert response.headers["Content-Type"].startswith("text/javascript")
        with urlopen(base + "/projects/123", timeout=2) as response:
            assert response.status == 200
            assert b"TVA" in response.read()
        with pytest.raises(HTTPError) as error:
            urlopen(base + "/assets/", timeout=2)
        assert error.value.code == 404
        assert b"app.js" not in error.value.read()
        with pytest.raises(HTTPError) as error:
            urlopen(base + "/__tva_static_health/", timeout=2)
        assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_process_ownership_requires_exact_identity_and_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "python.exe"
    command = f'"{executable}" -m uvicorn apps.backend.app.main:app --port 8000'
    identity = ProcessIdentity(1234, normalize_path(executable), 9876)
    record = record_process(
        identity=identity,
        package_root=tmp_path,
        role="backend",
        command_line=command,
        port=8000,
        owner_token="test-token",
    )
    monkeypatch.setattr("scripts.portable.process_ownership.process_identity", lambda pid: identity)
    monkeypatch.setattr(
        "scripts.portable.process_ownership.process_command_line", lambda pid: command
    )

    assert is_owned_record(
        record, tmp_path, expected_role="backend", expected_executable=executable
    )
    record["process_start_time"] = 9877
    assert not is_owned_record(
        record, tmp_path, expected_role="backend", expected_executable=executable
    )


class _FakeNativeFunction:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


class _FakeKernel32:
    def __init__(self, *, executable: str, start_time: int, wait_results: list[int], terminate_result: bool = True):
        self.handle = 0x1234
        self.executable = executable
        self.start_time = start_time
        self.wait_results = list(wait_results)
        self.terminate_result = terminate_result
        self.process_ids: list[int] = []
        self.enum_succeeds = True
        self.enum_full_once = False
        self.open_calls = []
        self.identity_query_handles = []
        self.times_query_handles = []
        self.wait_calls = []
        self.terminate_calls = []
        self.close_calls = []
        self.enum_calls = []
        self.call_order = []
        self.OpenProcess = _FakeNativeFunction(self._open_process)
        self.K32EnumProcesses = _FakeNativeFunction(self._enum_processes)
        self.QueryFullProcessImageNameW = _FakeNativeFunction(self._query_image)
        self.GetProcessTimes = _FakeNativeFunction(self._get_times)
        self.WaitForSingleObject = _FakeNativeFunction(self._wait)
        self.TerminateProcess = _FakeNativeFunction(self._terminate)
        self.CloseHandle = _FakeNativeFunction(self._close)

    def _open_process(self, access, inherit, pid):
        self.call_order.append("open")
        self.open_calls.append((access, inherit, pid))
        return self.handle

    def _enum_processes(self, process_ids, buffer_size, bytes_needed):
        self.call_order.append("enum")
        self.enum_calls.append(buffer_size)
        if not self.enum_succeeds:
            return 0
        item_size = ctypes.sizeof(ctypes.c_uint32)
        capacity = buffer_size // item_size
        if self.enum_full_once:
            self.enum_full_once = False
            for index in range(capacity):
                process_ids[index] = 0
            bytes_needed._obj.value = buffer_size
            return 1
        count = min(len(self.process_ids), capacity)
        for index, pid in enumerate(self.process_ids[:count]):
            process_ids[index] = pid
        bytes_needed._obj.value = count * item_size
        return 1

    def _query_image(self, handle, _flags, buffer, size):
        self.identity_query_handles.append(handle)
        buffer.value = self.executable
        size._obj.value = len(self.executable)
        return 1

    def _get_times(self, handle, created, _exited, _kernel, _user):
        self.times_query_handles.append(handle)
        created._obj.dwLowDateTime = self.start_time & 0xFFFFFFFF
        created._obj.dwHighDateTime = (self.start_time >> 32) & 0xFFFFFFFF
        return 1

    def _wait(self, handle, timeout_ms):
        self.wait_calls.append((handle, timeout_ms))
        return self.wait_results.pop(0) if self.wait_results else process_ownership.WAIT_TIMEOUT

    def _terminate(self, handle, _exit_code):
        self.terminate_calls.append(handle)
        return int(self.terminate_result)

    def _close(self, handle):
        self.close_calls.append(handle)
        return 1


def _windows_stop_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    native_executable: Path | None = None,
    native_start_time: int = 9876,
    observed_command: str | None = '"C:\\TVA\\python.exe" -m app --label "ทดสอบ"',
    wait_results: list[int] | None = None,
    terminate_result: bool = True,
) -> tuple[dict[str, object], Path, _FakeKernel32, str]:
    executable = tmp_path / "python.exe"
    command = '"C:\\TVA\\python.exe" -m app --label "ทดสอบ"'
    identity = ProcessIdentity(1234, normalize_path(executable), 9876)
    record = record_process(
        identity=identity,
        package_root=tmp_path,
        role="backend",
        command_line=command,
        port=8000,
        owner_token="test-token",
    )
    native = native_executable or executable
    fake = _FakeKernel32(
        executable=normalize_path(native),
        start_time=native_start_time,
        wait_results=wait_results or [process_ownership.WAIT_TIMEOUT] * 3,
        terminate_result=terminate_result,
    )
    monkeypatch.setattr(process_ownership.ctypes, "WinDLL", lambda *args, **kwargs: fake)
    monkeypatch.setattr(process_ownership, "process_command_line", lambda pid: observed_command)
    return record, executable, fake, command


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle contract")
def test_windows_failed_open_with_absent_pid_is_already_stopped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record, executable, fake, _command = _windows_stop_fixture(monkeypatch, tmp_path)
    fake.handle = 0

    def get_last_error() -> int:
        fake.call_order.append("get_last_error")
        return 87

    monkeypatch.setattr(process_ownership.ctypes, "get_last_error", get_last_error)

    result = stop_owned_process(
        record,
        tmp_path,
        expected_role="backend",
        expected_executable=executable,
    )

    assert result == ProcessStopResult(True, "already_stopped")
    assert len(fake.open_calls) == 1
    assert fake.call_order == ["open", "get_last_error", "enum"]
    assert fake.terminate_calls == []
    assert fake.close_calls == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle contract")
def test_launcher_removes_stale_record_after_absent_pid_proof(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record, executable, fake, _command = _windows_stop_fixture(monkeypatch, tmp_path)
    fake.handle = 0
    monkeypatch.setattr(process_ownership.ctypes, "get_last_error", lambda: 87)
    portable_launcher.write_record(tmp_path, "backend", record)
    monkeypatch.setattr(portable_launcher, "ROLES", ("backend",))
    monkeypatch.setattr(portable_launcher, "python_for", lambda _root: executable)

    assert portable_launcher.stop_owned(tmp_path, verbose=False)
    assert portable_launcher.read_record(tmp_path, "backend") is None
    assert len(fake.open_calls) == 1
    assert fake.enum_calls == [4096]
    assert fake.terminate_calls == []
    assert fake.close_calls == []


@pytest.mark.parametrize(
    ("open_error", "process_ids", "enum_succeeds", "expected_status", "enum_calls"),
    [
        (5, [], True, "process_open_access_denied", 0),
        (87, [1234], True, "process_open_failed", 1),
        (87, [], False, "process_presence_unavailable", 1),
    ],
)
@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle contract")
def test_launcher_retains_record_when_failed_open_is_not_proven_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    open_error: int,
    process_ids: list[int],
    enum_succeeds: bool,
    expected_status: str,
    enum_calls: int,
) -> None:
    record, executable, fake, _command = _windows_stop_fixture(monkeypatch, tmp_path)
    fake.handle = 0
    fake.process_ids = process_ids
    fake.enum_succeeds = enum_succeeds
    monkeypatch.setattr(process_ownership.ctypes, "get_last_error", lambda: open_error)
    portable_launcher.write_record(tmp_path, "backend", record)
    monkeypatch.setattr(portable_launcher, "ROLES", ("backend",))
    monkeypatch.setattr(portable_launcher, "python_for", lambda _root: executable)
    observed: list[ProcessStopResult] = []
    actual_stop = portable_launcher.stop_owned_process

    def observe_stop(*args: object, **kwargs: object) -> ProcessStopResult:
        result = actual_stop(*args, **kwargs)
        observed.append(result)
        return result

    monkeypatch.setattr(portable_launcher, "stop_owned_process", observe_stop)

    assert not portable_launcher.stop_owned(tmp_path, verbose=False)
    assert observed == [ProcessStopResult(False, expected_status)]
    assert portable_launcher.read_record(tmp_path, "backend") is not None
    assert len(fake.open_calls) == 1
    assert len(fake.enum_calls) == enum_calls
    assert fake.terminate_calls == []
    assert fake.close_calls == []


@pytest.mark.parametrize("process_ids", [[], [1234]])
@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle contract")
def test_windows_process_presence_retries_full_buffer_before_classifying(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    process_ids: list[int],
) -> None:
    _record, _executable, fake, _command = _windows_stop_fixture(monkeypatch, tmp_path)
    fake.process_ids = process_ids
    fake.enum_full_once = True

    result = process_ownership._windows_process_presence(1234)

    assert result == ("PRESENT" if process_ids else "ABSENT")
    assert fake.enum_calls == [4096, 8192]
    assert fake.open_calls == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle contract")
def test_windows_process_presence_api_failure_is_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _record, _executable, fake, _command = _windows_stop_fixture(monkeypatch, tmp_path)
    fake.enum_succeeds = False

    assert process_ownership._windows_process_presence(1234) == "UNKNOWN"
    assert fake.enum_calls == [4096]
    assert fake.open_calls == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows child lifecycle")
def test_windows_naturally_exited_child_record_recovers_and_fresh_start_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(portable_launcher, "app_root_for", lambda root: root)
    monkeypatch.setattr(portable_launcher, "ROLES", ("backend",))
    tmp_path.mkdir(parents=True, exist_ok=True)
    python = Path(sys.executable)
    expected_executable: list[Path] = []
    monkeypatch.setattr(
        portable_launcher,
        "python_for",
        lambda _root: expected_executable[0] if expected_executable else python,
    )
    termination_requests: list[int] = []
    original_terminate = process_ownership._WindowsProcessHandle.terminate

    def track_termination(process: process_ownership._WindowsProcessHandle) -> bool:
        termination_requests.append(process.pid)
        return original_terminate(process)

    monkeypatch.setattr(process_ownership._WindowsProcessHandle, "terminate", track_termination)
    children: list[subprocess.Popen[bytes]] = []
    markers = [tmp_path / "first-exit", tmp_path / "second-exit", tmp_path / "unrelated-exit"]

    def wait_command(marker: Path) -> list[str]:
        code = (
            "from pathlib import Path\n"
            "import time\n"
            f"marker = Path({str(marker)!r})\n"
            "deadline = time.monotonic() + 20\n"
            "while not marker.exists() and time.monotonic() < deadline:\n"
            "    time.sleep(0.05)\n"
        )
        return [str(python), "-c", code]

    try:
        unrelated = subprocess.Popen(
            wait_command(markers[2]),
            cwd=tmp_path,
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        children.append(unrelated)

        first = portable_launcher.spawn_child(
            root=tmp_path,
            role="backend",
            command=wait_command(markers[0]),
            port=None,
            environment=os.environ.copy(),
            owner_token="r17-1-first",
        )
        children.append(first)
        identity = process_ownership.process_identity(first.pid)
        assert identity is not None
        expected_executable.append(Path(identity.executable))
        assert portable_launcher.read_record(tmp_path, "backend") is not None

        markers[0].touch()
        first.wait(timeout=10)
        assert process_ownership._windows_process_presence(first.pid) == "ABSENT"

        assert portable_launcher.stop_owned(tmp_path, verbose=False)
        assert portable_launcher.read_record(tmp_path, "backend") is None
        assert termination_requests == []
        assert unrelated.poll() is None

        second = portable_launcher.spawn_child(
            root=tmp_path,
            role="backend",
            command=wait_command(markers[1]),
            port=None,
            environment=os.environ.copy(),
            owner_token="r17-1-second",
        )
        children.append(second)
        assert second.poll() is None
        second_record = portable_launcher.read_record(tmp_path, "backend")
        assert second_record is not None
        assert second_record["pid"] == second.pid
        assert unrelated.poll() is None
        assert termination_requests == []

        assert portable_launcher.stop_owned(tmp_path, verbose=False)
        assert portable_launcher.read_record(tmp_path, "backend") is None
        second.wait(timeout=10)
        assert termination_requests == [second.pid]
    finally:
        for marker in markers:
            marker.touch()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=5)
        if portable_launcher.read_record(tmp_path, "backend") is not None:
            portable_launcher.stop_owned(tmp_path, verbose=False)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle contract")
def test_windows_stop_uses_one_retained_handle_for_identity_termination_and_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record, executable, fake, _command = _windows_stop_fixture(
        monkeypatch,
        tmp_path,
        wait_results=[
            process_ownership.WAIT_TIMEOUT,
            process_ownership.WAIT_TIMEOUT,
            process_ownership.WAIT_TIMEOUT,
            process_ownership.WAIT_OBJECT_0,
        ],
    )

    result = stop_owned_process(
        record,
        tmp_path,
        expected_role="backend",
        expected_executable=executable,
        timeout_seconds=5,
    )

    expected_access = (
        process_ownership.PROCESS_QUERY_LIMITED_INFORMATION
        | process_ownership.SYNCHRONIZE
        | process_ownership.PROCESS_TERMINATE
    )
    assert result == ProcessStopResult(True, "stopped")
    assert fake.open_calls == [(expected_access, False, 1234)]
    assert fake.identity_query_handles == [fake.handle]
    assert fake.times_query_handles == [fake.handle]
    assert fake.terminate_calls == [fake.handle]
    assert {handle for handle, _timeout in fake.wait_calls} == {fake.handle}
    assert fake.close_calls == [fake.handle]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle contract")
def test_windows_pid_reuse_after_validation_never_opens_or_terminates_replacement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record, executable, fake, _command = _windows_stop_fixture(
        monkeypatch,
        tmp_path,
        observed_command='"C:\\TVA\\python.exe" -m app --label "ทดสอบ" replacement-process',
        wait_results=[process_ownership.WAIT_TIMEOUT, process_ownership.WAIT_OBJECT_0],
    )

    result = stop_owned_process(
        record,
        tmp_path,
        expected_role="backend",
        expected_executable=executable,
    )

    assert result == ProcessStopResult(True, "already_stopped")
    assert len(fake.open_calls) == 1
    assert fake.terminate_calls == []
    assert fake.close_calls == [fake.handle]


@pytest.mark.parametrize("mismatch", ["executable", "creation", "command", "unavailable"])
@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle contract")
def test_windows_ownership_mismatch_or_command_failure_never_terminates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mismatch: str
) -> None:
    record, executable, fake, _command = _windows_stop_fixture(
        monkeypatch,
        tmp_path,
        native_executable=tmp_path / "other.exe" if mismatch == "executable" else None,
        native_start_time=9877 if mismatch == "creation" else 9876,
        observed_command=(
            None
            if mismatch == "unavailable"
            else '"C:\\TVA\\python.exe" -m app --label "ทดสอบ" changed'
            if mismatch == "command"
            else '"C:\\TVA\\python.exe" -m app --label "ทดสอบ"'
        ),
        wait_results=[process_ownership.WAIT_TIMEOUT] * 3,
    )

    result = stop_owned_process(
        record,
        tmp_path,
        expected_role="backend",
        expected_executable=executable,
    )

    assert result.safe is False
    assert result.status in {"ownership_mismatch", "command_line_unavailable"}
    assert fake.terminate_calls == []
    assert fake.close_calls == [fake.handle]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle contract")
def test_windows_expected_executable_mismatch_never_terminates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record, executable, fake, _command = _windows_stop_fixture(monkeypatch, tmp_path)

    result = stop_owned_process(
        record,
        tmp_path,
        expected_role="backend",
        expected_executable=tmp_path / "other.exe",
    )

    assert result == ProcessStopResult(False, "ownership_mismatch")
    assert fake.terminate_calls == []
    assert fake.close_calls == [fake.handle]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle contract")
def test_windows_already_signaled_process_is_safe_without_command_line_or_pid_reopen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record, executable, fake, _command = _windows_stop_fixture(
        monkeypatch,
        tmp_path,
        wait_results=[process_ownership.WAIT_OBJECT_0],
    )
    command_calls: list[int] = []
    monkeypatch.setattr(
        process_ownership,
        "process_command_line",
        lambda pid: command_calls.append(pid) or pytest.fail("command line queried after exit"),
    )

    result = stop_owned_process(
        record,
        tmp_path,
        expected_role="backend",
        expected_executable=executable,
    )

    assert result == ProcessStopResult(True, "already_stopped")
    assert command_calls == []
    assert fake.open_calls and len(fake.open_calls) == 1
    assert fake.terminate_calls == []
    assert fake.close_calls == [fake.handle]


@pytest.mark.parametrize(
    ("post_failure_state", "expected"),
    [
        (process_ownership.WAIT_OBJECT_0, ProcessStopResult(True, "already_stopped")),
        (process_ownership.WAIT_TIMEOUT, ProcessStopResult(False, "termination_failed")),
    ],
)
@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle contract")
def test_windows_terminate_failure_fails_closed_unless_retained_handle_is_signaled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    post_failure_state: int,
    expected: ProcessStopResult,
) -> None:
    record, executable, fake, _command = _windows_stop_fixture(
        monkeypatch,
        tmp_path,
        wait_results=[
            process_ownership.WAIT_TIMEOUT,
            process_ownership.WAIT_TIMEOUT,
            process_ownership.WAIT_TIMEOUT,
            post_failure_state,
        ],
        terminate_result=False,
    )

    result = stop_owned_process(
        record,
        tmp_path,
        expected_role="backend",
        expected_executable=executable,
    )

    assert result == expected
    assert fake.terminate_calls == [fake.handle]
    assert fake.close_calls == [fake.handle]


def test_launcher_removes_record_only_after_atomic_stop_is_safe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "python.exe"
    identity = ProcessIdentity(1234, normalize_path(executable), 9876)
    record = record_process(
        identity=identity,
        package_root=tmp_path,
        role="backend",
        command_line="python.exe -m app",
        port=8000,
        owner_token="test-token",
    )
    portable_launcher.write_record(tmp_path, "backend", record)
    monkeypatch.setattr(portable_launcher, "ROLES", ("backend",))
    monkeypatch.setattr(portable_launcher, "python_for", lambda root: executable)
    for name in ("owned", "process_identity", "terminate_process", "wait_for_exit"):
        monkeypatch.setattr(
            portable_launcher,
            name,
            lambda *args, **kwargs: pytest.fail(f"legacy PID stop helper called: {name}"),
            raising=False,
        )
    outcomes = iter(
        [ProcessStopResult(False, "ownership_mismatch"), ProcessStopResult(True, "already_stopped")]
    )
    monkeypatch.setattr(
        portable_launcher,
        "stop_owned_process",
        lambda *args, **kwargs: next(outcomes),
    )

    assert portable_launcher.stop_owned(tmp_path, verbose=False) is False
    assert portable_launcher.read_record(tmp_path, "backend") is not None
    assert portable_launcher.stop_owned(tmp_path, verbose=False) is True
    assert portable_launcher.read_record(tmp_path, "backend") is None


def test_process_command_line_round_trips_thai_unicode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = '"C:\\ทดสอบ TVA\\python.exe" -m app --label "ทดสอบภาษาไทย"'
    calls: list[dict[str, object]] = []

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append({"args": args, "kwargs": kwargs})
        return subprocess.CompletedProcess(
            args[0], 0, b"\r\n" + base64.b64encode(text.encode("utf-16le")) + b" \r\n", b""
        )

    monkeypatch.setattr(process_ownership.os, "name", "nt")
    monkeypatch.setattr(process_ownership.subprocess, "run", run)

    assert process_command_line(1234) == text
    assert "text" not in calls[0]["kwargs"]
    query = calls[0]["args"][0][-1]
    assert "OutputEncoding" in query
    assert "ToBase64String" in query
    assert "Encoding]::Unicode" in query


def test_process_command_line_round_trips_mixed_unicode_and_spaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "python.exe --path D:\\ทดสอบ TVA\\input file.mp4 --note 'English ไทย'"
    monkeypatch.setattr(process_ownership.os, "name", "nt")
    monkeypatch.setattr(
        process_ownership.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, base64.b64encode(text.encode("utf-16le")), b""
        ),
    )

    assert process_command_line(1234) == text


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [
        (0, b"\r\n"),
        (1, base64.b64encode("ignored".encode("utf-16le"))),
        (0, b"not-base64!"),
        (0, base64.b64encode(b"\x00")),
    ],
)
def test_process_command_line_transport_and_decode_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: bytes,
) -> None:
    monkeypatch.setattr(process_ownership.os, "name", "nt")
    monkeypatch.setattr(
        process_ownership.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], returncode, stdout, b""),
    )

    assert process_command_line(1234) is None


def test_process_command_line_timeout_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_ownership.os, "name", "nt")

    def timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(args[0], 5)

    monkeypatch.setattr(process_ownership.subprocess, "run", timeout)

    assert process_command_line(1234) is None


@pytest.mark.parametrize("changed", [False, True])
def test_unicode_command_line_remains_part_of_ownership_proof(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, changed: bool
) -> None:
    executable = tmp_path / "python.exe"
    command = f'"{executable}" -m app --label "ทดสอบภาษาไทย"'
    observed = command + (" changed" if changed else "")
    identity = ProcessIdentity(1234, normalize_path(executable), 9876)
    record = record_process(
        identity=identity,
        package_root=tmp_path,
        role="backend",
        command_line=command,
        port=8000,
        owner_token="test-token",
    )
    monkeypatch.setattr(process_ownership, "process_identity", lambda pid: identity)
    monkeypatch.setattr(process_ownership, "process_command_line", lambda pid: observed)

    assert is_owned_record(
        record, tmp_path, expected_role="backend", expected_executable=executable
    ) is (not changed)


def test_package_privacy_scan_rejects_runtime_tools_and_allows_escaped_source(
    tmp_path: Path,
) -> None:
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "example.py").write_text(
        'pattern = r"\\\\u00D6\\\\u00D8"\npath = "C:/Windows/System32"\n',
        encoding="utf-8",
    )
    assert privacy_scan(clean)["status"] == "PASS"

    (clean / "node.exe").write_bytes(b"not shipped")
    result = privacy_scan(clean)
    assert result["status"] == "BLOCKED"
    assert "forbidden_tool:node.exe" in result["violations"]


def test_package_privacy_scan_detects_dynamic_current_user_paths_and_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USERNAME", "builder-review-user")
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.setenv("USERPROFILE", ('C:\\Users\\build' + 'er-review-user'))
    package = tmp_path / "package"
    package.mkdir()
    (package / "path.txt").write_text(
        ('C:\\Users\\builder-review-user\\A' + 'ppData\\Local\\Temp\\artifact.txt'), encoding="utf-8"
    )
    result = portable_distribution.privacy_scan(package)
    assert result["status"] == "BLOCKED"
    assert "absolute_path:path.txt" in result["violations"]

    (package / "markers.txt").write_text("refs/codex github_pat_example", encoding="utf-8")
    result = portable_distribution.privacy_scan(package)
    assert "private_marker:markers.txt" in result["violations"]


def test_manifest_records_and_rehashes_the_package_contract(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "app.txt").write_text("TVA", encoding="utf-8")
    excluded = {"PACKAGE_MANIFEST.json"}
    manifest = {
        "excluded_from_manifest": sorted(excluded),
        "files": package_file_records(package, excluded),
    }
    (package / "PACKAGE_PROVENANCE.json").write_text(
        json.dumps(
            {
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
        ),
        encoding="utf-8",
    )
    manifest["files"] = package_file_records(package, excluded)
    (package / "PACKAGE_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert verify_manifest(package, manifest, rehash=True)["status"] == "PASS"
    archive_path = tmp_path / "package.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in package.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(package).as_posix())
    qualification_result = verify_zip(
        archive_path,
        expected_source_sha="a" * 40,
        accepted_baseline_sha="b" * 40,
    )
    assert qualification_result["status"] == "BLOCKED"
    assert "qualification_license_inventory_invalid" in qualification_result["violations"]
    assert qualification_result["license_notice_validation"]["status"] == "BLOCKED"

    development_archive = tmp_path / "development.zip"
    with zipfile.ZipFile(archive_path) as source_archive, zipfile.ZipFile(
        development_archive, "w", compression=zipfile.ZIP_DEFLATED
    ) as target_archive:
        for info in source_archive.infolist():
            payload = source_archive.read(info)
            if info.filename == "PACKAGE_PROVENANCE.json":
                provenance = json.loads(payload.decode("utf-8"))
                provenance["source"]["build_mode"] = "DEVELOPMENT_ONLY"
                provenance["source"]["qualifiable"] = False
                provenance["gates"]["source_qualification"] = "DEVELOPMENT_ONLY_NON_QUALIFIABLE"
                payload = json.dumps(provenance).encode("utf-8")
            target_archive.writestr(info, payload)
    assert "source_qualification_not_auditable" in verify_zip(
        development_archive,
        expected_source_sha="a" * 40,
        accepted_baseline_sha="b" * 40,
    )["violations"]
    assert command_signature("python -m app") == command_signature("python   -m   app")


def test_verify_zip_blocks_internally_consistent_but_wrong_external_identity(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "package.zip"
    package = tmp_path / "package"
    package.mkdir()
    (package / "app.txt").write_text("TVA", encoding="utf-8")
    (package / "PACKAGE_PROVENANCE.json").write_text(
        json.dumps(
            {
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
        ),
        encoding="utf-8",
    )
    manifest = {
        "excluded_from_manifest": ["PACKAGE_MANIFEST.json"],
        "files": package_file_records(package, {"PACKAGE_MANIFEST.json"}),
    }
    (package / "PACKAGE_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in package.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(package).as_posix())

    result = verify_zip(
        archive_path,
        expected_source_sha="c" * 40,
        accepted_baseline_sha="d" * 40,
    )
    assert result["status"] == "BLOCKED"
    assert {
        "source_head_sha_not_expected",
        "provenance_expected_source_sha_not_expected",
        "source_base_sha_not_accepted",
        "provenance_accepted_baseline_sha_not_accepted",
    }.issubset(result["violations"])


def test_verify_zip_blocks_wrong_external_baseline_and_head_independently(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "package.zip"
    package = tmp_path / "package"
    package.mkdir()
    (package / "app.txt").write_text("TVA", encoding="utf-8")
    source = {
        "head_sha": "a" * 40,
        "base_sha": "b" * 40,
        "expected_source_sha": "a" * 40,
        "accepted_baseline_sha": "b" * 40,
        "baseline_is_ancestor": True,
        "build_mode": "QUALIFICATION",
        "qualifiable": True,
        "public_corresponding_source_sync": "PENDING",
    }
    (package / "PACKAGE_PROVENANCE.json").write_text(
        json.dumps(
            {
                "source": source,
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
        ),
        encoding="utf-8",
    )
    manifest = {
        "excluded_from_manifest": ["PACKAGE_MANIFEST.json"],
        "files": package_file_records(package, {"PACKAGE_MANIFEST.json"}),
    }
    (package / "PACKAGE_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in package.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(package).as_posix())

    wrong_baseline = verify_zip(
        archive_path,
        expected_source_sha="a" * 40,
        accepted_baseline_sha="c" * 40,
    )
    assert wrong_baseline["status"] == "BLOCKED"
    assert "source_base_sha_not_accepted" in wrong_baseline["violations"]

    wrong_head = verify_zip(
        archive_path,
        expected_source_sha="c" * 40,
        accepted_baseline_sha="b" * 40,
    )
    assert wrong_head["status"] == "BLOCKED"
    assert "source_head_sha_not_expected" in wrong_head["violations"]


def test_verify_zip_blocks_provenance_identity_fields_that_differ_from_caller(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "package.zip"
    package = tmp_path / "package"
    package.mkdir()
    (package / "app.txt").write_text("TVA", encoding="utf-8")
    source = {
        "head_sha": "a" * 40,
        "base_sha": "b" * 40,
        "expected_source_sha": "c" * 40,
        "accepted_baseline_sha": "d" * 40,
        "baseline_is_ancestor": True,
        "build_mode": "QUALIFICATION",
        "qualifiable": True,
        "public_corresponding_source_sync": "PENDING",
    }
    (package / "PACKAGE_PROVENANCE.json").write_text(
        json.dumps(
            {
                "source": source,
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
        ),
        encoding="utf-8",
    )
    manifest = {
        "excluded_from_manifest": ["PACKAGE_MANIFEST.json"],
        "files": package_file_records(package, {"PACKAGE_MANIFEST.json"}),
    }
    (package / "PACKAGE_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in package.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(package).as_posix())

    result = verify_zip(
        archive_path,
        expected_source_sha="a" * 40,
        accepted_baseline_sha="b" * 40,
    )
    assert result["status"] == "BLOCKED"
    assert "provenance_expected_source_sha_not_expected" in result["violations"]
    assert "provenance_accepted_baseline_sha_not_accepted" in result["violations"]


@pytest.mark.parametrize(
    ("expected_source_sha", "accepted_baseline_sha", "violation"),
    [
        ("invalid", "b" * 40, "caller_expected_source_sha_invalid"),
        ("a" * 40, "invalid", "caller_accepted_baseline_sha_invalid"),
    ],
)
def test_verify_zip_blocks_invalid_caller_shas(
    tmp_path: Path,
    expected_source_sha: str,
    accepted_baseline_sha: str,
    violation: str,
) -> None:
    result = verify_zip(
        tmp_path / "not-read.zip",
        expected_source_sha=expected_source_sha,
        accepted_baseline_sha=accepted_baseline_sha,
    )
    assert result["status"] == "BLOCKED"
    assert violation in result["violations"]


def test_verify_cli_requires_external_identity_arguments(tmp_path: Path) -> None:
    script = Path(__file__).parents[2] / "scripts" / "verify_portable_distribution.py"
    result = subprocess.run(
        [sys.executable, str(script), str(tmp_path / "missing.zip")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "--expected-source-sha" in result.stderr
    assert "--accepted-baseline-sha" in result.stderr


def test_portable_builder_defaults_are_project_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["build_portable_distribution.py", "--build-mode", "development"])
    args = portable_distribution.parse_args()
    project_root = Path(__file__).parents[2].resolve()

    for path in (args.output, args.cache_dir, args.wheelhouse, args.stage_root):
        assert path.is_relative_to(project_root)
    assert args.wheelhouse == project_root / ".local-data" / "portable-cache" / "pr73-wheelhouse"
    assert args.stage_root == project_root / ".local-data" / "s"


def test_builder_stage_child_name_is_short_and_builder_owned(tmp_path: Path) -> None:
    stage_root = tmp_path / "repo" / ".local-data" / "s"
    stage_root.mkdir(parents=True)

    stage = portable_distribution.create_stage(stage_root)
    try:
        assert portable_distribution.BUILDER_STAGE_NAME_RE.fullmatch(stage.name)
        assert len(stage.name) == len(".s-") + 8
    finally:
        portable_distribution.cleanup_stage(stage, stage_root)


def test_long_output_name_does_not_lengthen_stage_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    long_output = tmp_path / ("output-" + "x" * 120)
    paths = portable_distribution.resolve_build_paths(
        project_root,
        "development",
        "abc123",
        output=long_output,
    )
    paths.stage_root.mkdir(parents=True)
    captured: list[tuple[str, Path]] = []
    real_mkdtemp = portable_distribution.tempfile.mkdtemp

    def capture(*, prefix: str, dir: Path) -> str:
        captured.append((prefix, dir))
        return real_mkdtemp(prefix=prefix, dir=dir)

    monkeypatch.setattr(portable_distribution.tempfile, "mkdtemp", capture)
    stage = portable_distribution.create_stage(paths.stage_root)
    try:
        assert captured == [(".s-", paths.stage_root)]
        assert "x" * 20 not in stage.name
        assert stage.parent == paths.stage_root
    finally:
        portable_distribution.cleanup_stage(stage, paths.stage_root)


def test_runtime_path_budget_identifies_deepest_locked_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = {
        "distributions": [
            {"normalized_name": "shallow", "wheel_filename": "shallow.whl"},
            {"normalized_name": "deep", "wheel_filename": "deep.whl"},
        ]
    }
    sources = {
        "shallow": {
            "members": {
                "shallow": {
                    "member": "shallow.py",
                    "scheme": "purelib",
                    "target": "shallow.py",
                }
            },
            "entrypoints": {},
        },
        "deep": {
            "members": {
                "deep": {
                    "member": "deep/path/with-a-long-name.py",
                    "scheme": "purelib",
                    "target": "deep/" + "x" * 80 + ".py",
                }
            },
            "entrypoints": {},
        },
    }
    monkeypatch.setattr(runtime_lock, "verify_wheelhouse", lambda *_args: {"status": "PASS"})
    monkeypatch.setattr(
        runtime_lock,
        "_wheel_source",
        lambda _wheelhouse, item: sources[item["normalized_name"]],
    )
    site_packages = tmp_path / "site-packages"
    report = calculate_runtime_path_budget(
        tmp_path / "wheelhouse",
        lock,
        site_packages,
        stage_root=tmp_path / "stage",
    )

    assert report["status"] == "PASS"
    assert report["deepest_member"]["distribution"] == "deep"
    assert report["deepest_member"]["wheel_member"] == "deep/path/with-a-long-name.py"
    projected_path = (site_packages / ("deep/" + "x" * 80 + ".py")).resolve()
    assert report["deepest_member"]["path_utf16_code_units"] == utf16_code_units(projected_path)
    assert report["deepest_member"]["path_units_with_nul"] == utf16_code_units(projected_path) + 1
    assert report["max_projected_path_length"] == utf16_code_units(projected_path) + 1
    assert report["path_utf16_code_units"] == utf16_code_units(projected_path)
    assert report["path_units_with_nul"] == utf16_code_units(projected_path) + 1
    assert report["effective_site_packages_utf16_code_units"] == utf16_code_units(site_packages.resolve())
    assert "UTF-16" in report["path_limit_basis"]
    assert enforce_runtime_path_budget(report) is report


def test_packaged_path_budget_removes_machine_roots_and_preserves_raw_report_evidence(
    tmp_path: Path,
) -> None:
    stage_root = r"D:\A&B\traffic-video-analytics\.local-data\s"
    site_packages = (
        stage_root
        + r"\.s-example\TrafficVideoAnalytics\runtime\python\Lib\site-packages"
    )
    projected_path = site_packages + r"\torch\include\ATen\native\example.h"
    raw = {
        "status": "PASS",
        "path_limit": 260,
        "path_limit_basis": "UTF-16 code units including terminating NUL.",
        "path_utf16_code_units": 231,
        "path_units_with_nul": 232,
        "max_projected_path_length": 232,
        "stage_root": stage_root,
        "stage_root_utf16_code_units": 44,
        "stage_root_length": 44,
        "effective_site_packages": site_packages,
        "effective_site_packages_utf16_code_units": 111,
        "effective_site_packages_length": 111,
        "projected_member_count": 20464,
        "deepest_member": {
            "distribution": "torch",
            "wheel_filename": "torch-2.10.0+cu128-cp312-cp312-win_amd64.whl",
            "wheel_member": "torch/include/ATen/native/example.h",
            "materialization": "wheel_member",
            "install_scheme": "platlib",
            "materialized_target": "torch/include/ATen/native/example.h",
            "projected_path": projected_path,
            "path_utf16_code_units": 231,
            "path_units_with_nul": 232,
            "path_length": 232,
        },
    }
    original = json.loads(json.dumps(raw))

    packaged = portable_distribution.portable_path_budget_provenance(raw)
    provenance = {"path_budget": packaged}
    package_file = tmp_path / "package" / "PACKAGE_PROVENANCE.json"
    portable_distribution.write_packaged_provenance(package_file, provenance)

    serialized = package_file.read_text(encoding="utf-8")
    assert json.loads(serialized) == provenance
    assert packaged["path_measurement_scope"] == "qualification_build_machine"
    assert packaged["path_limit"] == raw["path_limit"]
    assert packaged["path_limit_basis"] == raw["path_limit_basis"]
    assert packaged["path_utf16_code_units"] == raw["path_utf16_code_units"]
    assert packaged["path_units_with_nul"] == raw["path_units_with_nul"]
    assert packaged["max_projected_path_length"] == raw["max_projected_path_length"]
    assert packaged["stage_root_utf16_code_units"] == raw["stage_root_utf16_code_units"]
    assert packaged["stage_root_length"] == raw["stage_root_length"]
    assert (
        packaged["effective_site_packages_utf16_code_units"]
        == raw["effective_site_packages_utf16_code_units"]
    )
    assert "stage_root" not in packaged
    assert "effective_site_packages" not in packaged
    assert "projected_path" not in packaged["deepest_member"]
    assert packaged["deepest_member"]["materialized_target"] == (
        "torch/include/ATen/native/example.h"
    )
    assert stage_root not in serialized
    assert packaged_provenance_absolute_path_violations(provenance) == []

    # The caller retains the unmodified operational values for BUILD_REPORT.json.
    assert raw == original
    assert raw["stage_root"] == stage_root
    assert raw["effective_site_packages"] == site_packages
    assert raw["deepest_member"]["projected_path"] == projected_path


def test_packaged_provenance_writer_blocks_and_redacts_absolute_path_diagnostic(
    tmp_path: Path,
) -> None:
    private_path = (
        r"D:\A&B\traffic-video-analytics\.local-data\s\.s-example"
        r"\TrafficVideoAnalytics\runtime\python\Lib\site-packages"
    )
    destination = tmp_path / "PACKAGE_PROVENANCE.json"

    with pytest.raises(
        portable_distribution.BuildError,
        match=r"packaged_provenance_absolute_path:path_budget\.stage_root",
    ) as exc_info:
        portable_distribution.write_packaged_provenance(
            destination, {"path_budget": {"stage_root": private_path}}
        )

    assert private_path not in str(exc_info.value)
    assert not destination.exists()


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/builds/output",
        "runtime/python/Lib/site-packages",
        "/api/v1/projects",
    ],
)
def test_provenance_path_guard_accepts_urls_and_relative_paths(value: str) -> None:
    assert packaged_provenance_absolute_path_violations({"value": value}) == []


def test_over_budget_preflight_fails_before_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = {"distributions": [{"normalized_name": "deep", "wheel_filename": "deep.whl"}]}
    source = {
        "members": {
            "deep": {
                "member": "deep/header.h",
                "scheme": "purelib",
                "target": "deep/" + "x" * 220 + ".h",
            }
        },
        "entrypoints": {},
    }
    monkeypatch.setattr(runtime_lock, "verify_wheelhouse", lambda *_args: {"status": "PASS"})
    monkeypatch.setattr(runtime_lock, "_wheel_source", lambda *_args: source)
    site_packages = tmp_path / ("site-packages-" + "y" * 100)
    report = calculate_runtime_path_budget(
        tmp_path / "wheelhouse",
        lock,
        site_packages,
        stage_root=tmp_path / "stage",
    )
    pip_calls: list[object] = []
    monkeypatch.setattr(
        portable_distribution,
        "install_locked_runtime",
        lambda *_args, **_kwargs: pip_calls.append(True),
    )

    assert report["path_units_with_nul"] > report["path_limit"]
    with pytest.raises(RuntimeLockError, match="runtime_path_budget_exceeded"):
        enforce_runtime_path_budget(report)
    assert pip_calls == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [("ASCII", 5), ("ภาษาไทย", len("ภาษาไทย")), ("😀", 2)],
)
def test_utf16_path_units_count_bmp_and_non_bmp_characters(value: str, expected: int) -> None:
    assert utf16_code_units(value) == expected


@pytest.mark.parametrize(
    ("path_units", "expected_status"), [(259, "PASS"), (260, "BLOCKED")]
)
def test_runtime_path_budget_calculates_utf16_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path_units: int,
    expected_status: str,
) -> None:
    lock = {"distributions": [{"normalized_name": "fixture", "wheel_filename": "fixture.whl"}]}
    site_packages = (tmp_path / "site-packages").resolve()
    target_length = path_units - utf16_code_units(site_packages) - 1 - len(".py")
    source = {
        "members": {
            "fixture": {
                "member": "fixture.py",
                "scheme": "purelib",
                "target": "x" * target_length + ".py",
            }
        },
        "entrypoints": {},
    }
    monkeypatch.setattr(runtime_lock, "verify_wheelhouse", lambda *_args: {"status": "PASS"})
    monkeypatch.setattr(runtime_lock, "_wheel_source", lambda *_args: source)

    report = calculate_runtime_path_budget(
        tmp_path / "wheelhouse", lock, site_packages, stage_root=tmp_path / "stage"
    )

    assert report["path_utf16_code_units"] == path_units
    assert report["path_units_with_nul"] == path_units + 1
    assert report["status"] == expected_status
    if expected_status == "PASS":
        assert enforce_runtime_path_budget(report) is report
    else:
        with pytest.raises(RuntimeLockError, match="runtime_path_budget_exceeded"):
            enforce_runtime_path_budget(report)


def test_explicit_output_does_not_expand_other_root_authority(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    external_output = tmp_path / "external-output"
    paths = portable_distribution.resolve_build_paths(
        project_root,
        "development",
        "abc123",
        output=external_output,
    )

    assert paths.output == external_output.resolve()
    assert paths.cache_dir.is_relative_to(project_root)
    assert paths.wheelhouse.is_relative_to(project_root)
    assert paths.stage_root.is_relative_to(project_root)


def test_explicit_cache_does_not_move_default_wheelhouse_or_staging(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    paths = portable_distribution.resolve_build_paths(
        project_root,
        "development",
        "abc123",
        cache_dir=tmp_path / "external-cache",
    )

    assert paths.cache_dir == (tmp_path / "external-cache").resolve()
    assert paths.wheelhouse.is_relative_to(project_root)
    assert paths.stage_root.is_relative_to(project_root)


def test_explicit_roots_are_used_without_synthesizing_siblings(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    roots = {
        "output": tmp_path / "out",
        "cache_dir": tmp_path / "cache",
        "wheelhouse": tmp_path / "wheelhouse",
        "stage_root": tmp_path / "stage",
    }
    paths = portable_distribution.resolve_build_paths(
        project_root,
        "qualification",
        "abc123",
        **roots,
    )

    assert paths.output == roots["output"].resolve()
    assert paths.cache_dir == roots["cache_dir"].resolve()
    assert paths.wheelhouse == roots["wheelhouse"].resolve()
    assert paths.stage_root == roots["stage_root"].resolve()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive-root regression")
def test_drive_output_never_derives_drive_root_staging(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    paths = portable_distribution.resolve_build_paths(
        project_root,
        "development",
        "abc123",
        output=Path("D:/TVA-portable-output"),
    )

    assert paths.stage_root.is_relative_to(project_root)
    assert paths.stage_root != Path("D:/").resolve() / ".tva-portable-stages"


def test_path_resolver_rejects_canonical_reparse_escape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    outside = tmp_path / "outside"
    escaped_default = project_root / ".local-data" / "portable-builds" / "p2-development-abc123"
    real_canonical_path = portable_distribution._canonical_path

    def canonical_path(path: Path) -> Path:
        if path == escaped_default:
            return outside.resolve()
        return real_canonical_path(path)

    monkeypatch.setattr(portable_distribution, "_canonical_path", canonical_path)
    with pytest.raises(portable_distribution.BuildError, match="path_outside_project:output"):
        portable_distribution.resolve_build_paths(project_root, "development", "abc123")
