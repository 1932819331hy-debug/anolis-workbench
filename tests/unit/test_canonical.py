"""Unit tests for the canonical authoring core (#255, PR 1).

These modules are additive and not yet wired into CRUD; what they must
guarantee up front is that anything the workbench AUTHORS satisfies the
contracts `install.sh` enforces at deploy time. The regexes and rules asserted
here are copied verbatim from `anolis/tools/install.sh` — if they drift, these
tests are the thing that notices.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest
import yaml

from anolis_workbench.core import canonical, canonical_validator, machine_profile

# Copied verbatim from anolis/tools/install.sh's render pass. Do NOT relax these
# to match the code — they are the external contract.
INSTALL_SH_PCMD = re.compile(r"\.\./anolis-provider-([a-z0-9-]+)/build/[^/]+/anolis-provider-([a-z0-9-]+)")
INSTALL_SH_PPATH = re.compile(r"\.\./anolis-projects/projects/([a-z0-9-]+)/(config|behaviors)/([^\s\"']+)")
# machine-profile.schema.json's machine_id pattern.
MACHINE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _runtime_doc(machine_id: str = "rig-a", *, automation: dict | None = None) -> dict:
    doc = {
        "runtime": {"name": machine_id},
        "http": {"enabled": True, "bind": "127.0.0.1", "port": 8080},
        "providers": [
            {
                "id": "bread0",
                "command": canonical.provider_command_token("bread"),
                "args": [
                    "--config",
                    canonical.project_path_token(machine_id, canonical.provider_config_relpath("bread", "bread0")),
                ],
                "timeout_ms": 5000,
            }
        ],
        "polling": {"interval_ms": 500},
        "logging": {"level": "info"},
    }
    if automation is not None:
        doc["automation"] = automation
    return doc


def _document(machine_id: str = "rig-a", *, variants: dict | None = None, components: dict | None = None) -> dict:
    return {
        "format": "machine-profile",
        "authored": True,
        "meta": {"name": "rig-a"},
        "profile": canonical.build_profile(
            machine_id=machine_id,
            display_name="Rig A",
            providers={"bread0": canonical.provider_config_relpath("bread", "bread0")},
            components=components
            or {
                "runtime": {"repo": "anolishq/anolis", "version": "0.1.39"},
                "providers": {"bread": {"repo": "anolishq/anolis-provider-bread", "version": "0.3.8"}},
            },
        ),
        "variants": variants if variants is not None else {canonical.MANUAL_VARIANT: _runtime_doc(machine_id)},
        "providers": {
            "bread0": {
                "kind": "bread",
                "config": {
                    "hardware": {"bus_path": "/dev/i2c-1"},
                    "discovery": {"mode": "manual", "addresses": ["0x0A"]},
                    "devices": [{"id": "rlht0", "type": "rlht", "address": "0x0A"}],
                },
            }
        },
        "host_paths": {"runtime_executable": "", "providers": {}},
    }


# ---------------------------------------------------------------------------
# Identity + deploy tokens (the install.sh contract)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("bioreactor-v1", "bioreactor-v1"),
        ("My_Rig 01", "my-rig-01"),
        ("UPPER", "upper"),
        ("__weird__", "weird"),
        ("!!!", "machine"),
    ],
)
def test_machine_id_satisfies_both_contracts(name: str, expected: str) -> None:
    machine_id = canonical.machine_id_from_name(name)
    assert machine_id == expected
    # Must satisfy the profile schema AND install.sh's path token class.
    assert MACHINE_ID_RE.fullmatch(machine_id), machine_id
    assert re.fullmatch(r"[a-z0-9-]+", machine_id), machine_id


def test_provider_command_token_matches_install_sh_pcmd() -> None:
    token = canonical.provider_command_token("bread")
    match = INSTALL_SH_PCMD.fullmatch(token)
    assert match is not None, token
    # install.sh rewrites using group(2) — the trailing kind must be right.
    assert match.group(2) == "bread"


def test_project_path_token_matches_install_sh_ppath() -> None:
    token = canonical.project_path_token("rig-a", canonical.provider_config_relpath("bread", "bread0"))
    match = INSTALL_SH_PPATH.fullmatch(token)
    assert match is not None, token
    assert match.group(1) == "rig-a"
    assert match.group(2) == "config"


def test_provider_config_filename_resolves_kind_for_install_sh() -> None:
    """install.sh takes the stem up to the FIRST dot as the provider kind."""
    name = canonical.provider_config_filename("bread", "bread0")
    assert name == "provider-bread.bread0.yaml"
    stem = pathlib.PurePosixPath(name).stem  # provider-bread.bread0
    assert stem.removeprefix("provider-").split(".")[0] == "bread"


def test_assert_deploy_tokens_accepts_authored_and_rejects_host_paths() -> None:
    doc = _runtime_doc("rig-a")
    assert canonical.assert_deploy_tokens("rig-a", doc) == []

    # A host path (what the OLD composer wrote) must be rejected — on Windows it
    # would not match pcmd at all, so install.sh would ship it unrewritten.
    doc["providers"][0]["command"] = "/opt/anolis/bin/anolis-provider-bread"
    problems = canonical.assert_deploy_tokens("rig-a", doc)
    assert any("deploy token" in p for p in problems), problems


def test_assert_deploy_tokens_catches_machine_id_mismatch() -> None:
    doc = _runtime_doc("other-rig")
    problems = canonical.assert_deploy_tokens("rig-a", doc)
    assert any("other-rig" in p and "rig-a" in p for p in problems), problems


# ---------------------------------------------------------------------------
# Inertness + runtime-config validation
# ---------------------------------------------------------------------------


def test_inertness_mirrors_install_sh() -> None:
    assert canonical.inertness_violation(_runtime_doc()) is None
    assert canonical.inertness_violation(_runtime_doc(automation={"enabled": False})) is None
    assert "enabled" in (canonical.inertness_violation(_runtime_doc(automation={"enabled": True})) or "")
    # Hooks present is a violation even when disabled — install.sh checks both.
    violation = canonical.inertness_violation(_runtime_doc(automation={"enabled": False, "mode_transition_hooks": []}))
    assert "mode_transition_hooks" in (violation or "")


def test_runtime_config_errors_flags_a_broken_doc() -> None:
    assert canonical.runtime_config_errors(_runtime_doc()) == []
    assert canonical.runtime_config_errors({}) != []  # providers is required


def test_vendored_schema_validates_the_safety_block() -> None:
    """The vendored schema must carry `safety`, or the authoring surface is blind to it.

    It was pinned at v0.1.30 for two months while canonical moved eleven releases
    on, so a malformed safe-state declaration produced no error at all. CI stayed
    green throughout: the upstream-schema check verifies the vendored copy against
    its own lock, and lock and copy agreed the whole time. Nothing was watching
    whether the pin was current.

    A machine whose software stop drives nothing is the recurring defect in this
    stack, and authoring is the cheapest place to catch it.
    """
    doc = _runtime_doc()
    doc["safety"] = {"safe_state": {"hooks": "not-an-array", "zero_is_safe": "yes-please"}}

    errors = canonical.runtime_config_errors(doc)
    paths = {e.split(":", 1)[0] for e in errors}

    assert "safety.safe_state.hooks" in paths, f"safety block not validated; got {errors}"
    assert "safety.safe_state.zero_is_safe" in paths, f"safety block not validated; got {errors}"

    # And a well-formed declaration passes, so the check discriminates rather
    # than rejecting the whole section.
    doc["safety"] = {
        "safe_state": {
            "hooks": [{"device_handle": "bread0/dcmt0", "function_name": "set_open_loop"}],
            "zero_is_safe": False,
        }
    }
    assert canonical.runtime_config_errors(doc) == []


# ---------------------------------------------------------------------------
# Profile building
# ---------------------------------------------------------------------------


def test_build_profile_never_invents_pins() -> None:
    profile = canonical.build_profile(
        machine_id="rig-a",
        display_name="Rig A",
        providers={"bread0": canonical.provider_config_relpath("bread", "bread0")},
    )
    assert "components" not in profile  # pins are AUTHORED data
    assert profile["runtime_profiles"] == {canonical.MANUAL_VARIANT: "config/anolis-runtime.manual.yaml"}
    assert profile["providers"] == {"bread0": {"config": "config/provider-bread.bread0.yaml"}}
    assert machine_profile.schema_errors(profile) == []


def test_build_profile_with_variants_and_safety_is_schema_valid() -> None:
    profile = canonical.build_profile(
        machine_id="rig-a",
        display_name="Rig A",
        providers={"bread0": canonical.provider_config_relpath("bread", "bread0")},
        variants={
            canonical.MANUAL_VARIANT: canonical.variant_relpath(canonical.MANUAL_VARIANT),
            canonical.AUTOMATION_VARIANT: canonical.variant_relpath(canonical.AUTOMATION_VARIANT),
        },
        behaviors=["behaviors/main.xml"],
        safety={"estop_topology": "power_cut"},
        components={
            "runtime": {"repo": "anolishq/anolis", "version": "0.1.39"},
            "providers": {"bread": {"repo": "anolishq/anolis-provider-bread", "version": "0.3.8"}},
        },
    )
    assert machine_profile.schema_errors(profile) == []
    assert profile["safety"] == {"estop_topology": "power_cut"}
    assert canonical.pinned_kinds(profile) == {"bread"}


# ---------------------------------------------------------------------------
# Round-trip: read/write a canonical project
# ---------------------------------------------------------------------------


def test_write_then_read_round_trips(tmp_path: pathlib.Path) -> None:
    pdir = tmp_path / "rig-a"
    doc = _document()
    canonical.write_project(pdir, doc)
    canonical.write_sidecar(
        pdir,
        canonical.default_sidecar(name="rig-a", created="2026-01-01T00:00:00Z", machine_id="rig-a"),
    )

    assert (pdir / "machine-profile.yaml").is_file()
    assert (pdir / "config" / "anolis-runtime.manual.yaml").is_file()
    assert (pdir / "config" / "provider-bread.bread0.yaml").is_file()

    read = canonical.read_project(pdir)
    assert read["authored"] is True
    assert read["profile"] == doc["profile"]
    assert read["variants"] == doc["variants"]
    assert read["providers"]["bread0"]["config"] == doc["providers"]["bread0"]["config"]
    assert read["providers"]["bread0"]["kind"] == "bread"  # derived from components x filename


def _authored(pdir: pathlib.Path) -> dict:
    """read_project + the authored flag, i.e. what a real save round-trip does."""
    doc = canonical.read_project(pdir)
    doc["authored"] = True
    return doc


def test_save_is_idempotent(tmp_path: pathlib.Path) -> None:
    pdir = tmp_path / "rig-a"
    canonical.write_project(pdir, _document())
    first = {p.name: p.read_bytes() for p in sorted((pdir / "config").iterdir())}
    first["machine-profile.yaml"] = (pdir / "machine-profile.yaml").read_bytes()

    canonical.write_project(pdir, _authored(pdir))
    second = {p.name: p.read_bytes() for p in sorted((pdir / "config").iterdir())}
    second["machine-profile.yaml"] = (pdir / "machine-profile.yaml").read_bytes()
    assert first == second


def test_unknown_keys_survive_a_round_trip(tmp_path: pathlib.Path) -> None:
    """The whole point of retiring system.json: the workbench holds the PARSED
    canonical documents, so keys it has no model for cannot be dropped."""
    pdir = tmp_path / "rig-a"
    doc = _document()
    doc["variants"][canonical.MANUAL_VARIANT]["runtime"]["data_dir"] = "/var/lib/anolis"
    doc["variants"][canonical.MANUAL_VARIANT]["http"]["thread_pool_size"] = 8
    doc["variants"][canonical.MANUAL_VARIANT]["automation"] = {
        "enabled": False,
        "manual_gating_policy": "require_ack",
        "parameters": {"setpoint_c": {"min": 20, "max": 40}},
    }
    canonical.write_project(pdir, doc)

    manual = canonical.read_project(pdir)["variants"][canonical.MANUAL_VARIANT]
    assert manual["runtime"]["data_dir"] == "/var/lib/anolis"
    assert manual["http"]["thread_pool_size"] == 8
    assert manual["automation"]["manual_gating_policy"] == "require_ack"
    assert manual["automation"]["parameters"]["setpoint_c"] == {"min": 20, "max": 40}


def test_bind_is_emitted_unquoted_for_the_install_sh_lan_rewrite(tmp_path: pathlib.Path) -> None:
    """install.sh's LAN-exposure rewrite matches `^\\s*bind:\\s*127\\.0\\.0\\.1\\s*$`.
    If a serializer change ever quotes it, the bind+auth_enabled rewrite
    silently stops firing and the runtime refuses to start on a LAN bind."""
    pdir = tmp_path / "rig-a"
    canonical.write_project(pdir, _document())
    text = (pdir / "config" / "anolis-runtime.manual.yaml").read_text(encoding="utf-8")
    assert re.search(r"^\s*bind:\s*127\.0\.0\.1\s*$", text, re.MULTILINE), text


def test_hex_addresses_round_trip_as_quoted_strings(tmp_path: pathlib.Path) -> None:
    pdir = tmp_path / "rig-a"
    canonical.write_project(pdir, _document())
    config = yaml.safe_load((pdir / "config" / "provider-bread.bread0.yaml").read_text(encoding="utf-8"))
    assert config["devices"][0]["address"] == "0x0A"
    assert config["discovery"]["addresses"] == ["0x0A"]


def test_write_prunes_configs_the_profile_no_longer_references(tmp_path: pathlib.Path) -> None:
    """A removed provider's config must go: install.sh globs
    `config/provider-*.yaml`, so a leftover would be installed for a provider
    the runtime never launches."""
    pdir = tmp_path / "rig-a"
    document = _document()
    canonical.write_project(pdir, document)
    dropped = pdir / "config" / "provider-bread.bread0.yaml"
    assert dropped.is_file()

    document["profile"]["providers"] = {"ezo0": {"config": "config/provider-ezo.ezo0.yaml"}}
    document["providers"] = {"ezo0": {"kind": "ezo", "config": {"provider": {"name": "ezo0"}}}}
    canonical.write_project(pdir, document)

    assert not dropped.exists()
    assert (pdir / "config" / "provider-ezo.ezo0.yaml").is_file()


def test_write_retires_a_stray_config_install_sh_would_consume(tmp_path: pathlib.Path) -> None:
    """install.sh's stage renderer GLOBS config/provider-*.yaml and
    config/anolis-runtime.*.yaml and hard-fails on one it cannot resolve, so a
    stray matching file breaks every deploy even though nothing references it.
    It is moved aside, not deleted — out of install.sh's view, still on disk."""
    pdir = tmp_path / "rig-a"
    canonical.write_project(pdir, _document())
    spare = pdir / "config" / "provider-bread.spare0.yaml"
    spare.write_text("staged: true\n", encoding="utf-8")

    canonical.write_project(pdir, _document())

    assert not spare.exists()
    retired = pdir / canonical.LAUNCH_DIR / "retired" / "provider-bread.spare0.yaml"
    assert retired.read_text(encoding="utf-8") == "staged: true\n"


