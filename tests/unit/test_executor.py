"""Unit tests for anolis_workbench.core.executor."""

from __future__ import annotations

from pathlib import Path
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
