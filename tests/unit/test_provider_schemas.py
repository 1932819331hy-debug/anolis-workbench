"""Provider config-schema envelope registry (#270)."""

import json
import pathlib

import provider_locks  # from scripts/, on the path via pytest's `pythonpath`
import pytest
import yaml

from anolis_workbench.core import provider_schemas

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SIM_QUICKSTART = REPO_ROOT / "anolis_workbench" / "templates" / "sim-quickstart" / "machine-profile.yaml"


@pytest.fixture(autouse=True)
def _fresh_cache():
    provider_schemas._envelope_cache = None
    yield
    provider_schemas._envelope_cache = None


def test_available_kinds_are_the_locked_providers():
    """The kinds offered at runtime are exactly the kinds the registry locks.

    Asserted against the lock directory rather than a literal list: naming the
    current three here would mean a fourth provider fails the suite, which is
    the code change #285 exists to remove.
    """
    assert provider_schemas.available_kinds() == provider_locks.locked_kinds(REPO_ROOT)


def test_envelopes_carry_the_profile_shape():
    for kind in provider_schemas.available_kinds():
        envelope = provider_schemas.get_envelope(kind)
        assert envelope is not None
        assert isinstance(envelope["config_schema_version"], int)
        assert envelope["config_schema_version"] >= 1
        assert isinstance(envelope["schema"], dict)
        # Not our convention to relax: install.sh fetches
        # `anolis-provider-<kind>-<ver>-linux-<arch>.tar.gz` and runs
        # `bin/anolis-provider-<kind>`, so the name is fixed by the deploy
        # contract for second-party providers too.
        assert envelope["provider"] == f"anolis-provider-{kind}"
        # Load-bearing for #283: an envelope that does not say which provider
        # version it came from silently disables skew detection for that kind,
        # with nothing else to notice it.
        assert isinstance(envelope.get("provider_version"), str)
        assert envelope["provider_version"].strip()


def test_unknown_kind_is_none():
    assert provider_schemas.get_envelope("custom") is None
    assert provider_schemas.get_envelope("nope") is None


def test_all_envelopes_matches_kinds():
    envelopes = provider_schemas.all_envelopes()
    assert sorted(envelopes.keys()) == provider_schemas.available_kinds()


def _profile(pins: dict[str, str]) -> dict:
    return {"components": {"providers": {kind: {"version": v} for kind, v in pins.items()}}}


def _packaged_version(kind: str) -> str:
    envelope = provider_schemas.get_envelope(kind)
    assert envelope is not None
    version = envelope["provider_version"]
    assert isinstance(version, str)
    return version


def _resolve(kind: str, version: str | None) -> provider_schemas.Resolution:
    resolution = provider_schemas.resolve(kind, version)
    assert resolution is not None
    return resolution


def _resolve_for_profile(profile: dict, kind: str) -> provider_schemas.Resolution:
    resolution = provider_schemas.resolve_for_profile(profile, kind)
    assert resolution is not None
    return resolution


def test_resolution_is_exact_when_the_pin_matches_the_envelope():
    resolution = _resolve("bread", _packaged_version("bread"))

    assert resolution.source == "packaged"
    assert resolution.exact is True
    assert resolution.skew_message() is None


def test_resolution_is_inexact_when_the_pin_disagrees():
    envelope_version = _packaged_version("bread")
    resolution = _resolve("bread", "0.3.6")

    assert resolution.exact is False
    # Still resolves — refusing to validate would be worse than validating
    # against a near neighbour and saying so.
    assert resolution.envelope is provider_schemas.get_envelope("bread")
    message = resolution.skew_message()
    assert message is not None
    assert "bread" in message
    assert envelope_version in message
    assert "0.3.6" in message


def test_absent_information_never_warns():
    """A dev profile with no pins has not asked for a version, so there is
    nothing for the envelope to contradict."""
    assert _resolve("bread", None).exact is True
    assert _resolve_for_profile({}, "bread").exact is True
    assert _resolve_for_profile({"components": {}}, "bread").exact is True
    assert _resolve_for_profile({"components": {"providers": {"bread": {}}}}, "bread").exact is True


