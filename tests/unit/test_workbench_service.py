"""Unit tests for the appliance-mode workbench systemd service.

Nothing here touches /etc, sudo or systemctl: the privileged commands go
to a stubbed subprocess.run that records them and reads the staged unit.
"""

from __future__ import annotations

import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from anolis_workbench.core import workbench_service


class _Recorder:
    """Stands in for subprocess.run: records argv, keeps what cp copies."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.copied: str | None = None
        self.fail_on = fail_on

    def __call__(self, cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        cmd = list(cmd)
        self.calls.append(cmd)
        if "cp" in cmd:
            self.copied = Path(cmd[cmd.index("cp") + 1]).read_text(encoding="utf-8")
        if self.fail_on and self.fail_on in cmd:
            return subprocess.CompletedProcess(cmd, 1, b"", f"{self.fail_on}: refused".encode())
        return subprocess.CompletedProcess(cmd, 0, b"", b"")


@pytest.fixture
def unit_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "systemd" / "system"
    monkeypatch.setattr(workbench_service, "_SYSTEMD_UNIT_DIR", target)
    return target


@pytest.fixture
def scripts_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The running interpreter's scripts dir, holding the entry point."""
    bin_dir = tmp_path / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "anolis-workbench").write_text("#!/bin/sh\n", encoding="utf-8")
    real_get_path = sysconfig.get_path
    monkeypatch.setattr(
        sysconfig, "get_path", lambda name, *a, **k: str(bin_dir) if name == "scripts" else real_get_path(name, *a, **k)
    )
    return bin_dir


def _lines(unit: str, key: str) -> list[str]:
    return [line for line in unit.splitlines() if line.startswith(key)]


