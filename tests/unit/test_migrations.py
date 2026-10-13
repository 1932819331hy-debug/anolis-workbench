"""Unit tests for core/migrations.py — system.json v1 -> v2 (#270).

The v1 fixtures under tests/fixtures/v1-templates/ are the pre-#270 bundled
templates, frozen verbatim. The strongest correctness anchor is that every
migrated document validates against the providers' own vendored
--config-schema envelopes (the provider is the authority on its config shape).
"""

import copy
import json
import pathlib

import pytest

from anolis_workbench.core import canonical, machine_profile, migrations, projects

V1_FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "v1-templates"
# The bundled templates are canonical dirs since #255, so the v2 documents they
# used to be are frozen here — this suite covers the v1 -> v2 step, and
# test_migrate_to_canonical.py picks up from v2.
V2_FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "v2-templates"

TEMPLATE_NAMES = ("sim-quickstart", "bioreactor-manual", "mixed-bus-mock")


def _load_v1(name: str) -> dict:
    return json.loads((V1_FIXTURES / f"{name}.v1.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _load_v2_template(name: str) -> dict:
    return json.loads((V2_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _validate_through_canonical(tmp_path: pathlib.Path, v2: dict) -> list[dict[str, str]]:
    """Carry a migrated v2 document on to canonical artifacts and run the live
    save-time validation, which checks each provider config against its
    vendored --config-schema envelope."""
    pdir = tmp_path / "migrated"
    pdir.mkdir()
    (pdir / "system.json").write_text(json.dumps(v2), encoding="utf-8")
    migrations.migrate_project_dir(pdir, project_name="migrated")
    return projects.validate_project_payload(canonical.read_project(pdir), pdir)


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_v1_template_migrates_to_checked_in_v2_template(name: str) -> None:
    migrated, changed = migrations.migrate_system(_load_v1(name))
    assert changed
    assert migrated == _load_v2_template(name)


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_migrated_document_passes_full_validation(name: str, tmp_path: pathlib.Path) -> None:
    migrated, _ = migrations.migrate_system(_load_v1(name))
    assert _validate_through_canonical(tmp_path, migrated) == []


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_migration_is_idempotent(name: str) -> None:
    migrated, _ = migrations.migrate_system(_load_v1(name))
    again, changed = migrations.migrate_system(copy.deepcopy(migrated))
    assert not changed
    assert again == migrated


def test_migration_does_not_mutate_input() -> None:
    v1 = _load_v1("bioreactor-manual")
    snapshot = copy.deepcopy(v1)
    migrations.migrate_system(v1)
    assert v1 == snapshot


def test_bus_path_moves_from_paths_into_config() -> None:
    migrated, _ = migrations.migrate_system(_load_v1("bioreactor-manual"))
    for pid in ("bread0", "ezo0"):
        assert "bus_path" not in migrated["paths"]["providers"][pid]
        assert migrated["topology"]["providers"][pid]["config"]["hardware"]["bus_path"] == "/dev/i2c-1"


def test_addresses_keep_their_authored_hex_string_form() -> None:
    migrated, _ = migrations.migrate_system(_load_v1("bioreactor-manual"))
    bread = migrated["topology"]["providers"]["bread0"]["config"]
    assert bread["discovery"]["addresses"] == ["0x0A", "0x14", "0x15"]
    assert [d["address"] for d in bread["devices"]] == ["0x0A", "0x14", "0x15"]


def test_migrated_configs_match_the_old_renderer_values() -> None:
    """Migrated configs must carry the values the old per-kind renderer
    emitted for the same v1 document (frozen here as literals), modulo the address representation (old: ints; now:
    the authored hex strings, which providers parse)."""
    migrated, _ = migrations.migrate_system(_load_v1("bioreactor-manual"))
    providers = migrated["topology"]["providers"]

    bread = providers["bread0"]["config"]
    assert bread["provider"] == {"name": "bread0"}
    assert bread["hardware"] == {
        "bus_path": "/dev/i2c-1",
        "query_delay_us": 15000,
        "timeout_ms": 150,
        "retry_count": 3,
    }
    # Old renderer emitted these as ints; the values must be numerically identical.
    assert [int(str(a), 0) for a in bread["discovery"]["addresses"]] == [10, 20, 21]
    assert bread["discovery"]["mode"] == "manual"
    assert [int(str(d["address"]), 0) for d in bread["devices"]] == [10, 20, 21]
    assert [d["id"] for d in bread["devices"]] == ["rlht0", "dcmt0", "dcmt1"]

    ezo = providers["ezo0"]["config"]
    assert ezo["discovery"] == {"mode": "manual"}
    assert [int(str(d["address"]), 0) for d in ezo["devices"]] == [0x63, 0x61]

    sim_migrated, _ = migrations.migrate_system(_load_v1("sim-quickstart"))
    sim = sim_migrated["topology"]["providers"]["sim0"]["config"]
    assert sim["provider"] == {"name": "sim0"}
    assert sim["startup_policy"] == "degraded"
    assert sim["simulation"] == {"mode": "non_interacting", "tick_rate_hz": 10.0}
    assert sim["devices"][0] == {"id": "tempctl0", "type": "tempctl", "initial_temp": 25.0}
    assert sim["devices"][1] == {"id": "motorctl0", "type": "motorctl", "max_speed": 3000.0}


def test_empty_provider_name_is_omitted_not_migrated_invalid(tmp_path: pathlib.Path) -> None:
    """The old composer seeded provider_name: "" on new bread/ezo providers;
    migrating that to provider.name "" would violate the envelope pattern on a
    doc the user never touched. It must be omitted (provider defaults the name)."""
    v1 = _load_v1("mixed-bus-mock")
    v1["topology"]["providers"]["bread0"]["provider_name"] = ""
    migrated, _ = migrations.migrate_system(v1)
    assert "provider" not in migrated["topology"]["providers"]["bread0"]["config"]
    assert _validate_through_canonical(tmp_path, migrated) == []


def test_bus_device_extras_are_preserved(tmp_path: pathlib.Path) -> None:
    """Migration is lossless: authored device keys beyond id/type/label/address
    (e.g. command_watchdog_ms, which the bread envelope describes) survive."""
    v1 = _load_v1("mixed-bus-mock")
    v1["topology"]["providers"]["bread0"]["devices"][0]["command_watchdog_ms"] = 500
    migrated, _ = migrations.migrate_system(v1)
    device = migrated["topology"]["providers"]["bread0"]["config"]["devices"][0]
    assert device["command_watchdog_ms"] == 500
    assert _validate_through_canonical(tmp_path, migrated) == []


def test_unknown_kind_migrates_to_an_empty_config() -> None:
    v1 = _load_v1("sim-quickstart")
    v1["topology"]["providers"]["custom0"] = {"kind": "custom"}
    migrated, _ = migrations.migrate_system(v1)
    assert migrated["topology"]["providers"]["custom0"] == {"kind": "custom", "config": {}}


def test_get_project_backs_up_the_legacy_document(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Opening a legacy project migrates it all the way to canonical artifacts,
    keeping ONE backup: the untouched document as it was found."""
    systems_root = tmp_path / "systems"
    monkeypatch.setattr(projects, "SYSTEMS_ROOT", systems_root)
    project_dir = systems_root / "legacy"
    project_dir.mkdir(parents=True)
    original = json.dumps(_load_v1("sim-quickstart"))
    (project_dir / "system.json").write_text(original, encoding="utf-8")

    projects.get_project("legacy")

    backup = project_dir / "system.json.pre255.bak"
    assert backup.read_text(encoding="utf-8") == original
    assert not (project_dir / "system.json").exists()
    # A second load must not clobber the original backup.
    projects.get_project("legacy")
    assert backup.read_text(encoding="utf-8") == original


def test_duplicate_project_leaves_no_half_copy_when_it_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Duplicating re-keys the copy's machine_id and every path token; if that
    fails the partial copy must be removed, not left behind as a project that
    would deploy into the ORIGINAL's directory."""
    systems_root = tmp_path / "systems"
    monkeypatch.setattr(projects, "SYSTEMS_ROOT", systems_root)
    source_dir = systems_root / "legacy"
    source_dir.mkdir(parents=True)
    (source_dir / "system.json").write_text(json.dumps(_load_v1("sim-quickstart")), encoding="utf-8")
    projects.get_project("legacy")  # migrate to canonical

    (source_dir / "machine-profile.yaml").write_text("{ not: valid: yaml", encoding="utf-8")

    with pytest.raises((machine_profile.ProfileError, ValueError)):
        projects.duplicate_project("legacy", "copy1")

    assert not (systems_root / "copy1").exists()
    assert (source_dir / "machine-profile.yaml").exists()  # source untouched


def test_sim_inert_mode_drops_tick_rate() -> None:
    v1 = _load_v1("sim-quickstart")
    v1["topology"]["providers"]["sim0"]["simulation_mode"] = "inert"
    migrated, _ = migrations.migrate_system(v1)
    assert migrated["topology"]["providers"]["sim0"]["config"]["simulation"] == {"mode": "inert"}


def test_get_project_migrates_and_persists(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    systems_root = tmp_path / "systems"
    monkeypatch.setattr(projects, "SYSTEMS_ROOT", systems_root)
    project_dir = systems_root / "legacy"
    project_dir.mkdir(parents=True)
    (project_dir / "system.json").write_text(json.dumps(_load_v1("mixed-bus-mock")), encoding="utf-8")

    document = projects.get_project("legacy")
    assert document["format"] == projects.FORMAT_MACHINE_PROFILE
    # The v1 -> v2 provider-native shape survived the second hop to canonical.
    assert document["providers"]["bread0"]["config"]["hardware"]["bus_path"] == "mock://mixed-bus"

    # Persisted, not just served.
    assert (project_dir / "machine-profile.yaml").is_file()
    assert canonical.read_project(project_dir)["providers"] == document["providers"]


def test_unknown_schema_version_left_untouched() -> None:
    doc = {"schema_version": 3, "meta": {}}
    out, changed = migrations.migrate_system(doc)
    assert not changed
    assert out is doc
