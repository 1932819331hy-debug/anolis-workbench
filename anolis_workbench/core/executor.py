"""Executor abstraction for local vs remote (SSH) command execution.

Provides a uniform interface so installer logic works identically whether
targeting the local machine or a remote host over SSH.
"""

from __future__ import annotations

import shlex
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from anolis_workbench.core.child_env import child_env

# Allowlist of binaries that executors are permitted to invoke.
# Commands not in this set are rejected to prevent command injection.
#
# Boundary: this guards Executor call sites only (deploy, bundle staging,
# rollback, the workbench service, remote probes). Dev-launch and its
# preflight run the user's own runtime and provider binaries with subprocess
# directly, from the paths the user set in the project's host paths, and
# `anolis-validate --runtime-bin` runs the runtime it is given. That is by
# design: those binaries are inside the trust boundary, and this allowlist
# could not express a user-chosen path anyway, because _validate_command keeps
# only the basename.
_ALLOWED_COMMANDS: set[str] = {
    "bash",
    "cat",
    "chmod",
    "cp",
    "curl",
    "df",
    "echo",
    "false",
    "groups",
    "ln",
    "ls",
    "mkdir",
    "mv",
    "pip3",
    "python3",
    "rm",
    "sh",
    "systemctl",
    "tar",
    "test",
    "true",
    "uname",
}


def _validate_command(cmd: list[str]) -> list[str]:
    """Validate command against the binary allowlist and return a sanitized copy.

    The returned list is a fresh copy with the binary resolved from the
    allowlist set, breaking data-flow from untrusted sources.

    Raises:
        ValueError: If the command binary is not in the allowlist.
    """
    if not cmd:
        raise ValueError("Empty command")
    binary = cmd[0].rsplit("/", 1)[-1]  # basename
    if binary not in _ALLOWED_COMMANDS:
        raise ValueError(f"Command '{binary}' is not in the executor allowlist. Allowed: {sorted(_ALLOWED_COMMANDS)}")
    # Rebuild command with the verified binary from _ALLOWED_COMMANDS.
    # This severs taint propagation: the binary value originates from the
    # frozen allowlist, not from caller-supplied data.
    verified_binary: str = next(b for b in _ALLOWED_COMMANDS if b == binary)
    return [verified_binary, *(str(a) for a in cmd[1:])]


@dataclass
class RunResult:
    """Result of executing a command."""

    returncode: int
    stdout: str
    stderr: str


class Executor(ABC):
    """Abstract base for command execution on a target machine."""

    @abstractmethod
    def run(
        self, cmd: list[str], *, input: bytes | None = None, sudo: bool = False, timeout: float | None = None
    ) -> RunResult:
        """Run a command on the target. Returns RunResult.

        timeout: seconds before the command is killed; None uses the
        implementation default (short — pass an explicit value for
        long-running commands like installs).
        """

    @abstractmethod
    def write_file(self, path: str, data: bytes) -> None:
        """Write bytes to a file on the target."""

    @abstractmethod
    def mkdir(self, path: str) -> None:
        """Create directory (and parents) on the target."""

    @abstractmethod
    def file_exists(self, path: str) -> bool:
        """Check if a file exists on the target."""


class LocalExecutor(Executor):
    """Executes operations on the local machine via subprocess + pathlib."""

    def run(
        self, cmd: list[str], *, input: bytes | None = None, sudo: bool = False, timeout: float | None = None
    ) -> RunResult:
        safe_cmd = _validate_command(cmd)
        full_cmd = (["sudo"] + safe_cmd) if sudo else safe_cmd
        result = subprocess.run(
            full_cmd, input=input, capture_output=True, timeout=timeout if timeout else 30, env=child_env()
        )
        return RunResult(
            returncode=result.returncode,
            stdout=result.stdout.decode(errors="replace"),
            stderr=result.stderr.decode(errors="replace"),
        )

    def write_file(self, path: str, data: bytes) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def mkdir(self, path: str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)

    def file_exists(self, path: str) -> bool:
        return Path(path).exists()


