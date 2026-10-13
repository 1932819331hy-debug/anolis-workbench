"""Project CRUD and filesystem helpers for Anolis Workbench."""

import functools
import json
import os
import pathlib
import re
import shutil
import threading
from datetime import datetime, timezone

import jsonschema

from anolis_workbench.core import canonical, canonical_validator, machine_profile, migrations, provider_schemas
from anolis_workbench.core import paths as paths_module

SYSTEMS_ROOT = paths_module.SYSTEMS_ROOT
TEMPLATES_ROOT = paths_module.TEMPLATES_ROOT

NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# The server is threaded, and every write path here is read-modify-write over a
# whole project directory — the profile, its variant configs and its provider
# configs have to agree with each other. Interleaving two of them produces a
# profile declaring a variant whose file another save just retired, which blocks
# EVERY deploy of the project and cannot be repaired from the UI (a variant with
# no document is invisible there). Coarse and correct beats clever here: these
# operations are user-initiated and rare.
_WRITE_LOCK = threading.RLock()


def _serialized(fn):
    """Run a project-mutating operation under the workspace write lock."""

    @functools.wraps(fn)
    def _wrapped(*args, **kwargs):
        with _WRITE_LOCK:
            return fn(*args, **kwargs)

    return _wrapped


SIDECAR_NAME = machine_profile.SIDECAR_NAME

FORMAT_SYSTEM = machine_profile.FORMAT_SYSTEM
FORMAT_MACHINE_PROFILE = machine_profile.FORMAT_MACHINE_PROFILE

# Only these are copied on import (issue #226): the profile plus everything it
# can reference. docs/, config-release/, workbench/ etc. stay in the source repo,
# and import REFUSES a profile whose references fall outside this set.
_IMPORT_COPY = machine_profile.IMPORT_COPY


class ProjectValidationError(ValueError):
    """Raised when a composer system document fails validation."""

    def __init__(self, errors: list[dict[str, str]]) -> None:
        super().__init__("Project validation failed")
        self.errors = errors


