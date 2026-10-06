"""Small, conservative process identity helpers used by the portable launcher.

Stopping is deliberately deny-by-default: a process must still have the same
PID, executable, creation time, package root, and command-line signature that
the launcher recorded before it can be terminated.
"""

from __future__ import annotations

import base64
import binascii
import ctypes
import hashlib
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
PROCESS_TERMINATE = 0x0001
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
ERROR_ACCESS_DENIED = 5


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    executable: str
    start_time_ns: int


@dataclass(frozen=True)
class ProcessStopResult:
    safe: bool
    status: str


def normalize_path(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve(strict=False)).casefold()


def command_signature(command_line: str) -> str:
    normalized = " ".join(str(command_line).split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _filetime_value(filetime: ctypes.Structure) -> int:
    return (int(filetime.dwHighDateTime) << 32) | int(filetime.dwLowDateTime)


class _FileTime(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]


def _configure_windows_process_api(kernel32: ctypes.CDLL) -> ctypes.CDLL:
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
    ]
    kernel32.GetProcessTimes.restype = ctypes.c_int
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel32.WaitForSingleObject.restype = ctypes.c_ulong
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel32.TerminateProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    return kernel32


def _windows_process_presence(pid: int) -> str:
    """Return PRESENT, ABSENT, or UNKNOWN from a complete Windows PID listing."""

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        enum_processes = kernel32.K32EnumProcesses
        enum_processes.argtypes = [
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        enum_processes.restype = ctypes.c_int
        capacity = 1024
        while True:
            process_ids = (ctypes.c_uint32 * capacity)()
            buffer_size = ctypes.sizeof(process_ids)
            bytes_needed = ctypes.c_uint32()
            if not enum_processes(process_ids, buffer_size, ctypes.byref(bytes_needed)):
                return "UNKNOWN"
            if bytes_needed.value > buffer_size or bytes_needed.value % ctypes.sizeof(
                ctypes.c_uint32
            ):
                return "UNKNOWN"
            if bytes_needed.value == buffer_size:
                if buffer_size > 0xFFFFFFFF // 2:
                    return "UNKNOWN"
                capacity *= 2
                continue
            process_count = bytes_needed.value // ctypes.sizeof(ctypes.c_uint32)
            return "PRESENT" if pid in process_ids[:process_count] else "ABSENT"
    except (
        AttributeError,
        ctypes.ArgumentError,
        MemoryError,
        OSError,
        OverflowError,
        TypeError,
        ValueError,
    ):
        return "UNKNOWN"


def _windows_process_identity_from_handle(
    kernel32: ctypes.CDLL, handle: ctypes.c_void_p, pid: int
) -> ProcessIdentity | None:
    buffer = ctypes.create_unicode_buffer(32768)
    size = ctypes.c_ulong(len(buffer))
    if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
        return None
    created = _FileTime()
    exited = _FileTime()
    kernel_time = _FileTime()
    user_time = _FileTime()
    if not kernel32.GetProcessTimes(
        handle,
        ctypes.byref(created),
        ctypes.byref(exited),
        ctypes.byref(kernel_time),
        ctypes.byref(user_time),
    ):
        return None
    # FILETIME is 100 ns since 1601. The unit and origin do not matter for
    # identity matching; preserving it as an integer avoids datetime drift.
    return ProcessIdentity(int(pid), normalize_path(buffer.value), _filetime_value(created))


def _windows_process_identity(pid: int) -> ProcessIdentity | None:
    kernel32 = _configure_windows_process_api(ctypes.WinDLL("kernel32", use_last_error=True))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, int(pid))
    if not handle:
        return None
    try:
        return _windows_process_identity_from_handle(kernel32, handle, pid)
    finally:
        kernel32.CloseHandle(handle)


def process_identity(pid: int) -> ProcessIdentity | None:
    """Return an identity for a live process, or ``None`` if it is gone."""

    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    if os.name == "nt":
        return _windows_process_identity(pid)
    try:
        executable = normalize_path(os.readlink(f"/proc/{pid}/exe"))
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
        # Linux starttime is field 22, index 21, measured in clock ticks.
        return ProcessIdentity(pid, executable, int(fields[21]))
    except (OSError, IndexError, ValueError):
        return None


