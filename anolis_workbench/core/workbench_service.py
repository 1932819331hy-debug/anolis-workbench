"""Systemd service for the Anolis Workbench web server (appliance mode).

Generates and installs an anolis-workbench.service unit that auto-starts
the workbench UI on boot. It binds loopback: the workbench has no login, so
other machines reach it over an ssh tunnel.

When the installed runtime has an API token, the install copies that token
alone, never the rest of the runtime's environment file, into a 0600 file
owned by the service's user, which the workbench reads.
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
    Environment=ANOLIS_WORKBENCH_HOST=127.0.0.1
    Environment=ANOLIS_WORKBENCH_OPEN_BROWSER=0
    Environment=PYTHONUNBUFFERED=1
    StandardOutput=journal
    StandardError=journal

    [Install]
    WantedBy=multi-user.target
""")

SERVICE_NAME = "anolis-workbench.service"
_SYSTEMD_UNIT_DIR = Path("/etc/systemd/system")

# The runtime's install.sh keeps its API token, as ANOLIS_API_TOKEN, in this
# root-only file, alongside other secrets (INFLUXDB_TOKEN, for one). Only the
# token is copied out, into a 0600 file of its own owned by the service's
# user. The unit does not name the file, so deleting it can never stop the
# service from starting; the workbench reads it, and a missing or empty file
# means no token.
RUNTIME_ENV_FILE = Path("/etc/anolis/runtime.env")
RUNTIME_TOKEN_FILE = Path("/etc/anolis/workbench-runtime-token")
_NO_TOKEN_EXIT = 3

# Runs as root: copies the token value (only) from $1 to $2, owned by $3 at
# mode 0600. mktemp creates the file 0600, and the owner is set, before
# anything is written to it; the rename makes the update atomic. The value
# never reaches argv or output. Exits 3, after removing any stale copy, when
# there is no token to copy.
_TOKEN_SYNC_SCRIPT = """\
set -eu
src=$1
dst=$2
owner=$3
if [ ! -f "$src" ] || ! grep -qE '^ANOLIS_API_TOKEN=[^[:space:]]' "$src"; then
    rm -f -- "$dst"
    exit 3
fi
tmp=$(mktemp "$dst.XXXXXX")
trap 'rm -f -- "$tmp"' EXIT
chmod 0600 "$tmp"
chown -- "$owner" "$tmp"
sed -n 's/^ANOLIS_API_TOKEN=//p' "$src" | tail -n 1 | tr -d '\\n' > "$tmp"
mv -f -- "$tmp" "$dst"
"""


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


def read_runtime_token() -> str:
    """The copied runtime token, or "" when there is no readable copy."""
    try:
        return RUNTIME_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def sync_runtime_token(exe: Executor, owner: str) -> bool:
    """Copy the runtime's API token into RUNTIME_TOKEN_FILE, owned by `owner`, under sudo.

    Returns True when a token was copied, False when the runtime has none
    (no runtime.env, or no ANOLIS_API_TOKEN in it). Re-running it picks up
    a rotated token.
    """
    cmd = ["sh", "-c", _TOKEN_SYNC_SCRIPT, "sh", str(RUNTIME_ENV_FILE), str(RUNTIME_TOKEN_FILE), owner]
    result = exe.run(cmd, sudo=True)
    if result.returncode == 0:
        return True
    if result.returncode == _NO_TOKEN_EXIT:
        return False
    detail = (result.stderr or result.stdout).strip()
    raise OSError(f"copying the runtime API token failed (exit {result.returncode}): {detail}")


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
    unit_path = _SYSTEMD_UNIT_DIR / SERVICE_NAME

    try:
        sync_runtime_token(exe, user)
        unit_content = render_unit_file(user, executable)
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


def _sudo_unless_not_loaded(exe: Executor, cmd: list[str]) -> None:
    """Run a systemctl step, tolerating failure only when the unit is not loaded.

    systemctl(1) advises against reading its exit codes and to check the
    unit state instead; LoadState is "not-found" when no unit file exists.
    """
    try:
        _sudo(exe, cmd)
    except OSError:
        state = exe.run(["systemctl", "show", "--property=LoadState", "--value", SERVICE_NAME])
        if state.stdout.strip() == "not-found":
            return
        raise


def uninstall_service(executor: Executor | None = None) -> WorkbenchServiceResult:
    """Stop, disable, and remove the workbench systemd service and its copy
    of the runtime token (rm -f: a missing copy is fine)."""
    exe = executor or LocalExecutor()
    unit_path = _SYSTEMD_UNIT_DIR / SERVICE_NAME

    try:
        _sudo_unless_not_loaded(exe, ["systemctl", "stop", SERVICE_NAME])
        _sudo_unless_not_loaded(exe, ["systemctl", "disable", SERVICE_NAME])
        _sudo(exe, ["rm", "-f", str(unit_path), str(RUNTIME_TOKEN_FILE)])
        _sudo(exe, ["systemctl", "daemon-reload"])
    except Exception as exc:
        return WorkbenchServiceResult(installed=False, enabled=False, started=False, error=str(exc))

    return WorkbenchServiceResult(installed=False, enabled=False, started=False)
