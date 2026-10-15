"""Unit tests for #226 — near-passthrough import of machine-profile projects.

The load-bearing property is VERBATIM-NESS: every imported file must be
byte-identical at every stage (source -> workspace -> materialized deploy
dir), including safety.estop_topology, all runtime_profiles variants, YAML
comments, and the automation variant's full safety contract.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil

import pytest

from anolis_workbench.core import deploy, machine_profile, projects, provider_schemas

FIXTURE = pathlib.Path(__file__).parent.parent / "fixtures" / "imported-profile"

COPIED = ("machine-profile.yaml", "config", "behaviors")


def _tree_digests(root: pathlib.Path, *, exclude: tuple[str, ...] = ()) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if rel in exclude:
            continue
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


@pytest.fixture(autouse=True)
def _allow_tmp_import_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Imports are allowlist-confined (default: $HOME + the data dir); tests
    import from pytest tmp dirs, so widen the roots for them explicitly."""
    monkeypatch.setenv("ANOLIS_IMPORT_ROOTS", str(tmp_path))


@pytest.fixture()
def systems_root(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "systems"
    monkeypatch.setattr(projects, "SYSTEMS_ROOT", root)
    return root


@pytest.fixture()
def source_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """A pristine copy of the fixture so tests can mutate it safely."""
    dest = tmp_path / "imported-profile"
    shutil.copytree(FIXTURE, dest)
    return dest


# ---------------------------------------------------------------------------
# import_project
# ---------------------------------------------------------------------------


def _copied_subset(root: pathlib.Path) -> dict[str, str]:
    """Source digests restricted to what an import is supposed to copy."""
    return {
        rel: digest
        for rel, digest in _tree_digests(root).items()
        if rel == "machine-profile.yaml" or rel.startswith(("config/", "behaviors/"))
    }


def test_import_copies_byte_identical(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    meta, warnings = projects.import_project(str(source_dir), "rig-a")

    assert meta["profile_dir_name"] == "imported-profile"
    assert warnings == []
    imported = systems_root / "rig-a"
    assert _tree_digests(imported, exclude=(projects.SIDECAR_NAME,)) == _copied_subset(source_dir)

    sidecar = json.loads((imported / projects.SIDECAR_NAME).read_text(encoding="utf-8"))
    assert sidecar["format"] == projects.FORMAT_MACHINE_PROFILE
    assert sidecar["meta"]["imported_from"] == str(source_dir.resolve())


def test_import_copies_only_the_declared_set(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    """docs/ (and anything else outside the copy set) stays in the source repo."""
    assert (source_dir / "docs" / "NOTES.md").is_file()  # fixture guard
    projects.import_project(str(source_dir), "rig-a")
    assert not (systems_root / "rig-a" / "docs").exists()


def test_import_rejects_existing_name(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    projects.import_project(str(source_dir), "rig-a")
    with pytest.raises(ValueError, match="already exists"):
        projects.import_project(str(source_dir), "rig-a")


def test_import_hard_fails_on_missing_referenced_file(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    (source_dir / "config" / "provider-ezo.bioreactor.yaml").unlink()
    with pytest.raises(projects.ImportValidationError) as exc_info:
        projects.import_project(str(source_dir), "rig-a")
    assert any("provider-ezo" in e for e in exc_info.value.errors)
    assert not (systems_root / "rig-a").exists()  # nothing half-copied


def test_import_hard_fails_on_schema_invalid_profile(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    profile_path = source_dir / "machine-profile.yaml"
    profile_path.write_text(
        profile_path.read_text(encoding="utf-8").replace("machine_id: imported-profile\n", ""),
        encoding="utf-8",
    )
    with pytest.raises(projects.ImportValidationError) as exc_info:
        projects.import_project(str(source_dir), "rig-a")
    assert any("machine_id" in e for e in exc_info.value.errors)


def test_import_warns_on_mismatched_project_dir_name(
    systems_root: pathlib.Path, source_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    renamed = tmp_path / "other-name"
    source_dir.rename(renamed)
    _, warnings = projects.import_project(str(renamed), "rig-a")
    assert any("other-name" in w and "path rewrites" in w for w in warnings), warnings


def test_import_warns_on_non_inert_manual_variant(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    manual = source_dir / "config" / "anolis-runtime.bioreactor.manual.yaml"
    manual.write_text(
        manual.read_text(encoding="utf-8").replace("automation:\n  enabled: false", "automation:\n  enabled: true"),
        encoding="utf-8",
    )
    _, warnings = projects.import_project(str(source_dir), "rig-a")
    assert any("verify-inert" in w for w in warnings), warnings


def test_import_warns_on_an_influxdb_token(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    """Deploy refuses an authored token (#463); say so at import, not only then."""
    rel = "config/anolis-runtime.bioreactor.telemetry.yaml"
    variant = source_dir / rel
    variant.write_text(
        variant.read_text(encoding="utf-8").replace("    org: anolis\n", "    org: anolis\n    token: abc\n"),
        encoding="utf-8",
    )
    _, warnings = projects.import_project(str(source_dir), "rig-a")
    token_warnings = [w for w in warnings if "telemetry.influxdb.token" in w]
    assert len(token_warnings) == 1, warnings
    assert token_warnings[0].startswith(f"Runtime variant 'telemetry' ({rel}): ")
    assert "INFLUXDB_TOKEN" in token_warnings[0]
    assert "re-import" in token_warnings[0]


def test_import_accepts_an_empty_influxdb_token(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    variant = source_dir / "config" / "anolis-runtime.bioreactor.telemetry.yaml"
    variant.write_text(
        variant.read_text(encoding="utf-8").replace("    org: anolis\n", '    org: anolis\n    token: ""\n'),
        encoding="utf-8",
    )
    _, warnings = projects.import_project(str(source_dir), "rig-a")
    assert warnings == []


def test_skew_on_an_imported_rig_is_derived_and_never_recorded(
    systems_root: pathlib.Path, source_dir: pathlib.Path
) -> None:
    """An imported rig pins whatever it was commissioned with — routinely older
    than the packaged envelope (#283).

    The warning must come from the READ, not the import: import warnings are
    written to the sidecar and never recomputed, so a skew recorded here would
    survive the schema sync that resolves it (#290) and could never be cleared
    on a project that is read-only by design.
    """
    profile = source_dir / "machine-profile.yaml"
    profile.write_text(
        profile.read_text(encoding="utf-8").replace('version: "0.5.0"', 'version: "0.3.6"'),
        encoding="utf-8",
    )

    _, warnings = projects.import_project(str(source_dir), "rig-a")
    assert not any("pinned at" in w for w in warnings), warnings

    sidecar = json.loads((systems_root / "rig-a" / machine_profile.SIDECAR_NAME).read_text(encoding="utf-8"))
    assert not any("pinned at" in w for w in sidecar.get("warnings") or []), sidecar

    skew = [w for w in projects.get_project("rig-a")["warnings"] if "pinned at 0.3.6" in w]
    assert len(skew) == 1, projects.get_project("rig-a")["warnings"]
    vendored = (provider_schemas.get_envelope("bread") or {}).get("provider_version")
    assert vendored and vendored in skew[0]
    # The unchanged ezo pin still matches, so it must stay quiet.
    assert not any("ezo" in w and "pinned at" in w for w in projects.get_project("rig-a")["warnings"])


# ---------------------------------------------------------------------------
# CRUD behavior on imported projects
# ---------------------------------------------------------------------------


def test_imported_project_listing_and_get(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    projects.import_project(str(source_dir), "rig-a")

    listing = projects.list_projects()
    assert [(p["name"], p["format"]) for p in listing] == [("rig-a", "machine-profile")]

    doc = projects.get_project("rig-a")
    assert doc["format"] == "machine-profile"
    assert doc["profile"]["safety"] == {"estop_topology": "power_cut"}
    assert sorted(doc["profile"]["runtime_profiles"]) == ["automation", "full", "manual", "telemetry"]
    # get_project must not have rewritten anything on disk
    assert _tree_digests(systems_root / "rig-a", exclude=(projects.SIDECAR_NAME,)) == _copied_subset(source_dir)


def test_save_rejected_for_imported(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    projects.import_project(str(source_dir), "rig-a")
    with pytest.raises(projects.ReadOnlyProjectError):
        projects.save_project("rig-a", {"schema_version": 2})


def test_duplicate_imported_is_verbatim(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    projects.import_project(str(source_dir), "rig-a")
    doc = projects.duplicate_project("rig-a", "rig-b")
    assert doc["format"] == "machine-profile"
    assert doc["meta"]["name"] == "rig-b"
    assert _tree_digests(systems_root / "rig-b", exclude=(projects.SIDECAR_NAME,)) == _copied_subset(source_dir)


# ---------------------------------------------------------------------------
# materialize_project_dir (imported projects)
# ---------------------------------------------------------------------------


def test_materialize_is_byte_identical_and_offline(
    systems_root: pathlib.Path,
    source_dir: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects.import_project(str(source_dir), "rig-a")

    def _no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("materialize_project_dir must not touch the network")

    monkeypatch.setattr(deploy.requests, "get", _no_network)
    monkeypatch.setattr(deploy.releases.requests, "get", _no_network)

    mat = deploy.materialize_project_dir(systems_root / "rig-a", tmp_path / "out")

    # Keyed on the ORIGINAL canonical dir name, not the workbench name.
    assert mat.project_dir == tmp_path / "out" / "imported-profile"
    assert mat.runtime_version == "0.1.39"  # from components, verbatim — no release lookup
    assert mat.provider_kinds == {"bread0": "bread", "ezo0": "ezo"}
    assert _tree_digests(mat.project_dir) == _copied_subset(source_dir)


def test_materialize_hard_fails_without_components(
    systems_root: pathlib.Path, source_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    profile_path = source_dir / "machine-profile.yaml"
    text = profile_path.read_text(encoding="utf-8")
    profile_path.write_text(text[: text.index("components:")], encoding="utf-8")
    projects.import_project(str(source_dir), "rig-a")

    with pytest.raises(deploy.DeployError, match="components"):
        deploy.materialize_project_dir(projects.SYSTEMS_ROOT / "rig-a", tmp_path / "out")


def test_materialize_rejects_unknown_variant(
    systems_root: pathlib.Path, source_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    projects.import_project(str(source_dir), "rig-a")
    with pytest.raises(deploy.DeployError, match="variant 'warp'"):
        deploy.materialize_project_dir(projects.SYSTEMS_ROOT / "rig-a", tmp_path / "out", variant="warp")


def test_install_args_variant() -> None:
    args = deploy._install_args(
        "/tmp/p",
        prefix=deploy.DEFAULT_INSTALL_PREFIX,
        no_start=False,
        dry_run=True,
        variant="automation",
    )
    assert args == ["--project", "/tmp/p", "--variant", "automation", "--dry-run"]


# ---------------------------------------------------------------------------
# machine_profile helpers
# ---------------------------------------------------------------------------


def test_derive_kinds_from_components_and_filenames(source_dir: pathlib.Path) -> None:
    profile = machine_profile.load_profile(source_dir)
    assert machine_profile.derive_kinds(profile, source_dir) == {"bread0": "bread", "ezo0": "ezo"}


def test_derive_kinds_falls_back_to_the_config_filename(source_dir: pathlib.Path) -> None:
    """Without pins the kind still has to resolve: a migrated project has no
    components yet (they are authored data), and reading every provider back as
    an unknown kind would block the very save that fills the pins in."""
    profile = machine_profile.load_profile(source_dir)
    del profile["components"]
    assert machine_profile.derive_kinds(profile, source_dir) == {"bread0": "bread", "ezo0": "ezo"}


def test_derive_kinds_is_none_when_the_filename_says_nothing(source_dir: pathlib.Path) -> None:
    profile = machine_profile.load_profile(source_dir)
    del profile["components"]
    profile["providers"]["bread0"]["config"] = "config/mystery.yaml"
    assert machine_profile.derive_kinds(profile, source_dir)["bread0"] is None


def test_validate_project_dir_reports_missing_dir(tmp_path: pathlib.Path) -> None:
    report = machine_profile.validate_project_dir(tmp_path / "nope", "nope")
    assert not report.ok
    assert any("Not a directory" in e for e in report.errors)


# ---------------------------------------------------------------------------
# Reference containment (adversarial review: MAJOR 1 + MAJOR 2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "expect"),
    [
        ("/etc/passwd", "absolute path"),
        ("../escape.yaml", "escapes the project directory"),
        ("config/../../escape.yaml", "escapes the project directory"),
    ],
)
def test_import_refuses_uncarriable_references(
    systems_root: pathlib.Path, source_dir: pathlib.Path, ref: str, expect: str
) -> None:
    """A reference the import cannot carry must HARD-FAIL, not produce a
    workspace silently missing the file (an absolute ref is the nasty case:
    Path(src) / "/etc/passwd" exists on the host, so a naive existence check
    passes at import AND at deploy while nothing is ever copied)."""
    profile_path = source_dir / "machine-profile.yaml"
    profile_path.write_text(
        profile_path.read_text(encoding="utf-8").replace(
            "  manual: config/anolis-runtime.bioreactor.manual.yaml",
            f"  manual: {ref}",
        ),
        encoding="utf-8",
    )
    with pytest.raises(projects.ImportValidationError) as exc_info:
        projects.import_project(str(source_dir), "rig-a")
    assert any(expect in e for e in exc_info.value.errors), exc_info.value.errors
    assert not (systems_root / "rig-a").exists()


def test_referenced_files_includes_validation_script() -> None:
    """validation.check_http_script is a profile-relative path too — it must be
    validated (and therefore carried), not silently dropped."""
    profile = {"validation": {"check_http_script": "config/check_http.sh"}}
    assert machine_profile.referenced_files(profile) == ["config/check_http.sh"]


def test_materialize_refuses_uncarriable_reference(
    systems_root: pathlib.Path, source_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Defence in depth: even a hand-edited workspace cannot deploy a profile
    that references files outside the carried set."""
    projects.import_project(str(source_dir), "rig-a")
    profile_path = systems_root / "rig-a" / "machine-profile.yaml"
    profile_path.write_text(
        profile_path.read_text(encoding="utf-8").replace(
            "  manual: config/anolis-runtime.bioreactor.manual.yaml",
            "  manual: /etc/passwd",
        ),
        encoding="utf-8",
    )
    with pytest.raises(deploy.DeployError, match="cannot carry"):
        deploy.materialize_project_dir(systems_root / "rig-a", tmp_path / "out")


# ---------------------------------------------------------------------------
# Robustness (adversarial review MINORs)
# ---------------------------------------------------------------------------


def test_corrupt_sidecar_does_not_break_the_project_list(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    """A non-UTF-8 / unparseable sidecar must degrade to the profile-file
    fallback, not 500 the whole listing."""
    projects.import_project(str(source_dir), "rig-a")
    projects.sidecar_path("rig-a").write_bytes(b"\xff\xfe not json")

    listing = projects.list_projects()
    assert [(p["name"], p["format"]) for p in listing] == [("rig-a", "machine-profile")]
    assert projects.get_project("rig-a")["format"] == "machine-profile"


def test_materialize_rejects_traversal_in_sidecar_dir_name(
    systems_root: pathlib.Path, source_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    projects.import_project(str(source_dir), "rig-a")
    sidecar_file = projects.sidecar_path("rig-a")
    sidecar = json.loads(sidecar_file.read_text(encoding="utf-8"))
    sidecar["meta"]["profile_dir_name"] = "../escaped"
    sidecar_file.write_text(json.dumps(sidecar), encoding="utf-8")

    with pytest.raises(deploy.DeployError, match="invalid project directory name"):
        deploy.materialize_project_dir(systems_root / "rig-a", tmp_path / "out")
    assert not (tmp_path / "escaped").exists()


def test_materialize_is_idempotent_into_the_same_dest(
    systems_root: pathlib.Path, source_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    projects.import_project(str(source_dir), "rig-a")
    out = tmp_path / "out"
    first = deploy.materialize_project_dir(systems_root / "rig-a", out)
    second = deploy.materialize_project_dir(systems_root / "rig-a", out)
    assert _tree_digests(first.project_dir) == _tree_digests(second.project_dir)


def test_import_of_a_taken_name_leaves_the_existing_project_intact(
    systems_root: pathlib.Path, source_dir: pathlib.Path
) -> None:
    """The failure path must never rmtree a directory it did not create."""
    projects.import_project(str(source_dir), "rig-a")
    before = _tree_digests(systems_root / "rig-a")
    with pytest.raises(ValueError, match="already exists"):
        projects.import_project(str(source_dir), "rig-a")
    assert _tree_digests(systems_root / "rig-a") == before


def test_import_carries_referenced_dirs_outside_the_base_set(
    systems_root: pathlib.Path, source_dir: pathlib.Path
) -> None:
    """A profile may legally reference e.g. validation/check.sh; the copy set
    adapts so nothing the profile depends on is left behind (and the file is
    NOT copied merely because the directory exists — only when referenced)."""
    (source_dir / "validation").mkdir()
    (source_dir / "validation" / "check_http.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    # Not referenced yet -> not carried.
    projects.import_project(str(source_dir), "rig-plain")
    assert not (systems_root / "rig-plain" / "validation").exists()

    profile_path = source_dir / "machine-profile.yaml"
    profile_path.write_text(
        profile_path.read_text(encoding="utf-8").replace(
            "behaviors:\n",
            "validation:\n  check_http_script: validation/check_http.sh\n\nbehaviors:\n",
            1,
        ),
        encoding="utf-8",
    )
    _, warnings = projects.import_project(str(source_dir), "rig-b")
    carried = systems_root / "rig-b" / "validation" / "check_http.sh"
    assert carried.is_file(), warnings
    assert carried.read_bytes() == (source_dir / "validation" / "check_http.sh").read_bytes()


# ---------------------------------------------------------------------------
# Import source allowlist (CodeQL py/path-injection: the import path is
# caller-supplied and the workbench can be operated over LAN)
# ---------------------------------------------------------------------------


def test_import_refuses_a_source_outside_the_allowed_roots(
    systems_root: pathlib.Path, source_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.setenv("ANOLIS_IMPORT_ROOTS", str(tmp_path / "allowed"))
    (tmp_path / "allowed").mkdir()

    with pytest.raises(machine_profile.ImportSourceError, match="allowed root"):
        projects.import_project(str(source_dir), "rig-a")
    assert not (systems_root / "rig-a").exists()


def test_import_refuses_a_symlink_escaping_the_allowed_roots(
    systems_root: pathlib.Path, source_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Containment is checked on the FULLY RESOLVED path, so a symlink inside
    an allowed root cannot be used to reach outside it."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    source_dir.rename(outside)
    (allowed / "link").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("ANOLIS_IMPORT_ROOTS", str(allowed))

    with pytest.raises(machine_profile.ImportSourceError, match="allowed root"):
        projects.import_project(str(allowed / "link"), "rig-a")


def test_import_rejects_a_non_directory_and_empty_path(tmp_path: pathlib.Path) -> None:
    with pytest.raises(machine_profile.ImportSourceError, match="path required"):
        projects.import_project("", "rig-a")
    plain_file = tmp_path / "afile"
    plain_file.write_text("x", encoding="utf-8")
    with pytest.raises(machine_profile.ImportSourceError, match="Not a directory"):
        projects.import_project(str(plain_file), "rig-a")


def test_import_name_defaults_to_the_source_directory_name(
    systems_root: pathlib.Path, source_dir: pathlib.Path
) -> None:
    meta, _ = projects.import_project(str(source_dir))
    assert meta["name"] == "imported-profile"
    assert (systems_root / "imported-profile").is_dir()


def test_import_rejects_an_invalid_project_name(systems_root: pathlib.Path, source_dir: pathlib.Path) -> None:
    with pytest.raises(ValueError, match="Project name"):
        projects.import_project(str(source_dir), "../escape")


def test_importing_one_source_twice_warns_about_the_shared_deploy_directory(
    systems_root: pathlib.Path, source_dir: pathlib.Path
) -> None:
    """Both projects keep the source's directory name, and install.sh removes
    that directory before installing. The copies are byte-identical so this is
    benign today — but it stops being benign if one is hand-edited."""
    _, first = projects.import_project(str(source_dir), "rig-a")
    assert not any("also uses" in w for w in first)

    _, second = projects.import_project(str(source_dir), "rig-b")
    assert any("also uses" in w and "rig-a" in w for w in second), second


def test_materialize_refuses_a_non_inert_manual_variant(
    systems_root: pathlib.Path, source_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Imported projects never pass through save-time validation, so deploy is
    the only place that can catch this before install.sh does on the target."""
    manual = source_dir / "config" / "anolis-runtime.bioreactor.manual.yaml"
    manual.write_text(
        manual.read_text(encoding="utf-8").replace(
            "automation:\n  enabled: false", "automation:\n  enabled: false\n  mode_transition_hooks: []"
        ),
        encoding="utf-8",
    )
    projects.import_project(str(source_dir), "rig-a")

    with pytest.raises(deploy.DeployError, match="not inert"):
        deploy.materialize_project_dir(systems_root / "rig-a", tmp_path / "out")


def test_materialize_refuses_crlf_configs(
    systems_root: pathlib.Path, source_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """install.sh parses these with awk, which does not strip the carriage
    return: the runtime variant fails to resolve and the LAN-exposure check is
    silently skipped. An imported project is copied byte-for-byte, so the
    workbench's own LF writing does not cover this."""
    profile = source_dir / "machine-profile.yaml"
    profile.write_bytes(profile.read_bytes().replace(b"\n", b"\r\n"))
    projects.import_project(str(source_dir), "rig-a")

    with pytest.raises(deploy.DeployError, match="carriage returns"):
        deploy.materialize_project_dir(systems_root / "rig-a", tmp_path / "out")


def test_materialize_allows_crlf_where_install_sh_does_not_use_awk(
    systems_root: pathlib.Path, source_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Behaviour trees are byte-copied and provider configs go through pyyaml,
    so a CR in those is harmless — blocking them would strand an imported
    project with a message that is false and no way to clear it."""
    behavior = next((source_dir / "behaviors").glob("*.xml"))
    behavior.write_bytes(behavior.read_bytes().replace(b"\n", b"\r\n"))
    projects.import_project(str(source_dir), "rig-a")

    mat = deploy.materialize_project_dir(systems_root / "rig-a", tmp_path / "out")
    assert (mat.project_dir / "behaviors" / behavior.name).is_file()