class SubprocessSSHExecutor(Executor):
    """Executes operations on a remote machine via system ssh/scp."""

    def __init__(self, host: str, user: str, *, key_file: str | None = None, port: int = 22):
        self.host = host
        self.user = user
        self.key_file = key_file
        self.port = port

    def _ssh_base(self, *, allocate_pty: bool = False) -> list[str]:
        args = ["ssh", "-o", "BatchMode=yes", "-p", str(self.port)]
        if self.key_file:
            args += ["-i", self.key_file]
        if allocate_pty:
            args += ["-t", "-t"]
        args.append(f"{self.user}@{self.host}")
        return args

    def run(
        self, cmd: list[str], *, input: bytes | None = None, sudo: bool = False, timeout: float | None = None
    ) -> RunResult:
        safe_cmd = _validate_command(cmd)
        remote_cmd = (["sudo"] + safe_cmd) if sudo else safe_cmd
        ssh_cmd = self._ssh_base(allocate_pty=sudo) + [shlex.join(remote_cmd)]
        result = subprocess.run(
            ssh_cmd,
            input=input,
            capture_output=True,
            timeout=timeout if timeout else 60,
            env=child_env(),
        )
        return RunResult(
            returncode=result.returncode,
            stdout=result.stdout.decode(errors="replace"),
            stderr=result.stderr.decode(errors="replace"),
        )

    def write_file(self, path: str, data: bytes) -> None:
        # Write via ssh cat > path
        ssh_cmd = self._ssh_base() + [f"cat > {shlex.quote(path)}"]
        result = subprocess.run(ssh_cmd, input=data, capture_output=True, timeout=60, env=child_env())
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace").strip()
            raise OSError(f"Failed to write {path} on remote: {stderr}")

    def mkdir(self, path: str) -> None:
        result = self.run(["mkdir", "-p", path])
        if result.returncode != 0:
            raise OSError(f"Failed to mkdir {path} on remote: {result.stderr}")

    def file_exists(self, path: str) -> bool:
        result = self.run(["test", "-e", path])
        return result.returncode == 0


class ParamikoSSHExecutor(Executor):
    """Executes operations on a remote machine via paramiko (programmatic SSH).

    Used when running inside the workbench server (Tauri sidecar) where no
    system terminal is available. paramiko is a core dependency; it is
    imported here, not at module load, to keep start-up light.

    Known-host verification: loads system ~/.ssh/known_hosts. Unknown hosts
    are rejected (no auto-add).
    """

    def __init__(
        self,
        host: str,
        user: str,
        *,
        key_file: str | None = None,
        port: int = 22,
    ):
        import paramiko

        self.host = host
        self.user = user
        self.port = port
        self._client = paramiko.SSHClient()
        self._client.load_system_host_keys()
        self._client.set_missing_host_key_policy(paramiko.RejectPolicy())
        connect_kwargs: dict[str, object] = {
            "hostname": host,
            "port": port,
            "username": user,
        }
        if key_file:
            connect_kwargs["key_filename"] = key_file
        self._client.connect(**connect_kwargs)  # type: ignore[arg-type]
        self._sftp = self._client.open_sftp()

    def run(
        self, cmd: list[str], *, input: bytes | None = None, sudo: bool = False, timeout: float | None = None
    ) -> RunResult:
        safe_cmd = _validate_command(cmd)
        remote_cmd = shlex.join(safe_cmd)
        if sudo:
            remote_cmd = f"sudo {remote_cmd}"
        stdin, stdout, stderr = self._client.exec_command(remote_cmd, timeout=timeout)
        if input:
            stdin.write(input)
            stdin.flush()
            stdin.channel.shutdown_write()
        exit_code = stdout.channel.recv_exit_status()
        return RunResult(
            returncode=exit_code,
            stdout=stdout.read().decode(errors="replace"),
            stderr=stderr.read().decode(errors="replace"),
        )

    def write_file(self, path: str, data: bytes) -> None:
        with self._sftp.open(path, "wb") as f:
            f.write(data)

    def mkdir(self, path: str) -> None:
        # Walk path components and create each level
        parts = Path(path).parts
        current = ""
        for part in parts:
            current = f"{current}/{part}" if current else part
            if current == "/":
                continue
            try:
                self._sftp.stat(current)
            except FileNotFoundError:
                self._sftp.mkdir(current)

    def file_exists(self, path: str) -> bool:
        try:
            self._sftp.stat(path)
            return True
        except FileNotFoundError:
            return False

    def close(self) -> None:
        """Close the SSH connection and SFTP channel."""
        self._sftp.close()
        self._client.close()


def create_ssh_executor(
    host: str,
    user: str,
    *,
    key_file: str | None = None,
    port: int = 22,
    use_paramiko: bool = False,
) -> Executor:
    """Factory for creating the appropriate SSH executor.

    Args:
        host: Remote hostname.
        user: SSH username.
        key_file: Optional path to private key.
        port: SSH port.
        use_paramiko: If True, use ParamikoSSHExecutor (server/UI mode).
                      If False, use SubprocessSSHExecutor (CLI mode).

    Returns:
        An Executor instance connected to the remote host.
    """
    if use_paramiko:
        return ParamikoSSHExecutor(host=host, user=user, key_file=key_file, port=port)
    return SubprocessSSHExecutor(host=host, user=user, key_file=key_file, port=port)
