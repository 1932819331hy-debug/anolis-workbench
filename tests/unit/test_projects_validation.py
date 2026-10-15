"""Save-time validation of a canonical project document (#255).

Two layers run on every save: `canonical_validator` (profile schema, runtime
schema, containment, and mirrors of the install.sh gates that would otherwise
only fail at `sudo` on the target) and the vendored provider config-schema
envelopes from #270.
"""

from __future__ import annotations

import copy
import pathlib

import conftest
import pytest

from anolis_workbench.core import canonical, machine_profile, projects

TEMPLATES_ROOT = pathlib.Path(__file__).parent.parent.parent / "anolis_workbench" / "templates"
FIXTURES_ROOT = pathlib.Path(__file__).parent.parent / "fixtures"


def _load_template(name: str) -> dict:
    """A canonical project as an in-memory document.

    `sim-quickstart` is the one SHIPPED template; `mixed-bus-mock` is a test
    fixture (a multi-provider I2C machine, which is what these checks need —
    nothing user-facing should offer to create a mock-bus machine).
    """
    root = TEMPLATES_ROOT if (TEMPLATES_ROOT / name).is_dir() else FIXTURES_ROOT
    return copy.deepcopy(canonical.read_project(root / name))


def _manual(document: dict) -> dict:
    return document["variants"][canonical.MANUAL_VARIANT]  # type: ignore[no-any-return]


