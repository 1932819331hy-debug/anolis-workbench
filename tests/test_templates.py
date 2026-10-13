"""The bundled templates are shipped ARTIFACTS (#255), not renderer input.

A template is a canonical project directory that gets copied to create a
project and copied again to deploy, so every property install.sh depends on has
to hold in the checked-in files themselves. That is what this module asserts.

Only `sim-quickstart` ships. It is synthetic — no hardware, no counterpart in
anolis-projects — so there is nothing for it to drift from. Templates that
mirrored REAL machines were removed: a fork of a machine config cannot be kept
honest by a test, because the source of truth lives in another repo that CI
cannot see, and the bioreactor fork had already silently lost
`command_watchdog_ms` from its bread devices. Real machines are imported (#226).
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

from anolis_workbench.core import canonical, canonical_validator, machine_profile

TEMPLATES_DIR = pathlib.Path(__file__).parent.parent / "anolis_workbench" / "templates"
FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures" / "bioreactor"

TEMPLATES = ("sim-quickstart",)


def _load_yaml(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@pytest.fixture(params=TEMPLATES)
def template(request: pytest.FixtureRequest) -> pathlib.Path:
    return TEMPLATES_DIR / str(request.param)


def test_every_bundled_template_is_shipped(template: pathlib.Path) -> None:
    assert template.is_dir()
    assert (template / machine_profile.PROFILE_FILENAME).is_file()
    assert (template / machine_profile.SIDECAR_NAME).is_file()


def test_template_is_a_valid_canonical_project(template: pathlib.Path) -> None:
    document = canonical.read_project(template)
    assert document["authored"] is True
    assert canonical_validator.validate_project(document) == []


def test_template_machine_id_matches_its_directory(template: pathlib.Path) -> None:
    """install.sh keys its `../anolis-projects/projects/<X>/` rewrites on the
    deploy directory basename, which is the machine_id — so a template whose id
    and directory disagree produces configs pointing at the wrong project."""
    profile = machine_profile.load_profile(template)
    assert profile["machine_id"] == template.name


def test_template_manual_variant_is_inert(template: pathlib.Path) -> None:
    """install.sh refuses a non-inert `manual` variant outright."""
    document = canonical.read_project(template)
    manual = document["variants"][canonical.MANUAL_VARIANT]
    assert canonical.inertness_violation(manual) is None


def test_template_paths_are_canonical_deploy_tokens(template: pathlib.Path) -> None:
    document = canonical.read_project(template)
    profile = document["profile"]
    kinds = {pid: entry["kind"] for pid, entry in document["providers"].items()}
    for variant, doc in document["variants"].items():
        problems = canonical.assert_deploy_tokens(profile["machine_id"], doc, kinds)
        assert problems == [], f"{template.name}/{variant}: {problems}"


def test_template_pins_every_kind_it_runs(template: pathlib.Path) -> None:
    """install.sh fetches provider binaries by `components.providers` KEY and
    hard-fails when a variant's command resolves to an unpinned kind."""
    document = canonical.read_project(template)
    pinned = canonical.pinned_kinds(document["profile"])
    assert pinned, f"{template.name} ships without component pins"
    for doc in document["variants"].values():
        for entry in doc["providers"]:
            assert canonical.command_kind(entry["command"]) in pinned


def test_template_provider_filenames_follow_the_install_sh_convention(template: pathlib.Path) -> None:
    """install.sh takes the provider config's stem up to the FIRST dot as the
    installed name, so the kind has to come first."""
    document = canonical.read_project(template)
    for pid, entry in document["profile"]["providers"].items():
        name = pathlib.PurePosixPath(entry["config"]).name
        assert name == canonical.provider_config_filename(document["providers"][pid]["kind"], pid)