def test_write_leaves_alone_what_install_sh_never_looks_at(tmp_path: pathlib.Path) -> None:
    """Anything outside install.sh's globs is the user's, including behaviour
    trees the workbench cannot recreate."""
    pdir = tmp_path / "rig-a"
    document = _document()
    document["profile"]["behaviors"] = ["behaviors/main.xml"]
    canonical.write_project(pdir, document)
    behavior = pdir / "behaviors" / "main.xml"
    behavior.parent.mkdir(exist_ok=True)
    behavior.write_text("<root />\n", encoding="utf-8")
    notes = pdir / "config" / "notes.yaml"
    notes.write_text("note: keep me\n", encoding="utf-8")

    # Removing the automation variant drops the behaviours entry — the exact
    # sequence that must NOT take the hand-authored .xml with it.
    document["profile"].pop("behaviors")
    canonical.write_project(pdir, document)

    assert behavior.read_text(encoding="utf-8") == "<root />\n"
    assert notes.read_text(encoding="utf-8") == "note: keep me\n"


def test_sidecar_authored_flag_defaults_false_for_pre_255_sidecars(tmp_path: pathlib.Path) -> None:
    pdir = tmp_path / "rig-a"
    pdir.mkdir()
    # A #274-era sidecar has no `authored` key — it is an import.
    (pdir / machine_profile.SIDECAR_NAME).write_text(
        json.dumps({"schema_version": 1, "format": "machine-profile", "meta": {}}), encoding="utf-8"
    )
    assert canonical.is_authored(machine_profile.read_sidecar(pdir)) is False

    canonical.write_sidecar(pdir, canonical.default_sidecar(name="a", created="t", machine_id="rig-a"))
    assert canonical.is_authored(machine_profile.read_sidecar(pdir)) is True


