"""No process the workbench spawns inherits its runtime token.

Every spawn site is driven with its subprocess entry point stubbed to record
the environment it was handed. Nothing is executed.
"""

from __future__ import annotations

import pathlib
import subprocess
from typing import Any

import pytest

from anolis_workbench.core import child_env as child_env_module
from anolis_workbench.core import executor, launcher, package_validator, updater

FAKE_TOKEN = "fake-runtime-token-for-tests-0000"


class _Spawn:
    """Stands in for subprocess.run / subprocess.Popen and records env."""

    def __init__(self) -> None:
        self.envs: list[dict[str, str] | None] = []

    def __call__(self, cmd: Any, *args: Any, **kwargs: Any) -> Any:
        self.envs.append(kwargs.get("env"))
        return _Proc(cmd)


class _Proc(subprocess.CompletedProcess):
    """Enough of CompletedProcess and Popen for every caller."""

    def __init__(self, cmd: Any) -> None:
        super().__init__(cmd, 0, b"", b"")
        self.pid = 4242


@pytest.fixture
def secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANOLIS_WORKBENCH_RUNTIME_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", "/run/credentials/anolis-workbench.service")
    monkeypatch.setenv("KEEP_ME", "1")


def _assert_scrubbed(spawn: _Spawn) -> None:
    assert spawn.envs, "nothing was spawned"
    for env in spawn.envs:
        assert env is not None, "the child inherits the full environment"
        for key in child_env_module.WITHHELD:
            assert key not in env
        assert FAKE_TOKEN not in env.values()
        assert env.get("KEEP_ME") == "1"


def test_child_env_withholds_the_token_variables(secrets: None) -> None:
    env = child_env_module.child_env()
    assert "ANOLIS_WORKBENCH_RUNTIME_TOKEN" not in env
    assert "CREDENTIALS_DIRECTORY" not in env
    assert env["KEEP_ME"] == "1"


def test_local_executor(secrets: None, monkeypatch: pytest.MonkeyPatch) -> None:
    spawn = _Spawn()
    monkeypatch.setattr(executor.subprocess, "run", spawn)
    executor.LocalExecutor().run(["true"])
    executor.LocalExecutor().run(["true"], sudo=True)
    _assert_scrubbed(spawn)


def test_ssh_executor(secrets: None, monkeypatch: pytest.MonkeyPatch) -> None:
    spawn = _Spawn()
    monkeypatch.setattr(executor.subprocess, "run", spawn)
    ssh = executor.SubprocessSSHExecutor("192.0.2.10", "someone")
    ssh.run(["true"])
    ssh.write_file("/tmp/x", b"")
    assert len(spawn.envs) == 2
    _assert_scrubbed(spawn)


def test_updater(secrets: None, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "install.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(updater, "download_install_script", lambda version: script)
    spawn = _Spawn()
    monkeypatch.setattr(updater.subprocess, "run", spawn)
    updater.perform_update("0.0.1")
    _assert_scrubbed(spawn)


def test_package_replay(secrets: None, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / "manual.yaml").write_text("{}\n", encoding="utf-8")
    runtime_bin = tmp_path / "anolis-runtime"
    runtime_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    spawn = _Spawn()
    monkeypatch.setattr(package_validator.subprocess, "run", spawn)
    package_validator._validate_replay(
        package_root=tmp_path,
        manifest_payload={"runtime_profiles": {"manual": "runtime/manual.yaml"}},
        runtime_bin=runtime_bin,
    )
    _assert_scrubbed(spawn)


def test_launcher_preflight_checks(secrets: None, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = tmp_path / "bin"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text("{}\n", encoding="utf-8")
    spawn = _Spawn()
    monkeypatch.setattr(launcher.subprocess, "run", spawn)
    launcher._check_config_binary("runtime", exe, config)
    launcher._check_host_binary("provider", exe, config)
    assert len(spawn.envs) == 2
    _assert_scrubbed(spawn)


@pytest.fixture
def launch_stubs(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> _Spawn:
    """launch()/restart() with everything but the Popen call stubbed out."""
    exe = tmp_path / "anolis-runtime"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    config = tmp_path / "launch.yaml"
    config.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(launcher, "materialize_launch_config", lambda project_dir, system: config)
    monkeypatch.setattr(launcher.canonical, "host_paths", lambda doc: {"runtime_executable": str(exe)})
    monkeypatch.setattr(launcher.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    monkeypatch.setattr(launcher, "_state", dict(launcher._state, log_lines_by_project={}))
    spawn = _Spawn()
    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
    return spawn


def test_launch(secrets: None, launch_stubs: _Spawn, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher, "_current_runtime_snapshot", lambda clean_stale=False: {"running": False})
    launcher.launch("p", {}, tmp_path / "p")
    _assert_scrubbed(launch_stubs)


def test_restart(secrets: None, launch_stubs: _Spawn, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from anolis_workbench.core import projects as projects_module

    monkeypatch.setattr(
        launcher, "_current_runtime_snapshot", lambda clean_stale=False: {"running": True, "project": "p"}
    )
    monkeypatch.setattr(launcher, "stop", lambda: None)
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)
    monkeypatch.setattr(projects_module, "get_project", lambda name: {})
    launcher.restart("p", tmp_path / "p")
    _assert_scrubbed(launch_stubs)