def test_blank_pin_is_treated_as_unpinned():
    assert provider_schemas.pinned_version(_profile({"bread": "   "}), "bread") is None


def test_resolve_for_profile_reads_the_machines_pin():
    resolution = _resolve_for_profile(_profile({"bread": "0.3.6"}), "bread")
    assert resolution.pinned_version == "0.3.6"
    assert resolution.exact is False


def test_unknown_kind_resolves_to_none():
    assert provider_schemas.resolve("custom", "1.0.0") is None
    assert provider_schemas.resolve_for_profile(_profile({"custom": "1.0.0"}), "custom") is None


def test_version_skew_warnings_names_both_versions():
    document = {
        "profile": _profile({"bread": "0.3.6"}),
        "providers": {"b0": {"kind": "bread"}},
    }
    warnings = provider_schemas.version_skew_warnings(document)
    assert len(warnings) == 1
    assert "0.3.6" in warnings[0]
    assert _packaged_version("bread") in warnings[0]


def test_version_skew_warnings_are_silent_when_pins_match():
    document = {
        "profile": _profile({"bread": _packaged_version("bread")}),
        "providers": {"b0": {"kind": "bread"}},
    }
    assert provider_schemas.version_skew_warnings(document) == []


def test_version_skew_warnings_deduplicate_across_providers_of_a_kind():
    """Two bread providers share one pin, so they share one warning."""
    document = {
        "profile": _profile({"bread": "0.3.6"}),
        "providers": {"b0": {"kind": "bread"}, "b1": {"kind": "bread"}},
    }
    assert len(provider_schemas.version_skew_warnings(document)) == 1


def test_version_skew_warnings_tolerate_malformed_documents():
    assert provider_schemas.version_skew_warnings(None) == []
    assert provider_schemas.version_skew_warnings({}) == []
    assert provider_schemas.version_skew_warnings({"providers": "nope"}) == []
    assert provider_schemas.version_skew_warnings({"providers": {"b0": {"kind": None}}}) == []
    assert provider_schemas.version_skew_warnings({"providers": {"b0": "nope"}}) == []


def test_a_padded_pin_is_not_a_skew():
    """The profile schema accepts "0.3.8 ". Comparing it unstripped produces a
    warning that names the same version twice and cannot be acted on."""
    padded = f"  {_packaged_version('bread')} "
    resolution = _resolve_for_profile(_profile({"bread": padded}), "bread")

    assert resolution.pinned_version == _packaged_version("bread")
    assert resolution.exact is True
    assert resolution.skew_message() is None


def test_the_sim_quickstart_template_pins_the_packaged_sim():
    """Onboarding creates projects from this template, so a pin behind the
    vendored envelope puts a skew warning on every new quickstart project."""
    profile = yaml.safe_load(SIM_QUICKSTART.read_text(encoding="utf-8"))
    resolution = _resolve_for_profile(profile, "sim")

    assert resolution.pinned_version == resolution.envelope_version
    assert resolution.skew_message() is None
    assert str(profile["compatibility"]["providers"]["sim0"]["version"]) == resolution.envelope_version


def test_resolution_never_touches_the_network(monkeypatch):
    """Resolution runs on the authoring path, which must work air-gapped.

    Blocking the socket layer rather than one client library: the guarantee is
    "no network", not "not urllib".
    """
    import socket

    def _boom(*args, **kwargs):  # pragma: no cover - only runs on regression
        raise AssertionError("resolution must never open a socket")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)

    resolution = _resolve_for_profile(_profile({"bread": "0.3.6"}), "bread")
    assert resolution.skew_message() is not None


def test_malformed_envelope_fails_loudly(monkeypatch, tmp_path):
    (tmp_path / "bogus.config-schema.json").write_text(json.dumps({"config_schema_version": True, "schema": {}}))
    monkeypatch.setattr(provider_schemas.paths_module, "PROVIDER_SCHEMAS_DIR", tmp_path)
    provider_schemas._envelope_cache = None
    with pytest.raises(ValueError, match="config_schema_version"):
        provider_schemas.available_kinds()