# ---------------------------------------------------------------------------
# Validator — the three install.sh gate mirrors + coherence
# ---------------------------------------------------------------------------


def test_valid_document_passes() -> None:
    assert canonical_validator.validate_project(_document()) == []


def test_gate_manual_variant_must_be_inert() -> None:
    doc = _document()
    doc["variants"][canonical.MANUAL_VARIANT]["automation"] = {"enabled": True, "behavior_tree": "b.xml"}
    errors = canonical_validator.validate_project(doc)
    assert any("not inert" in e and canonical.AUTOMATION_VARIANT in e for e in errors), errors


def test_gate_provider_kind_must_be_pinned() -> None:
    """install.sh resolves the kind from the RENDERED COMMAND, so the gate must
    key on the command token — not on the kind derived from the filename."""
    doc = _document()
    doc["variants"][canonical.MANUAL_VARIANT]["providers"][0]["command"] = canonical.provider_command_token("ezo")
    errors = canonical_validator.validate_project(doc)
    assert any("not in the profile's pinned components" in e for e in errors), errors


def test_gate_catches_command_disagreeing_with_the_config_filename() -> None:
    """The exact hole the review found: a bread config file whose command names
    ezo passed validation and then hard-failed at install.sh bundle time."""
    doc = _document()
    doc["profile"]["components"]["providers"]["ezo"] = {
        "repo": "anolishq/anolis-provider-ezo",
        "version": "0.3.4",
    }
    doc["variants"][canonical.MANUAL_VARIANT]["providers"][0]["command"] = canonical.provider_command_token("ezo")
    errors = canonical_validator.validate_project(doc)
    assert any("resolves to kind 'ezo'" in e and "declares 'bread'" in e for e in errors), errors