class ImportValidationError(ValueError):
    """Raised when a machine-profile directory fails import validation."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("Import validation failed")
        self.errors = errors


class ReadOnlyProjectError(ValueError):
    """Raised when a write path is attempted on an imported (verbatim) project."""


def validate_name(name: str) -> "str | None":
    if not NAME_RE.match(name or ""):
        return "Project name must be 1-64 characters: letters, digits, hyphens, underscores only."
    return None


def _json_path_from_iter(path_parts: list) -> str:
    if not path_parts:
        return "$"
    out = "$"
    for part in path_parts:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}"
    return out


def project_dir(name: str) -> pathlib.Path:
    return SYSTEMS_ROOT / name


def running_json_path(name: str) -> pathlib.Path:
    return project_dir(name) / "running.json"


def log_path(name: str) -> pathlib.Path:
    return project_dir(name) / "logs" / "latest.log"


# ---------------------------------------------------------------------------
# PID existence check (cross-platform, no required external deps)
# ---------------------------------------------------------------------------


def _pid_exists(pid: int) -> bool:
    try:
        import psutil

        return bool(psutil.pid_exists(pid))
    except ImportError:
        pass
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_INFORMATION = 0x0400
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)  # type: ignore[attr-defined]
        if handle == 0:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return True
    # Unix
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


def is_running(name: str) -> bool:
    rj = running_json_path(name)
    if not rj.exists():
        return False
    try:
        data = json.loads(rj.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    pid = data.get("pid")
    if pid is None:
        return False
    return _pid_exists(pid)


def cleanup_stale_running_files() -> None:
    if not SYSTEMS_ROOT.exists():
        return
    for project in SYSTEMS_ROOT.iterdir():
        if not project.is_dir():
            continue
        rj = project / "running.json"
        if rj.exists() and not is_running(project.name):
            print(f"[projects] Cleaning stale running.json for '{project.name}'")
            rj.unlink()


# ---------------------------------------------------------------------------
# Core CRUD
# ---------------------------------------------------------------------------


def sidecar_path(name: str) -> pathlib.Path:
    return project_dir(name) / SIDECAR_NAME


def _read_sidecar(pdir: pathlib.Path) -> dict | None:
    return machine_profile.read_sidecar(pdir)


def _sidecar_or_default(pdir: pathlib.Path) -> dict:
    """The workbench sidecar, or a fresh authored-project skeleton."""
    sidecar = machine_profile.read_sidecar(pdir)
    if isinstance(sidecar, dict):
        return sidecar
    return {"schema_version": canonical.SIDECAR_SCHEMA_VERSION, "format": FORMAT_MACHINE_PROFILE, "meta": {}}


def _meta_of(sidecar: dict) -> dict:
    meta = sidecar.get("meta")
    return meta if isinstance(meta, dict) else {}


def deploy_dir_name(pdir: pathlib.Path) -> str | None:
    """The directory this project installs into on the target.

    Must match `deploy.materialize_project_dir`: the sidecar's
    `profile_dir_name` wins, because an imported project legitimately keeps its
    source directory name while its machine_id says something else.
    """
    sidecar = machine_profile.read_sidecar(pdir) or {}
    meta = sidecar.get("meta")
    if isinstance(meta, dict):
        name = meta.get("profile_dir_name")
        if isinstance(name, str) and name:
            return name
    try:
        profile = machine_profile.load_profile(pdir)
    except (machine_profile.ProfileError, FileNotFoundError, OSError):
        return None
    machine_id = profile.get("machine_id")
    return machine_id if isinstance(machine_id, str) and machine_id else None


def deploy_dirs_in_use(exclude: str | None = None) -> dict[str, str]:
    """deploy directory name -> the project that owns it, across the workspace."""
    owners: dict[str, str] = {}
    if not SYSTEMS_ROOT.exists():
        return owners
    for d in sorted(SYSTEMS_ROOT.iterdir()):
        if not d.is_dir() or d.name == exclude:
            continue
        name = deploy_dir_name(d)
        if name is not None:
            owners.setdefault(name, d.name)
    return owners


def deploy_dir_conflict(deploy_name: str, name: str) -> str | None:
    """The project already using this deploy directory, as a message, or None."""
    owner = deploy_dirs_in_use(exclude=name).get(deploy_name)
    if owner is None:
        return None
    return (
        f"This project deploys into '{deploy_name}', which project '{owner}' also uses. "
        "install.sh removes that directory before installing, so deploying one replaces "
        "the other's installed config."
    )


def _assert_deploy_dir_free(deploy_name: str, name: str) -> None:
    """install.sh `rm -rf`s `{prefix}/projects/<dir>` before installing.

    Two workspace projects that deploy into the same directory therefore fight
    over it on the rig: deploying one wipes the other's installed config. The
    collision is easy to reach — `Rig_A` and `rig-a` slugify identically, a
    rename deliberately keeps the old identity, and importing one source twice
    carries the same directory name both times.
    """
    owner = deploy_dirs_in_use(exclude=name).get(deploy_name)
    if owner is not None:
        raise ValueError(
            f"This project would deploy into '{deploy_name}', which project '{owner}' already "
            "uses. Two projects cannot share one deploy directory — installing either would "
            "overwrite the other on the target. Choose a different name."
        )


def is_authored(name: str) -> bool:
    """Whether the workbench authored this project (vs. imported it).

    Since #255 every project is machine-profile FORMAT, so format can no longer
    tell the two apart — `authored` is the only distinction, and callers that
    gate on "is this an import" must ask this instead.
    """
    pdir = project_dir(name)
    if _dir_format(pdir) is None:
        raise FileNotFoundError(f"Project '{name}' not found")
    return canonical.is_authored(_read_sidecar(pdir))


def _dir_format(pdir: pathlib.Path, *, migrate: bool = True) -> str | None:
    """Project format for a workspace dir, or None if it isn't a project.

    A legacy system.json project is migrated in place on first sight (#255), so
    no caller downstream — CRUD, deploy, launcher, exporter — ever has to know
    the retired layout existed.
    """
    if (pdir / "system.json").is_file():
        if not migrate:
            return FORMAT_SYSTEM
        try:
            migrations.migrate_project_dir(pdir, project_name=pdir.name)
        except Exception as exc:  # noqa: BLE001 - one bad project must not break the workspace
            # A read-only workspace, a truncated system.json, or a legacy
            # document too degenerate to migrate. Report the project honestly
            # and keep going: this runs from list_projects, so raising here
            # would make EVERY other project unreachable in the UI.
            print(f"[projects] Could not migrate '{pdir.name}' to the canonical layout: {exc}")
            return FORMAT_SYSTEM
    sidecar = _read_sidecar(pdir)
    if sidecar and sidecar.get("format") == FORMAT_MACHINE_PROFILE:
        return FORMAT_MACHINE_PROFILE
    if (pdir / machine_profile.PROFILE_FILENAME).is_file():
        return FORMAT_MACHINE_PROFILE
    return None


def migrate_workspace() -> list[str]:
    """Migrate every legacy project in the workspace (called at server start).

    Eager migration keeps a stale layout from reaching a CLI or deploy path
    that never opens the project in the UI first.
    """
    migrated: list[str] = []
    if not SYSTEMS_ROOT.exists():
        return migrated
    for d in sorted(SYSTEMS_ROOT.iterdir()):
        if not d.is_dir() or not (d / "system.json").is_file():
            continue
        try:
            did, _ = migrations.migrate_project_dir(d, project_name=d.name)
        except (OSError, ValueError) as exc:
            print(f"[projects] Could not migrate '{d.name}' to the canonical layout: {exc}")
            continue
        if did:
            migrated.append(d.name)
    return migrated


def project_format(name: str) -> str:
    fmt = _dir_format(project_dir(name))
    if fmt is None:
        raise FileNotFoundError(f"Project '{name}' not found")
    return fmt


def list_projects() -> list:
    if not SYSTEMS_ROOT.exists():
        return []
    result = []
    for d in sorted(SYSTEMS_ROOT.iterdir()):
        if not d.is_dir():
            continue
        fmt = _dir_format(d)
        if fmt is None:
            continue
        sidecar = _read_sidecar(d) or {}
        meta = sidecar.get("meta") if isinstance(sidecar.get("meta"), dict) else {}
        # A project still reporting FORMAT_SYSTEM could not be migrated (a
        # read-only workspace, or a legacy document too degenerate). List it
        # anyway — hiding it would make a real project look deleted.
        result.append(
            {
                "name": d.name,
                "format": fmt,
                "authored": canonical.is_authored(sidecar),
                "meta": meta,
            }
        )
    return result


def _degraded_project(name: str) -> dict:
    """A project whose canonical files could not be fully parsed.

    Returns the FULL document shape with empty collections rather than a
    partial one: the API contract requires those keys, and a client that finds
    `authored` missing reads the project as editable — then throws on the first
    edit and never shows the parse error that caused this.
    """
    pdir = project_dir(name)
    sidecar = _read_sidecar(pdir) or {}
    try:
        profile = machine_profile.load_profile(pdir)
    except machine_profile.ProfileError as exc:
        profile = {}
        warnings = [str(exc)]
    else:
        raw_warnings = sidecar.get("warnings")
        warnings = list(raw_warnings) if isinstance(raw_warnings, list) else []
    return {
        "format": FORMAT_MACHINE_PROFILE,
        # Degraded projects are never editable: saving would rewrite files we
        # could not read in the first place.
        "authored": False,
        "meta": sidecar.get("meta") if isinstance(sidecar.get("meta"), dict) else {"name": name},
        "profile": profile,
        "variants": {},
        "providers": {},
        "host_paths": canonical.host_paths(sidecar),
        "launch": sidecar.get("launch") if isinstance(sidecar.get("launch"), dict) else {},
        "warnings": warnings,
    }


def _with_derived_warnings(document: dict) -> list[str]:
    """Sidecar warnings plus the ones derived from the document itself.

    Version skew (#283) is a pure function of the document and the packaged
    envelopes, so it is computed here rather than recorded at import or save
    time — correcting a pin, or syncing the envelope that resolves the skew,
    clears it on the next read with no stale copy left behind (#290).
    """
    carried = document.get("warnings")
    warnings = [w for w in carried if isinstance(w, str)] if isinstance(carried, list) else []
    return warnings + [w for w in provider_schemas.version_skew_warnings(document) if w not in warnings]


def get_project(name: str) -> dict:
    """The whole canonical project as one document.

    Imported projects are parsed for display only and never written back;
    authored ones round-trip through save_project.
    """
    fmt = _dir_format(project_dir(name))
    if fmt == FORMAT_MACHINE_PROFILE:
        pdir = project_dir(name)
        try:
            document = canonical.read_project(pdir)
        except (machine_profile.ProfileError, canonical.CanonicalError) as exc:
            return {**_degraded_project(name), "warnings": [str(exc)]}
        document["meta"] = document.get("meta") or {"name": name}
        document["warnings"] = _with_derived_warnings(document)
        return document
    if fmt == FORMAT_SYSTEM:
        # Listed (hiding it would look like deletion) but not migratable, so
        # serve the degraded shape rather than 404ing a project the UI shows.
        return _degraded_project(name)
    raise FileNotFoundError(f"Project '{name}' not found")


@_serialized
def import_project(source_path: str, name: str | None = None) -> tuple[dict, list[str]]:
    """Import a canonical machine-profile directory as a verbatim project (#226).

    Copies the profile plus everything it references byte-for-byte and writes
    the workbench sidecar. `name` defaults to the source directory's name.
    Returns (project meta, import warnings).
    """
    # Canonicalize + allowlist-check the caller-supplied path BEFORE any
    # filesystem use; everything below works on the returned safe path.
    source = machine_profile.resolve_import_source(source_path)
    profile_dir_name = source.name
    if profile_dir_name == "":  # e.g. "/" — deploy needs a real project dir name
        raise ImportValidationError([f"Cannot import {source}: not a project directory"])

    if name is None:
        name = profile_dir_name
    name_error = validate_name(name)
    if name_error:
        raise ValueError(name_error)
    if project_dir(name).exists():
        raise ValueError(f"Project '{name}' already exists")

    report = machine_profile.validate_project_dir(source, profile_dir_name)
    if not report.ok:
        raise ImportValidationError(report.errors)

    # Importing one source twice gives both projects the same deploy directory.
    # Imported projects are carried verbatim so the two are byte-identical, which
    # makes this benign today — but say so, because it stops being benign the
    # moment someone hand-edits one of them.
    conflict = deploy_dir_conflict(profile_dir_name, name)
    if conflict is not None:
        report.warnings.append(conflict)

    pdir = project_dir(name)
    # Claim the directory FIRST (exist_ok=False): a concurrent same-name import
    # must lose here, before the cleanup branch below can delete a live project.
    try:
        pdir.mkdir(parents=True)
    except FileExistsError as exc:
        raise ValueError(f"Project '{name}' already exists") from exc
    try:
        for entry in machine_profile.copy_entries(report.profile or {}):
            src = source / entry
            if src.is_file():
                shutil.copy2(src, pdir / entry)
            elif src.is_dir():
                shutil.copytree(src, pdir / entry, copy_function=shutil.copy2)
        meta = {
            "name": name,
            "created": datetime.now(timezone.utc).isoformat(),
            "imported_from": str(source),
            "profile_dir_name": profile_dir_name,
        }
        sidecar = {
            "schema_version": 1,
            "format": FORMAT_MACHINE_PROFILE,
            "meta": meta,
            "warnings": report.warnings,
        }
        sidecar_path(name).write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    except Exception:
        shutil.rmtree(pdir, ignore_errors=True)
        raise
    return meta, report.warnings


@_serialized
def save_project(name: str, document: dict) -> None:
    """Persist an authored canonical project document."""
    pdir = project_dir(name)
    sidecar = _sidecar_or_default(pdir)
    if pdir.exists() and not canonical.is_authored(sidecar):
        raise ReadOnlyProjectError(
            f"Project '{name}' was imported from a machine profile and is carried "
            "verbatim — edit it in its source repository and re-import."
        )

    errors = validate_project_payload(document, project_dir=pdir)
    if errors:
        raise ProjectValidationError(errors)

    document = {**document, "authored": True}
    canonical.write_project(pdir, document)

    # The sidecar is workbench-owned; carry host paths and launch settings the
    # caller sent, but never let it claim a project is imported.
    host_paths = document.get("host_paths")
    if isinstance(host_paths, dict):
        sidecar["host_paths"] = host_paths
    launch = document.get("launch")
    if isinstance(launch, dict):
        sidecar["launch"] = launch
    sidecar.setdefault("schema_version", canonical.SIDECAR_SCHEMA_VERSION)
    sidecar["format"] = FORMAT_MACHINE_PROFILE
    sidecar["authored"] = True
    meta = _meta_of(sidecar)
    meta.setdefault("name", name)
    meta["profile_dir_name"] = document.get("profile", {}).get("machine_id") or meta.get("profile_dir_name")
    sidecar["meta"] = meta
    # Keep warnings the migrator recorded (a dropped provider, an automation
    # variant that could not be carried) — they describe data the user LOST and
    # must not be erased by the first save. Only the advisory set this function
    # owns is recomputed.
    advisory = set(canonical_validator.project_warnings({**document, "profile": {}, "variants": {}}))
    previous = sidecar.get("warnings")
    carried = [w for w in previous if isinstance(w, str)] if isinstance(previous, list) else []
    recomputed = canonical_validator.project_warnings(document)
    sidecar["warnings"] = [w for w in carried if w not in advisory and w not in recomputed] + recomputed
    canonical.write_sidecar(pdir, sidecar)


def validate_project_payload(document: object, project_dir: pathlib.Path | None = None) -> list[dict[str, str]]:
    """Structured save-time errors for a canonical project document."""
    if not isinstance(document, dict):
        return [
            {
                "source": "schema",
                "code": "schema.type",
                "path": "$",
                "message": "project payload must be a JSON object",
            }
        ]
    errors = [
        {"source": "canonical", "code": "canonical.validation", "path": "$", "message": message}
        for message in canonical_validator.validate_project(document, project_dir)
    ]
    errors.extend(_validate_canonical_provider_configs(document))
    return errors


def _validate_canonical_provider_configs(document: dict) -> list[dict[str, str]]:
    """Each provider config against the --config-schema envelope for the version
    the machine pins (#270, #283)."""
    errors: list[dict[str, str]] = []
    providers = document.get("providers")
    if not isinstance(providers, dict):
        return errors
    profile = document.get("profile")
    for pid, entry in sorted(providers.items()):
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        base = f"$.providers.{pid}"
        resolution = provider_schemas.resolve_for_profile(profile, kind) if isinstance(kind, str) else None
        if resolution is None:
            known = ", ".join(provider_schemas.available_kinds())
            errors.append(
                {
                    "source": "provider-schema",
                    "code": "provider.unknown_kind",
                    "path": f"{base}.kind",
                    "message": f"Unknown provider kind '{kind}' — no vendored config schema (known kinds: {known}).",
                }
            )
            continue
        config = entry.get("config")
        schema = resolution.envelope["schema"]
        validator = jsonschema.Draft202012Validator(schema)
        for err in sorted(validator.iter_errors(config), key=lambda e: list(e.path)):
            errors.append(
                {
                    "source": "provider-schema",
                    "code": "provider.schema",
                    "path": f"{base}.config" + _json_path_from_iter(list(err.path))[1:],
                    "message": err.message,
                }
            )
        for violation in provider_schemas.unique_violations(schema, config):
            errors.append(
                {
                    "source": "provider-schema",
                    "code": "provider.unique",
                    "path": f"{base}.config" + violation["path"][1:],
                    "message": violation["message"],
                }
            )
    return errors


@_serialized
def create_project_from_template(name: str, template: str) -> dict:
    """Copy a canonical template directory and re-key it onto this project."""
    if project_dir(name).exists():
        raise ValueError(f"Project '{name}' already exists")
    tpl_dir = TEMPLATES_ROOT / template
    if not (tpl_dir / machine_profile.PROFILE_FILENAME).is_file():
        raise FileNotFoundError(f"Template '{template}' not found")
    _assert_deploy_dir_free(canonical.machine_id_from_name(name), name)

    pdir = project_dir(name)
    try:
        pdir.mkdir(parents=True)
    except FileExistsError as exc:
        raise ValueError(f"Project '{name}' already exists") from exc
    try:
        for entry in machine_profile.copy_entries(machine_profile.load_profile(tpl_dir)) + [
            machine_profile.SIDECAR_NAME
        ]:
            src = tpl_dir / entry
            if src.is_file():
                shutil.copy2(src, pdir / entry)
            elif src.is_dir():
                shutil.copytree(src, pdir / entry, copy_function=shutil.copy2)

        machine_id = canonical.machine_id_from_name(name)
        canonical.retarget_project(pdir, machine_id)

        profile = machine_profile.load_profile(pdir)
        profile["display_name"] = name
        canonical._write_yaml_atomic(pdir / machine_profile.PROFILE_FILENAME, profile)

        sidecar = _sidecar_or_default(pdir)
        sidecar["authored"] = True
        sidecar["format"] = FORMAT_MACHINE_PROFILE
        sidecar["meta"] = {
            **_meta_of(sidecar),
            "name": name,
            "created": datetime.now(timezone.utc).isoformat(),
            "template": template,
            "profile_dir_name": machine_id,
        }
        canonical.write_sidecar(pdir, sidecar)
    except Exception:
        shutil.rmtree(pdir, ignore_errors=True)
        raise
    return get_project(name)


@_serialized
def rename_project(old_name: str, new_name: str) -> None:
    """Rename the workspace directory.

    The profile's machine_id is deliberately NOT changed: it keys the deploy
    directory and every config path token, so renaming the workbench-side label
    must not silently re-target a deployed machine.
    """
    if not project_dir(old_name).exists():
        raise FileNotFoundError(f"Project '{old_name}' not found")
    if is_running(old_name):
        raise ValueError(f"Project '{old_name}' is running")
    if project_dir(new_name).exists():
        raise ValueError(f"Project '{new_name}' already exists")
    project_dir(old_name).rename(project_dir(new_name))

    pdir = project_dir(new_name)
    sidecar = _read_sidecar(pdir)
    if sidecar is not None:
        meta = _meta_of(sidecar)
        meta["name"] = new_name
        sidecar["meta"] = meta
        canonical.write_sidecar(pdir, sidecar)


@_serialized
def duplicate_project(source_name: str, new_name: str) -> dict:
    src = project_dir(source_name)
    if not src.exists():
        raise FileNotFoundError(f"Project '{source_name}' not found")
    if project_dir(new_name).exists():
        raise ValueError(f"Project '{new_name}' already exists")
    # An authored copy is re-keyed below, so it must not land on a name already
    # in use. An imported copy keeps its source directory name by design (#226
    # carries it verbatim) — that is recorded as a warning, not refused.
    if canonical.is_authored(_read_sidecar(src)):
        _assert_deploy_dir_free(canonical.machine_id_from_name(new_name), new_name)

    shutil.copytree(
        src,
        project_dir(new_name),
        ignore=shutil.ignore_patterns("running.json", "logs", ".workbench", "*.bak"),
    )
    try:
        pdir = project_dir(new_name)
        sidecar = _sidecar_or_default(pdir)
        sidecar["format"] = FORMAT_MACHINE_PROFILE
        if canonical.is_authored(sidecar):
            # A copy must not deploy into the ORIGINAL's directory: install.sh
            # keys its path rewrites on the project dir name, so re-key every
            # token as well as the profile.
            canonical.retarget_project(pdir, canonical.machine_id_from_name(new_name))
        meta = _meta_of(sidecar)
        meta["name"] = new_name
        meta["created"] = datetime.now(timezone.utc).isoformat()
        if canonical.is_authored(sidecar):
            meta["profile_dir_name"] = canonical.machine_id_from_name(new_name)
        sidecar["meta"] = meta
        if not canonical.is_authored(sidecar):
            conflict = deploy_dir_conflict(deploy_dir_name(pdir) or new_name, new_name)
            if conflict is not None:
                warnings = sidecar.get("warnings")
                sidecar["warnings"] = ([*warnings] if isinstance(warnings, list) else []) + [conflict]
        canonical.write_sidecar(pdir, sidecar)
    except Exception:
        shutil.rmtree(project_dir(new_name), ignore_errors=True)
        raise
    return get_project(new_name)


@_serialized
def delete_project(name: str) -> None:
    if not project_dir(name).exists():
        raise FileNotFoundError(f"Project '{name}' not found")
    if is_running(name):
        raise ValueError(f"Project '{name}' is running")
    shutil.rmtree(project_dir(name))
