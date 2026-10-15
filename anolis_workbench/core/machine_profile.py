"""Canonical machine-profile helpers for imported (passthrough) projects (#226).

An imported project carries a canonical machine-profile directory VERBATIM —
the workbench parses these files for reading (display, validation) only and
never re-emits them. This module owns profile loading, schema validation, the
import-time validation matrix, and provider-kind derivation.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any

import jsonschema
import yaml

from anolis_workbench.core import provider_schemas

PROFILE_FILENAME = "machine-profile.yaml"

# Sidecar for imported projects — the only workbench-owned file in an imported
# project dir (deliberately the #255 residual artifact).
SIDECAR_NAME = "workbench.json"

# What an import copies. Everything the profile references MUST resolve inside
# this set, or the imported workspace would be missing files the profile needs.
IMPORT_COPY = ("machine-profile.yaml", "config", "behaviors")

# Workspace project formats. `system` is the legacy system.json shim retired by
# #255; it survives only as a detection branch that triggers migration.
FORMAT_SYSTEM = "system"
FORMAT_MACHINE_PROFILE = "machine-profile"

_PROFILE_SCHEMA_CACHE: dict[str, Any] | None = None
_PROJECTS_PATH_RE = re.compile(r"\.\./anolis-projects/projects/([^/\s\"']+)/")


class ProfileError(RuntimeError):
    """Raised when a machine-profile directory cannot be read at all."""


class ImportSourceError(ValueError):
    """Raised when an import source path is outside the permitted roots."""


def import_roots() -> list[Path]:
    """Directories an import may read from.

    The workbench can be operated over LAN (token-authenticated), so an
    arbitrary caller-supplied path must not be able to reach anywhere on the
    host. Defaults to the operator's home directory plus the workbench data
    dir; override with ANOLIS_IMPORT_ROOTS (os.pathsep-separated).
    """
    raw = os.getenv("ANOLIS_IMPORT_ROOTS")
    if raw:
        roots = [Path(part).expanduser() for part in raw.split(os.pathsep) if part.strip()]
    else:
        from anolis_workbench.core import paths as paths_module

        roots = [Path.home(), paths_module.DATA_ROOT]
    resolved: list[Path] = []
    for root in roots:
        try:
            resolved.append(Path(os.path.realpath(root)))
        except OSError:
            continue
    return resolved


def resolve_import_source(raw_path: str) -> Path:
    """Validate and canonicalize a caller-supplied import source directory.

    Fully resolves the path (following symlinks) and requires the result to be
    a directory inside one of `import_roots()`. Everything downstream operates
    on the returned canonical path, never on the caller's string.
    """
    if not isinstance(raw_path, str) or raw_path.strip() == "":
        raise ImportSourceError("path required (path to a machine-profile project directory)")

    candidate = Path(os.path.realpath(Path(raw_path.strip()).expanduser()))

    roots = import_roots()
    permitted = False
    for root in roots:
        try:
            if os.path.commonpath([str(candidate), str(root)]) == str(root):
                permitted = True
                break
        except ValueError:  # different drives (Windows) — not contained
            continue
    if not permitted:
        allowed = ", ".join(str(r) for r in roots) or "(none configured)"
        raise ImportSourceError(
            f"Import source must be inside an allowed root ({allowed}). "
            "Set ANOLIS_IMPORT_ROOTS to permit other locations."
        )

    if not candidate.is_dir():
        raise ImportSourceError(f"Not a directory: {candidate}")
    return candidate


@dataclass
class ImportReport:
    """Result of validating a candidate machine-profile directory."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    profile: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


def read_sidecar(project_dir: Path) -> dict[str, Any] | None:
    path = project_dir / SIDECAR_NAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):  # ValueError covers JSONDecodeError + UnicodeDecodeError
        return None
    return payload if isinstance(payload, dict) else None