def test_command_kind_requires_both_captures_to_agree() -> None:
    assert canonical.command_kind(canonical.provider_command_token("bread")) == "bread"
    # install.sh rewrites using the SECOND capture; a mismatched token would
    # render to a different provider than the path implies.
    assert canonical.command_kind("../anolis-provider-bread/build/dev-release/anolis-provider-ezo") is None
    assert canonical.command_kind("/opt/anolis/bin/anolis-provider-bread") is None


def test_gate_path_tokens_must_carry_the_machine_id() -> None:
    doc = _document()
    doc["variants"][canonical.MANUAL_VARIANT] = _runtime_doc("someone-else")
    errors = canonical_validator.validate_project(doc)
    assert any("someone-else" in e for e in errors), errors


def test_missing_manual_variant_is_rejected() -> None:
    doc = _document(variants={})
    doc["profile"]["runtime_profiles"] = {canonical.AUTOMATION_VARIANT: "config/anolis-runtime.automation.yaml"}
    errors = canonical_validator.validate_project(doc)
    assert any("manual" in e for e in errors), errors


def test_provider_coherence_across_profile_variant_and_config() -> None:
    doc = _document()
    doc["providers"]["ezo0"] = {"kind": "ezo", "config": {}}
    errors = canonical_validator.validate_project(doc)
    assert any("not declared in the machine-profile" in e and "ezo0" in e for e in errors), errors


