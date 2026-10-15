"""Unit tests for install.sh deployment delegation."""

from __future__ import annotations

import pathlib
from typing import Callable

import pytest
import requests
import yaml

from anolis_workbench.core import canonical, deploy, releases
from anolis_workbench.core.executor import Executor, RunResult


@pytest.fixture(autouse=True)
def _stub_release_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed the release cache and block network so tests never hit GitHub.

    Since #255 nothing on the deploy path resolves a version — pins are read
    from the project's own machine-profile — so this exists for `run_rollback`,
    which legitimately needs the latest install.sh, and as a tripwire proving
    the rest of the path stays offline.
    """
    monkeypatch.setattr(releases, "_RELEASE_CACHE", {"anolishq/anolis": "0.1.27"})

    def _no_network(*args: object, **kwargs: object) -> None:
        raise requests.RequestException("network disabled in tests")

    monkeypatch.setattr(releases.requests, "get", _no_network)
    monkeypatch.setattr(deploy.requests, "get", _no_network)


@pytest.fixture()
def project_dir(canonical_project: Callable[..., pathlib.Path], tmp_path: pathlib.Path) -> pathlib.Path:
    """An authored canonical project — the only thing deploy accepts now."""
    return canonical_project(
        tmp_path / "workspace",
        machine_id="deploy-fixture",
        behavior="behaviors/local.xml",
    )


class RecordingExecutor(Executor):
    """Fake Executor that records calls and succeeds."""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.commands: list[dict] = []
        self.files: dict[str, bytes] = {}
        self.mkdirs: list[str] = []

    def run(self, cmd, *, input=None, sudo=False, timeout=None):
        self.commands.append({"cmd": list(cmd), "sudo": sudo, "timeout": timeout})
        return RunResult(returncode=self.returncode, stdout="ok", stderr="")

    def write_file(self, path, data):
        self.files[path] = data

    def mkdir(self, path):
        self.mkdirs.append(path)

    def file_exists(self, path):
        return False


# ---------------------------------------------------------------------------
# materialize_project_dir
# ---------------------------------------------------------------------------


def test_materialize_produces_install_sh_layout(project_dir: pathlib.Path, tmp_path: pathlib.Path) -> None:
    mat = deploy.materialize_project_dir(project_dir, tmp_path / "out")
    pd = mat.project_dir
    # Keyed on machine_id, which is what install.sh's ../anolis-projects/
    # path rewrites resolve against — NOT the (renamable) workbench name.
    assert pd.name == "deploy-fixture"
    assert (pd / "machine-profile.yaml").is_file()
    assert (pd / "config" / "anolis-runtime.manual.yaml").is_file()
    assert (pd / "config" / "anolis-runtime.automation.yaml").is_file()
    assert (pd / "config" / "provider-sim.sim0.yaml").is_file()
    assert (pd / "behaviors" / "local.xml").is_file()
    assert mat.runtime_version == "0.1.27"
    assert mat.provider_kinds == {"sim0": "sim"}


def test_materialize_carries_deploy_tokens_untouched(project_dir: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """The workbench does NOT rewrite paths to production ones — install.sh
    owns that rewrite. Materialize is an honest copy, so what ships is exactly
    what was authored, tokens and all."""
    mat = deploy.materialize_project_dir(project_dir, tmp_path / "out")
    runtime = yaml.safe_load((mat.project_dir / "config" / "anolis-runtime.manual.yaml").read_text())
    entry = runtime["providers"][0]
    assert entry["command"] == canonical.provider_command_token("sim")
    assert entry["args"] == [
        "--config",
        "../anolis-projects/projects/deploy-fixture/config/provider-sim.sim0.yaml",
    ]
    assert canonical.command_kind(entry["command"]) == "sim"
    # bind is left as authored — install.sh owns the LAN-exposure rewrite, and
    # it only fires on an UNQUOTED 127.0.0.1.
    assert runtime["http"]["bind"] == "127.0.0.1"
    manual_text = (mat.project_dir / "config" / "anolis-runtime.manual.yaml").read_text()
    assert "bind: 127.0.0.1\n" in manual_text


def test_materialize_keeps_automation_out_of_the_manual_variant(
    project_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """install.sh REFUSES a non-inert manual variant, so automation lives in
    its own variant. (Before #255 the composer wrote it into the only config
    and every automation deploy was rejected at the target.)"""
    mat = deploy.materialize_project_dir(project_dir, tmp_path / "out")
    manual = yaml.safe_load((mat.project_dir / "config" / "anolis-runtime.manual.yaml").read_text())
    automation = yaml.safe_load((mat.project_dir / "config" / "anolis-runtime.automation.yaml").read_text())

    assert canonical.inertness_violation(manual) is None
    assert automation["automation"]["enabled"] is True
    expected = "../anolis-projects/projects/deploy-fixture/behaviors/local.xml"
    assert automation["automation"]["behavior_tree"] == expected


def test_materialize_uses_the_projects_own_pins_and_never_the_network(
    project_dir: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins are AUTHORED data. The old path resolved them from live GitHub
    lookups at deploy time, which could bump the runtime under a running rig
    and made deploying impossible offline."""
    monkeypatch.setattr(releases, "_RELEASE_CACHE", {})

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("materialize must not resolve versions from the network")

    monkeypatch.setattr(releases, "latest_release_version", _boom)

    mat = deploy.materialize_project_dir(project_dir, tmp_path / "out")
    profile = yaml.safe_load((mat.project_dir / "machine-profile.yaml").read_text())
    assert profile["components"]["runtime"] == {"repo": "anolishq/anolis", "version": "0.1.27"}
    assert profile["components"]["providers"]["sim"] == {
        "repo": "anolishq/anolis-provider-sim",
        "version": "0.2.5",
    }
    assert mat.runtime_version == "0.1.27"