def process_command_line(pid: int) -> str | None:
    """Read the live command line without relying on a broad process search."""

    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    if os.name == "nt":
        powershell = (
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        query = (
            "[Console]::OutputEncoding = [System.Text.Encoding]::ASCII; "
            "$p=Get-CimInstance Win32_Process -Filter 'ProcessId=%d' -ErrorAction SilentlyContinue; "
            "if ($null -ne $p -and $null -ne $p.CommandLine) { "
            "$bytes=[System.Text.Encoding]::Unicode.GetBytes([string]$p.CommandLine); "
            "[Console]::Out.Write([Convert]::ToBase64String($bytes)) }"
        ) % pid
        try:
            result = subprocess.run(
                [str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", query],
                check=False,
                capture_output=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0 or not isinstance(result.stdout, bytes):
            return None
        try:
            payload = base64.b64decode(result.stdout.strip(), validate=True)
            value = payload.decode("utf-16le")
        except (binascii.Error, UnicodeDecodeError):
            return None
        return value or None
    try:
        value = (
            Path(f"/proc/{pid}/cmdline")
            .read_bytes()
            .replace(b"\x00", b" ")
            .decode("utf-8")
        )
        return value.strip() or None
    except (OSError, UnicodeDecodeError):
        return None


def record_process(
    *,
    identity: ProcessIdentity,
    package_root: Path,
    role: str,
    command_line: str,
    port: int | None,
    owner_token: str,
) -> dict[str, object]:
    return {
        "pid": identity.pid,
        "role": role,
        "root": str(package_root.resolve()),
        "executable": identity.executable,
        "process_start_time": identity.start_time_ns,
        "command_signature": command_signature(command_line),
        "port": port,
        "owner_token": owner_token,
        "recorded_at": time.time_ns(),
    }


def _record_matches_observation(
    record: dict[str, object],
    package_root: Path,
    *,
    expected_role: str | None,
    expected_executable: Path | None,
    identity: ProcessIdentity,
    command_line: str | None,
) -> bool:
    try:
        if command_line is None:
            return False
        if normalize_path(record["root"]) != normalize_path(package_root):
            return False
        if expected_role is not None and str(record.get("role")) != expected_role:
            return False
        if int(record["pid"]) != identity.pid:
            return False
        if normalize_path(record["executable"]) != identity.executable:
            return False
        if expected_executable is not None and identity.executable != normalize_path(
            expected_executable
        ):
            return False
        if int(record["process_start_time"]) != identity.start_time_ns:
            return False
        return str(record["command_signature"]) == command_signature(command_line)
    except (KeyError, TypeError, ValueError, OSError):
        return False


def is_owned_record(
    record: dict[str, object],
    package_root: Path,
    *,
    expected_role: str | None = None,
    expected_executable: Path | None = None,
) -> bool:
    """Validate all ownership fields against the current live process."""

    try:
        if normalize_path(record["root"]) != normalize_path(package_root):
            return False
        if expected_role is not None and str(record.get("role")) != expected_role:
            return False
        pid = int(record["pid"])
        identity = process_identity(pid)
        command_line = process_command_line(pid)
        if identity is None or command_line is None:
            return False
        return _record_matches_observation(
            record,
            package_root,
            expected_role=expected_role,
            expected_executable=expected_executable,
            identity=identity,
            command_line=command_line,
        )
    except (KeyError, TypeError, ValueError, OSError):
        return False


class _WindowsProcessHandle:
    def __init__(self, pid: int) -> None:
        self.pid = int(pid)
        self.kernel32 = _configure_windows_process_api(
            ctypes.WinDLL("kernel32", use_last_error=True)
        )
        self.handle = self.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE | PROCESS_TERMINATE,
            False,
            self.pid,
        )
        self.open_error = None if self.handle else int(ctypes.get_last_error())
        self._closed = False

    def __enter__(self) -> _WindowsProcessHandle:
        return self

    def __exit__(self, _exc_type: object, _exc_value: object, _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if self.handle and not self._closed:
            self.kernel32.CloseHandle(self.handle)
            self._closed = True

    def identity(self) -> ProcessIdentity | None:
        return _windows_process_identity_from_handle(self.kernel32, self.handle, self.pid)

    def signaled(self) -> bool | None:
        result = int(self.kernel32.WaitForSingleObject(self.handle, 0))
        if result == WAIT_OBJECT_0:
            return True
        if result == WAIT_TIMEOUT:
            return False
        if result == WAIT_FAILED:
            return None
        return None

    def terminate(self) -> bool:
        return bool(self.kernel32.TerminateProcess(self.handle, 0))

    def wait_for_exit(self, timeout_seconds: float) -> bool:
        timeout_ms = min(max(0, int(timeout_seconds * 1000)), 0xFFFFFFFF)
        return int(self.kernel32.WaitForSingleObject(self.handle, timeout_ms)) == WAIT_OBJECT_0


def _safe_stop(status: str) -> ProcessStopResult:
    return ProcessStopResult(True, status)


def _unsafe_stop(status: str) -> ProcessStopResult:
    return ProcessStopResult(False, status)


def _windows_open_failure_result(pid: int, open_error: int | None) -> ProcessStopResult:
    if open_error == ERROR_ACCESS_DENIED:
        return _unsafe_stop("process_open_access_denied")
    presence = _windows_process_presence(pid)
    if presence == "ABSENT":
        return _safe_stop("already_stopped")
    if presence == "PRESENT":
        return _unsafe_stop("process_open_failed")
    return _unsafe_stop("process_presence_unavailable")


def _windows_stop_owned_process(
    record: dict[str, object],
    package_root: Path,
    *,
    expected_role: str | None,
    expected_executable: Path | None,
    timeout_seconds: float,
) -> ProcessStopResult:
    try:
        pid = int(record["pid"])
        if pid <= 0:
            return _unsafe_stop("invalid_pid")
    except (KeyError, TypeError, ValueError):
        return _unsafe_stop("invalid_pid")

    try:
        if normalize_path(record["root"]) != normalize_path(package_root):
            return _unsafe_stop("ownership_mismatch")
        if expected_role is not None and str(record.get("role")) != expected_role:
            return _unsafe_stop("ownership_mismatch")
    except (KeyError, TypeError, ValueError, OSError):
        return _unsafe_stop("ownership_mismatch")

    try:
        process = _WindowsProcessHandle(pid)
    except OSError:
        return _windows_open_failure_result(pid, None)
    if not process.handle:
        return _windows_open_failure_result(pid, process.open_error)

    with process:
        identity = process.identity()
        if identity is None:
            state = process.signaled()
            return _safe_stop("already_stopped") if state is True else _unsafe_stop(
                "identity_unavailable"
            )

        state = process.signaled()
        if state is True:
            return _safe_stop("already_stopped")
        if state is None:
            return _unsafe_stop("process_state_unavailable")

        command_line = process_command_line(pid)
        if command_line is None:
            state = process.signaled()
            return _safe_stop("already_stopped") if state is True else _unsafe_stop(
                "command_line_unavailable"
            )

        state = process.signaled()
        if state is True:
            return _safe_stop("already_stopped")
        if state is None:
            return _unsafe_stop("process_state_unavailable")
        if not _record_matches_observation(
            record,
            package_root,
            expected_role=expected_role,
            expected_executable=expected_executable,
            identity=identity,
            command_line=command_line,
        ):
            return _unsafe_stop("ownership_mismatch")

        state = process.signaled()
        if state is True:
            return _safe_stop("already_stopped")
        if state is None:
            return _unsafe_stop("process_state_unavailable")
        if not process.terminate():
            state = process.signaled()
            return _safe_stop("already_stopped") if state is True else _unsafe_stop(
                "termination_failed"
            )
        return _safe_stop("stopped") if process.wait_for_exit(timeout_seconds) else _unsafe_stop(
            "termination_timeout"
        )


def stop_owned_process(
    record: dict[str, object],
    package_root: Path,
    *,
    expected_role: str | None = None,
    expected_executable: Path | None = None,
    timeout_seconds: float = 5.0,
) -> ProcessStopResult:
    """Validate and stop one owned process without reopening its Windows PID."""

    if os.name == "nt":
        return _windows_stop_owned_process(
            record,
            package_root,
            expected_role=expected_role,
            expected_executable=expected_executable,
            timeout_seconds=timeout_seconds,
        )
    try:
        pid = int(record["pid"])
        if not is_owned_record(
            record,
            package_root,
            expected_role=expected_role,
            expected_executable=expected_executable,
        ):
            return _unsafe_stop("ownership_mismatch")
        terminate_process(pid)
        return _safe_stop("stopped") if wait_for_exit(pid, timeout_seconds) else _unsafe_stop(
            "termination_timeout"
        )
    except (KeyError, TypeError, ValueError, OSError):
        return _unsafe_stop("termination_failed")


def terminate_process(pid: int) -> None:
    """Request termination of one already-validated process."""

    if os.name == "nt":
        kernel32 = _configure_windows_process_api(ctypes.WinDLL("kernel32", use_last_error=True))
        handle = kernel32.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, int(pid))
        if handle:
            try:
                kernel32.TerminateProcess(handle, 0)
            finally:
                kernel32.CloseHandle(handle)
        return
    os.kill(int(pid), signal.SIGTERM)


def wait_for_exit(pid: int, timeout_seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while time.monotonic() < deadline:
        if process_identity(pid) is None:
            return True
        time.sleep(0.1)
    return process_identity(pid) is None