def test_provider_run_in_only_some_variants_is_legal() -> None:
    """A per-variant provider set is a main reason variants exist. install.sh
    unions across all runtime configs and only WARNS, so a save must not fail."""
    doc = _document()
    telemetry = _runtime_doc("rig-a")
    doc["profile"]["runtime_profiles"]["telemetry"] = canonical.variant_relpath("telemetry")
    doc["variants"]["telemetry"] = telemetry
    # bread0 runs in manual only; telemetry runs a different (also pinned) set.
    doc["profile"]["components"]["providers"]["ezo"] = {
        "repo": "anolishq/anolis-provider-ezo",
        "version": "0.3.4",
    }
    doc["profile"]["providers"]["ezo0"] = canonical.provider_config_relpath("ezo", "ezo0")
    doc["profile"]["providers"] = {
        "bread0": {"config": canonical.provider_config_relpath("bread", "bread0")},
        "ezo0": {"config": canonical.provider_config_relpath("ezo", "ezo0")},
    }
    doc["providers"]["ezo0"] = {"kind": "ezo", "config": {"hardware": {"bus_path": "/dev/i2c-2"}}}
    telemetry["providers"] = [
        {
            "id": "ezo0",
            "command": canonical.provider_command_token("ezo"),
            "args": [
                "--config",
                canonical.project_path_token("rig-a", canonical.provider_config_relpath("ezo", "ezo0")),
            ],
        }
    ]
    assert canonical_validator.validate_project(doc) == []
    assert canonical_validator.project_warnings(doc) == []


