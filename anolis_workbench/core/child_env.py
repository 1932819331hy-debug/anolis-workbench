"""The environment the workbench hands to the processes it spawns.

The workbench may hold a runtime bearer token: ANOLIS_WORKBENCH_RUNTIME_TOKEN,
set by the operator, or the copy install --workbench-service writes, whose
path is fixed in code rather than in the environment. Processes the
workbench starts (a dev-launched runtime and, through it, providers whose
executables come from a project's host paths; preflight checks; install.sh;
ssh) have no use for it, so it, and systemd's CREDENTIALS_DIRECTORY should a
unit ever set one, are withheld from their environment.
"""

from __future__ import annotations

import os

WITHHELD = ("ANOLIS_WORKBENCH_RUNTIME_TOKEN", "CREDENTIALS_DIRECTORY")


def child_env() -> dict[str, str]:
    """os.environ without the variables in WITHHELD."""
    return {key: value for key, value in os.environ.items() if key not in WITHHELD}