@pytest.fixture(autouse=True)
def _systems_root(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "systems"
    monkeypatch.setattr(projects, "SYSTEMS_ROOT", root)
    return root


def test_save_project_rejects_schema_invalid_payload(_systems_root: pathlib.Path) -> None:
    with pytest.raises(projects.ProjectValidationError) as exc_info:
        projects.save_project("invalid-schema", {"schema_version": 1})

    errors = exc_info.value.errors
    assert any(err.get("source") == "canonical" for err in errors), errors
    assert not (_systems_root / "invalid-schema" / machine_profile.PROFILE_FILENAME).exists()


def test_save_project_rejects_semantic_invalid_payload(_systems_root: pathlib.Path) -> None:
    document = _load_template("sim-quickstart")
    entry = copy.deepcopy(_manual(document)["providers"][0])
    _manual(document)["providers"].append(entry)  # same id twice

    with pytest.raises(projects.ProjectValidationError) as exc_info:
        projects.save_project("invalid-semantic", document)

    errors = exc_info.value.errors
    assert any("uplicate" in err.get("message", "") for err in errors), errors
    assert not (_systems_root / "invalid-semantic" / machine_profile.PROFILE_FILENAME).exists()


def test_save_project_rejects_unknown_provider_kind(_systems_root: pathlib.Path) -> None:
    """Kinds are the set of vendored config-schema envelopes; anything else is rejected."""
    document = _load_template("sim-quickstart")
    document["providers"]["custom0"] = {"kind": "custom", "config": {}}
    document["profile"]["providers"]["custom0"] = {"config": canonical.provider_config_relpath("custom", "custom0")}
    document["profile"]["compatibility"]["providers"]["custom0"] = {
        "strategy": "local-build",
        "version": "unspecified",
    }

    with pytest.raises(projects.ProjectValidationError) as exc_info:
        projects.save_project("invalid-custom", document)

    errors = exc_info.value.errors
    unknown = [err for err in errors if err.get("code") == "provider.unknown_kind"]
    assert unknown, errors
    assert unknown[0]["path"] == "$.providers.custom0.kind"
    assert "custom" in unknown[0]["message"]
    assert not (_systems_root / "invalid-custom" / machine_profile.PROFILE_FILENAME).exists()


def test_save_project_rejects_a_command_whose_kind_is_not_pinned(_systems_root: pathlib.Path) -> None:
    """install.sh iterates EVERY variant and sys.exits when a provider command
    does not resolve to a pinned component — mirror that at save time so it
    fails in the composer rather than at sudo on the target."""
    document = _load_template("sim-quickstart")
    _manual(document)["providers"][0]["command"] = canonical.provider_command_token("bread")

    with pytest.raises(projects.ProjectValidationError) as exc_info:
        projects.save_project("unpinned-kind", document)
    assert any("bread" in err.get("message", "") for err in exc_info.value.errors), exc_info.value.errors


def test_save_project_rejects_a_non_inert_manual_variant(_systems_root: pathlib.Path) -> None:
    """install.sh refuses to install a manual variant that boots into automation."""
    document = _load_template("sim-quickstart")
    _manual(document)["automation"] = {"enabled": True}

    with pytest.raises(projects.ProjectValidationError) as exc_info:
        projects.save_project("non-inert", document)
    assert any("inert" in err.get("message", "").lower() for err in exc_info.value.errors), exc_info.value.errors


def test_validate_provider_config_envelope_failure_matrix() -> None:
    """Representative envelope violations must surface as provider-schema errors
    with paths anchored under the provider's config."""
    document = _load_template("mixed-bus-mock")
    bread = document["providers"]["bread0"]["config"]

    # 1. Missing required section (hardware)
    del bread["hardware"]
    errors = projects.validate_project_payload(document)
    assert any(e["code"] == "provider.schema" and e["path"] == "$.providers.bread0.config" for e in errors), errors

    # 2. Wrong type deep in the config
    document = _load_template("mixed-bus-mock")
    document["providers"]["bread0"]["config"]["hardware"]["timeout_ms"] = "fast"
    errors = projects.validate_project_payload(document)
    assert any(
        e["code"] == "provider.schema" and e["path"] == "$.providers.bread0.config.hardware.timeout_ms" for e in errors
    ), errors

    # 3. Conditional: discovery.mode manual requires addresses
    document = _load_template("mixed-bus-mock")
    del document["providers"]["bread0"]["config"]["discovery"]["addresses"]
    errors = projects.validate_project_payload(document)
    assert any(
        e["code"] == "provider.schema" and e["path"].startswith("$.providers.bread0.config.discovery") for e in errors
    ), errors

    # 4. Out-of-range I2C address (pattern/anyOf)
    document = _load_template("mixed-bus-mock")
    document["providers"]["ezo0"]["config"]["devices"][0]["address"] = "0xFF"
    errors = projects.validate_project_payload(document)
    assert any(e["code"] == "provider.schema" and "devices[0]" in e["path"] and "ezo0" in e["path"] for e in errors), (
        errors
    )

    # 5. Unknown key under additionalProperties: false
    document = _load_template("mixed-bus-mock")
    document["providers"]["bread0"]["config"]["require_live_session"] = True
    errors = projects.validate_project_payload(document)
    assert any(e["code"] == "provider.schema" and "bread0" in e["path"] for e in errors), errors


def test_validate_unique_annotations_catch_normalized_duplicates() -> None:
    """10 vs '0x0A' name the same address — uniqueItems misses it, x-anolis-unique must not."""
    document = _load_template("mixed-bus-mock")
    bread = document["providers"]["bread0"]["config"]
    bread["devices"][1]["address"] = 10  # rlht0 already claims "0x0A"
    bread["discovery"]["addresses"] = ["0x0A", 10, "0x15"]

    errors = projects.validate_project_payload(document)
    paths = {e["path"] for e in errors if e["code"] == "provider.unique"}
    assert "$.providers.bread0.config.devices" in paths, errors
    assert "$.providers.bread0.config.discovery.addresses" in paths, errors


def test_validate_unique_annotations_catch_duplicate_device_ids() -> None:
    document = _load_template("sim-quickstart")
    devices = document["providers"]["sim0"]["config"]["devices"]
    devices[1]["id"] = devices[0]["id"]

    errors = projects.validate_project_payload(document)
    unique = [e for e in errors if e["code"] == "provider.unique"]
    assert any(e["path"] == "$.providers.sim0.config.devices" for e in unique), errors


def test_save_project_writes_the_canonical_layout(_systems_root: pathlib.Path) -> None:
    document = _load_template("sim-quickstart")
    projects.save_project("valid-project", document)

    root = _systems_root / "valid-project"
    assert (root / machine_profile.PROFILE_FILENAME).is_file()
    assert (root / canonical.variant_relpath(canonical.MANUAL_VARIANT)).is_file()
    assert (root / canonical.provider_config_relpath("sim", "sim0")).is_file()
    assert (root / machine_profile.SIDECAR_NAME).is_file()
    # No trace of the retired shadow document.
    assert not (root / "system.json").exists()
    assert not (root / "anolis-runtime.yaml").exists()

    reread = canonical.read_project(root)
    assert reread["authored"] is True
    assert reread["providers"]["sim0"]["config"] == document["providers"]["sim0"]["config"]


def test_save_project_rejects_an_off_host_bind_without_auth(_systems_root: pathlib.Path) -> None:
    """install.sh refuses this too, but only in its config phase — AFTER the
    binaries on the target have already been replaced. Catching it at save time
    keeps it a one-field fix instead of a half-finished install."""
    document = _load_template("sim-quickstart")
    _manual(document)["http"]["bind"] = "0.0.0.0"

    with pytest.raises(projects.ProjectValidationError) as exc_info:
        projects.save_project("open-bind", document)
    assert any("authentication" in err.get("message", "") for err in exc_info.value.errors)


def test_an_off_host_bind_with_auth_is_allowed(_systems_root: pathlib.Path) -> None:
    document = _load_template("sim-quickstart")
    _manual(document)["http"]["bind"] = "0.0.0.0"
    _manual(document)["http"]["auth_enabled"] = True

    projects.save_project("open-bind-auth", document)
    assert (_systems_root / "open-bind-auth" / machine_profile.PROFILE_FILENAME).is_file()


def test_loopback_binds_are_not_flagged(_systems_root: pathlib.Path) -> None:
    for bind in ("127.0.0.1", "127.0.1.1", "::1", "localhost"):
        document = _load_template("sim-quickstart")
        _manual(document)["http"]["bind"] = bind
        assert projects.validate_project_payload(document) == [], bind


def test_two_projects_cannot_share_a_deploy_directory(_systems_root: pathlib.Path) -> None:
    """install.sh `rm -rf`s {prefix}/projects/<dir> before installing, so a
    collision means deploying one project destroys the other's config on the
    rig. `Rig_A` and `rig-a` slugify identically, so this is easy to hit."""
    projects.create_project_from_template("Rig_A", "sim-quickstart")

    with pytest.raises(ValueError, match="already uses"):
        projects.create_project_from_template("rig-a", "sim-quickstart")
    with pytest.raises(ValueError, match="already uses"):
        projects.duplicate_project("Rig_A", "rig-a")


def test_rename_keeps_the_deploy_identity_and_does_not_self_collide(_systems_root: pathlib.Path) -> None:
    projects.create_project_from_template("Rig_A", "sim-quickstart")
    projects.rename_project("Rig_A", "Rig_A_old")
    # The deploy identity deliberately survives a rename...
    assert projects.get_project("Rig_A_old")["profile"]["machine_id"] == "rig-a"
    # ...which is exactly why the name it freed up must stay blocked.
    with pytest.raises(ValueError, match="already uses"):
        projects.create_project_from_template("rig-a", "sim-quickstart")


def test_a_declared_variant_with_no_configuration_is_rejected(_systems_root: pathlib.Path) -> None:
    """The profile is a manifest: a variant it declares but nothing writes leaves
    a config file that does not exist, which blocks EVERY deploy of the project —
    and is invisible in the UI, since a variant with no document is not rendered."""
    document = _load_template("sim-quickstart")
    document["profile"]["runtime_profiles"]["telemetry"] = canonical.variant_relpath("telemetry")

    with pytest.raises(projects.ProjectValidationError) as exc_info:
        projects.save_project("phantom", document)
    assert any("has no configuration" in err.get("message", "") for err in exc_info.value.errors)


def test_concurrent_saves_cannot_declare_a_variant_they_did_not_write(
    _systems_root: pathlib.Path,
) -> None:
    """The server is threaded and every save is a read-modify-write over the whole
    project. Interleaving two of them used to leave the profile declaring a
    variant whose file the other save had just retired."""
    import copy
    import threading

    import yaml

    projects.create_project_from_template("rig", "sim-quickstart")
    base = projects.get_project("rig")

    def with_variant(name: str) -> dict:
        document = copy.deepcopy(base)
        document["variants"][name] = copy.deepcopy(document["variants"][canonical.MANUAL_VARIANT])
        document["profile"]["runtime_profiles"][name] = canonical.variant_relpath(name)
        return document

    barrier = threading.Barrier(2)

    def save(name: str) -> None:
        barrier.wait()
        try:
            projects.save_project("rig", with_variant(name))
        except projects.ProjectValidationError:
            pass

    threads = [threading.Thread(target=save, args=(n,)) for n in ("telemetry", "automation")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    pdir = projects.project_dir("rig")
    profile = yaml.safe_load((pdir / machine_profile.PROFILE_FILENAME).read_text(encoding="utf-8"))
    for variant, rel in profile["runtime_profiles"].items():
        assert (pdir / rel).is_file(), f"profile declares {variant} but {rel} is missing"


def test_migration_warnings_survive_the_first_save(_systems_root: pathlib.Path) -> None:
    """They record what the migration could NOT carry — a dropped provider, an
    automation variant left behind. Erasing them on the first save means the
    only notice of real data loss disappears before the user reads it."""
    import json

    legacy = json.loads(
        (pathlib.Path(__file__).parent.parent / "fixtures" / "v2-templates" / "bioreactor-manual.json").read_text(
            encoding="utf-8"
        )
    )
    del legacy["topology"]["providers"]["ezo0"]["kind"]
    pdir = _systems_root / "legacy"
    pdir.mkdir(parents=True)
    (pdir / "system.json").write_text(json.dumps(legacy), encoding="utf-8")

    document = projects.get_project("legacy")
    assert any("has no kind" in w for w in document["warnings"])

    document["profile"]["components"] = {
        "runtime": {"repo": "anolishq/anolis", "version": "0.1.39"},
        "providers": {"bread": {"repo": "anolishq/anolis-provider-bread", "version": "0.3.8"}},
    }
    projects.save_project("legacy", document)

    assert any("has no kind" in w for w in projects.get_project("legacy")["warnings"])


# ---------------------------------------------------------------------------
# Version-keyed schema resolution (#283)
# ---------------------------------------------------------------------------


def _skew_warnings(warnings: list[str]) -> list[str]:
    return [w for w in warnings if "pinned at" in w]


def _packaged_sim_version() -> str:
    from anolis_workbench.core import provider_schemas

    envelope = provider_schemas.get_envelope("sim")
    assert envelope is not None
    version = envelope["provider_version"]
    assert isinstance(version, str)
    return version


def test_get_project_warns_when_the_pin_disagrees_with_the_packaged_envelope(
    _systems_root: pathlib.Path, canonical_project
) -> None:
    """The live bug #283 fixes: a machine pinned to an older provider had its
    config validated against whichever envelope happened to be vendored, and
    nothing said so."""
    canonical_project(_systems_root / "skewed", machine_id="skewed")

    warnings = _skew_warnings(projects.get_project("skewed")["warnings"])

    assert len(warnings) == 1
    # Both versions named, so the reader can tell which way the skew runs.
    assert _packaged_sim_version() in warnings[0]
    assert conftest.FIXTURE_PROVIDER_VERSIONS["sim"] in warnings[0]


def test_matching_pin_produces_no_warning(_systems_root: pathlib.Path, canonical_project) -> None:
    canonical_project(
        _systems_root / "matched",
        machine_id="matched",
        components={
            "runtime": {"repo": "anolishq/anolis", "version": "0.1.39"},
            "providers": {"sim": {"repo": "anolishq/anolis-provider-sim", "version": _packaged_sim_version()}},
        },
    )

    assert _skew_warnings(projects.get_project("matched")["warnings"]) == []


def test_the_skew_warning_is_derived_not_persisted(_systems_root: pathlib.Path, canonical_project) -> None:
    """Correcting the pin clears the warning on the next read.

    It is never written to the sidecar, so it cannot outlive what it describes
    the way carried warnings do (#290) — the failure mode this avoids.
    """
    import json

    pdir = canonical_project(_systems_root / "drift", machine_id="drift")
    assert _skew_warnings(projects.get_project("drift")["warnings"])

    sidecar = json.loads((pdir / machine_profile.SIDECAR_NAME).read_text(encoding="utf-8"))
    assert _skew_warnings(sidecar.get("warnings") or []) == []

    # Round-tripping a document that CARRIES the warning must not persist it —
    # otherwise the derived warning becomes a carried one on the first save and
    # inherits exactly the staleness it was written to avoid.
    projects.save_project("drift", projects.get_project("drift"))
    sidecar = json.loads((pdir / machine_profile.SIDECAR_NAME).read_text(encoding="utf-8"))
    assert _skew_warnings(sidecar.get("warnings") or []) == []

    document = projects.get_project("drift")
    document["profile"]["components"]["providers"]["sim"]["version"] = _packaged_sim_version()
    projects.save_project("drift", document)

    assert _skew_warnings(projects.get_project("drift")["warnings"]) == []
    sidecar = json.loads((pdir / machine_profile.SIDECAR_NAME).read_text(encoding="utf-8"))
    assert _skew_warnings(sidecar.get("warnings") or []) == []


def test_skew_warns_once_per_kind_not_once_per_provider(_systems_root: pathlib.Path, canonical_project) -> None:
    """The pin lives in components.providers, so providers of a kind share it."""
    canonical_project(
        _systems_root / "many",
        machine_id="many",
        providers={"sim0": "sim", "sim1": "sim", "sim2": "sim"},
    )

    assert len(_skew_warnings(projects.get_project("many")["warnings"])) == 1


def test_an_unpinned_provider_never_warns(_systems_root: pathlib.Path, canonical_project) -> None:
    """A local-build profile has not asked for a version, so there is nothing
    for the packaged envelope to contradict."""
    canonical_project(_systems_root / "unpinned", machine_id="unpinned", pin_components=False)

    assert _skew_warnings(projects.get_project("unpinned")["warnings"]) == []


def test_a_skewed_pin_still_validates_the_config(_systems_root: pathlib.Path, canonical_project) -> None:
    """Warning, never error: an inexact resolution must still catch real config
    mistakes, or the offline lab loses validation entirely."""
    pdir = canonical_project(_systems_root / "still-checked", machine_id="still-checked")
    document = projects.get_project("still-checked")
    document["providers"]["sim0"]["config"]["startup_policy"] = "not-a-policy"

    with pytest.raises(projects.ProjectValidationError) as exc_info:
        projects.save_project("still-checked", document)

    assert any(err.get("code") == "provider.schema" for err in exc_info.value.errors), exc_info.value.errors
    assert pdir.is_dir()


# ---------------------------------------------------------------------------
# Providers with no declared software safe state (#371)
# ---------------------------------------------------------------------------


def _safe_state_hits(document: dict) -> list[str]:
    return [w for w in document["warnings"] if projects.SAFE_STATE_WARNING in w]


def test_providers_with_an_empty_safe_state_warn_on_read(_systems_root: pathlib.Path) -> None:
    document = _load_template("sim-quickstart")
    assert _manual(document)["providers"]
    assert "safety" not in _manual(document)
    projects.save_project("rig", document)

    hits = _safe_state_hits(projects.get_project("rig"))

    assert hits == [f"Variant 'manual' has providers and no safety.safe_state. {projects.SAFE_STATE_WARNING}"]


def test_the_warning_is_derived_so_declaring_a_safe_state_clears_it(_systems_root: pathlib.Path) -> None:
    projects.save_project("rig", _load_template("sim-quickstart"))
    document = projects.get_project("rig")
    assert _safe_state_hits(document)

    _manual(document)["safety"] = {"safe_state": {"zero_is_safe": True}}
    projects.save_project("rig", document)

    assert _safe_state_hits(projects.get_project("rig")) == []


def test_a_variant_without_providers_does_not_warn() -> None:
    document = _load_template("sim-quickstart")
    _manual(document)["providers"] = []

    assert projects.safe_state_warnings(document) == []


@pytest.mark.parametrize(
    "safe_state",
    [
        {"hooks": [{"device_handle": "sim0/tempctl0", "function_name": "set_relay", "args": {"on": False}}]},
        {"setpoints": [{"device_handle": "sim0/motorctl0", "function_name": "set_motor_duty", "args": {"duty": 0}}]},
        {"zero_is_safe": True},
    ],
    ids=["hooks", "setpoints", "zero_is_safe"],
)
def test_a_declared_safe_state_does_not_warn(safe_state: dict) -> None:
    document = _load_template("sim-quickstart")
    _manual(document)["safety"] = {"safe_state": safe_state}

    assert projects.safe_state_warnings(document) == []


@pytest.mark.parametrize(
    "safety",
    [{}, {"safe_state": {}}, {"safe_state": {"hooks": [], "setpoints": [], "zero_is_safe": False}}],
    ids=["no-safe-state", "empty-safe-state", "all-empty"],
)
def test_an_empty_safe_state_warns(safety: dict) -> None:
    document = _load_template("sim-quickstart")
    _manual(document)["safety"] = safety

    assert len(projects.safe_state_warnings(document)) == 1


# ---------------------------------------------------------------------------
# Onboarding's marker ends with the user's first save or copy (#424)
# ---------------------------------------------------------------------------


def _seed_as_onboarding(monkeypatch: pytest.MonkeyPatch, root: pathlib.Path) -> pathlib.Path:
    """The project Onboarding's install job seeds, through its own path."""
    from anolis_workbench.core import paths as paths_module
    from anolis_workbench.server.routes import provision

    monkeypatch.setattr(paths_module, "SYSTEMS_ROOT", root)
    project_dir = provision._prepare_workspace({}, lambda *args: None)
    assert _created_by(project_dir) == "onboarding"
    return project_dir


def _created_by(project_dir: pathlib.Path) -> object:
    return ((machine_profile.read_sidecar(project_dir) or {}).get("meta") or {}).get("created_by")


def test_saving_an_onboarding_project_makes_it_the_users(
    monkeypatch: pytest.MonkeyPatch, _systems_root: pathlib.Path
) -> None:
    project_dir = _seed_as_onboarding(monkeypatch, _systems_root)

    projects.save_project(project_dir.name, canonical.read_project(project_dir))

    assert _created_by(project_dir) is None


def test_a_duplicate_of_an_onboarding_project_is_the_users(
    monkeypatch: pytest.MonkeyPatch, _systems_root: pathlib.Path
) -> None:
    project_dir = _seed_as_onboarding(monkeypatch, _systems_root)

    projects.duplicate_project(project_dir.name, "my-copy")

    assert _created_by(_systems_root / "my-copy") is None
    assert _created_by(project_dir) == "onboarding"