def test_provider_run_by_no_variant_is_a_warning_not_an_error() -> None:
    doc = _document()
    doc["profile"]["providers"]["ghost0"] = {"config": canonical.provider_config_relpath("bread", "ghost0")}
    doc["providers"]["ghost0"] = {"kind": "bread", "config": {}}
    assert canonical_validator.validate_project(doc) == []
    assert any("run by no runtime variant" in w for w in canonical_validator.project_warnings(doc))


def test_shared_bus_address_is_left_to_the_runtime() -> None:
    """Two providers naming one bus address is not the workbench's to judge: the
    providers publish claims and the runtime refuses the conflict (anolis#318)."""
    doc = _document()
    doc["profile"]["providers"]["ezo0"] = {"config": canonical.provider_config_relpath("ezo", "ezo0")}
    doc["profile"]["components"]["providers"]["ezo"] = {"repo": "anolishq/anolis-provider-ezo", "version": "0.3.4"}
    doc["providers"]["ezo0"] = {
        "kind": "ezo",
        "config": {
            "hardware": {"bus_path": "/dev/i2c-1"},
            "devices": [{"id": "ph0", "type": "ph", "address": 10}],  # decimal 10 == 0x0A
        },
    }
    doc["variants"][canonical.MANUAL_VARIANT]["providers"].append(
        {
            "id": "ezo0",
            "command": canonical.provider_command_token("ezo"),
            "args": [
                "--config",
                canonical.project_path_token("rig-a", canonical.provider_config_relpath("ezo", "ezo0")),
            ],
        }
    )
    errors = canonical_validator.validate_project(doc)
    assert not any("claimed by both" in e for e in errors), errors


