"""system.json -> canonical artifacts migration (#255).

The acceptance criterion for #255 is "existing projects migrate via the current
renderer output", so the load-bearing test here is PARITY: a migrated project's
runtime config must equal what the retired `renderer.render` produced, modulo
the deploy-token rewrite that is the whole point of the change. That output is
frozen beside each fixture as `<template>.rendered-runtime.yaml`.
"""

from __future__ import annotations

import copy
import json
import pathlib

import pytest
import yaml

from anolis_workbench.core import canonical, canonical_validator, migrations

# The bundled templates are canonical dirs now (#255); these are the last
# system.json templates, frozen so the migration path stays covered.
LEGACY_TEMPLATES = pathlib.Path(__file__).parent.parent / "fixtures" / "v2-templates"
TEMPLATE_NAMES = ("sim-quickstart", "bioreactor-manual", "mixed-bus-mock")


def _legacy_project(tmp_path: pathlib.Path, template: str, mutate=None) -> tuple[pathlib.Path, dict]:
    system = json.loads((LEGACY_TEMPLATES / f"{template}.json").read_text(encoding="utf-8"))
    if mutate is not None:
        mutate(system)
    pdir = tmp_path / template
    pdir.mkdir(parents=True)
    (pdir / "system.json").write_text(json.dumps(system, indent=2), encoding="utf-8")
    return pdir, system


def _without_deploy_tokens(runtime_doc: dict) -> dict:
    """Everything except the fields the migration deliberately rewrites."""
    stripped = copy.deepcopy(runtime_doc)
    for entry in stripped.get("providers", []):
        entry.pop("command", None)
        entry.pop("args", None)
    return stripped


@pytest.mark.parametrize("template", TEMPLATE_NAMES)
def test_migrated_runtime_config_matches_the_retired_renderer(tmp_path: pathlib.Path, template: str) -> None:
    pdir, _ = _legacy_project(tmp_path, template)
    expected = yaml.safe_load((LEGACY_TEMPLATES / f"{template}.rendered-runtime.yaml").read_text(encoding="utf-8"))

    migrated, _ = migrations.migrate_project_dir(pdir, project_name=template)
    assert migrated
    actual = canonical.read_project(pdir)["variants"][canonical.MANUAL_VARIANT]

    assert _without_deploy_tokens(actual) == _without_deploy_tokens(expected)


@pytest.mark.parametrize("template", TEMPLATE_NAMES)
def test_migrated_provider_configs_are_unchanged(tmp_path: pathlib.Path, template: str) -> None:
    pdir, system = _legacy_project(tmp_path, template)
    # The retired renderer dumped each non-empty v2 config verbatim.
    expected = {pid: entry["config"] for pid, entry in system["topology"]["providers"].items() if entry.get("config")}

    migrations.migrate_project_dir(pdir, project_name=template)
    actual = {pid: entry["config"] for pid, entry in canonical.read_project(pdir)["providers"].items()}
    assert actual == expected


@pytest.mark.parametrize("template", TEMPLATE_NAMES)
def test_migrated_project_validates(tmp_path: pathlib.Path, template: str) -> None:
    pdir, _ = _legacy_project(tmp_path, template)
    migrations.migrate_project_dir(pdir, project_name=template)
    doc = canonical.read_project(pdir)
    assert canonical_validator.validate_project(doc) == []


def test_deploy_tokens_replace_host_paths(tmp_path: pathlib.Path) -> None:
    pdir, system = _legacy_project(tmp_path, "bioreactor-manual")
    host_exe = system["paths"]["providers"]["bread0"]["executable"]

    migrations.migrate_project_dir(pdir, project_name="bioreactor-manual")
    doc = canonical.read_project(pdir)
    entry = next(e for e in doc["variants"][canonical.MANUAL_VARIANT]["providers"] if e["id"] == "bread0")

    assert entry["command"] == canonical.provider_command_token("bread")
    assert entry["command"] != host_exe
    assert canonical.command_kind(entry["command"]) == "bread"
    # The host path is not lost — it becomes the dev-launch residual.
    assert doc["host_paths"]["providers"]["bread0"]["executable"] == host_exe
    assert doc["host_paths"]["runtime_executable"] == system["paths"]["runtime_executable"]


