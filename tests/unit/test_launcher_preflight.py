"""Smoke tests for launcher.preflight's provider --check-config path.

Two properties matter here. From #270: every provider binary gets an
unconditional --check-config probe, with graceful degradation for binaries that
predate the verb. From #255: launch and preflight project the canonical variant
into a THROWAWAY host config under .workbench/ and never touch the canonical
files, which are the ones that get deployed.
"""

from __future__ import annotations

import hashlib
import pathlib
import sys

import pytest
import yaml

from anolis_workbench.core import canonical, launcher

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="uses /bin/sh stub binaries")


def _fake_binary(tmp_path: pathlib.Path, name: str, body: str) -> pathlib.Path:
    script = tmp_path / name
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def _tree_digests(root: pathlib.Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and canonical.LAUNCH_DIR not in p.parts
    }


@pytest.fixture()
def project(canonical_project, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    """A canonical project whose sidecar host paths point at stub binaries."""
    # --check-config subprocesses run with cwd=DATA_ROOT (~/.anolis), which
    # does not exist on CI runners — point it at the sandbox.
    monkeypatch.setattr("anolis_workbench.core.paths.DATA_ROOT", tmp_path)

    def _make(provider_body: str) -> tuple[pathlib.Path, dict]:
        runtime_bin = _fake_binary(tmp_path, "anolis-runtime", 'test "$1" = --check-config && test -f "$2" && exit 0')
        provider_bin = _fake_binary(tmp_path, "anolis-provider-sim", provider_body)
        pdir = canonical_project(
            tmp_path / "project",
            machine_id="smoke",
            host_paths={
                "runtime_executable": str(runtime_bin),
                "providers": {"sim0": {"executable": str(provider_bin)}},
            },
        )
        # Avoid a false port-in-use failure.
        variant = pdir / canonical.variant_relpath(canonical.MANUAL_VARIANT)
        doc = yaml.safe_load(variant.read_text(encoding="utf-8"))
        doc["http"]["port"] = 39321
        variant.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        return pdir, canonical.read_project(pdir)

    return _make


def _check(result: dict, name: str) -> dict:
    matches: list[dict] = [c for c in result["checks"] if c["name"] == name]
    assert matches, [c["name"] for c in result["checks"]]
    return matches[0]


def test_preflight_projects_host_paths_and_check_config_passes(project) -> None:
    pdir, document = project('test "$1" = --check-config && test -f "$2" && exit 0')
    before = _tree_digests(pdir)

    result = launcher.preflight("smoke", document, pdir)

    assert _check(result, "Provider sim0 --check-config")["ok"] is True
    assert _check(result, "Runtime --check-config")["ok"] is True
    assert result["ok"] is True

    # The launch projection is a throwaway with the HOST binary substituted for
    # the deploy token; the canonical files are byte-identical afterwards.
    launched = yaml.safe_load(
        (pdir / canonical.LAUNCH_DIR / "launch" / canonical.variant_filename(canonical.MANUAL_VARIANT)).read_text(
            encoding="utf-8"
        )
    )
    entry = launched["providers"][0]
    assert entry["command"].endswith("anolis-provider-sim")
    assert not entry["command"].startswith("../")
    assert entry["args"] == ["--config", str(pdir / canonical.provider_config_relpath("sim", "sim0"))]
    assert _tree_digests(pdir) == before


def test_preflight_reports_provider_config_rejection(project) -> None:
    pdir, document = project('echo "config invalid: bad tick" >&2; exit 1')

    result = launcher.preflight("smoke", document, pdir)

    check = _check(result, "Provider sim0 --check-config")
    assert check["ok"] is False
    assert "config invalid" in (check["error"] or "")
    assert result["ok"] is False


def test_preflight_degrades_for_binaries_without_the_verb(project) -> None:
    pdir, document = project('echo "unknown option: --check-config" >&2; exit 1')

    result = launcher.preflight("smoke", document, pdir)

    check = _check(result, "Provider sim0 --check-config")
    assert check["ok"] is None
    assert check["note"] == "Not yet available"
    assert result["ok"] is True  # a missing verb must not fail preflight


# --check-host (executable profile v1 §6): the provider says what it needs from
# this host; the workbench relays it. Envelopes below are bread 0.5.0's shape.
_UNMET_ENVELOPE = (
    '{"check_host_version":1,"provider":"anolis-provider-bread","requirements":['
    '{"id":"i2c.bus_present","status":"met","detail":"/dev/i2c-1 exists"},'
    '{"id":"i2c.bus_access","status":"unmet","detail":"camlab cannot open /dev/i2c-1 read-write",'
    '"remedy":"add camlab to group i2c"},'
    '{"id":"i2c.bus_clock","status":"unknown","detail":"the platform does not expose i2c-1 bus clock"}]}'
)
_MET_ENVELOPE = (
    '{"check_host_version":1,"provider":"anolis-provider-bread","requirements":['
    '{"id":"i2c.bus_present","status":"met","detail":"/dev/i2c-1 exists"}]}'
)


def _verbs(check_host: str) -> str:
    """A provider stub that passes --check-config and answers --check-host as given."""
    return f'case "$1" in --check-config) exit 0;; --check-host) {check_host};; *) exit 64;; esac'


def test_preflight_reports_unmet_host_requirements_with_their_remedy(project) -> None:
    pdir, document = project(_verbs(f"echo '{_UNMET_ENVELOPE}'; exit 1"))

    result = launcher.preflight("smoke", document, pdir)

    check = _check(result, "Provider sim0 --check-host")
    assert check["ok"] is False
    assert check["error"] == "i2c.bus_access: camlab cannot open /dev/i2c-1 read-write"
    assert check["hint"] == "add camlab to group i2c"
    assert "i2c.bus_clock unknown" in (check["note"] or "")
    assert result["ok"] is False


def test_preflight_passes_met_host_requirements(project) -> None:
    pdir, document = project(_verbs(f"echo '{_MET_ENVELOPE}'; exit 0"))

    result = launcher.preflight("smoke", document, pdir)

    check = _check(result, "Provider sim0 --check-host")
    assert check["ok"] is True
    assert check["note"] is None
    assert result["ok"] is True


def test_preflight_skips_a_provider_without_check_host(project) -> None:
    # The conformance rule install.sh uses too: non-zero with no JSON is no answer.
    pdir, document = project(_verbs('echo "usage: provider --config FILE"; exit 2'))

    result = launcher.preflight("smoke", document, pdir)

    check = _check(result, "Provider sim0 --check-host")
    assert check["ok"] is None
    assert check["note"] == "Not yet available"
    assert result["ok"] is True


def test_preflight_fails_when_host_requirements_cannot_be_evaluated(project) -> None:
    pdir, document = project(_verbs(f"echo '{_MET_ENVELOPE}'; echo 'cannot read config' >&2; exit 2"))

    result = launcher.preflight("smoke", document, pdir)

    check = _check(result, "Provider sim0 --check-host")
    assert check["ok"] is False
    assert "cannot read config" in (check["error"] or "")


def test_preflight_counts_a_skipped_check_as_skipped_not_passed(project) -> None:
    """#338: ok means "nothing failed" and gates Launch, but a skipped check
    verified nothing, so the result must never report it as a pass."""
    # --check-config predates the binary (skipped); --check-host is met.
    pdir, document = project(
        'case "$1" in --check-config) echo "unknown option: --check-config" >&2; exit 1;; '
        f"--check-host) echo '{_MET_ENVELOPE}'; exit 0;; *) exit 64;; esac"
    )

    result = launcher.preflight("smoke", document, pdir)

    passed = sum(1 for c in result["checks"] if c["ok"] is True)
    assert result["summary"] == {"passed": passed, "skipped": 1, "failed": 0}
    assert passed == len(result["checks"]) - 1


def test_preflight_summary_counts_failures(project) -> None:
    # --check-config rejects the config; --check-host is met.
    pdir, document = project(
        'case "$1" in --check-config) echo "config invalid: bad tick" >&2; exit 1;; '
        f"--check-host) echo '{_MET_ENVELOPE}'; exit 0;; *) exit 64;; esac"
    )

    result = launcher.preflight("smoke", document, pdir)

    assert result["summary"]["failed"] == 1
    assert result["summary"]["skipped"] == 0
    assert sum(result["summary"].values()) == len(result["checks"])


def test_preflight_carries_a_summary_when_the_launch_projection_fails(project, monkeypatch: pytest.MonkeyPatch) -> None:
    """The early return when the config cannot be projected is still a
    preflight result; the UI reads summary unconditionally."""
    pdir, document = project("exit 0")

    def _boom(*_args: object, **_kwargs: object) -> pathlib.Path:
        raise RuntimeError("cannot project")

    monkeypatch.setattr(launcher, "materialize_launch_config", _boom)

    result = launcher.preflight("smoke", document, pdir)

    assert result["ok"] is False
    assert result["summary"] == {"passed": 0, "skipped": 0, "failed": 1}
    assert result["checks"][0]["error"] == "cannot project"