def load_profile(source_dir: Path) -> dict[str, Any]:
    """Parse <dir>/machine-profile.yaml (read-only; never re-emitted)."""
    profile_path = source_dir / PROFILE_FILENAME
    if not profile_path.is_file():
        raise ProfileError(f"No {PROFILE_FILENAME} in {source_dir}")
    try:
        payload = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
        raise ProfileError(f"Failed reading {profile_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProfileError(f"{profile_path}: root must be a mapping")
    return payload


def _load_profile_schema() -> dict[str, Any]:
    global _PROFILE_SCHEMA_CACHE
    if _PROFILE_SCHEMA_CACHE is None:
        schema_file = resources.files("anolis_workbench").joinpath("schemas").joinpath("machine-profile.schema.json")
        payload = json.loads(schema_file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ProfileError("Bundled machine-profile schema root must be an object")
        _PROFILE_SCHEMA_CACHE = payload
    return _PROFILE_SCHEMA_CACHE


def schema_errors(profile: dict[str, Any]) -> list[str]:
    """Validate a parsed profile against the vendored machine-profile schema."""
    validator = jsonschema.Draft7Validator(_load_profile_schema())
    errors = sorted(validator.iter_errors(profile), key=lambda e: list(e.path))
    return [f"{'.'.join(str(p) for p in e.path) or '$'}: {e.message}" for e in errors]


def referenced_files(profile: dict[str, Any]) -> list[str]:
    """Every profile-relative file path the profile references."""
    refs: list[str] = []
    runtime_profiles = profile.get("runtime_profiles")
    if isinstance(runtime_profiles, dict):
        refs.extend(str(v) for v in runtime_profiles.values() if isinstance(v, str))
    providers = profile.get("providers")
    if isinstance(providers, dict):
        for entry in providers.values():
            if isinstance(entry, dict) and isinstance(entry.get("config"), str):
                refs.append(entry["config"])
    behaviors = profile.get("behaviors")
    if isinstance(behaviors, list):
        refs.extend(str(b) for b in behaviors if isinstance(b, str))
    validation = profile.get("validation")
    if isinstance(validation, dict) and isinstance(validation.get("check_http_script"), str):
        refs.append(validation["check_http_script"])
    return refs


def containment_error(ref: str) -> str | None:
    """Why `ref` cannot be carried by an import, or None if it can.

    A reference must be relative and must not escape the project directory.
    An ABSOLUTE ref is the dangerous case: `Path(source) / "/etc/x"` discards
    the left side, so a host-local file would satisfy an existence check at
    import AND at deploy while never being carried anywhere.
    """
    if ref.strip() == "":
        return "empty path"
    if PurePosixPath(ref).is_absolute() or Path(ref).is_absolute():
        return f"'{ref}' is an absolute path — profile references must be relative to the project directory"
    if ".." in PurePosixPath(ref).parts:
        return f"'{ref}' escapes the project directory"
    return None


def copy_entries(profile: dict[str, Any]) -> list[str]:
    """Top-level entries an import must copy for this profile.

    The base set plus any additional top-level directory the profile actually
    references (e.g. a `validation/` script), so nothing a profile depends on
    is silently left behind. Callers must reject uncarriable references
    (see `containment_error`) first.
    """
    entries = list(IMPORT_COPY)
    for ref in referenced_files(profile):
        if containment_error(ref) is not None:
            continue
        parts = PurePosixPath(ref).parts
        top = parts[0] if parts else ""
        if top and top not in entries:
            entries.append(top)
    return entries


_CONFIG_KIND_RE = re.compile(r"^provider-([a-z0-9-]+)\.")


def derive_kinds(profile: dict[str, Any], source_dir: Path) -> dict[str, str | None]:
    """provider instance id -> component kind.

    The profile itself doesn't name kinds; they come from the instance's config
    filename (`provider-<kind>.*` — the same rule install.sh's bundle assembly
    uses), preferring a match against `components.providers` when pins exist so
    that a kind containing a dot or dash resolves the way install.sh will.

    The filename fallback matters: a project migrated from the retired layout
    has no pins yet (they are authored data now), and without it EVERY provider
    would read back as an unknown kind — which blocks the very save that would
    let the user fill the pins in.
    """
    components = profile.get("components")
    kinds = []
    if isinstance(components, dict) and isinstance(components.get("providers"), dict):
        kinds = list(components["providers"].keys())

    result: dict[str, str | None] = {}
    providers = profile.get("providers")
    if not isinstance(providers, dict):
        return result
    for pid, entry in providers.items():
        result[pid] = None
        config_ref = entry.get("config") if isinstance(entry, dict) else None
        if not isinstance(config_ref, str):
            continue
        basename = Path(config_ref).name
        for kind in kinds:
            if basename.startswith(f"provider-{kind}."):
                result[pid] = kind
                break
        else:
            match = _CONFIG_KIND_RE.match(basename)
            if match is not None:
                result[pid] = match.group(1)
    return result


def validate_project_dir(source_dir: Path, profile_dir_name: str) -> ImportReport:
    """The #226 import validation matrix: hard-fails as errors, the rest as
    warnings (install.sh remains the authoritative gate at deploy time)."""
    report = ImportReport()
    source_dir = source_dir.resolve()

    if not source_dir.is_dir():
        report.errors.append(f"Not a directory: {source_dir}")
        return report
    try:
        profile = load_profile(source_dir)
    except ProfileError as exc:
        report.errors.append(str(exc))
        return report
    report.profile = profile

    report.errors.extend(schema_errors(profile))
    if report.errors:
        return report

    for ref in referenced_files(profile):
        problem = containment_error(ref)
        if problem is not None:
            report.errors.append(f"Referenced file {problem}")
            continue
        if not (source_dir / ref).is_file():
            report.errors.append(f"Referenced file missing: {ref}")
    if report.errors:
        return report

    components = profile.get("components")
    if not isinstance(components, dict):
        report.warnings.append(
            "Profile has no components block (local-build/dev profile) — "
            "importable, but deploy requires pinned components."
        )

    runtime_profiles = profile.get("runtime_profiles", {})
    manual_ref = runtime_profiles.get("manual") if isinstance(runtime_profiles, dict) else None
    if not isinstance(manual_ref, str):
        report.warnings.append("No 'manual' runtime profile — install.sh boot-inert deploys need one.")
    else:
        report.warnings.extend(_manual_inertness_warnings(source_dir / manual_ref, manual_ref))
    if isinstance(runtime_profiles, dict):
        report.warnings.extend(_influx_token_warnings(source_dir, runtime_profiles))

    kinds = derive_kinds(profile, source_dir)
    for pid, kind in kinds.items():
        entry = profile.get("providers", {}).get(pid, {})
        config_ref = entry.get("config") if isinstance(entry, dict) else None
        if kind is None:
            report.warnings.append(
                f"Provider '{pid}': kind not derivable from components.providers keys and "
                "its config filename — deploy will rely on install.sh's own resolution."
            )
            continue
        resolution = provider_schemas.resolve_for_profile(profile, kind)
        if resolution is None:
            report.warnings.append(
                f"Provider '{pid}' (kind '{kind}'): no vendored config schema — "
                "config carried verbatim without workbench-side validation."
            )
            continue
        # NB: version skew (#283) is deliberately NOT reported here. Import
        # warnings are persisted to the sidecar and never recomputed, so a skew
        # recorded now would survive the schema sync that resolves it — the
        # #290 failure mode. It is derived at read time in projects.get_project
        # instead, which covers imported projects too.
        if isinstance(config_ref, str):
            report.warnings.extend(_envelope_warnings(source_dir / config_ref, config_ref, pid, resolution.envelope))

    report.warnings.extend(_project_path_warnings(source_dir, profile, profile_dir_name))
    return report


def _influx_token_warnings(source_dir: Path, runtime_profiles: dict) -> list[str]:
    """Deploy refuses a runtime variant that carries telemetry.influxdb.token,
    and an import is carried verbatim, so the fix is in the source."""
    from anolis_workbench.core import canonical  # local: canonical imports this module

    warnings: list[str] = []
    for variant, rel in sorted(runtime_profiles.items()):
        if not isinstance(rel, str):
            continue
        try:
            doc = yaml.safe_load((source_dir / rel).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        problem = canonical.influx_token_error(doc)
        if problem is not None:
            warnings.append(
                f"Runtime variant '{variant}' ({rel}): {problem} Deploy refuses this project until the token is "
                "removed: edit the source and re-import."
            )
    return warnings


def _manual_inertness_warnings(path: Path, ref: str) -> list[str]:
    """The same gate every other path uses, so an import cannot be told its
    manual variant is fine when install.sh will refuse it."""
    from anolis_workbench.core import canonical  # local: canonical imports this module

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"Could not read manual variant {ref}: {exc}"]
    violation = canonical.inertness_violation_text(text)
    if violation is not None:
        return [
            f"Manual variant {ref} is not inert ({violation}) — install.sh's "
            "verify-inert gate will REFUSE this profile at deploy time."
        ]
    return []


def _envelope_warnings(path: Path, ref: str, pid: str, envelope: dict[str, Any]) -> list[str]:
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
        return [f"Could not parse provider config {ref}: {exc}"]
    validator = jsonschema.Draft202012Validator(envelope["schema"])
    return [
        f"Provider '{pid}' config {ref}: {err.message} "
        "(warning only — the provider binary's --check-config is authoritative)"
        for err in sorted(validator.iter_errors(config), key=lambda e: list(e.path))
    ]


def _project_path_warnings(source_dir: Path, profile: dict[str, Any], profile_dir_name: str) -> list[str]:
    """install.sh keys its path rewrites on the project dir basename; configs
    referencing a DIFFERENT ../anolis-projects/projects/<X>/ would end up
    pointing at a nonexistent install path."""
    warnings: list[str] = []
    for ref in referenced_files(profile):
        candidate = source_dir / ref
        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in sorted(set(_PROJECTS_PATH_RE.findall(text))):
            if match != profile_dir_name:
                warnings.append(
                    f"{ref} references ../anolis-projects/projects/{match}/ but the project "
                    f"directory is named '{profile_dir_name}' — install.sh path rewrites key on "
                    "the directory name, so these paths will not resolve after install."
                )
    return warnings
