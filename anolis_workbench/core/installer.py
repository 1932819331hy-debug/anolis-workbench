"""Workspace project authoring for Anolis provisioning.

Creates ready-to-launch workbench projects from bundled templates or an
existing canonical project directory. Deployment (binaries, /opt/anolis,
systemd) is delegated to the canonical anolis install.sh — see core/deploy.py.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from anolis_workbench.core import canonical, machine_profile
from anolis_workbench.core import paths as paths_module
from anolis_workbench.core import projects as projects_module
from anolis_workbench.core.executor import Executor

# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

_RUNTIME_BINARY_NAME = "anolis-runtime"

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InstallerError(RuntimeError):
    """Base error for provisioning failures."""


# ---------------------------------------------------------------------------
# Project provisioning
# ---------------------------------------------------------------------------


def provision_project(
    template_name: str,
    project_name: str,
    install_prefix: Path,
    *,
    force: bool = False,
    executor: Executor | None = None,
    systems_root: Path | None = None,
    system_path: Path | None = None,
) -> Path:
    """Create a workbench project from a bundled canonical template.

    Since #255 a project IS the canonical artifact set, so this copies the
    template directory and re-keys it onto the new project's machine_id. Host
    binary paths (the dev-launch residual) are pointed at the install prefix in
    the workbench sidecar; the canonical configs keep their deploy tokens,
    which install.sh rewrites to whatever --prefix it is given.

    Args:
        template_name: Template directory name under templates/.
        project_name: Name for the created project.
        install_prefix: Binary install prefix, used for the sidecar host paths.
        force: If True, replace an existing project.
        executor: Unused for local writes; kept for signature compatibility.
        systems_root: Override systems root. Defaults to local SYSTEMS_ROOT.
        system_path: Optional path to an existing canonical project directory to
            copy instead of a bundled template.

    Returns:
        Path to the created project directory.
    """
    if systems_root is None:
        systems_root = paths_module.SYSTEMS_ROOT

    project_dir = systems_root / project_name
    if project_dir.exists():
        if not force:
            raise ValueError(f"Project '{project_name}' already exists at {project_dir}. Use --force to overwrite.")
        paths_module.rmtree_within(project_dir, systems_root)

    source = system_path if system_path is not None else paths_module.TEMPLATES_ROOT / template_name
    if source.is_file():  # a legacy system.json handed to the CLI
        source = source.parent
    if not (source / machine_profile.PROFILE_FILENAME).is_file():
        available = (
            ", ".join(sorted(d.name for d in paths_module.TEMPLATES_ROOT.iterdir() if d.is_dir()))
            if paths_module.TEMPLATES_ROOT.is_dir()
            else "(none)"
        )
        raise FileNotFoundError(
            f"Template '{template_name}' not found at {source} (available: {available}). "
            "Templates that mirrored real machines were removed — import the machine's own "
            "project directory instead of seeding a copy of it."
        )

    previous_root = projects_module.SYSTEMS_ROOT
    try:
        projects_module.SYSTEMS_ROOT = systems_root
        if system_path is not None:
            _copy_canonical(source, project_dir)
            machine_id = canonical.machine_id_from_name(project_name)
            canonical.retarget_project(project_dir, machine_id)
            # retarget rewrote every path token onto the new machine_id, so the
            # sidecar's deploy-directory name has to follow. Leaving the copied
            # source's name behind installs the project into one directory while
            # its configs point at another — install.sh reports success and the
            # providers then crash-loop on a config that isn't there.
            sidecar = machine_profile.read_sidecar(project_dir) or {}
            meta = sidecar.get("meta")
            meta = meta if isinstance(meta, dict) else {}
            meta["profile_dir_name"] = machine_id
            meta["name"] = project_name
            sidecar["meta"] = meta
            canonical.write_sidecar(project_dir, sidecar)
        else:
            projects_module.create_project_from_template(project_name, template_name)
        _point_host_paths_at_prefix(project_dir, install_prefix)
    finally:
        projects_module.SYSTEMS_ROOT = previous_root

    return project_dir


def _copy_canonical(source: Path, dest: Path) -> None:
    dest.mkdir(parents=True)
    entries = list(machine_profile.copy_entries(machine_profile.load_profile(source)))
    entries.append(machine_profile.SIDECAR_NAME)
    for entry in entries:
        src = source / entry
        if src.is_file():
            shutil.copy2(src, dest / entry)
        elif src.is_dir():
            shutil.copytree(src, dest / entry, copy_function=shutil.copy2)


def _point_host_paths_at_prefix(project_dir: Path, install_prefix: Path) -> None:
    """The sidecar's host paths are what dev-launch runs; point them at the
    installed binaries. The canonical configs are untouched — their command
    tokens are rewritten by install.sh at deploy time."""
    sidecar = machine_profile.read_sidecar(project_dir) or {}
    bin_dir = install_prefix / "bin"
    host_paths = canonical.host_paths(sidecar)
    host_paths["runtime_executable"] = str(bin_dir / _RUNTIME_BINARY_NAME)
    profile = machine_profile.load_profile(project_dir)
    kinds = machine_profile.derive_kinds(profile, project_dir)
    host_paths["providers"] = {
        pid: {"executable": str(bin_dir / f"anolis-provider-{kind}")}
        for pid, kind in kinds.items()
        if isinstance(kind, str)
    }
    sidecar["host_paths"] = host_paths
    canonical.write_sidecar(project_dir, sidecar)
