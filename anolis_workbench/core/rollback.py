"""Rollback: thin wrapper over `install.sh --rollback`.

install.sh backs the prior binaries up to <prefix>/.prev on every install
and owns the restore (copy back, chown, restart, health check) — one
provisioning engine for deploy and rollback alike. Works locally or over
SSH via the Executor.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from anolis_workbench.core import deploy
from anolis_workbench.core.executor import Executor


@dataclass
class RollbackResult:
    """Result of a rollback operation."""

    success: bool
    output: str = ""
    error: str | None = None
    # Set when install.sh ran without a SHA256SUMS line to check it against.
    warning: str | None = None


def rollback(
    prefix: Path,
    *,
    executor: Executor | None = None,
) -> RollbackResult:
    """Restore the previous binaries via install.sh --rollback.

    Args:
        prefix: Install prefix (e.g. /opt/anolis).
        executor: Executor for the target (local default, or SSH).

    Returns:
        RollbackResult; failure details (e.g. no .prev backup) in .error.
    """
    warnings: list[str] = []

    def _progress(step: str, detail: str = "") -> None:
        if step == "unverified":
            warnings.append(detail)

    try:
        output = deploy.run_rollback(executor, prefix=prefix, progress_callback=_progress)
    except deploy.DeployError as exc:
        return RollbackResult(success=False, error=str(exc))
    return RollbackResult(success=True, output=output, warning=warnings[0] if warnings else None)
