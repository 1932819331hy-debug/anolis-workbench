"""Systemd service for the Anolis Workbench web server (appliance mode).

Generates and installs an anolis-workbench.service unit that auto-starts
the workbench UI on boot, making it accessible from any LAN device.
"""

from __future__ import annotations

import os
import shutil
import sys
import sysconfig
import tempfile
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

from anolis_workbench.core.executor import Executor, LocalExecutor

_UNIT_FILE = dedent("""\
    [Unit]
    Description=Anolis Workbench — Commissioning Web UI
    After=network-online.target
    Wants=network-online.target

    [Service]
    Type=simple
    User={user}
    ExecStart={exec_start}
    Restart=on-failure
    RestartSec=5s
    Environment=ANOLIS_WORKBENCH_HOST=0.0.0.0
    Environment=ANOLIS_WORKBENCH_OPEN_BROWSER=0
    StandardOutput=journal
    StandardError=journal

    [Install]
    WantedBy=multi-user.target
""")

SERVICE_NAME = "anolis-workbench.service"
_SYSTEMD_UNIT_DIR = Path("/etc/systemd/system")


@dataclass
class WorkbenchServiceResult:
    """Result of workbench service install operation."""

    installed: bool
    enabled: bool
    started: bool
    error: str | None = None


def invoking_user() -> str:
    """The user who ran the command: SUDO_USER under sudo, else USER."""
    return os.environ.get("SUDO_USER") or os.environ.get("USER") or "root"


def _find_workbench_executable() -> str:
    """Locate the anolis-workbench entry point as an absolute path.

    The running install's own scripts dir comes first: under sudo, PATH is
    secure_path and holds no pipx or venv bin dir.
    """
    beside = os.path.join(sysconfig.get_path("scripts"), "anolis-workbench")
    if os.path.isfile(beside):
        return os.path.abspath(beside)
    which = shutil.which("anolis-workbench")
    if which:
        return os.path.abspath(which)
    # Fallback: use the current Python interpreter with module invocation
    return f"{sys.executable} -m anolis_workbench.cli.main"


def render_unit_file(user: str, executable: str | None = None) -> str:
    """Render the workbench systemd unit file.

    Args:
        user: System user to run the service as.
        executable: Override for ExecStart (auto-detected if None).
    """
    exec_start = executable or _find_workbench_executable()
    return _UNIT_FILE.format(user=user, exec_start=exec_start)


def _sudo(exe: Executor, cmd: list[str]) -> None:
    result = exe.run(cmd, sudo=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise OSError(f"sudo {' '.join(cmd)} failed (exit {result.returncode}): {detail}")


def install_service(
    user: str,
    *,
    executable: str | None = None,
    executor: Executor | None = None,
) -> WorkbenchServiceResult:
    """Install, enable, and start the workbench systemd service.

    Args:
        user: System user to run the service as.
        executable: Override for ExecStart.
        executor: Command executor (defaults to local).
    """
    exe = executor or LocalExecutor()
    unit_content = render_unit_file(user, executable)
    unit_path = _SYSTEMD_UNIT_DIR / SERVICE_NAME

    try:
        # Staged in a private temp dir, then copied into place under sudo:
        # the unit dir is root's, and the caller is usually not.
        with tempfile.TemporaryDirectory(prefix="anolis-workbench-unit-") as td:
            staged = os.path.join(td, SERVICE_NAME)
            exe.write_file(staged, unit_content.encode())
            _sudo(exe, ["cp", staged, str(unit_path)])
        _sudo(exe, ["chmod", "0644", str(unit_path)])
        _sudo(exe, ["systemctl", "daemon-reload"])
        _sudo(exe, ["systemctl", "enable", SERVICE_NAME])
        _sudo(exe, ["systemctl", "restart", SERVICE_NAME])
    except Exception as exc:
        return WorkbenchServiceResult(installed=False, enabled=False, started=False, error=str(exc))

    return WorkbenchServiceResult(installed=True, enabled=True, started=True)


def uninstall_service(executor: Executor | None = None) -> WorkbenchServiceResult:
    """Stop, disable, and remove the workbench systemd service."""
    exe = executor or LocalExecutor()
    unit_path = _SYSTEMD_UNIT_DIR / SERVICE_NAME

    try:
        exe.run(["systemctl", "stop", SERVICE_NAME], sudo=True)
        exe.run(["systemctl", "disable", SERVICE_NAME], sudo=True)
        exe.run(["rm", "-f", str(unit_path)], sudo=True)
        exe.run(["systemctl", "daemon-reload"], sudo=True)
    except Exception as exc:
        return WorkbenchServiceResult(installed=False, enabled=False, started=False, error=str(exc))

    return WorkbenchServiceResult(installed=False, enabled=False, started=False)
