"""Unit tests for ParamikoSSHExecutor."""

from __future__ import annotations

from importlib.metadata import requires
from unittest.mock import MagicMock, patch

from anolis_workbench.core.executor import create_ssh_executor


def test_paramiko_is_a_core_dependency() -> None:
    # Onboarding offers remote setup on every install, so a plain
    # `pip install anolis-workbench` must bring paramiko with it (#437).
    reqs = requires("anolis-workbench") or []
    assert any(r.startswith("paramiko") and "extra ==" not in r for r in reqs), reqs


class TestParamikoSSHExecutor:
    """Tests for ParamikoSSHExecutor using mocked paramiko."""

    @patch("anolis_workbench.core.executor.ParamikoSSHExecutor.__init__", return_value=None)
    def test_run_command(self, mock_init: MagicMock) -> None:
        from anolis_workbench.core.executor import ParamikoSSHExecutor

        executor = ParamikoSSHExecutor.__new__(ParamikoSSHExecutor)
        executor.host = "192.168.1.10"
        executor.user = "pi"
        executor.port = 22

        # Mock the SSH client
        mock_client = MagicMock()
        executor._client = mock_client
        executor._sftp = MagicMock()

        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b"x86_64\n"
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b""
        mock_stdin = MagicMock()
        mock_client.exec_command.return_value = (mock_stdin, mock_stdout, mock_stderr)

        result = executor.run(["uname", "-m"])

        assert result.returncode == 0
        assert result.stdout == "x86_64\n"
        mock_client.exec_command.assert_called_once_with("uname -m", timeout=None)

    @patch("anolis_workbench.core.executor.ParamikoSSHExecutor.__init__", return_value=None)
    def test_run_with_sudo(self, mock_init: MagicMock) -> None:
        from anolis_workbench.core.executor import ParamikoSSHExecutor

        executor = ParamikoSSHExecutor.__new__(ParamikoSSHExecutor)
        executor._client = MagicMock()
        executor._sftp = MagicMock()

        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b""
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b""
        mock_stdin = MagicMock()
        executor._client.exec_command.return_value = (mock_stdin, mock_stdout, mock_stderr)

        executor.run(["tar", "-xz", "-C", "/usr/local"], sudo=True)

        executor._client.exec_command.assert_called_once_with("sudo tar -xz -C /usr/local", timeout=None)

    @patch("anolis_workbench.core.executor.ParamikoSSHExecutor.__init__", return_value=None)
    def test_run_with_input(self, mock_init: MagicMock) -> None:
        from anolis_workbench.core.executor import ParamikoSSHExecutor

        executor = ParamikoSSHExecutor.__new__(ParamikoSSHExecutor)
        executor._client = MagicMock()
        executor._sftp = MagicMock()

        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b""
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b""
        mock_stdin = MagicMock()
        executor._client.exec_command.return_value = (mock_stdin, mock_stdout, mock_stderr)

        executor.run(["cat"], input=b"hello")

        mock_stdin.write.assert_called_once_with(b"hello")
        mock_stdin.flush.assert_called_once()
        mock_stdin.channel.shutdown_write.assert_called_once()

    @patch("anolis_workbench.core.executor.ParamikoSSHExecutor.__init__", return_value=None)
    def test_write_file(self, mock_init: MagicMock) -> None:
        from anolis_workbench.core.executor import ParamikoSSHExecutor

        executor = ParamikoSSHExecutor.__new__(ParamikoSSHExecutor)
        executor._sftp = MagicMock()

        mock_file = MagicMock()
        executor._sftp.open.return_value.__enter__ = MagicMock(return_value=mock_file)
        executor._sftp.open.return_value.__exit__ = MagicMock(return_value=False)

        executor.write_file("/tmp/test.txt", b"content")

        executor._sftp.open.assert_called_once_with("/tmp/test.txt", "wb")

    @patch("anolis_workbench.core.executor.ParamikoSSHExecutor.__init__", return_value=None)
    def test_file_exists_true(self, mock_init: MagicMock) -> None:
        from anolis_workbench.core.executor import ParamikoSSHExecutor

        executor = ParamikoSSHExecutor.__new__(ParamikoSSHExecutor)
        executor._sftp = MagicMock()
        executor._sftp.stat.return_value = MagicMock()

        assert executor.file_exists("/usr/local/bin/anolis-runtime") is True

    @patch("anolis_workbench.core.executor.ParamikoSSHExecutor.__init__", return_value=None)
    def test_file_exists_false(self, mock_init: MagicMock) -> None:
        from anolis_workbench.core.executor import ParamikoSSHExecutor

        executor = ParamikoSSHExecutor.__new__(ParamikoSSHExecutor)
        executor._sftp = MagicMock()
        executor._sftp.stat.side_effect = FileNotFoundError()

        assert executor.file_exists("/usr/local/bin/missing") is False

    @patch("anolis_workbench.core.executor.ParamikoSSHExecutor.__init__", return_value=None)
    def test_close(self, mock_init: MagicMock) -> None:
        from anolis_workbench.core.executor import ParamikoSSHExecutor

        executor = ParamikoSSHExecutor.__new__(ParamikoSSHExecutor)
        executor._sftp = MagicMock()
        executor._client = MagicMock()

        executor.close()

        executor._sftp.close.assert_called_once()
        executor._client.close.assert_called_once()


class TestCreateSSHExecutor:
    def test_creates_subprocess_executor_by_default(self) -> None:
        from anolis_workbench.core.executor import SubprocessSSHExecutor

        executor = create_ssh_executor("host", "user")
        assert isinstance(executor, SubprocessSSHExecutor)

    @patch("anolis_workbench.core.executor.ParamikoSSHExecutor.__init__", return_value=None)
    def test_creates_paramiko_executor_when_requested(self, mock_init: MagicMock) -> None:
        from anolis_workbench.core.executor import ParamikoSSHExecutor

        executor = create_ssh_executor("host", "user", use_paramiko=True)
        assert isinstance(executor, ParamikoSSHExecutor)