def test_workbench_port_conflict_is_reported() -> None:
    doc = _document()
    doc["variants"][canonical.MANUAL_VARIANT]["http"]["port"] = 3010
    errors = canonical_validator.validate_project(doc)
    assert any("3010" in e for e in errors), errors


# ---------------------------------------------------------------------------
# Adversarial-review regressions (PR 1 hardening)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref",
    ["../../ESCAPE.yaml", "/tmp/ABSOLUTE.yaml", "config/sub/../provider-bread.bread0.yaml"],
)
def test_write_project_refuses_references_that_escape_or_normalize_away(tmp_path: pathlib.Path, ref: str) -> None:
    """Profile data reaches write_project from the HTTP API. An escaping ref
    wrote outside the workspace; a normalizing one was written and then pruned
    in the SAME call, silently yielding a project with a missing config."""
    pdir = tmp_path / "rig-a"
    doc = _document()
    doc["profile"]["providers"]["bread0"]["config"] = ref
    with pytest.raises(canonical.CanonicalError):
        canonical.write_project(pdir, doc)
    assert not (tmp_path / "ESCAPE.yaml").exists()
    assert not pathlib.Path("/tmp/ABSOLUTE.yaml").exists()


def test_write_project_creates_nothing_when_a_reference_is_rejected(tmp_path: pathlib.Path) -> None:
    pdir = tmp_path / "rig-a"
    doc = _document()
    doc["profile"]["providers"]["bread0"]["config"] = "../../escape.yaml"
    with pytest.raises(canonical.CanonicalError):
        canonical.write_project(pdir, doc)
    assert not (pdir / "machine-profile.yaml").exists()


def test_write_project_refuses_an_imported_document(tmp_path: pathlib.Path) -> None:
    """#274's verbatim invariant: imported profiles are never re-emitted."""
    pdir = tmp_path / "rig-a"
    doc = _document()
    doc["authored"] = False
    with pytest.raises(canonical.CanonicalError, match="verbatim"):
        canonical.write_project(pdir, doc)
    assert not pdir.exists()


def test_validate_project_reports_profile_schema_errors() -> None:
    doc = _document()
    del doc["profile"]["compatibility"]
    doc["profile"]["display_name"] = ""
    errors = canonical_validator.validate_project(doc)
    assert any("machine-profile:" in e and "compatibility" in e for e in errors), errors


def test_validate_project_rejects_escaping_references() -> None:
    doc = _document()
    doc["profile"]["providers"]["bread0"]["config"] = "../../escape.yaml"
    errors = canonical_validator.validate_project(doc)
    assert any("escapes the project directory" in e for e in errors), errors


def test_build_profile_refuses_a_zero_provider_project() -> None:
    """The canonical contract has no representation for it: both
    compatibility.providers and the runtime providers list are non-empty."""
    with pytest.raises(canonical.CanonicalError, match="at least one provider"):
        canonical.build_profile(machine_id="rig-a", display_name="Rig A", providers={})


def test_pinned_gate_fires_when_components_lists_no_providers() -> None:
    """components.runtime pinned but components.providers missing: deploy's own
    guard only checks components.runtime, so this reached install.sh and
    hard-failed for every provider."""
    doc = _document(components={"runtime": {"repo": "anolishq/anolis", "version": "0.1.39"}})
    errors = canonical_validator.validate_project(doc)
    assert any("none pinned" in e for e in errors), errors


def test_machine_id_is_length_capped() -> None:
    machine_id = canonical.machine_id_from_name("x" * 300)
    assert len(machine_id) <= canonical.MACHINE_ID_MAX_LEN
    assert MACHINE_ID_RE.fullmatch(machine_id)


