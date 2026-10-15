"""First-run onboarding detection endpoint."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from anolis_workbench.core import fleet, machine_profile
from anolis_workbench.server.routes.provision import CREATED_BY_PROVISION


def _systems_root() -> Path:
    """Return the systems root directory (~/.anolis/systems)."""
    return Path.home() / ".anolis" / "systems"


def _has_projects() -> bool:
    """Check if any project directories exist under ~/.anolis/systems/."""
    root = _systems_root()
    if not root.is_dir():
        return False
    return any(p.is_dir() for p in root.iterdir())


def _has_user_project() -> bool:
    """Check for a project other than one onboarding seeded for its install."""
    root = _systems_root()
    if not root.is_dir():
        return False
    for project_dir in root.iterdir():
        if not project_dir.is_dir():
            continue
        meta = (machine_profile.read_sidecar(project_dir) or {}).get("meta")
        if not (isinstance(meta, dict) and meta.get("created_by") == CREATED_BY_PROVISION):
            return True
    return False


def _runtime_path() -> str:
    """Return the expected runtime binary path."""
    return os.environ.get("ANOLIS_RUNTIME_PATH", "/opt/anolis/bin/anolis-runtime")


def _has_runtime() -> bool:
    """Check if the runtime binary exists."""
    return Path(_runtime_path()).is_file()


def _fleet_registry_path() -> Path:
    """Return the fleet registry a successful remote deploy writes to."""
    return fleet._FLEET_REGISTRY_PATH


def _has_fleet_entry() -> bool:
    """Check if the fleet registry lists at least one target."""
    path = _fleet_registry_path()
    if not path.is_file():
        return False
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return False
    targets = raw.get("targets") if isinstance(raw, dict) else None
    return isinstance(targets, list) and any(isinstance(t, dict) for t in targets)


def get_onboarding_status(handler: Any) -> None:
    """GET /api/onboarding — return first-run detection status."""
    has_projects = _has_projects()
    has_runtime = _has_runtime()
    has_fleet_entry = _has_fleet_entry()
    # First run lasts until a deploy has succeeded (a local one leaves the
    # runtime binary, a remote one registers its host) or the user has a
    # project of their own. The project onboarding's install job seeds before
    # install.sh runs does not count, so a failed install keeps onboarding.
    first_run = not has_runtime and not has_fleet_entry and not _has_user_project()

    data = {
        "first_run": first_run,
        "has_projects": has_projects,
        "has_runtime": has_runtime,
        "has_fleet_entry": has_fleet_entry,
        "runtime_path": _runtime_path(),
    }
    handler._json(200, data)
