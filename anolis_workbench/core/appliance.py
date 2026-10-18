"""Appliance mode detection for Raspberry Pi environments.

When the workbench is running directly on a Raspberry Pi (aarch64 + device
tree model contains "Raspberry Pi"), it should default to appliance behavior:
- Skip browser auto-open (headless environment)
- Auto-add localhost as the default runtime target

The bind address is loopback in every mode. The workbench has no login, so
it is reached from another machine over an ssh tunnel (see tunnel_hint). An
explicit ANOLIS_WORKBENCH_HOST still wins.
"""

from __future__ import annotations

import platform
from pathlib import Path

_DT_MODEL_PATH = Path("/sys/firmware/devicetree/base/model")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3010


def is_raspberry_pi() -> bool:
    """Detect if running on a Raspberry Pi.

    Checks: architecture is aarch64 AND device tree model contains
    "Raspberry Pi".
    """
    if platform.machine() != "aarch64":
        return False
    try:
        model = _DT_MODEL_PATH.read_text(encoding="utf-8", errors="replace").strip("\x00\n")
        return "Raspberry Pi" in model
    except OSError:
        return False


def is_appliance_mode() -> bool:
    """Detect if workbench should run in appliance mode.

    Appliance mode is active when:
    1. Running on a Raspberry Pi, AND
    2. ANOLIS_WORKBENCH_HOST is not explicitly set (user hasn't overridden)
    """
    import os

    if os.getenv("ANOLIS_WORKBENCH_HOST"):
        return False  # user explicitly set the bind address
    return is_raspberry_pi()


def default_host() -> str:
    """Return the default bind host: loopback, in appliance mode too."""
    return DEFAULT_HOST


def default_open_browser() -> bool:
    """Return whether the browser should auto-open.

    Returns False in appliance mode (headless Pi), True otherwise.
    """
    return not is_appliance_mode()


def tunnel_hint(port: int = DEFAULT_PORT) -> str:
    """How to reach a loopback-bound workbench from another machine."""
    return (
        "The workbench listens on 127.0.0.1 only. To reach it from another machine, open an ssh tunnel:\n"
        f"  ssh -L {port}:127.0.0.1:{port} <user>@<host>\n"
        f"then browse to http://127.0.0.1:{port}"
    )