class TestUnitContent:
    def test_renders_user_exec_start_and_host(self) -> None:
        unit = workbench_service.render_unit_file("alice", "/opt/wb/bin/anolis-workbench")
        assert _lines(unit, "User=") == ["User=alice"]
        assert _lines(unit, "ExecStart=") == ["ExecStart=/opt/wb/bin/anolis-workbench"]
        assert "Environment=ANOLIS_WORKBENCH_HOST=0.0.0.0" in unit.splitlines()

    def test_exec_start_is_the_running_install_entry_point(
        self, scripts_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # sudo's secure_path holds no pipx or venv bin dir.
        monkeypatch.setenv("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        unit = workbench_service.render_unit_file("alice")
        assert _lines(unit, "ExecStart=") == [f"ExecStart={scripts_dir / 'anolis-workbench'}"]


class TestInstallService:
    def test_unit_goes_through_a_privileged_copy(self, unit_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        rec = _Recorder()
        monkeypatch.setattr(subprocess, "run", rec)
        result = workbench_service.install_service("alice", executable="/opt/wb/bin/anolis-workbench")

        assert result.error is None
        unit_path = str(unit_dir / workbench_service.SERVICE_NAME)
        assert not Path(unit_path).exists(), "the unit was written without sudo"
        sudo_calls = [c[1:] for c in rec.calls if c[0] == "sudo"]
        assert len(sudo_calls) == len(rec.calls), rec.calls
        assert [c[0] for c in sudo_calls[:2]] == ["cp", "chmod"]
        assert sudo_calls[0][-1] == unit_path
        assert sudo_calls[1] == ["chmod", "0644", unit_path]
        assert sudo_calls[2:4] == [
            ["systemctl", "daemon-reload"],
            ["systemctl", "enable", workbench_service.SERVICE_NAME],
        ]
        assert rec.copied is not None
        assert _lines(rec.copied, "User=") == ["User=alice"]

    @pytest.mark.parametrize("step", ["cp", "daemon-reload", "enable"])
    def test_a_failed_step_is_an_error(self, step: str, unit_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(subprocess, "run", _Recorder(fail_on=step))
        result = workbench_service.install_service("alice", executable="/opt/wb/bin/anolis-workbench")
        assert result.error is not None
        assert f"{step}: refused" in result.error
        assert result.installed is False


class TestProvisionInstallFlag:
    def _run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> int:
        from anolis_workbench.cli import provision_cli
        from anolis_workbench.core import deploy

        result = deploy.DeployResult(project_name="p", runtime_version="0.1.45", prefix="/opt/anolis", output="")
        monkeypatch.setattr(provision_cli, "_provision_workspace", lambda args: tmp_path)
        monkeypatch.setattr(provision_cli.deploy, "deploy_local", lambda **kwargs: result)
        monkeypatch.setattr(sys, "argv", ["anolis-provision", "install", "--workbench-service"])
        return provision_cli._run_install(provision_cli._parse_args())

    def test_failure_fails_the_install(
        self, tmp_path: Path, unit_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(subprocess, "run", _Recorder(fail_on="enable"))
        assert self._run(tmp_path, monkeypatch) == 1
        out, err = capsys.readouterr()
        assert "ERROR" in err and "workbench service" in err
        assert "WARNING" not in err
        assert "Installation complete!" not in out

    def test_sudo_invoked_runs_as_the_invoking_user(
        self, tmp_path: Path, unit_dir: Path, scripts_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("USER", "root")
        monkeypatch.setenv("SUDO_USER", "alice")
        monkeypatch.setenv("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        rec = _Recorder()
        monkeypatch.setattr(subprocess, "run", rec)

        assert self._run(tmp_path, monkeypatch) == 0
        assert rec.copied is not None
        assert _lines(rec.copied, "User=") == ["User=alice"]
        assert _lines(rec.copied, "ExecStart=") == [f"ExecStart={scripts_dir / 'anolis-workbench'}"]

    def test_plain_user_runs_as_that_user(
        self, tmp_path: Path, unit_dir: Path, scripts_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("USER", "bob")
        monkeypatch.delenv("SUDO_USER", raising=False)
        rec = _Recorder()
        monkeypatch.setattr(subprocess, "run", rec)

        assert self._run(tmp_path, monkeypatch) == 0
        assert not (unit_dir / workbench_service.SERVICE_NAME).exists()
        assert rec.copied is not None
        assert _lines(rec.copied, "User=") == ["User=bob"]


class _UninstallRecorder:
    """Stands in for subprocess.run: fails the named steps, reports LoadState."""

    def __init__(self, fail_on: tuple[str, ...] = (), load_state: str | None = "loaded") -> None:
        self.calls: list[list[str]] = []
        self.fail_on = fail_on
        self.load_state = load_state

    def __call__(self, cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        cmd = list(cmd)
        self.calls.append(cmd)
        if "show" in cmd:
            if self.load_state is None:
                return subprocess.CompletedProcess(cmd, 1, b"", b"show: failed")
            return subprocess.CompletedProcess(cmd, 0, f"{self.load_state}\n".encode(), b"")
        failing = [step for step in self.fail_on if step in cmd]
        if failing:
            return subprocess.CompletedProcess(cmd, 1, b"", f"{failing[0]}: refused".encode())
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    def sudo_steps(self) -> list[list[str]]:
        return [c[1:] for c in self.calls if c[0] == "sudo"]


class TestUninstallService:
    def test_removes_the_unit_under_sudo(self, unit_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        rec = _UninstallRecorder()
        monkeypatch.setattr(subprocess, "run", rec)
        result = workbench_service.uninstall_service()

        assert result.error is None
        assert rec.sudo_steps() == [
            ["systemctl", "stop", workbench_service.SERVICE_NAME],
            ["systemctl", "disable", workbench_service.SERVICE_NAME],
            ["rm", "-f", str(unit_dir / workbench_service.SERVICE_NAME)],
            ["systemctl", "daemon-reload"],
        ]

    @pytest.mark.parametrize("step", ["rm", "daemon-reload"])
    def test_a_failed_step_is_an_error(self, step: str, unit_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(subprocess, "run", _UninstallRecorder(fail_on=(step,), load_state="not-found"))
        result = workbench_service.uninstall_service()
        assert result.error is not None
        assert f"{step}: refused" in result.error

    @pytest.mark.parametrize("step", ["stop", "disable"])
    @pytest.mark.parametrize("load_state", ["loaded", "masked", None])
    def test_stop_or_disable_failing_on_a_loaded_unit_is_an_error(
        self, step: str, load_state: str | None, unit_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rec = _UninstallRecorder(fail_on=(step,), load_state=load_state)
        monkeypatch.setattr(subprocess, "run", rec)
        result = workbench_service.uninstall_service()
        assert result.error is not None
        assert f"{step}: refused" in result.error
        assert not any("rm" in c for c in rec.sudo_steps())

    def test_stop_and_disable_on_a_not_loaded_unit_are_tolerated(
        self, unit_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rec = _UninstallRecorder(fail_on=("stop", "disable"), load_state="not-found")
        monkeypatch.setattr(subprocess, "run", rec)
        result = workbench_service.uninstall_service()

        assert result.error is None
        assert rec.sudo_steps()[2:] == [
            ["rm", "-f", str(unit_dir / workbench_service.SERVICE_NAME)],
            ["systemctl", "daemon-reload"],
        ]

    @pytest.mark.parametrize("load_state", ["loaded", "not-found"])
    def test_a_refused_sudo_is_an_error(self, load_state: str, unit_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(subprocess, "run", _UninstallRecorder(fail_on=("sudo",), load_state=load_state))
        result = workbench_service.uninstall_service()
        assert result.error is not None
        assert "sudo: refused" in result.error
