"""Deterministic commissioning handoff package exporter.

Core export logic with no HTTP/UI dependency. Thin wrappers in server.py and
tools/package.py should call build_package().
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import pathlib
import re
import tempfile
import zipfile
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

import yaml

from anolis_workbench.core import canonical, machine_profile
from anolis_workbench.core import paths as paths_module


class ExportError(RuntimeError):
    """Raised when export cannot produce a valid package."""


_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
_MACHINE_ID_RE = re.compile(r"[^a-z0-9-]+")

logger = logging.getLogger(__name__)


def build_package(project_dir: pathlib.Path, out_path: pathlib.Path) -> None:
    """Build a deterministic .anpkg zip archive from a canonical project.

    Sources every artifact from disk (#255) rather than re-rendering: the
    profile IS the project's, so the pins it carries are the ones exported —
    no release lookups, no synthesis. The package keeps its v1 layout, which
    differs from the project layout and is a shipped contract.
    """
    out_path = out_path.resolve()  # canonicalize before any path operations
    project_root = project_dir.resolve()
    if not (project_root / machine_profile.PROFILE_FILENAME).is_file():
        raise ExportError(f"Project file not found: {project_root / machine_profile.PROFILE_FILENAME}")

    project_name = project_root.name
    try:
        document = canonical.read_project(project_root)
    except (machine_profile.ProfileError, canonical.CanonicalError) as exc:
        raise ExportError(str(exc)) from exc

    profile = copy.deepcopy(document["profile"])
    runtime_payload = copy.deepcopy(launcher_variant(document))
    if not isinstance(runtime_payload, dict):
        raise ExportError("Project has no runtime variant to export")

    provider_ids = _rewrite_provider_args(runtime_payload)
    provider_files: dict[str, bytes] = {}
    for pid in provider_ids:
        entry = document["providers"].get(pid)
        config = entry.get("config") if isinstance(entry, dict) else None
        if not isinstance(config, dict):
            raise ExportError(f"Provider config not found for {pid}")
        provider_files[f"providers/{pid}.yaml"] = yaml.safe_dump(config, sort_keys=False).encode("utf-8")

    behavior_rel_paths = _rewrite_and_collect_behaviors(
        runtime_payload=runtime_payload,
        project_dir=project_root,
    )

    redaction_applied = _redact_runtime_secrets(runtime_payload)
    runtime_text = yaml.safe_dump(runtime_payload, sort_keys=False)

    # The package's own layout: providers live at providers/<pid>.yaml and
    # behaviors under runtime/behaviors/, so re-point the profile at them.
    profile["providers"] = {pid: {"config": f"providers/{pid}.yaml"} for pid in provider_ids}
    profile["runtime_profiles"] = {"manual": "runtime/anolis-runtime.yaml"}
    if behavior_rel_paths:
        profile["behaviors"] = sorted(behavior_rel_paths.keys())
    else:
        profile.pop("behaviors", None)
    _validate_machine_profile(profile)
    machine_profile_text = yaml.safe_dump(profile, sort_keys=False)

    exported_at = _deterministic_exported_at({"meta": document.get("meta", {})})
    # Package format v1 has a single runtime config, so a multi-variant project
    # exports its inert `manual` variant only. Say so rather than letting the
    # recipient assume the package is the whole machine.
    omitted = sorted(v for v in (document.get("variants") or {}) if v != canonical.MANUAL_VARIANT)
    if omitted:
        logger.warning(
            "exporting only the '%s' variant; %s not carried (package format v1 holds one runtime config)",
            canonical.MANUAL_VARIANT,
            ", ".join(omitted),
        )
    provenance = {
        "exported_at": exported_at,
        "schema_versions": {
            "machine-profile": 1,
            "runtime-config": 1,
        },
        "package_format_version": 1,
        "source_project": project_name,
        "exported_variant": canonical.MANUAL_VARIANT,
        "omitted_variants": omitted,
        "redaction_policy": {
            "telemetry_influxdb_token_removed": redaction_applied,
            "deploy_time_env_var": "INFLUXDB_TOKEN",
        },
    }
    provenance_text = json.dumps(provenance, indent=2, sort_keys=True) + "\n"

    files: dict[str, bytes] = {
        "machine-profile.yaml": machine_profile_text.encode("utf-8"),
        "runtime/anolis-runtime.yaml": runtime_text.encode("utf-8"),
        "meta/provenance.json": provenance_text.encode("utf-8"),
    }
    files.update(provider_files)
    for rel_path, source_path in behavior_rel_paths.items():
        files[rel_path] = source_path.read_bytes()

    _assert_no_secret_leak(files)
    files["meta/checksums.sha256"] = _checksums_file_bytes(files)
    _write_zip_deterministic(files=files, out_path=out_path)


def launcher_variant(document: dict[str, Any]) -> dict[str, Any] | None:
    """The variant a package exports: the inert `manual` one."""
    variants = document.get("variants") or {}
    doc = variants.get(canonical.MANUAL_VARIANT)
    return doc if isinstance(doc, dict) else None


def _rewrite_provider_args(runtime_payload: dict[str, Any]) -> list[str]:
    providers = runtime_payload.get("providers")
    if not isinstance(providers, list) or not providers:
        raise ExportError("runtime.providers must be a non-empty list")

    provider_ids: list[str] = []
    seen: set[str] = set()
    for entry in providers:
        if not isinstance(entry, dict):
            raise ExportError("runtime.providers entries must be objects")
        provider_id = entry.get("id")
        if not isinstance(provider_id, str) or provider_id.strip() == "":
            raise ExportError("runtime.providers[].id must be a non-empty string")
        provider_id = provider_id.strip()
        if provider_id in seen:
            raise ExportError(f"Duplicate provider id in runtime.providers: {provider_id}")
        seen.add(provider_id)
        provider_ids.append(provider_id)

        expected_cfg = f"providers/{provider_id}.yaml"
        raw_args = entry.get("args")
        args = [str(item) for item in raw_args] if isinstance(raw_args, list) else []
        replaced = False
        for idx, token in enumerate(args[:-1]):
            if token == "--config":
                args[idx + 1] = expected_cfg
                replaced = True
                break
        if not replaced:
            args.extend(["--config", expected_cfg])
        entry["args"] = args

    return provider_ids


def _rewrite_and_collect_behaviors(
    *,
    runtime_payload: dict[str, Any],
    project_dir: pathlib.Path,
) -> dict[str, pathlib.Path]:
    behavior_files: dict[str, pathlib.Path] = {}
    automation = runtime_payload.get("automation")
    if not isinstance(automation, dict):
        return behavior_files

    behavior_ref = automation.get("behavior_tree") or automation.get("behavior_tree_path")
    if not isinstance(behavior_ref, str) or behavior_ref.strip() == "":
        return behavior_files

    source = _resolve_behavior_path(behavior_ref.strip(), project_dir)
    rel = f"runtime/behaviors/{source.name}"
    automation["behavior_tree"] = rel
    automation.pop("behavior_tree_path", None)
    behavior_files[rel] = source
    return behavior_files


def _resolve_behavior_path(raw: str, project_dir: pathlib.Path) -> pathlib.Path:
    raw_path = pathlib.Path(raw)
    if raw_path.is_absolute():
        raise ExportError("automation.behavior_tree must be a relative path")

    data_root = paths_module.DATA_ROOT.resolve()
    candidates = [
        (project_dir / raw_path).resolve(),
        (data_root / raw_path).resolve(),
    ]
    for candidate in candidates:
        if candidate.is_file() and (_is_within(candidate, project_dir) or _is_within(candidate, data_root)):
            return candidate
    raise ExportError(f"Behavior tree file not found for automation.behavior_tree='{raw}'")


def _is_within(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _redact_runtime_secrets(runtime_payload: dict[str, Any]) -> bool:
    redacted = False
    telemetry = runtime_payload.get("telemetry")
    if not isinstance(telemetry, dict):
        return redacted

    if "influx_token" in telemetry:
        telemetry.pop("influx_token", None)
        redacted = True

    influxdb = telemetry.get("influxdb")
    if isinstance(influxdb, dict) and "token" in influxdb:
        influxdb.pop("token", None)
        redacted = True

    return redacted


def _validate_machine_profile(profile: dict[str, Any]) -> None:
    try:
        errors = machine_profile.schema_errors(profile)
    except (machine_profile.ProfileError, FileNotFoundError) as exc:
        raise ExportError(f"Bundled machine-profile schema unavailable: {exc}") from exc
    if errors:
        raise ExportError(f"Generated machine-profile failed schema validation: {'; '.join(errors)}")


def _deterministic_exported_at(system: dict[str, Any]) -> str:
    meta = system.get("meta")
    if isinstance(meta, dict):
        created = meta.get("created")
        if isinstance(created, str):
            parsed = _normalize_iso_timestamp(created)
            if parsed is not None:
                return parsed
    return "1970-01-01T00:00:00Z"


def _normalize_iso_timestamp(raw: str) -> str | None:
    text = raw.strip()
    if text == "":
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _assert_no_secret_leak(files: dict[str, bytes]) -> None:
    for rel_path, content in sorted(files.items()):
        if rel_path.endswith(".xml"):
            continue

        text = content.decode("utf-8")
        if rel_path.endswith(".yaml") or rel_path.endswith(".yml"):
            payload = yaml.safe_load(text)
        elif rel_path.endswith(".json"):
            payload = json.loads(text)
        else:
            continue

        for key_path, value in _iter_key_values(payload):
            if "token" in key_path.split(".")[-1].lower():
                if isinstance(value, str) and value.strip() != "":
                    raise ExportError(f"Secret-like token value leaked in package file '{rel_path}' at {key_path}")


def _iter_key_values(payload: Any, prefix: str = "$"):
    if isinstance(payload, dict):
        for key, value in payload.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            yield from _iter_key_values(value, child)
        return
    if isinstance(payload, list):
        for idx, value in enumerate(payload):
            child = f"{prefix}[{idx}]"
            yield from _iter_key_values(value, child)
        return
    yield prefix, payload


def _checksums_file_bytes(files: dict[str, bytes]) -> bytes:
    lines: list[str] = []
    for rel_path in sorted(path for path in files if path != "meta/checksums.sha256"):
        digest = hashlib.sha256(files[rel_path]).hexdigest()
        lines.append(f"{digest}  {rel_path}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _write_zip_deterministic(*, files: dict[str, bytes], out_path: pathlib.Path) -> None:
    out_path = out_path.resolve()  # ensure canonical path at function entry
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(out_path.parent), prefix=f".{out_path.name}.", suffix=".tmp")
    tmp_path = pathlib.Path(tmp)
    handle = None
    archive = None
    try:
        try:
            handle = os.fdopen(fd, "wb")
        except BaseException:
            # fdopen has not transferred ownership to a file object.
            with suppress(OSError):
                os.close(fd)
            raise
        archive = zipfile.ZipFile(handle, mode="w")
        for rel_path in sorted(files):
            info = zipfile.ZipInfo(filename=rel_path, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (0o100644 & 0xFFFF) << 16
            archive.writestr(info, files[rel_path])

        archive.close()
        handle.close()
        tmp_path.replace(out_path)
    except BaseException:
        # Closing the archive can write again; keep the original failure.
        for resource in (archive, handle):
            if resource is not None:
                with suppress(BaseException):
                    resource.close()
        with suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise
