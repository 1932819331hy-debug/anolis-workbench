"""Unit tests for anolis_workbench.core.executor."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from anolis_workbench.core.executor import LocalExecutor, SubprocessSSHExecutor


class TestLocalExecutor:
    def test_run_simple_command(self) -> None:
        executor = LocalExecutor()
        result = executor.run(["echo", "hello"])
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_run_failing_command(self) -> None:
        executor = LocalExecutor()
        result = executor.run(["false"])
        assert result.returncode != 0

    def test_run_with_input(self) -> None:
        executor = LocalExecutor()
        result = executor.run(["cat"], input=b"test data")
        assert result.returncode == 0
        assert "test data" in result.stdout

    def test_write_file(self, tmp_path: Path) -> None:
        executor = LocalExecutor()
        target = str(tmp_path / "subdir" / "test.txt")
        executor.write_file(target, b"hello world")
        assert Path(target).read_bytes() == b"hello world"

    def test_mkdir(self, tmp_path: Path) -> None:
        executor = LocalExecutor()
        target = str(tmp_path / "a" / "b" / "c")
        executor.mkdir(target)
        assert Path(target).is_dir()

    def test_file_exists_true(self, tmp_path: Path) -> None:
        executor = LocalExecutor()
        f = tmp_path / "exists.txt"
        f.write_text("x")
        assert executor.file_exists(str(f)) is True

    def test_file_exists_false(self, tmp_path: Path) -> None:
        executor = LocalExecutor()
        assert executor.file_exists(str(tmp_path / "nope.txt")) is False


_PASSWORD = b"pw-7f3c-not-a-real-one"
_WRAP = ["sh", "-c", 'exec "$0" "$@" </dev/null']


@pytest.fixture()
def fake_sudo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A `sudo` first on PATH that records its argv and env, then runs the
    command after `--` (or its whole argv, for plain `sudo cmd`).

    With `-S` it reads one line from stdin, as sudo does for the password,
    unless the returned directory holds a `skip-stdin` file: that stands in
    for a sudoers rule that runs the command without asking, leaving the
    password line unread on the command's stdin.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "sudo-calls.jsonl"
    script = bindir / "sudo"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"log, skip = {str(log)!r}, {str(tmp_path / 'skip-stdin')!r}\n"
        "args = sys.argv[1:]\n"
        "line = b''\n"
        "if '-S' in args and not os.path.exists(skip):\n"
        "    line = sys.stdin.buffer.readline()\n"
        "with open(log, 'a') as f:\n"
        "    f.write(json.dumps({'argv': args, 'env': dict(os.environ), 'stdin': line.hex()}) + '\\n')\n"
        "cmd = args[args.index('--') + 1 :] if '--' in args else args\n"
        "os.execvp(cmd[0], cmd)\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return tmp_path


def _sudo_calls(root: Path) -> list[dict[str, Any]]:
    log = root / "sudo-calls.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


class TestLocalExecutorSudoPassword:
    """`sudo -S` reads the password from stdin: never argv, which `ps`
    shows every user, and never the environment (#230)."""

    def test_password_arrives_on_stdin_and_nowhere_else(self, fake_sudo: Path) -> None:
        result = LocalExecutor(sudo_password=bytearray(_PASSWORD)).run(["true"], sudo=True)

        assert result.returncode == 0
        [call] = _sudo_calls(fake_sudo)
        assert bytes.fromhex(call["stdin"]) == _PASSWORD + b"\n"
        secret = _PASSWORD.decode()
        assert not [a for a in call["argv"] if secret in a]
        assert not [k for k, v in call["env"].items() if secret in k or secret in v]
        assert secret not in result.stdout + result.stderr

    def test_sudo_neither_uses_nor_refreshes_the_credential_cache(self, fake_sudo: Path) -> None:
        """-k: a later request with no password cannot ride this one's cache."""
        LocalExecutor(sudo_password=bytearray(_PASSWORD)).run(["true"], sudo=True)

        [call] = _sudo_calls(fake_sudo)
        assert call["argv"] == ["-k", "-S", "-p", "", "--", *_WRAP, "true"]

    def test_an_unread_password_line_never_reaches_the_command(self, fake_sudo: Path) -> None:
        """A sudoers rule that skips the password must not hand the line to
        the command, whose output reaches the job's error and events."""
        (fake_sudo / "skip-stdin").touch()
        result = LocalExecutor(sudo_password=bytearray(_PASSWORD)).run(["cat"], sudo=True)

        assert result.returncode == 0
        assert result.stdout == ""

    def test_a_failing_install_never_carries_the_password_into_the_job(
        self, fake_sudo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The install's output tail becomes job.error and the SSE error event."""
        from anolis_workbench.core import deploy
        from anolis_workbench.server.routes import provision

        (fake_sudo / "skip-stdin").touch()
        install_sh = tmp_path / "install.sh"
        install_sh.write_text('read -r line; echo "read: [$line]"; exit 1\n')

        def _deploy(**kwargs: Any) -> None:
            deploy._run_install_sh(kwargs["executor"], str(install_sh), [], None)

        monkeypatch.setattr(provision, "_prepare_workspace", lambda params, progress: tmp_path)
        monkeypatch.setattr(deploy, "_reboot_pending", lambda executor: False)
        monkeypatch.setattr(deploy, "deploy_local", _deploy)
        job = provision.ProvisionJob(job_id="leak")
        provision._run_install_job(job, {"project": "rig"}, bytearray(_PASSWORD))

        assert job.status == "failed"
        assert "read: [" in str(job.error)
        assert _PASSWORD.decode() not in str(job.error) + json.dumps(job.events)

    def test_command_input_with_a_password_is_refused(self, fake_sudo: Path) -> None:
        with pytest.raises(ValueError, match="input"):
            LocalExecutor(sudo_password=bytearray(_PASSWORD)).run(["cat"], input=b"payload", sudo=True)
        assert _sudo_calls(fake_sudo) == []

    def test_the_stdin_buffer_is_zeroed_after_the_run(self, fake_sudo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        real_run = subprocess.run
        seen: list[Any] = []

        def _spy(*args: Any, **kwargs: Any) -> Any:
            seen.append(kwargs.get("input"))
            return real_run(*args, **kwargs)

        monkeypatch.setattr(subprocess, "run", _spy)
        LocalExecutor(sudo_password=bytearray(_PASSWORD)).run(["true"], sudo=True)

        buf = seen[-1]
        assert isinstance(buf, bytearray)
        assert len(buf) == len(_PASSWORD) + 1
        assert buf == bytearray(len(buf))
        assert bytes.fromhex(_sudo_calls(fake_sudo)[0]["stdin"]) == _PASSWORD + b"\n"

    def test_without_a_password_sudo_runs_as_before(self, fake_sudo: Path) -> None:
        LocalExecutor().run(["true"], sudo=True)

        [call] = _sudo_calls(fake_sudo)
        assert call["argv"] == ["true"]
        assert call["stdin"] == ""

    def test_zero_buffer_clears_in_place(self) -> None:
        from anolis_workbench.core.executor import zero_buffer

        buf = bytearray(_PASSWORD)
        zero_buffer(buf)
        assert buf == bytearray(len(_PASSWORD))


class TestSubprocessSSHExecutor:
    def test_ssh_base_default(self) -> None:
        executor = SubprocessSSHExecutor(host="192.168.1.10", user="pi")
        base = executor._ssh_base()
        assert base == ["ssh", "-o", "BatchMode=yes", "-p", "22", "pi@192.168.1.10"]

    def test_ssh_base_with_key_and_port(self) -> None:
        executor = SubprocessSSHExecutor(host="mypi", user="lab", key_file="/home/dev/.ssh/id_rpi", port=2222)
        base = executor._ssh_base()
        assert "-i" in base
        assert "/home/dev/.ssh/id_rpi" in base
        assert "-p" in base
        assert "2222" in base
        assert "lab@mypi" in base

    def test_ssh_base_with_pty(self) -> None:
        executor = SubprocessSSHExecutor(host="pi", user="pi")
        base = executor._ssh_base(allocate_pty=True)
        assert "-t" in base

    @patch("subprocess.run")
    def test_run_without_sudo(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout=b"aarch64\n", stderr=b"")
        executor = SubprocessSSHExecutor(host="pi", user="pi")
        result = executor.run(["uname", "-m"])

        assert result.returncode == 0
        assert "aarch64" in result.stdout
        call_args = mock_run.call_args[0][0]
        assert call_args[-1] == "uname -m"
        assert "sudo" not in call_args[-1]

    @patch("subprocess.run")
    def test_run_with_sudo(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        executor = SubprocessSSHExecutor(host="pi", user="pi")
        executor.run(["tar", "-xz"], sudo=True)

        call_args = mock_run.call_args[0][0]
        assert "sudo tar -xz" in call_args[-1]
        # PTY allocated for sudo
        assert "-t" in call_args

    @patch("subprocess.run")
    def test_write_file(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        executor = SubprocessSSHExecutor(host="pi", user="pi")
        executor.write_file("/tmp/test.txt", b"data")

        call_args = mock_run.call_args
        assert b"data" == call_args[1]["input"]
        cmd = call_args[0][0]
        assert "cat > /tmp/test.txt" in cmd[-1]

    @patch("subprocess.run")
    def test_write_file_failure_raises(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stderr=b"Permission denied")
        executor = SubprocessSSHExecutor(host="pi", user="pi")

        with pytest.raises(OSError, match="Permission denied"):
            executor.write_file("/etc/secret", b"data")

    @patch("subprocess.run")
    def test_mkdir(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        executor = SubprocessSSHExecutor(host="pi", user="pi")
        executor.mkdir("/home/pi/.anolis/systems/test")

        call_args = mock_run.call_args[0][0]
        assert "mkdir -p /home/pi/.anolis/systems/test" in call_args[-1]

    @patch("subprocess.run")
    def test_file_exists_true(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        executor = SubprocessSSHExecutor(host="pi", user="pi")
        assert executor.file_exists("/usr/local/bin/anolis-runtime") is True

    @patch("subprocess.run")
    def test_file_exists_false(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout=b"", stderr=b"")
        executor = SubprocessSSHExecutor(host="pi", user="pi")
        assert executor.file_exists("/usr/local/bin/nope") is False


class TestAllowlistBoundary:
    """The allowlist guards Executor call sites only. Dev-launch runs the
    user's own runtime and provider binaries with subprocess directly, and the
    allowlist could not express a user-chosen path anyway: it keeps the
    basename and drops the directory (#284)."""

    @pytest.mark.parametrize(
        "path",
        [
            "/home/me/build/anolis-runtime",
            "/home/me/build/anolis-provider-sim",
            "/opt/anolis/bin/anolis-provider-x",
        ],
    )
    def test_runtime_and_provider_binaries_are_not_allowlisted(self, path: str) -> None:
        from anolis_workbench.core.executor import _validate_command

        with pytest.raises(ValueError, match="not in the executor allowlist"):
            _validate_command([path, "--config", "x.yaml"])

    def test_allowlist_names_no_runtime_or_provider_binary(self) -> None:
        from anolis_workbench.core.executor import _ALLOWED_COMMANDS

        assert not [c for c in _ALLOWED_COMMANDS if c.startswith("anolis-")]


class TestRuntimeVersionPath:
    """allow_runtime_version admits one exact path, and only with --version (#567)."""

    def test_the_exact_path_with_version_is_allowed(self) -> None:
        from anolis_workbench.core.executor import _validate_command

        executor = LocalExecutor()
        path = executor.allow_runtime_version("/opt/anolis")
        assert path == "/opt/anolis/bin/anolis-runtime"
        assert _validate_command([path, "--version"], executor._allowed_paths) == [path, "--version"]

    @pytest.mark.parametrize(
        "args",
        [[], ["--config", "x.yaml"], ["--version", "--config", "x.yaml"], ["-c", "id"]],
        ids=["none", "other", "extra", "shell"],
    )
    def test_the_path_with_other_arguments_is_refused(self, args: list[str]) -> None:
        from anolis_workbench.core.executor import _validate_command

        executor = LocalExecutor()
        path = executor.allow_runtime_version("/opt/anolis")
        with pytest.raises(ValueError, match="allowed only as"):
            _validate_command([path, *args], executor._allowed_paths)

    @pytest.mark.parametrize(
        "binary",
        ["/home/me/bin/anolis-runtime", "/opt/anolis-old/bin/anolis-runtime", "anolis-runtime"],
        ids=["other-dir", "other-prefix", "bare"],
    )
    def test_any_other_anolis_runtime_is_refused(self, binary: str) -> None:
        from anolis_workbench.core.executor import _validate_command

        executor = LocalExecutor()
        executor.allow_runtime_version("/opt/anolis")
        with pytest.raises(ValueError, match="not in the executor allowlist"):
            _validate_command([binary, "--version"], executor._allowed_paths)

    def test_the_path_is_admitted_only_by_the_executor_that_allowed_it(self) -> None:
        allowed = LocalExecutor()
        allowed.allow_runtime_version("/opt/anolis")
        with pytest.raises(ValueError, match="not in the executor allowlist"):
            LocalExecutor().run(["/opt/anolis/bin/anolis-runtime", "--version"])

    @pytest.mark.parametrize(
        "prefix", ["opt/anolis", "/opt/../tmp", "/opt/anolis/.."], ids=["relative", "dotdot", "trailing-dotdot"]
    )
    def test_a_relative_or_dotdot_prefix_is_refused(self, prefix: str) -> None:
        executor = LocalExecutor()
        with pytest.raises(ValueError, match="must be absolute, with no '..'"):
            executor.allow_runtime_version(prefix)
        assert executor._allowed_paths == frozenset()