def test_automation_is_split_into_its_own_variant(tmp_path: pathlib.Path) -> None:
    """The legacy model wrote automation into its ONLY config, which deploy then
    filed as `manual` — and install.sh refuses a non-inert manual variant. That
    path was undeployable; migration splits it."""

    def enable_automation(system: dict) -> None:
        system["topology"]["runtime"]["automation_enabled"] = True
        system["topology"]["runtime"]["behavior_tree_path"] = "behaviors/main.xml"

    pdir, _ = _legacy_project(tmp_path, "bioreactor-manual", enable_automation)
    (pdir / "behaviors").mkdir()
    (pdir / "behaviors" / "main.xml").write_text("<root />\n", encoding="utf-8")
    _, warnings = migrations.migrate_project_dir(pdir, project_name="bio")
    doc = canonical.read_project(pdir)

    assert sorted(doc["variants"]) == [canonical.AUTOMATION_VARIANT, canonical.MANUAL_VARIANT]
    assert canonical.inertness_violation(doc["variants"][canonical.MANUAL_VARIANT]) is None
    automation = doc["variants"][canonical.AUTOMATION_VARIANT]["automation"]
    assert automation["enabled"] is True
    assert automation["behavior_tree"] == canonical.project_path_token("bio", "behaviors/main.xml")
    assert doc["profile"]["behaviors"] == ["behaviors/main.xml"]
    assert canonical_validator.validate_project(doc) == []
    assert any("boots inert" in w for w in warnings), warnings


def test_pins_are_not_invented(tmp_path: pathlib.Path) -> None:
    """The legacy path resolved components from live GitHub lookups at deploy
    time. Migration is offline and leaves them absent, with a warning."""
    pdir, _ = _legacy_project(tmp_path, "sim-quickstart")
    _, warnings = migrations.migrate_project_dir(pdir, project_name="sim")
    profile = canonical.read_project(pdir)["profile"]

    assert "components" not in profile
    assert any("Resolve component pins" in w for w in warnings), warnings


def test_legacy_artifacts_are_retired_with_a_backup(tmp_path: pathlib.Path) -> None:
    pdir, _ = _legacy_project(tmp_path, "sim-quickstart")
    original = (pdir / "system.json").read_text(encoding="utf-8")
    (pdir / "anolis-runtime.yaml").write_text("stale: true\n", encoding="utf-8")
    (pdir / "providers").mkdir()
    (pdir / "providers" / "sim0.yaml").write_text("stale: true\n", encoding="utf-8")

    migrations.migrate_project_dir(pdir, project_name="sim")

    assert not (pdir / "system.json").exists()
    assert not (pdir / "anolis-runtime.yaml").exists()
    assert not (pdir / "providers").exists()
    assert (pdir / "system.json.pre255.bak").read_text(encoding="utf-8") == original


def test_migration_is_idempotent(tmp_path: pathlib.Path) -> None:
    pdir, _ = _legacy_project(tmp_path, "sim-quickstart")
    assert migrations.migrate_project_dir(pdir, project_name="sim")[0] is True

    before = {p.name: p.read_bytes() for p in sorted(pdir.rglob("*")) if p.is_file()}
    assert migrations.migrate_project_dir(pdir, project_name="sim") == (False, [])
    after = {p.name: p.read_bytes() for p in sorted(pdir.rglob("*")) if p.is_file()}
    assert before == after


def test_backup_is_never_overwritten(tmp_path: pathlib.Path) -> None:
    pdir, _ = _legacy_project(tmp_path, "sim-quickstart")
    (pdir / "system.json.pre255.bak").write_text("ORIGINAL", encoding="utf-8")
    migrations.migrate_project_dir(pdir, project_name="sim")
    assert (pdir / "system.json.pre255.bak").read_text(encoding="utf-8") == "ORIGINAL"


def test_no_system_json_is_a_no_op(tmp_path: pathlib.Path) -> None:
    pdir = tmp_path / "empty"
    pdir.mkdir()
    assert migrations.migrate_project_dir(pdir) == (False, [])


def test_v1_document_migrates_through_to_canonical(tmp_path: pathlib.Path) -> None:
    """A pre-#270 doc on disk still migrates: v1 -> v2 -> canonical."""
    v1 = json.loads(
        (pathlib.Path(__file__).parent.parent / "fixtures" / "v1-templates" / "bioreactor-manual.v1.json").read_text(
            encoding="utf-8"
        )
    )
    pdir = tmp_path / "legacy-v1"
    pdir.mkdir()
    (pdir / "system.json").write_text(json.dumps(v1), encoding="utf-8")

    migrated, _ = migrations.migrate_project_dir(pdir, project_name="legacy-v1")
    assert migrated
    doc = canonical.read_project(pdir)
    assert canonical_validator.validate_project(doc) == []
    # bus_path survived v1's paths.providers -> v2 config -> canonical config
    assert doc["providers"]["bread0"]["config"]["hardware"]["bus_path"] == "/dev/i2c-1"