def test_materialize_fails_on_missing_behavior_file(project_dir: pathlib.Path, tmp_path: pathlib.Path) -> None:
    (project_dir / "behaviors" / "local.xml").unlink()
    with pytest.raises(deploy.DeployError, match="missing files"):
        deploy.materialize_project_dir(project_dir, tmp_path / "out")


def test_materialize_fails_without_pins(canonical_project: Callable[..., pathlib.Path], tmp_path: pathlib.Path) -> None:
    """A migrated project has no pins yet — deploy must refuse it rather than
    invent them (#255 decision 1)."""
    unpinned = canonical_project(tmp_path / "unpinned", machine_id="unpinned", pin_components=False)
    with pytest.raises(deploy.DeployError, match="components"):
        deploy.materialize_project_dir(unpinned, tmp_path / "out")


# ---------------------------------------------------------------------------
# fetch_install_sh
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes = b"") -> None:
        self.status_code = status_code
        self.content = content


def test_fetch_install_sh_downloads_pinned_release(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    urls: list[str] = []

    def _get(url, **kwargs):
        urls.append(url)
        return _FakeResponse(200, b"#!/usr/bin/env bash\n")

    monkeypatch.setattr(deploy.requests, "get", _get)
    path = deploy.fetch_install_sh("0.1.27", tmp_path)
    assert path.read_bytes().startswith(b"#!")
    assert urls == ["https://github.com/anolishq/anolis/releases/download/v0.1.27/install.sh"]


def test_fetch_install_sh_raises_on_http_error(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deploy.requests, "get", lambda *a, **k: _FakeResponse(404))
    with pytest.raises(deploy.DeployError, match="HTTP 404"):
        deploy.fetch_install_sh("0.1.27", tmp_path)


# ---------------------------------------------------------------------------
# deploy_local / deploy_remote
# ---------------------------------------------------------------------------


def _stub_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_fetch(version: str, dest: pathlib.Path) -> pathlib.Path:
        path = dest / "install.sh"
        path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        return path

    monkeypatch.setattr(deploy, "fetch_install_sh", _fake_fetch)


def test_deploy_local_runs_install_sh_project(project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor()
    result = deploy.deploy_local(
        project_dir=project_dir,
        project_name="deploy-fixture",
        no_start=True,
        executor=executor,
    )
    assert result.runtime_version == "0.1.27"
    assert len(executor.commands) == 1
    call = executor.commands[0]
    assert call["sudo"] is True
    assert call["timeout"] == deploy.INSTALL_TIMEOUT_S
    cmd = call["cmd"]
    assert cmd[0] == "bash"
    assert cmd[1].endswith("/install.sh")
    assert cmd[2] == "--project"
    assert cmd[3].endswith("/deploy-fixture")
    assert "--no-start" in cmd
    assert "--prefix" not in cmd  # default prefix omitted


def test_deploy_local_passes_custom_prefix(project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor()
    deploy.deploy_local(
        project_dir=project_dir,
        project_name="deploy-fixture",
        prefix=pathlib.Path("/srv/anolis"),
        executor=executor,
    )
    cmd = executor.commands[0]["cmd"]
    assert "--prefix" in cmd
    assert cmd[cmd.index("--prefix") + 1] == "/srv/anolis"


def test_deploy_local_threads_with_telemetry_export(project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Telemetry-export provisioning is delegated to install.sh (anolishq/anolis#137);
    # workbench requests it via the flag, and only when asked.
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor()
    deploy.deploy_local(
        project_dir=project_dir,
        project_name="deploy-fixture",
        with_telemetry_export=True,
        executor=executor,
    )
    assert "--with-telemetry-export" in executor.commands[0]["cmd"]


def test_deploy_local_omits_telemetry_flag_by_default(
    project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor()
    deploy.deploy_local(
        project_dir=project_dir,
        project_name="deploy-fixture",
        executor=executor,
    )
    assert "--with-telemetry-export" not in executor.commands[0]["cmd"]


def test_deploy_local_threads_with_observability(project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Observability is installed natively on the target by install.sh
    # (anolishq/anolis#162); workbench requests it via the flag, and only when asked.
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor()
    deploy.deploy_local(
        project_dir=project_dir,
        project_name="deploy-fixture",
        with_observability=True,
        executor=executor,
    )
    assert "--with-observability" in executor.commands[0]["cmd"]


def test_deploy_local_omits_observability_flag_by_default(
    project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor()
    deploy.deploy_local(
        project_dir=project_dir,
        project_name="deploy-fixture",
        executor=executor,
    )
    assert "--with-observability" not in executor.commands[0]["cmd"]


def test_deploy_remote_threads_with_observability(project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor()
    deploy.deploy_remote(
        executor=executor,
        project_dir=project_dir,
        project_name="deploy-fixture",
        with_observability=True,
    )
    assert "--with-observability" in executor.commands[-1]["cmd"]


def test_deploy_local_raises_on_install_failure(project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor(returncode=1)
    with pytest.raises(deploy.DeployError, match="install.sh failed"):
        deploy.deploy_local(
            project_dir=project_dir,
            project_name="deploy-fixture",
            executor=executor,
        )


def test_deploy_remote_pushes_config_and_runs(project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor()
    result = deploy.deploy_remote(
        executor=executor,
        project_dir=project_dir,
        project_name="deploy-fixture",
    )
    assert result.runtime_version == "0.1.27"
    pushed = set(executor.files)
    assert "/tmp/anolis-deploy/deploy-fixture/machine-profile.yaml" in pushed
    assert "/tmp/anolis-deploy/deploy-fixture/config/anolis-runtime.manual.yaml" in pushed
    assert "/tmp/anolis-deploy/deploy-fixture/config/provider-sim.sim0.yaml" in pushed
    assert "/tmp/anolis-deploy/deploy-fixture/behaviors/local.xml" in pushed
    assert "/tmp/anolis-deploy/install.sh" in pushed
    call = executor.commands[-1]
    assert call["sudo"] is True
    assert call["cmd"][:4] == [
        "bash",
        "/tmp/anolis-deploy/install.sh",
        "--project",
        "/tmp/anolis-deploy/deploy-fixture",
    ]


def test_deploy_passes_the_chosen_variant_to_install_sh(
    project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor()
    deploy.deploy_remote(executor=executor, project_dir=project_dir, project_name="deploy-fixture", variant="manual")
    cmd = executor.commands[-1]["cmd"]
    assert cmd[cmd.index("--variant") + 1] == "manual"


# ---------------------------------------------------------------------------
# run_rollback
# ---------------------------------------------------------------------------


def test_run_rollback_stages_and_invokes_install_sh(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor()
    output = deploy.run_rollback(executor)
    assert output == "ok"
    assert "/tmp/anolis-deploy/install.sh" in executor.files
    call = executor.commands[-1]
    assert call["sudo"] is True
    assert call["cmd"] == ["bash", "/tmp/anolis-deploy/install.sh", "--rollback"]


def test_run_rollback_passes_custom_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor()
    deploy.run_rollback(executor, prefix=pathlib.Path("/srv/anolis"))
    cmd = executor.commands[-1]["cmd"]
    assert cmd[cmd.index("--prefix") + 1] == "/srv/anolis"


def test_run_rollback_raises_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor(returncode=1)
    with pytest.raises(deploy.DeployError, match="--rollback failed"):
        deploy.run_rollback(executor)


def test_run_rollback_raises_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(releases, "_RELEASE_CACHE", {"anolishq/anolis": None})
    with pytest.raises(deploy.DeployError, match="latest anolis release"):
        deploy.run_rollback(RecordingExecutor())


# ---------------------------------------------------------------------------
# stage_bundle
# ---------------------------------------------------------------------------


def test_stage_bundle_invokes_stage_and_returns_tarball(
    project_dir: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch)
    out_dir = tmp_path / "bundles"
    recorded: list[list[str]] = []

    class _StagingExecutor(RecordingExecutor):
        def run(self, cmd, *, input=None, sudo=False, timeout=None):
            recorded.append(list(cmd))
            (out_dir / "anolis-deploy-fixture-0.1.27-arm64.tar.gz").write_bytes(b"tar")
            return RunResult(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(deploy, "LocalExecutor", _StagingExecutor)
    tarball = deploy.stage_bundle(
        project_dir=project_dir,
        project_name="deploy-fixture",
        out_dir=out_dir,
        arch="arm64",
    )
    assert tarball.name == "anolis-deploy-fixture-0.1.27-arm64.tar.gz"
    cmd = recorded[0]
    assert cmd[0] == "bash"
    assert "--stage" in cmd and "--project" in cmd
    assert cmd[cmd.index("--arch") + 1] == "arm64"


def test_stage_bundle_raises_when_no_tarball(
    project_dir: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch)

    class _NoopExecutor(RecordingExecutor):
        pass

    monkeypatch.setattr(deploy, "LocalExecutor", _NoopExecutor)
    with pytest.raises(deploy.DeployError, match="produced no bundle"):
        deploy.stage_bundle(
            project_dir=project_dir,
            project_name="deploy-fixture",
            out_dir=tmp_path / "bundles",
        )


def test_materialize_refuses_when_tokens_name_another_project(
    project_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """install.sh rewrites `../anolis-projects/projects/<X>/` using the TOKEN's
    own <X>, but installs under the directory it is handed. When they disagree
    the install SUCCEEDS and the rig is broken — configs resolve to a path
    nothing was written to — so it has to be caught here."""
    variant = project_dir / canonical.variant_relpath(canonical.MANUAL_VARIANT)
    variant.write_text(
        variant.read_text(encoding="utf-8").replace("/projects/deploy-fixture/", "/projects/some-other-rig/"),
        encoding="utf-8",
    )
    with pytest.raises(deploy.DeployError, match="some-other-rig"):
        deploy.materialize_project_dir(project_dir, tmp_path / "out")


def test_stage_bundle_does_not_pass_variant_to_install_sh(
    project_dir: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install.sh's bundle assembly always stages `manual` and ignores the flag.
    Passing it would tell the operator they had chosen something they had not."""
    _stub_fetch(monkeypatch)
    out_dir = tmp_path / "bundles"
    recorded: list[list[str]] = []

    class _StagingExecutor(RecordingExecutor):
        def run(self, cmd, *, input=None, sudo=False, timeout=None):
            recorded.append(list(cmd))
            (out_dir / "anolis-deploy-fixture-0.1.27-arm64.tar.gz").write_bytes(b"tar")
            return RunResult(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(deploy, "LocalExecutor", _StagingExecutor)
    deploy.stage_bundle(
        project_dir=project_dir,
        project_name="deploy-fixture",
        out_dir=out_dir,
        arch="arm64",
        variant="automation",
    )
    assert "--variant" not in recorded[0]


# ---------------------------------------------------------------------------
# Host preflight and a pending reboot (#417)
# ---------------------------------------------------------------------------

# install.sh v0.1.43's stdout, verbatim with its colour codes, from a run on a
# Raspberry Pi 4 with the service user taken out of the bus's group.
_PREFLIGHT_UNMET_STDOUT = (
    "\x1b[0;32m✓ root check\x1b[0m\n\nAnolis Provisioning\n━━━━━━━━━━━━━━━━━━━\n\n"
    "\x1b[0;32m✓ install binaries: anolis-provider-ezo anolis-provider-bread anolis-runtime \x1b[0m\n"
    "\x1b[0;33m→ config (providers): 2 preserved, 0 written\x1b[0m\n"
    "\x1b[0;32m✓ host preflight: anolis-runtime runs as anolis\x1b[0m\n"
    "\x1b[0;31m✗ host preflight: bread0: host requirements unmet\x1b[0m\n"
    "    i2c.bus_access (unmet): anolis cannot open /dev/i2c-1 read-write (group 'i2c', mode 0660)\n"
    "      fix: add anolis to group 'i2c', the group that owns the node on this host, and start a new login"
    " or restart the service\n"
    "\x1b[0;31m✗ host preflight: ezo0: host requirements unmet\x1b[0m\n"
    "    i2c.bus_access (unmet): anolis cannot open /dev/i2c-1 read-write (group 'i2c', mode 0660)\n"
    "      fix: add anolis to group 'i2c', the group that owns the node on this host, and start a new login"
    " or restart the service\n"
    "  Fix the host (the project's host prep, then any reboot it asks for) and re-run,\n"
    "  or pass --allow-unmet-host to install now and fix the host after.\n"
    "\x1b[0;31m✗ host preflight: requirements unmet\x1b[0m\n"
)

# The same release's stdout around a passing preflight.
_PREFLIGHT_MET_STDOUT = (
    "\x1b[0;33m→ config (providers): 2 preserved, 0 written\x1b[0m\n"
    "\x1b[0;32m✓ host preflight: anolis-runtime runs as anolis\x1b[0m\n"
    "\x1b[0;32m✓ host preflight: bread0: host requirements met\x1b[0m\n"
    "\x1b[0;32m✓ host preflight: ezo0: host requirements met\x1b[0m\n"
    "\x1b[0;32m✓ manifest: written\x1b[0m\n"
    "\x1b[0;32m✓ health: runtime responding (v0.1.43)\x1b[0m\n"
)

# Composed from install.sh's own --allow-unmet-host branch (phase_host_preflight).
_PREFLIGHT_ALLOWED_STDOUT = (
    "\x1b[0;32m✓ host preflight: anolis-runtime runs as anolis\x1b[0m\n"
    "\x1b[0;31m✗ host preflight: bread0: host requirements unmet\x1b[0m\n"
    "    i2c.bus_clock (unmet): i2c-1 is configured for 100000 Hz, above this config's maximum of 50000 Hz\n"
    "      fix: set i2c-1's clock to at most 50000 Hz in the platform's configuration\n"
    "\x1b[0;33m⚠ host preflight: continuing (--allow-unmet-host); those providers start up not ready\x1b[0m\n"
    "  Once the host is fixed, restart the service (or reboot): providers check the host only at startup.\n"
    "\x1b[0;32m✓ manifest: written\x1b[0m\n"
)


class _ScriptedExecutor(RecordingExecutor):
    """install.sh's exit code and stdout as given; /run/reboot-required as given."""

    def __init__(self, returncode: int, stdout: str, *, reboot: bool = False) -> None:
        super().__init__(returncode)
        self.stdout = stdout
        self.reboot = reboot

    def run(self, cmd, *, input=None, sudo=False, timeout=None):
        self.commands.append({"cmd": list(cmd), "sudo": sudo, "timeout": timeout})
        return RunResult(returncode=self.returncode, stdout=self.stdout, stderr="")

    def file_exists(self, path):
        return self.reboot and path == deploy.REBOOT_REQUIRED_PATH


def test_host_preflight_block_is_the_whole_preflight_without_colour() -> None:
    block = deploy.host_preflight_block(_PREFLIGHT_UNMET_STDOUT)
    assert block[0] == "✓ host preflight: anolis-runtime runs as anolis"
    assert block[-1] == "✗ host preflight: requirements unmet"
    assert sum("fix: add anolis to group 'i2c'" in line for line in block) == 2
    assert not any("\x1b" in line for line in block)
    assert not any("install binaries" in line for line in block)


def test_host_preflight_block_stops_at_the_next_phase() -> None:
    block = deploy.host_preflight_block(_PREFLIGHT_MET_STDOUT)
    assert block == [
        "✓ host preflight: anolis-runtime runs as anolis",
        "✓ host preflight: bread0: host requirements met",
        "✓ host preflight: ezo0: host requirements met",
    ]
    assert deploy.host_preflight_block("✓ verify: all checksums pass\n") == []


def test_failed_preflight_reports_every_provider_and_its_fix(
    project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two providers' unmet requirements are 9 lines; the old last-15-lines tail
    also carried colour codes and whatever stderr came after."""
    _stub_fetch(monkeypatch)
    executor = _ScriptedExecutor(1, _PREFLIGHT_UNMET_STDOUT)
    with pytest.raises(deploy.DeployError) as info:
        deploy.deploy_local(project_dir=project_dir, project_name="deploy-fixture", executor=executor)
    message = str(info.value)
    assert "bread0: host requirements unmet" in message
    assert "ezo0: host requirements unmet" in message
    assert message.count("fix: add anolis to group 'i2c'") == 2
    assert "--allow-unmet-host" in message
    assert "\x1b" not in message
    assert "install binaries" not in message
    assert info.value.host_preflight[-1] == "✗ host preflight: requirements unmet"
    assert info.value.reboot_pending is False


def test_failure_before_the_preflight_keeps_the_output_tail(
    project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch)
    stdout = "\x1b[0;32m✓ root check\x1b[0m\n\x1b[0;31m✗ verify: checksum mismatch for anolis-runtime\x1b[0m\n"
    executor = _ScriptedExecutor(1, stdout)
    with pytest.raises(deploy.DeployError, match="checksum mismatch") as info:
        deploy.deploy_local(project_dir=project_dir, project_name="deploy-fixture", executor=executor)
    assert info.value.host_preflight == []
    assert "\x1b" not in str(info.value)


def test_allow_unmet_host_is_an_explicit_opt_in(project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)
    executor = RecordingExecutor()
    deploy.deploy_local(project_dir=project_dir, project_name="deploy-fixture", executor=executor)
    assert "--allow-unmet-host" not in executor.commands[0]["cmd"]

    executor = RecordingExecutor()
    deploy.deploy_remote(
        executor=executor, project_dir=project_dir, project_name="deploy-fixture", allow_unmet_host=True
    )
    assert "--allow-unmet-host" in executor.commands[-1]["cmd"]


def test_success_carries_the_preflight_only_when_it_needs_attention(
    project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch)
    met = deploy.deploy_local(
        project_dir=project_dir, project_name="deploy-fixture", executor=_ScriptedExecutor(0, _PREFLIGHT_MET_STDOUT)
    )
    assert met.host_preflight == []

    allowed = deploy.deploy_local(
        project_dir=project_dir,
        project_name="deploy-fixture",
        allow_unmet_host=True,
        executor=_ScriptedExecutor(0, _PREFLIGHT_ALLOWED_STDOUT),
    )
    assert any("i2c.bus_clock (unmet)" in line for line in allowed.host_preflight)
    assert any(line.startswith("⚠ host preflight: continuing") for line in allowed.host_preflight)


def test_reboot_pending_is_reported_on_success_and_failure(
    project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch)
    ok = deploy.deploy_remote(
        executor=_ScriptedExecutor(0, _PREFLIGHT_MET_STDOUT, reboot=True),
        project_dir=project_dir,
        project_name="deploy-fixture",
    )
    assert ok.reboot_pending is True
    assert (
        deploy.deploy_local(
            project_dir=project_dir, project_name="deploy-fixture", executor=_ScriptedExecutor(0, _PREFLIGHT_MET_STDOUT)
        ).reboot_pending
        is False
    )

    with pytest.raises(deploy.DeployError, match="reboot is pending") as info:
        deploy.deploy_local(
            project_dir=project_dir,
            project_name="deploy-fixture",
            executor=_ScriptedExecutor(1, _PREFLIGHT_UNMET_STDOUT, reboot=True),
        )
    assert info.value.reboot_pending is True


def test_a_target_that_cannot_be_asked_is_not_reported_as_pending(
    project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch)

    class _Unreachable(_ScriptedExecutor):
        def file_exists(self, path):
            raise OSError("connection reset")

    with pytest.raises(deploy.DeployError, match="ezo0: host requirements unmet") as info:
        deploy.deploy_local(
            project_dir=project_dir, project_name="deploy-fixture", executor=_Unreachable(1, _PREFLIGHT_UNMET_STDOUT)
        )
    assert info.value.reboot_pending is False


# ---------------------------------------------------------------------------
# An existing runtime config is kept, and --variant is not applied (#354)
# ---------------------------------------------------------------------------

# Composed from install.sh's own phase_config_runtime and
# phase_config_providers messages, colour codes included, for a redeploy with
# --variant over an installed machine.
_PRESERVED_STDOUT = (
    "\x1b[0;33m→ backup: incoming binaries identical to installed — .prev preserved for rollback\x1b[0m\n"
    "\x1b[0;32m✓ install binaries: anolis-runtime \x1b[0m\n"
    "\x1b[0;33m→ config (runtime): /opt/anolis/config/runtime.yaml exists (preserved)\x1b[0m\n"
    "\x1b[0;33m⚠ config (runtime): --variant 'manual' NOT applied; "
    "/opt/anolis/config/runtime.yaml is preserved.\x1b[0m\n"
    "  To switch variants, remove /opt/anolis/config/runtime.yaml and re-run, or edit it directly.\n"
    "\x1b[0;33m→ auth: existing API token preserved (/etc/anolis/runtime.env)\x1b[0m\n"
    "\x1b[0;33m→ config (providers): 2 preserved, 0 written\x1b[0m\n"
    "\x1b[0;33m→ telemetry-export: venv present (preserved)\x1b[0m\n"
    "\x1b[0;33m→ telemetry-export: config exists (preserved)\x1b[0m\n"
    "\x1b[0;32m✓ manifest: written\x1b[0m\n"
)

# install.sh's own way to switch variants: remove runtime.yaml and re-run. The
# runtime config is written fresh; the token, provider configs and binaries are
# still kept.
_VARIANT_SWITCH_STDOUT = (
    "\x1b[0;33m→ backup: incoming binaries identical to installed — .prev preserved for rollback\x1b[0m\n"
    "\x1b[0;32m✓ config (runtime): installed variant 'automation'\x1b[0m\n"
    "\x1b[0;33m→ auth: existing API token preserved (/etc/anolis/runtime.env)\x1b[0m\n"
    "\x1b[0;33m→ config (providers): 2 preserved, 0 written\x1b[0m\n"
)

# The same phases on a fresh install.
_FRESH_STDOUT = (
    "\x1b[0;32m✓ config (runtime): installed variant 'manual'\x1b[0m\n"
    "\x1b[0;32m✓ config (providers): 2 written\x1b[0m\n"
)


class _InstalledExecutor(_ScriptedExecutor):
    """A target that already has <prefix>/config/runtime.yaml."""

    def file_exists(self, path):
        return path == "/opt/anolis/config/runtime.yaml"


def test_preserved_lines_name_what_install_sh_kept() -> None:
    assert deploy.preserved_lines(_PRESERVED_STDOUT) == [
        "→ config (runtime): /opt/anolis/config/runtime.yaml exists (preserved)",
        "⚠ config (runtime): --variant 'manual' NOT applied; /opt/anolis/config/runtime.yaml is preserved.",
        "→ config (providers): 2 preserved, 0 written",
        "→ telemetry-export: config exists (preserved)",
    ]
    assert deploy.preserved_lines(_FRESH_STDOUT) == []


def test_a_variant_switch_does_not_report_the_runtime_config_as_kept(
    project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The token and the backup are kept on every redeploy; they are not the
    runtime config, and a fresh runtime.yaml must not read as kept."""
    _stub_fetch(monkeypatch)
    result = deploy.deploy_local(
        project_dir=project_dir,
        project_name="deploy-fixture",
        variant="manual",
        executor=_ScriptedExecutor(0, _VARIANT_SWITCH_STDOUT),
    )
    assert result.runtime_config_kept is False
    assert result.preserved == ["→ config (providers): 2 preserved, 0 written"]


def test_a_redeploy_says_before_install_sh_runs_that_the_config_is_kept(
    project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch)
    executor = _InstalledExecutor(0, _PRESERVED_STDOUT)
    seen: list[tuple[str, str, int]] = []

    result = deploy.deploy_remote(
        executor=executor,
        project_dir=project_dir,
        project_name="deploy-fixture",
        variant="manual",
        progress_callback=lambda step, detail: seen.append((step, detail, len(executor.commands))),
    )

    notices = [(detail, ran) for step, detail, ran in seen if step == "existing-config"]
    assert notices == [
        (
            "/opt/anolis/config/runtime.yaml exists on the target and is kept: install.sh does not replace "
            "it with this project's runtime config, and variant 'manual' is not applied.",
            0,
        )
    ]
    assert result.preserved[1].startswith("⚠ config (runtime): --variant 'manual' NOT applied")
    assert result.runtime_config_kept is True


def test_a_fresh_install_carries_no_notice_and_nothing_preserved(
    project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch)
    seen: list[str] = []
    result = deploy.deploy_local(
        project_dir=project_dir,
        project_name="deploy-fixture",
        executor=_ScriptedExecutor(0, _FRESH_STDOUT),
        progress_callback=lambda step, detail: seen.append(step),
    )
    assert "existing-config" not in seen
    assert result.preserved == []


def test_the_notice_names_no_variant_when_none_was_chosen(
    project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch)
    seen: list[tuple[str, str]] = []
    deploy.deploy_local(
        project_dir=project_dir,
        project_name="deploy-fixture",
        executor=_InstalledExecutor(0, _PRESERVED_STDOUT),
        progress_callback=lambda step, detail: seen.append((step, detail)),
    )
    notice = next(detail for step, detail in seen if step == "existing-config")
    assert notice.endswith("this project's runtime config.")