def test_template_bind_is_emitted_unquoted(template: pathlib.Path) -> None:
    """install.sh's LAN-exposure rewrite matches `^\\s*bind: 127.0.0.1\\s*$`.
    A quoted value silently skips the rewrite AND the auth it turns on."""
    for rel in machine_profile.load_profile(template)["runtime_profiles"].values():
        text = (template / rel).read_text(encoding="utf-8")
        if "bind:" in text:
            assert "bind: 127.0.0.1\n" in text


def test_template_runtime_configs_are_schema_valid(template: pathlib.Path) -> None:
    for variant, doc in canonical.read_project(template)["variants"].items():
        assert canonical.runtime_config_errors(doc) == [], f"{template.name}/{variant}"


@pytest.mark.parametrize(
    "baseline",
    ["anolis-runtime.bioreactor.manual.yaml", "anolis-runtime.bioreactor.telemetry.yaml"],
)
def test_real_world_baselines_validate_against_the_vendored_schema(baseline: str) -> None:
    """The vendored runtime-config schema is what save-time validation rejects
    on — it must accept hand-authored configs that the runtime itself accepts.

    These fixtures are captured from the real bioreactor project. They are kept
    as a CONTRACT check on the schema, not as a copy of a machine we ship."""
    assert canonical.runtime_config_errors(_load_yaml(FIXTURE_DIR / baseline)) == []


@pytest.mark.parametrize(
    ("fixture", "kind"),
    [("provider-bread.bioreactor.yaml", "bread"), ("provider-ezo.bioreactor.yaml", "ezo")],
)
def test_real_world_provider_configs_satisfy_the_vendored_envelopes(fixture: str, kind: str) -> None:
    """Captured from the real bioreactor machine. Kept as a contract check on
    the provider schemas — not as a machine config we ship. If a provider's
    schema ever stops accepting what the rig actually runs, this fails."""
    import jsonschema

    from anolis_workbench.core import provider_schemas

    envelope = provider_schemas.get_envelope(kind)
    assert envelope is not None, f"no vendored envelope for {kind}"
    jsonschema.Draft202012Validator(envelope["schema"]).validate(_load_yaml(FIXTURE_DIR / fixture))


class _CapturingHandler:
    def __init__(self) -> None:
        self.responses: list[tuple[int, object]] = []

    def _json(self, status: int, data: object) -> None:
        self.responses.append((status, data))


def _served_templates() -> list[dict]:
    from anolis_workbench.server.routes import compose

    handler = _CapturingHandler()
    compose.serve_templates(handler)
    [(status, data)] = handler.responses
    assert status == 200
    assert isinstance(data, list)
    return data


def test_served_templates_are_exactly_the_shipped_directories() -> None:
    """A template that fails to load must fail the build, not quietly drop out
    of GET /api/templates (#356)."""
    served = _served_templates()
    shipped = sorted(d.name for d in TEMPLATES_DIR.iterdir() if d.is_dir())
    assert [t["id"] for t in served] == shipped
    broken = {t["id"]: t["error"] for t in served if "error" in t}
    assert broken == {}, f"templates that fail to load: {broken}"


def test_a_broken_template_is_listed_with_its_error(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from anolis_workbench.core import paths as paths_module

    good = tmp_path / "good"
    good.mkdir()
    (good / machine_profile.PROFILE_FILENAME).write_text("display_name: Good\n", encoding="utf-8")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / machine_profile.PROFILE_FILENAME).write_text("- not a mapping\n", encoding="utf-8")
    (tmp_path / "no-profile").mkdir()
    monkeypatch.setattr(paths_module, "TEMPLATES_ROOT", tmp_path)

    served = {t["id"]: t for t in _served_templates()}

    assert sorted(served) == ["bad", "good", "no-profile"]
    assert "error" not in served["good"]
    assert served["good"]["meta"] == {"name": "Good"}
    assert "root must be a mapping" in served["bad"]["error"]
    assert f"No {machine_profile.PROFILE_FILENAME}" in served["no-profile"]["error"]
