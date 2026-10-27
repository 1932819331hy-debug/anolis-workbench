"""Shared fixtures for the canonical project layout (#255).

Since the flip there is exactly ONE on-disk project format — machine-profile +
config/ + the workbench sidecar — so most suites need the same small builder.
It goes through `canonical.write_project`/`write_sidecar` rather than writing
YAML by hand, which keeps the fixtures honest: a project these helpers produce
is one the workbench itself could have authored.
"""

from __future__ import annotations

import copy
import pathlib
from typing import Any, Callable

import pytest

from anolis_workbench.core import canonical, deploy


@pytest.fixture(autouse=True)
def _install_log_dir(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """Deploys keep install.sh's output under DATA_ROOT; keep tests' out of $HOME (#649)."""
    logs = tmp_path_factory.mktemp("install-logs")
    monkeypatch.setattr(deploy, "INSTALL_LOG_DIR", logs, raising=False)
    return logs


# Pins the fixtures author. They are AUTHORED data (#255 decision 1) — nothing
# in the deploy path resolves them from the network any more.
FIXTURE_RUNTIME_VERSION = "0.1.27"
FIXTURE_PROVIDER_VERSIONS = {"sim": "0.2.5", "bread": "0.3.8", "ezo": "0.3.4"}

BEHAVIOR_XML = "<root />\n"


def runtime_variant(
    machine_id: str,
    providers: dict[str, str],
    *,
    behavior: str | None = None,
    port: int = 8080,
    bind: str = "127.0.0.1",
) -> dict[str, Any]:
    """A schema-valid runtime config whose paths are all deploy TOKENS."""
    doc: dict[str, Any] = {
        "runtime": {"name": "anolis-main", "shutdown_timeout_ms": 2000, "startup_timeout_ms": 30000},
        "http": {"enabled": True, "bind": bind, "port": port},
        "providers": [
            {
                "id": pid,
                "command": canonical.provider_command_token(kind),
                "args": [
                    "--config",
                    canonical.project_path_token(machine_id, canonical.provider_config_relpath(kind, pid)),
                ],
                "timeout_ms": 5000,
                "restart_policy": {"enabled": False},
            }
            for pid, kind in providers.items()
        ],
        "polling": {"interval_ms": 500},
        "telemetry": {"enabled": False},
        "logging": {"level": "info"},
        "automation": {"enabled": False},
    }
    if behavior is not None:
        doc["automation"] = {
            "enabled": True,
            "behavior_tree": canonical.project_path_token(machine_id, behavior),
        }
    return doc


def provider_config(pid: str, kind: str) -> dict[str, Any]:
    if kind == "sim":
        return {
            "provider": {"name": pid},
            "startup_policy": "degraded",
            "devices": [{"id": "tempctl0", "type": "tempctl", "initial_temp": 25.0}],
            "simulation": {"mode": "non_interacting", "tick_rate_hz": 10.0},
        }
    return {
        "provider": {"name": pid},
        "startup_policy": "degraded",
        "hardware": {"bus_path": "/dev/i2c-1"},
        "devices": [{"id": f"{kind}0", "address": "0x0A"}],
    }


def build_canonical_project(
    dest: pathlib.Path,
    *,
    machine_id: str = "deploy-fixture",
    display_name: str | None = None,
    providers: dict[str, str] | None = None,
    behavior: str | None = None,
    components: dict[str, Any] | None = None,
    pin_components: bool = True,
    host_paths: dict[str, Any] | None = None,
    created: str = "2026-01-01T00:00:00Z",
) -> pathlib.Path:
    """Write an authored canonical project at `dest` and return it.

    `providers` maps provider id -> kind. `behavior` (a project-relative path
    like `behaviors/local.xml`) adds the separate `automation` variant that the
    manual variant is forbidden to carry.
    """
    providers = dict(providers or {"sim0": "sim"})
    variants = {canonical.MANUAL_VARIANT: canonical.variant_relpath(canonical.MANUAL_VARIANT)}
    variant_docs = {canonical.MANUAL_VARIANT: runtime_variant(machine_id, providers)}
    behaviors: list[str] = []
    if behavior is not None:
        behaviors = [behavior]
        variants[canonical.AUTOMATION_VARIANT] = canonical.variant_relpath(canonical.AUTOMATION_VARIANT)
        variant_docs[canonical.AUTOMATION_VARIANT] = runtime_variant(machine_id, providers, behavior=behavior)

    if components is None and pin_components:
        components = {
            "runtime": {"repo": "anolishq/anolis", "version": FIXTURE_RUNTIME_VERSION},
            "providers": {
                kind: {
                    "repo": f"anolishq/anolis-provider-{kind}",
                    "version": FIXTURE_PROVIDER_VERSIONS.get(kind, "0.0.1"),
                }
                for kind in sorted(set(providers.values()))
            },
        }

    profile = canonical.build_profile(
        machine_id=machine_id,
        display_name=display_name or machine_id,
        providers={pid: canonical.provider_config_relpath(kind, pid) for pid, kind in providers.items()},
        variants=variants,
        behaviors=behaviors or None,
        components=components,
    )
    document = {
        "authored": True,
        "profile": profile,
        "variants": variant_docs,
        "providers": {pid: {"kind": kind, "config": provider_config(pid, kind)} for pid, kind in providers.items()},
    }
    canonical.write_project(dest, document)
    for rel in behaviors:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(BEHAVIOR_XML, encoding="utf-8")

    canonical.write_sidecar(
        dest,
        canonical.default_sidecar(
            name=display_name or machine_id,
            created=created,
            machine_id=machine_id,
            host_paths=host_paths
            or {
                "runtime_executable": "build/dev-release/core/anolis-runtime",
                "providers": {
                    pid: {"executable": f"../anolis-provider-{kind}/build/dev-release/anolis-provider-{kind}"}
                    for pid, kind in providers.items()
                },
            },
        ),
    )
    return dest


@pytest.fixture()
def canonical_project() -> Callable[..., pathlib.Path]:
    """Factory fixture around `build_canonical_project`."""
    return build_canonical_project


@pytest.fixture()
def canonical_document() -> Callable[..., dict[str, Any]]:
    """An in-memory canonical document, for save/validate tests."""

    def _make(
        *,
        machine_id: str = "rig",
        providers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        providers = dict(providers or {"sim0": "sim"})
        profile = canonical.build_profile(
            machine_id=machine_id,
            display_name=kwargs.pop("display_name", machine_id),
            providers={pid: canonical.provider_config_relpath(kind, pid) for pid, kind in providers.items()},
        )
        return {
            "authored": True,
            "format": "machine-profile",
            "meta": {"name": machine_id},
            "profile": profile,
            "variants": {canonical.MANUAL_VARIANT: runtime_variant(machine_id, providers)},
            "providers": {
                pid: {"kind": kind, "config": copy.deepcopy(provider_config(pid, kind))}
                for pid, kind in providers.items()
            },
            "host_paths": {"runtime_executable": "", "providers": {}},
        }

    return _make
