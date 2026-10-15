"""system.json schema migrations.

v1 -> v2 (#270): provider config moves to the PROVIDER-NATIVE shape — each
``topology.providers`` entry becomes ``{kind, config}`` where ``config`` is
exactly the document the provider's ``--config-schema`` envelope describes
(and what the renderer emits verbatim). The transform is the old per-kind
renderer logic relocated here: what v1 stored field-by-field and the renderer
reassembled, v2 stores assembled. ``paths.providers[pid].bus_path`` moves into
``config.hardware.bus_path``; I2C addresses keep their authored form (hex
strings like "0x0A" — provider binaries parse both).

Migration is applied on load (persisted by projects.get_project, defensively
elsewhere) so every consumer downstream of a loader only ever sees v2.
"""

from __future__ import annotations

import copy
import json
import pathlib
import shutil
from typing import Any

import yaml

from anolis_workbench.core import canonical, machine_profile

CURRENT_SCHEMA_VERSION = 2


def migrate_system(system: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return (document at the current schema version, whether it changed).

    v1 documents are migrated to v2; anything else is returned untouched
    (schema validation is responsible for rejecting unknown versions loudly).
    """
    if system.get("schema_version") != 1:
        return system, False
    return _migrate_v1_to_v2(system), True


def _migrate_v1_to_v2(system: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(system)
    out["schema_version"] = 2

    paths = out.get("paths", {})
    provider_paths = paths.get("providers", {}) if isinstance(paths, dict) else {}

    topology = out.get("topology", {})
    providers = topology.get("providers", {}) if isinstance(topology, dict) else {}
    for pid, pdata in list(providers.items()):
        if not isinstance(pdata, dict):
            continue
        kind = pdata.get("kind")
        path_data = provider_paths.get(pid, {}) if isinstance(provider_paths, dict) else {}
        if kind == "sim":
            config = _sim_config(pdata)
        elif kind == "bread":
            config = _bus_provider_config(pdata, path_data, synthesize_addresses=True)
        elif kind == "ezo":
            config = _bus_provider_config(pdata, path_data, synthesize_addresses=False)
        else:
            # Unknown/custom kinds carried no renderable v1 fields; keep any
            # pre-existing config object so the migration stays total.
            existing = pdata.get("config")
            config = existing if isinstance(existing, dict) else {}
        providers[pid] = {"kind": kind, "config": config}

    if isinstance(provider_paths, dict):
        for entry in provider_paths.values():
            if isinstance(entry, dict):
                entry.pop("bus_path", None)

    return out


def _sim_config(pdata: dict[str, Any]) -> dict[str, Any]:
    config: dict[str, Any] = {}

    # The old composer seeded provider_name: "" — an empty name violates the
    # envelope pattern, and the provider defaults the name anyway, so omit it.
    if pdata.get("provider_name"):
        config["provider"] = {"name": pdata["provider_name"]}

    if "startup_policy" in pdata:
        config["startup_policy"] = pdata["startup_policy"]

    devices = []
    for dev in pdata.get("devices", []):
        d: dict[str, Any] = {"id": dev.get("id"), "type": dev.get("type")}
        # Preserve per-device extras (initial_temp, max_speed, ...) — the sim
        # schema leaves device items open for type-specific fields.
        for key, value in dev.items():
            if key not in d:
                d[key] = value
        devices.append(d)
    config["devices"] = devices

    simulation_cfg = pdata.get("simulation")
    if not isinstance(simulation_cfg, dict):
        simulation_cfg = {}
    mode = pdata.get("simulation_mode") or simulation_cfg.get("mode") or "non_interacting"
    simulation: dict[str, Any] = {"mode": mode}
    tick_rate_hz = pdata.get("tick_rate_hz", simulation_cfg.get("tick_rate_hz"))
    if mode != "inert" and tick_rate_hz is not None:
        simulation["tick_rate_hz"] = tick_rate_hz
    for key in ("physics_config", "ambient_temp_c", "ambient_signal_path"):
        if key in simulation_cfg:
            simulation[key] = simulation_cfg[key]
    config["simulation"] = simulation

    return config


def _bus_provider_config(
    pdata: dict[str, Any],
    path_data: dict[str, Any],
    *,
    synthesize_addresses: bool,
) -> dict[str, Any]:
    config: dict[str, Any] = {}

    if pdata.get("provider_name"):
        config["provider"] = {"name": pdata["provider_name"]}

    hardware: dict[str, Any] = {"bus_path": path_data.get("bus_path", "")}
    for key in ("query_delay_us", "timeout_ms", "retry_count"):
        if key in pdata:
            hardware[key] = pdata[key]
    config["hardware"] = hardware

    discovery: dict[str, Any] = {"mode": "manual"}
    if synthesize_addresses:
        addresses = [dev["address"] for dev in pdata.get("devices", []) if "address" in dev]
        if addresses:
            discovery["addresses"] = addresses
    config["discovery"] = discovery

    devices = []
    for dev in pdata.get("devices", []):
        d: dict[str, Any] = {"id": dev.get("id"), "type": dev.get("type")}
        if "label" in dev:
            d["label"] = dev["label"]
        if "address" in dev:
            d["address"] = dev["address"]
        # Preserve any other authored device keys (e.g. command_watchdog_ms,
        # which the bread envelope describes). Migration must be lossless;
        # keys a schema rejects surface as loud, path-scoped save errors
        # rather than being silently destroyed here.
        for key, value in dev.items():
            if key not in d:
                d[key] = value
        devices.append(d)
    config["devices"] = devices

    return config


# ---------------------------------------------------------------------------
# system.json -> canonical project artifacts (#255)
#
# This is the one-shot migrator. The legacy runtime-assembly below is
# renderer.py's body relocated verbatim (the issue's explicit instruction:
# "delete renderer.py — its logic becomes the one-shot migrator"), so a
# migrated project's runtime config is byte-comparable to what the workbench
# rendered before, modulo the deploy-token rewrite.
# ---------------------------------------------------------------------------


def _legacy_runtime_doc(system: dict[str, Any]) -> dict[str, Any]:
    """renderer.render()'s runtime half, relocated."""
    topology = system["topology"]
    rt = topology["runtime"]
    provider_paths = system.get("paths", {}).get("providers", {})

    runtime_doc: dict[str, Any] = {}

    runtime_section: dict[str, Any] = {}
    if rt.get("name"):
        runtime_section["name"] = rt["name"]
    if "shutdown_timeout_ms" in rt:
        runtime_section["shutdown_timeout_ms"] = rt["shutdown_timeout_ms"]
    if "startup_timeout_ms" in rt:
        runtime_section["startup_timeout_ms"] = rt["startup_timeout_ms"]
    if runtime_section:
        runtime_doc["runtime"] = runtime_section

    http_section: dict[str, Any] = {
        "enabled": True,
        "bind": rt.get("http_bind", "127.0.0.1"),
        "port": rt["http_port"],
        "cors_allow_credentials": rt.get("cors_allow_credentials", False),
    }
    if "cors_origins" in rt:
        http_section["cors_allowed_origins"] = rt["cors_origins"]
    runtime_doc["http"] = http_section

    provider_list = []
    for p in rt.get("providers", []):
        pid = p["id"]
        entry: dict[str, Any] = {
            "id": pid,
            "command": provider_paths.get(pid, {}).get("executable", ""),
            "args": ["--config", ""],  # rewritten to a canonical token below
            "timeout_ms": p.get("timeout_ms", 5000),
        }
        if "hello_timeout_ms" in p:
            entry["hello_timeout_ms"] = p["hello_timeout_ms"]
        if "ready_timeout_ms" in p:
            entry["ready_timeout_ms"] = p["ready_timeout_ms"]
        rp = p.get("restart_policy")
        if rp is not None:
            rp_out: dict[str, Any] = {"enabled": rp.get("enabled", False)}
            if rp.get("enabled"):
                rp_out["max_attempts"] = rp["max_attempts"]
                rp_out["backoff_ms"] = rp["backoff_ms"]
                rp_out["timeout_ms"] = rp["timeout_ms"]
                if "success_reset_ms" in rp:
                    rp_out["success_reset_ms"] = rp["success_reset_ms"]
            entry["restart_policy"] = rp_out
        provider_list.append(entry)
    runtime_doc["providers"] = provider_list

    if "polling_interval_ms" in rt:
        runtime_doc["polling"] = {"interval_ms": rt["polling_interval_ms"]}

    telemetry_cfg = _legacy_telemetry(rt)
    runtime_doc["telemetry"] = {"enabled": telemetry_cfg["enabled"]}
    if telemetry_cfg["enabled"] and telemetry_cfg["influxdb"]:
        runtime_doc["telemetry"]["influxdb"] = telemetry_cfg["influxdb"]

    runtime_doc["logging"] = {"level": rt.get("log_level", "info")}
    return runtime_doc


def _legacy_telemetry(rt: dict[str, Any]) -> dict[str, Any]:
    telemetry = rt.get("telemetry")
    if not isinstance(telemetry, dict):
        telemetry = {}
    enabled = telemetry.get("enabled")
    if enabled is None:
        enabled = rt.get("telemetry_enabled", False)
    influx_in = telemetry.get("influxdb")
    influx_cfg = influx_in if isinstance(influx_in, dict) else {}
    influx_out: dict[str, Any] = {}
    for key in (
        "url",
        "org",
        "bucket",
        # No "token": the runtime reads INFLUXDB_TOKEN from its environment,
        # and a token in the config would shadow it (#257).
        "batch_size",
        "flush_interval_ms",
        "max_retry_buffer_size",
        "queue_size",
    ):
        value = influx_cfg.get(key)
        if value not in (None, ""):
            influx_out[key] = value
    return {"enabled": bool(enabled), "influxdb": influx_out}


def _harvest_host_paths(system: dict[str, Any]) -> dict[str, Any]:
    """The dev-launch residual: host binary paths, which the canonical configs
    deliberately do not carry (their `command` is a deploy token)."""
    paths = system.get("paths", {}) if isinstance(system.get("paths"), dict) else {}
    provider_paths = paths.get("providers", {}) if isinstance(paths.get("providers"), dict) else {}
    return {
        "runtime_executable": paths.get("runtime_executable") or "",
        "providers": {
            pid: {"executable": entry.get("executable") or ""}
            for pid, entry in provider_paths.items()
            if isinstance(entry, dict)
        },
    }


def migrate_project_dir(project_dir: pathlib.Path, *, project_name: str | None = None) -> tuple[bool, list[str]]:
    """Convert a legacy system.json project into canonical artifacts (#255).

    Returns (migrated, warnings). Idempotent: the presence of system.json is
    the "needs migration" signal, and it is renamed LAST, so an interrupted run
    simply retries. Offline by construction — component pins are AUTHORED data
    and are therefore left absent with a warning rather than invented from
    release lookups (the defect this migration exists to end).
    """
    system_path = project_dir / "system.json"
    if not system_path.is_file():
        return False, []

    raw = system_path.read_text(encoding="utf-8")
    system, _ = migrate_system(json.loads(raw))
    warnings: list[str] = []

    name = project_name or system.get("meta", {}).get("name") or project_dir.name
    machine_id = canonical.machine_id_from_name(str(name))

    topo_providers = system.get("topology", {}).get("providers", {})
    if not isinstance(topo_providers, dict):
        topo_providers = {}

    # Provider kind is recoverable from the legacy entry; without it the config
    # filename cannot encode the kind install.sh needs.
    provider_relpaths: dict[str, str] = {}
    provider_configs: dict[str, dict[str, Any]] = {}
    dropped: set[str] = set()
    for pid, entry in topo_providers.items():
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        if not isinstance(kind, str) or kind == "":
            warnings.append(f"Provider '{pid}' has no kind; it was dropped from the migrated project.")
            dropped.add(pid)
            continue
        config = entry.get("config")
        config = config if isinstance(config, dict) else {}
        if not config:
            # The retired layout let a provider config live ONLY as a
            # hand-written providers/<pid>.yaml, which the old deploy and
            # export paths fell back to. Carry it, or the migration silently
            # replaces a commissioned config with an empty document.
            config = _legacy_provider_yaml(project_dir, pid)
            if config:
                warnings.append(f"Provider '{pid}' config was carried over from the hand-written providers/{pid}.yaml.")
        provider_relpaths[pid] = canonical.provider_config_relpath(kind, pid)
        provider_configs[pid] = config

    runtime_doc = _legacy_runtime_doc(system)

    # A provider the profile no longer declares must not stay in a runtime
    # variant: install.sh would launch it with an unresolvable --config.
    if dropped:
        runtime_doc["providers"] = [
            entry for entry in runtime_doc.get("providers", []) if entry.get("id") not in dropped
        ]

    # Rewrite host paths -> canonical deploy tokens.
    for provider_entry in runtime_doc.get("providers", []):
        pid = provider_entry.get("id")
        rel = provider_relpaths.get(pid)
        kind = topo_providers.get(pid, {}).get("kind") if isinstance(topo_providers.get(pid), dict) else None
        if isinstance(kind, str) and kind:
            provider_entry["command"] = canonical.provider_command_token(kind)
        if isinstance(rel, str):
            provider_entry["args"] = ["--config", canonical.project_path_token(machine_id, rel)]

    rt = system.get("topology", {}).get("runtime", {})
    behaviors: list[str] = []
    variants = {canonical.MANUAL_VARIANT: runtime_doc}
    variant_relpaths = {canonical.MANUAL_VARIANT: canonical.variant_relpath(canonical.MANUAL_VARIANT)}

    behavior_ref = rt.get("behavior_tree_path") or rt.get("behavior_tree")
    if isinstance(behavior_ref, str) and behavior_ref.strip():
        # The legacy model could enable automation in its ONLY config, which
        # install.sh then refuses as a non-inert `manual` variant. Split it:
        # manual stays inert, automation becomes its own variant.
        behavior_name = pathlib.PurePosixPath(behavior_ref.strip()).name
        behavior_rel = f"{canonical.BEHAVIORS_DIR}/{behavior_name}"
        # The legacy path could point anywhere under the project (trees/x.xml);
        # the canonical layout carries behaviours in behaviors/, so relocate it.
        # If the file cannot be found, the automation variant is DROPPED rather
        # than declared: a profile that references a missing file blocks every
        # deploy of the project, including the manual variant.
        if _relocate_behavior(project_dir, behavior_ref.strip(), behavior_rel, warnings):
            behaviors = [behavior_rel]
            automation_doc = copy.deepcopy(runtime_doc)
            automation_doc["automation"] = {
                "enabled": True,
                "behavior_tree": canonical.project_path_token(machine_id, behavior_rel),
            }
            variants[canonical.AUTOMATION_VARIANT] = automation_doc
            variant_relpaths[canonical.AUTOMATION_VARIANT] = canonical.variant_relpath(canonical.AUTOMATION_VARIANT)
            if rt.get("automation_enabled"):
                warnings.append(
                    "Automation was enabled in the legacy config; it is now the separate "
                    "'automation' variant and 'manual' boots inert (install.sh refuses a "
                    "non-inert manual variant)."
                )
    runtime_doc["automation"] = {"enabled": False}

    profile = canonical.build_profile(
        machine_id=machine_id,
        display_name=str(system.get("meta", {}).get("name") or name),
        providers=provider_relpaths,
        variants=variant_relpaths,
        behaviors=behaviors or None,
    )
    warnings.append(
        "Component pins were not carried over (the legacy model resolved them from "
        "GitHub at deploy time). Resolve component pins before deploying this project."
    )

    document = {
        "format": machine_profile.FORMAT_MACHINE_PROFILE,
        "authored": True,
        "profile": profile,
        "variants": variants,
        "providers": {pid: {"kind": None, "config": cfg} for pid, cfg in provider_configs.items()},
    }
    canonical.write_project(project_dir, document)

    created = system.get("meta", {}).get("created") or ""
    sidecar = canonical.default_sidecar(
        name=str(name),
        created=str(created),
        machine_id=machine_id,
        template=system.get("meta", {}).get("template"),
        host_paths=_harvest_host_paths(system),
        warnings=warnings,
    )
    sidecar["migrated_from"] = "system.json"
    canonical.write_sidecar(project_dir, sidecar)

    # Retire the legacy artifacts LAST: system.json's presence is the
    # needs-migration signal, so nothing is lost if we die before this point.
    backup = project_dir / "system.json.pre255.bak"
    if not backup.exists():
        backup.write_text(raw, encoding="utf-8")
    system_path.unlink()
    (project_dir / "anolis-runtime.yaml").unlink(missing_ok=True)
    legacy_providers = project_dir / "providers"
    if legacy_providers.is_dir():
        # Moved aside, never deleted: these files could be hand-written and are
        # not covered by the system.json backup.
        retired = project_dir / "providers.pre255.bak"
        if retired.exists():
            shutil.rmtree(legacy_providers, ignore_errors=True)
        else:
            legacy_providers.rename(retired)

    return True, warnings


def _legacy_provider_yaml(project_dir: pathlib.Path, provider_id: str) -> dict[str, Any]:
    """A hand-written providers/<pid>.yaml from the retired layout, if any."""
    path = project_dir / "providers" / f"{provider_id}.yaml"
    if not path.is_file():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _relocate_behavior(project_dir: pathlib.Path, source_ref: str, target_rel: str, warnings: list[str]) -> bool:
    """Carry a legacy behaviour tree into `behaviors/`. False if it isn't there."""
    target = project_dir / target_rel
    if target.is_file():
        return True
    source = project_dir / source_ref
    if machine_profile.containment_error(source_ref) is not None or not source.is_file():
        warnings.append(
            f"Behavior tree '{source_ref}' was not found in the project, so the automation "
            f"variant was not carried over. Add the file at {target_rel} and re-add the "
            "automation variant to restore it."
        )
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return True
