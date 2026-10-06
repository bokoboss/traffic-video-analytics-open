from __future__ import annotations

from pathlib import Path

import pytest

from scripts.portable import launcher
from scripts.portable.process_ownership import ProcessIdentity


class _FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.terminated = False
        self.waited = False

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.terminated = True

    def wait(self, timeout: float) -> int:
        self.waited = True
        return 0


def _prepare_spawn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, process: _FakeProcess) -> None:
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(launcher, "logs_dir", lambda root: tmp_path / "logs")
    monkeypatch.setattr(launcher, "app_root_for", lambda root: tmp_path)
    monkeypatch.setattr(launcher.time, "sleep", lambda seconds: None)


def test_spawn_child_fails_closed_when_live_command_line_never_observed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = _FakeProcess(2001)
    identity = ProcessIdentity(process.pid, str(tmp_path / "python.exe"), 42)
    _prepare_spawn(monkeypatch, tmp_path, process)
    monkeypatch.setattr(launcher, "process_identity", lambda pid: identity)
    monkeypatch.setattr(launcher, "process_command_line", lambda pid: None)
    ticks = iter((0.0, 0.1, 1.0, 2.0, 3.1, 3.2))
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(ticks, 3.2))
    persisted: list[dict[str, object]] = []
    monkeypatch.setattr(launcher, "write_record", lambda root, role, payload: persisted.append(payload))

    with pytest.raises(launcher.LauncherError, match="process_command_line_unavailable:backend"):
        launcher.spawn_child(
            root=tmp_path,
            role="backend",
            command=["python.exe", "-m", "app"],
            port=8000,
            environment={},
            owner_token="token",
        )

    assert process.terminated is True
    assert process.waited is True
    assert persisted == []


def test_spawn_child_retries_until_live_command_line_is_observed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = _FakeProcess(2002)
    identity = ProcessIdentity(process.pid, str(tmp_path / "python.exe"), 43)
    _prepare_spawn(monkeypatch, tmp_path, process)
    monkeypatch.setattr(launcher, "process_identity", lambda pid: identity)
    observations = iter((None, '"C:\\TVA With Spaces\\python.exe" -m app'))
    monkeypatch.setattr(launcher, "process_command_line", lambda pid: next(observations))
    ticks = iter((0.0, 0.1, 0.2, 0.3))
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(ticks, 0.3))
    persisted: list[dict[str, object]] = []
    monkeypatch.setattr(launcher, "write_record", lambda root, role, payload: persisted.append(payload))

    result = launcher.spawn_child(
        root=tmp_path,
        role="backend",
        command=[r"C:\TVA With Spaces\python.exe", "-m", "app"],
        port=8000,
        environment={},
        owner_token="token",
    )

    assert result is process
    assert process.terminated is False
    assert len(persisted) == 1
    assert persisted[0]["command_signature"] != launcher.record_process(
        identity=identity,
        package_root=tmp_path,
        role="backend",
        command_line=" ".join([r"C:\TVA With Spaces\python.exe", "-m", "app"]),
        port=8000,
        owner_token="token",
    )["command_signature"]
    assert persisted[0]["command_signature"] == launcher.record_process(
        identity=identity,
        package_root=tmp_path,
        role="backend",
        command_line='"C:\\TVA With Spaces\\python.exe" -m app',
        port=8000,
        owner_token="token",
    )["command_signature"]