def test_machine_id_is_derived_and_used_in_every_path_token(tmp_path: pathlib.Path) -> None:
    pdir, _ = _legacy_project(tmp_path, "sim-quickstart")
    migrations.migrate_project_dir(pdir, project_name="My_Rig 01")
    doc = canonical.read_project(pdir)

    assert doc["profile"]["machine_id"] == "my-rig-01"
    arg = doc["variants"][canonical.MANUAL_VARIANT]["providers"][0]["args"][1]
    assert "/projects/my-rig-01/" in arg
    assert canonical_validator.validate_project(doc) == []


def test_a_behavior_tree_outside_behaviors_is_relocated(tmp_path: pathlib.Path) -> None:
    """The legacy path could point anywhere under the project; the canonical
    layout carries behaviours in behaviors/."""

    def elsewhere(system: dict) -> None:
        system["topology"]["runtime"]["automation_enabled"] = True
        system["topology"]["runtime"]["behavior_tree_path"] = "trees/reactor.xml"

    pdir, _ = _legacy_project(tmp_path, "bioreactor-manual", elsewhere)
    (pdir / "trees").mkdir()
    (pdir / "trees" / "reactor.xml").write_text("<root />\n", encoding="utf-8")

    migrations.migrate_project_dir(pdir, project_name="bio")
    doc = canonical.read_project(pdir)

    assert (pdir / "behaviors" / "reactor.xml").read_text(encoding="utf-8") == "<root />\n"
    assert doc["profile"]["behaviors"] == ["behaviors/reactor.xml"]
    assert canonical_validator.validate_project(doc) == []


def test_a_missing_behavior_tree_drops_the_automation_variant(tmp_path: pathlib.Path) -> None:
    """Declaring a file that isn't there would block EVERY deploy of the
    project, manual included — so the variant is dropped with a warning
    instead, leaving a project that still works."""

    def enable_automation(system: dict) -> None:
        system["topology"]["runtime"]["automation_enabled"] = True
        system["topology"]["runtime"]["behavior_tree_path"] = "behaviors/ghost.xml"

    pdir, _ = _legacy_project(tmp_path, "bioreactor-manual", enable_automation)
    _, warnings = migrations.migrate_project_dir(pdir, project_name="bio")
    doc = canonical.read_project(pdir)

    assert sorted(doc["variants"]) == [canonical.MANUAL_VARIANT]
    assert "behaviors" not in doc["profile"]
    assert any("was not found" in w for w in warnings), warnings
    assert canonical_validator.validate_project(doc) == []


def test_a_hand_written_provider_yaml_is_carried_over(tmp_path: pathlib.Path) -> None:
    """The retired layout let a provider config live ONLY as a hand-written
    providers/<pid>.yaml, which the old deploy and export paths fell back to.
    Dropping it would replace a commissioned config with an empty document."""

    def blank_config(system: dict) -> None:
        system["topology"]["providers"]["bread0"]["config"] = {}

    pdir, _ = _legacy_project(tmp_path, "bioreactor-manual", blank_config)
    (pdir / "providers").mkdir()
    (pdir / "providers" / "bread0.yaml").write_text("hardware:\n  bus_path: /dev/i2c-9\n", encoding="utf-8")

    _, warnings = migrations.migrate_project_dir(pdir, project_name="bio")
    doc = canonical.read_project(pdir)

    assert doc["providers"]["bread0"]["config"]["hardware"]["bus_path"] == "/dev/i2c-9"
    assert any("providers/bread0.yaml" in w for w in warnings), warnings
    # Moved aside, never deleted: these files are not covered by the backup.
    assert (pdir / "providers.pre255.bak" / "bread0.yaml").is_file()


def test_a_provider_without_a_kind_is_dropped_from_the_variants_too(tmp_path: pathlib.Path) -> None:
    """Leaving it in a variant hands the runtime an unresolvable --config, and
    makes the project unsaveable (a variant may not run a provider the profile
    does not declare)."""

    def drop_kind(system: dict) -> None:
        del system["topology"]["providers"]["ezo0"]["kind"]

    pdir, _ = _legacy_project(tmp_path, "bioreactor-manual", drop_kind)
    _, warnings = migrations.migrate_project_dir(pdir, project_name="bio")
    doc = canonical.read_project(pdir)

    assert sorted(doc["profile"]["providers"]) == ["bread0"]
    assert [e["id"] for e in doc["variants"][canonical.MANUAL_VARIANT]["providers"]] == ["bread0"]
    assert any("no kind" in w for w in warnings), warnings
    assert canonical_validator.validate_project(doc) == []
