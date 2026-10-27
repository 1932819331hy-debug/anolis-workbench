"""Workspace project authoring for Anolis provisioning.

Creates ready-to-launch workbench projects from bundled templates or an
existing canonical project directory. Deployment (binaries, /opt/anolis,
systemd) is delegated to the canonical anolis install.sh — see core/deploy.py.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from anolis_workbench.core import canonical, machine_profile
from anolis_workbench.core import paths as paths_module
from anolis_workbench.core import projects as projects_module
from anolis_workbench.core.executor import Executor

# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

_RUNTIME_BINARY_NAME = "anolis-runtime"

# What the last provisioning wrote into a workspace project, so a re-provision
# can tell the operator's edits from changes in the source (#650). A dotfile:
# no machine profile copies it, so it is never deployed.
PROVISIONED_RECORD = ".provisioned.json"
# Sidecar fields every provisioning rewrites; never a local edit.
_REGENERATED_SIDECAR_KEYS = ("meta", "host_paths")

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
    report: Callable[[str], None] | None = None,
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
            copy instead of a bundled template. An existing project is then
            updated from it without `force`, unless it has local edits (#650).
        report: Receives a line saying what a re-provision changed.

    Returns:
        Path to the created project directory.
    """
    if systems_root is None:
        systems_root = paths_module.SYSTEMS_ROOT

    project_dir = systems_root / project_name
    if project_dir.exists():
        if not force:
            if system_path is None:
                raise ValueError(f"Project '{project_name}' already exists at {project_dir}. Use --force to overwrite.")
            return _reprovision(template_name, project_name, install_prefix, system_path, systems_root, report)
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

    _write_record(project_dir)
    return project_dir


def _reprovision(
    template_name: str,
    project_name: str,
    install_prefix: Path,
    system_path: Path,
    systems_root: Path,
    report: Callable[[str], None] | None,
) -> Path:
    """Update an existing workspace project from `system_path`, unless it has
    edits of the operator's since it was last provisioned (#650)."""
    project_dir = systems_root / project_name
    record = _read_record(project_dir)
    current = _fingerprints(project_dir)
    if record is not None:
        edited = _differing(record, current)
        if edited:
            raise ValueError(
                f"Project '{project_name}' at {project_dir} has local edits since it was last provisioned: "
                f"{', '.join(edited)}. Re-provisioning from {system_path} would overwrite them; "
                "use --force to replace them."
            )
    # Staged beside the project, on the same filesystem, so it moves into place.
    with tempfile.TemporaryDirectory(prefix=".reprovision-", dir=systems_root) as staging:
        staged = provision_project(
            template_name, project_name, install_prefix, system_path=system_path, systems_root=Path(staging)
        )
        changed = _differing(current, _fingerprints(staged))
        if record is None and changed:
            raise ValueError(
                f"Project '{project_name}' at {project_dir} has no record of what was last provisioned into it "
                f"(an earlier workbench provisioned it), so its local edits can't be told apart from changes in "
                f"{system_path}. These files differ from it: {', '.join(changed)}. Check them, then re-provision "
                "once with --force; later re-provisions update it without --force."
            )
        paths_module.rmtree_within(project_dir, systems_root)
        shutil.move(str(staged), str(project_dir))
    if report is not None:
        if changed:
            report(f"Updated workspace project {project_name} from {system_path}: {', '.join(changed)}")
        else:
            report(f"Workspace project {project_name} is up to date with {system_path}")
    return project_dir


def _fingerprints(project_dir: Path) -> dict[str, str]:
    """sha256 per file of a workspace project, by relative path. The sidecar
    is hashed without the fields every provisioning rewrites."""
    out: dict[str, str] = {}
    for path in sorted(project_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(project_dir).as_posix()
        if rel == PROVISIONED_RECORD:
            continue
        data = path.read_bytes()
        if rel == machine_profile.SIDECAR_NAME:
            try:
                sidecar = json.loads(data)
            except ValueError:
                pass
            else:
                if isinstance(sidecar, dict):
                    kept = {k: v for k, v in sidecar.items() if k not in _REGENERATED_SIDECAR_KEYS}
                    data = json.dumps(kept, sort_keys=True).encode("utf-8")
        out[rel] = hashlib.sha256(data).hexdigest()
    return out


def _differing(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))


def _write_record(project_dir: Path) -> None:
    record = {"files": _fingerprints(project_dir)}
    (project_dir / PROVISIONED_RECORD).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_record(project_dir: Path) -> dict[str, str] | None:
    try:
        record = json.loads((project_dir / PROVISIONED_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    files = record.get("files") if isinstance(record, dict) else None
    if not isinstance(files, dict) or not all(isinstance(v, str) for v in files.values()):
        return None
    return {str(k): v for k, v in files.items()}


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