@pytest.mark.parametrize(
    ("automation", "inert"),
    [
        ({"enabled": False}, True),
        ({"enabled": False, "behavior_tree": "behaviors/main.xml"}, True),
        ({"enabled": True}, False),
        # install.sh un-quotes and lowercases the field, then compares against a
        # false-ish set — so these ARE inert to it and must not be blocked.
        ({"enabled": "false"}, True),
        ({"enabled": "False"}, True),
        ({"enabled": "no"}, True),
        ({"enabled": "off"}, True),
        # ...but a number is not in that set.
        ({"enabled": 0}, False),
        ({"enabled": 1}, False),
        ({"mode_transition_hooks": []}, False),
        # Its scan is not depth-anchored: a flag or hook nested anywhere in the
        # automation block trips it.
        ({"enabled": False, "policy": {"enabled": True}}, False),
        ({"enabled": False, "policy": {"mode_transition_hooks": []}}, False),
    ],
)
def test_inertness_matches_install_sh(automation: dict, inert: bool) -> None:
    """install.sh's install-time gate is an awk pass over the raw YAML. Anywhere
    this is laxer, the workbench authors a manual variant that install.sh then
    refuses at sudo time on the target."""
    violation = canonical.inertness_violation(_runtime_doc(automation=automation))
    assert (violation is None) is inert, violation


def test_inertness_rejects_an_empty_automation_block() -> None:
    """install.sh needs a plain block mapping; `automation:` alone is not one —
    and safe_dump writes a bare key exactly that way, so this is reachable just
    by round-tripping such a config through the workbench."""
    assert canonical.inertness_violation({"automation": None, "providers": []}) is not None
    assert canonical.inertness_violation({"automation": {}, "providers": []}) is not None
    assert canonical.inertness_violation({"automation": "yes", "providers": []}) is not None
    assert canonical.inertness_violation({"providers": []}) is None


def test_deploy_tokens_reject_traversal_and_bare_relative_args() -> None:
    doc = _runtime_doc("rig-a")
    doc["providers"][0]["args"] = ["--config", "../anolis-projects/projects/rig-a/config/../../etc/passwd"]
    assert any("escapes" in p for p in canonical.assert_deploy_tokens("rig-a", doc))

    doc = _runtime_doc("rig-a")
    doc["providers"][0]["args"] = ["--config", "config/provider-bread.bread0.yaml"]
    assert any("bare relative path" in p for p in canonical.assert_deploy_tokens("rig-a", doc))


def test_retiring_an_orphan_never_clobbers_an_earlier_one(tmp_path: pathlib.Path) -> None:
    """Two rounds of retirement must not silently overwrite the first file."""
    pdir = tmp_path / "rig-a"
    canonical.write_project(pdir, _document())
    stray = pdir / "config" / "provider-bread.spare0.yaml"

    stray.write_text("version one\n", encoding="utf-8")
    canonical.write_project(pdir, _document())
    stray.write_text("version two\n", encoding="utf-8")
    canonical.write_project(pdir, _document())

    retired = sorted(p.read_text(encoding="utf-8").strip() for p in (pdir / canonical.LAUNCH_DIR / "retired").iterdir())
    assert retired == ["version one", "version two"]


def test_retiring_an_orphan_does_not_follow_a_symlink_out_of_the_project(tmp_path: pathlib.Path) -> None:
    """Moving the RESOLVED path would drag a file out of the user's home and
    leave a dangling link that breaks install.sh's config render."""
    outside = tmp_path / "elsewhere" / "provider-bread.note.yaml"
    outside.parent.mkdir(parents=True)
    outside.write_text("mine\n", encoding="utf-8")

    pdir = tmp_path / "rig-a"
    canonical.write_project(pdir, _document())
    (pdir / "config" / "provider-bread.note.yaml").symlink_to(outside)

    canonical.write_project(pdir, _document())

    assert outside.read_text(encoding="utf-8") == "mine\n"
