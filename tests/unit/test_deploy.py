"""Unit tests for install.sh deployment delegation."""

from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any, Callable

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


_RELEASE = "https://github.com/anolishq/anolis/releases/download/v0.1.27"
_SCRIPT = b"#!/usr/bin/env bash\n"
_UNVERIFIED = "WARNING: install.sh not verified: anolis v0.1.27 predates install.sh in SHA256SUMS"


def _serve_release(monkeypatch: pytest.MonkeyPatch, sums: _FakeResponse | Exception) -> list[str]:
    """install.sh at v0.1.27, and ``sums`` for its SHA256SUMS."""
    urls: list[str] = []

    def _get(url, **kwargs):
        urls.append(url)
        if url == f"{_RELEASE}/install.sh":
            return _FakeResponse(200, _SCRIPT)
        assert url == f"{_RELEASE}/SHA256SUMS"
        if isinstance(sums, Exception):
            raise sums
        return sums

    monkeypatch.setattr(deploy.requests, "get", _get)
    return urls


def _sums(digest: str) -> _FakeResponse:
    return _FakeResponse(200, f"{'0' * 64}  anolis-0.1.27-linux-arm64.tar.gz\n{digest}  install.sh\n".encode())


def test_fetch_install_sh_downloads_pinned_release(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    urls = _serve_release(monkeypatch, _sums(hashlib.sha256(_SCRIPT).hexdigest()))
    path, warning = deploy.fetch_install_sh("0.1.27", tmp_path)
    assert path.read_bytes() == _SCRIPT
    assert warning is None
    assert urls == [f"{_RELEASE}/install.sh", f"{_RELEASE}/SHA256SUMS"]


def test_fetch_install_sh_refuses_a_mismatch(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_release(monkeypatch, _sums(hashlib.sha256(b"something else").hexdigest()))
    with pytest.raises(deploy.DeployError, match="does not match its SHA256SUMS"):
        deploy.fetch_install_sh("0.1.27", tmp_path)
    assert not (tmp_path / "install.sh").exists()


@pytest.mark.parametrize(
    "sums",
    [
        pytest.param(_FakeResponse(200, f"{'0' * 64}  anolis-0.1.27-linux-arm64.tar.gz\n".encode()), id="no-line"),
        pytest.param(_FakeResponse(404), id="404"),
    ],
)
def test_fetch_install_sh_warns_when_the_release_cannot_check_it(
    sums: _FakeResponse, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve_release(monkeypatch, sums)
    path, warning = deploy.fetch_install_sh("0.1.27", tmp_path)
    assert path.read_bytes() == _SCRIPT
    assert warning == _UNVERIFIED


@pytest.mark.parametrize(
    "sums, reason",
    [
        pytest.param(requests.ConnectionError("connection reset"), "connection reset", id="fetch-error"),
        pytest.param(_FakeResponse(500), "HTTP 500", id="http-500"),
    ],
)
def test_fetch_install_sh_refuses_when_sha256sums_cannot_be_fetched(
    sums: _FakeResponse | Exception, reason: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve_release(monkeypatch, sums)
    with pytest.raises(deploy.DeployError, match=f"could not fetch SHA256SUMS.*{reason}.*refusing"):
        deploy.fetch_install_sh("0.1.27", tmp_path)
    assert not (tmp_path / "install.sh").exists()


def test_fetch_install_sh_raises_on_http_error(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deploy.requests, "get", lambda *a, **k: _FakeResponse(404))
    with pytest.raises(deploy.DeployError, match="HTTP 404"):
        deploy.fetch_install_sh("0.1.27", tmp_path)


# ---------------------------------------------------------------------------
# deploy_local / deploy_remote
# ---------------------------------------------------------------------------


def _stub_fetch(monkeypatch: pytest.MonkeyPatch, warning: str | None = None) -> None:
    def _fake_fetch(version: str, dest: pathlib.Path) -> tuple[pathlib.Path, str | None]:
        path = dest / "install.sh"
        path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        return path, warning

    monkeypatch.setattr(deploy, "fetch_install_sh", _fake_fetch)


# ---------------------------------------------------------------------------
# The installed runtime, measured before anything is fetched (#567)
# ---------------------------------------------------------------------------

_RUNTIME_BIN = "/opt/anolis/bin/anolis-runtime"


class InstalledRuntimeExecutor(RecordingExecutor):
    """A target whose prefix holds a runtime answering --version with stdout."""

    def __init__(self, stdout: str | None, *, returncode: int = 0, exists_error: Exception | None = None) -> None:
        super().__init__()
        self.version_stdout = stdout
        self.version_returncode = returncode
        self.exists_error = exists_error

    def file_exists(self, path):
        if self.exists_error is not None:
            raise self.exists_error
        return path == _RUNTIME_BIN and self.version_stdout is not None

    def run(self, cmd, *, input=None, sudo=False, timeout=None):
        if cmd[0] == _RUNTIME_BIN:
            assert list(cmd) == [_RUNTIME_BIN, "--version"]
            assert sudo is False
            self.commands.append({"cmd": list(cmd), "sudo": sudo, "timeout": timeout})
            return RunResult(returncode=self.version_returncode, stdout=self.version_stdout or "", stderr="")
        return super().run(cmd, input=input, sudo=sudo, timeout=timeout)


def _pin_runtime(project_dir: pathlib.Path, version: str) -> None:
    path = project_dir / "machine-profile.yaml"
    profile = yaml.safe_load(path.read_text(encoding="utf-8"))
    profile["components"]["runtime"]["version"] = version
    path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")


def _refuse_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fetch(version: str, dest: pathlib.Path) -> tuple[pathlib.Path, str | None]:
        raise AssertionError("install.sh was fetched")

    monkeypatch.setattr(deploy, "fetch_install_sh", _fetch)


def _deploy(remote: bool, project_dir: pathlib.Path, executor: Executor, events: list[tuple[str, str]]):
    deploy_fn = deploy.deploy_remote if remote else deploy.deploy_local
    return deploy_fn(
        project_dir=project_dir,
        project_name="deploy-fixture",
        executor=executor,
        progress_callback=lambda step, detail: events.append((step, detail)),
    )


@pytest.mark.parametrize("remote", [False, True], ids=["local", "remote"])
def test_deploy_refuses_a_pre_guard_pin_below_the_installed_runtime(
    remote: bool, project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _refuse_fetch(monkeypatch)
    executor = InstalledRuntimeExecutor("anolis-runtime 0.1.50\n")
    with pytest.raises(deploy.DeployError, match=r"runs runtime 0\.1\.50.*pin 0\.1\.27.*predates the downgrade guard"):
        _deploy(remote, project_dir, executor, [])
    # Only the version probe ran: install.sh was neither fetched nor run, and
    # nothing was pushed.
    assert executor.commands == [{"cmd": [_RUNTIME_BIN, "--version"], "sudo": False, "timeout": 30}]
    assert executor.files == {}


@pytest.mark.parametrize("remote", [False, True], ids=["local", "remote"])
def test_deploy_warns_on_a_guarded_pin_below_the_installed_runtime(
    remote: bool, project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin_runtime(project_dir, "0.1.46")
    _stub_fetch(monkeypatch)
    executor = InstalledRuntimeExecutor("anolis-runtime 0.1.50\n")
    events: list[tuple[str, str]] = []
    result = _deploy(remote, project_dir, executor, events)
    assert result.runtime_version == "0.1.46"
    warnings = [detail for step, detail in events if step == "downgrade"]
    assert len(warnings) == 1
    assert "runs runtime 0.1.50, newer than this project's pin 0.1.46" in warnings[0]
    # install.sh 0.1.46 refuses the downgrade itself; it still runs.
    assert executor.commands[-1]["cmd"][0] == "bash"


@pytest.mark.parametrize("remote", [False, True], ids=["local", "remote"])
@pytest.mark.parametrize("installed", ["anolis-runtime 0.1.27\n", "anolis-runtime 0.1.9\n"], ids=["same", "older"])
def test_deploy_proceeds_quietly_when_the_pin_is_not_older(
    remote: bool, installed: str, project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 0.1.9 < 0.1.27 numerically; as strings it would sort above.
    _stub_fetch(monkeypatch)
    executor = InstalledRuntimeExecutor(installed)
    events: list[tuple[str, str]] = []
    _deploy(remote, project_dir, executor, events)
    assert not [step for step, _ in events if step == "downgrade"]
    assert executor.commands[0]["cmd"] == [_RUNTIME_BIN, "--version"]
    assert executor.commands[-1]["cmd"][0] == "bash"


def test_deploy_refuses_a_pin_that_is_not_xyz_over_an_installed_runtime(
    project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The machine-profile schema takes any non-empty version string.
    _pin_runtime(project_dir, "latest")
    _refuse_fetch(monkeypatch)
    executor = InstalledRuntimeExecutor("anolis-runtime 0.1.50\n")
    with pytest.raises(deploy.DeployError, match=r"pin 'latest' is not X\.Y\.Z\. Nothing was deployed\."):
        _deploy(False, project_dir, executor, [])
    assert executor.files == {}


@pytest.mark.parametrize("remote", [False, True], ids=["local", "remote"])
def test_deploy_treats_a_target_with_no_runtime_as_fresh(
    remote: bool, project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch)
    executor = InstalledRuntimeExecutor(None)
    events: list[tuple[str, str]] = []
    _deploy(remote, project_dir, executor, events)
    assert not [c for c in executor.commands if c["cmd"][0] == _RUNTIME_BIN]
    assert not [step for step, _ in events if step == "downgrade"]
    assert executor.commands[-1]["cmd"][0] == "bash"


@pytest.mark.parametrize("remote", [False, True], ids=["local", "remote"])
@pytest.mark.parametrize(
    ("executor", "reason"),
    [
        (InstalledRuntimeExecutor("", returncode=126), r"reports no version \(exit 126: no output\)"),
        (InstalledRuntimeExecutor("anolis-runtime dev\n"), r"reports no version \(exit 0: anolis-runtime dev\)"),
        (InstalledRuntimeExecutor(None, exists_error=OSError("connection reset")), r"could not read.*connection reset"),
    ],
    ids=["will-not-run", "no-xyz", "cannot-check"],
)
def test_deploy_refuses_a_runtime_whose_version_cannot_be_read(
    remote: bool,
    executor: InstalledRuntimeExecutor,
    reason: str,
    project_dir: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _refuse_fetch(monkeypatch)
    with pytest.raises(deploy.DeployError, match=f"{reason}.*nothing was deployed"):
        _deploy(remote, project_dir, executor, [])
    assert not [c for c in executor.commands if c["cmd"][0] == "bash"]
    assert executor.files == {}


# ---------------------------------------------------------------------------
# check_target: the pins beside the target's installed versions (#567)
# ---------------------------------------------------------------------------

_MANIFEST = "/opt/anolis/manifest.json"


def _manifest(runtime: str, providers: dict[str, str], optional: dict[str, str] | None = None) -> str:
    components: dict[str, Any] = {
        "runtime": {"version": runtime},
        "providers": {n: {"version": v} for n, v in providers.items()},
    }
    if optional is not None:
        # install.sh writes components.optional only when a pin is declared.
        components["optional"] = {n: {"version": v} for n, v in optional.items()}
    return json.dumps({"schema_version": 1, "version": runtime, "components": components}, indent=2)


def _pin_optional(project_dir: pathlib.Path, name: str, version: str) -> None:
    path = project_dir / "machine-profile.yaml"
    profile = yaml.safe_load(path.read_text(encoding="utf-8"))
    profile["components"]["optional"] = {name: {"package": "anolis-telemetry-export", "version": version}}
    path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")


class TargetExecutor(InstalledRuntimeExecutor):
    """InstalledRuntimeExecutor whose prefix also holds manifest_text, if given."""

    def __init__(self, stdout: str | None, manifest_text: str | None = None, **kwargs: Any) -> None:
        super().__init__(stdout, **kwargs)
        self.manifest_text = manifest_text

    def file_exists(self, path):
        if path == _MANIFEST:
            return self.manifest_text is not None
        return super().file_exists(path)

    def run(self, cmd, *, input=None, sudo=False, timeout=None):
        if list(cmd) == ["cat", _MANIFEST]:
            self.commands.append({"cmd": list(cmd), "sudo": sudo, "timeout": timeout})
            return RunResult(returncode=0, stdout=self.manifest_text or "", stderr="")
        return super().run(cmd, input=input, sudo=sudo, timeout=timeout)


def _read_only(executor: RecordingExecutor) -> None:
    # Only the version probe and the manifest read: nothing written, no sudo.
    assert {tuple(c["cmd"]) for c in executor.commands} <= {(_RUNTIME_BIN, "--version"), ("cat", _MANIFEST)}
    assert not [c for c in executor.commands if c["sudo"]]
    assert executor.files == {} and executor.mkdirs == []


@pytest.mark.parametrize(
    ("pin", "verdict", "says"),
    [
        ("0.1.27", "refuse", "predates the downgrade guard"),
        ("0.1.46", "downgrade", "refuses to downgrade it"),
        ("0.1.50", "ok", ""),
        ("0.1.52", "ok", ""),
    ],
)
def test_check_target_gives_the_runtime_verdict_deploy_would(
    pin: str, verdict: str, says: str, project_dir: pathlib.Path
) -> None:
    _pin_runtime(project_dir, pin)
    executor = TargetExecutor("anolis-runtime 0.1.50\n", _manifest("0.1.50", {"sim": "0.2.5"}))
    check = deploy.check_target(executor, project_dir=project_dir)
    assert (check.runtime.name, check.runtime.pinned, check.runtime.installed) == ("runtime", pin, "0.1.50")
    assert check.runtime.verdict == verdict
    assert says in check.runtime.message
    _read_only(executor)


def test_check_target_and_deploy_refuse_with_the_same_words(
    project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _refuse_fetch(monkeypatch)
    check = deploy.check_target(TargetExecutor("anolis-runtime 0.1.50\n"), project_dir=project_dir)
    with pytest.raises(deploy.DeployError) as info:
        deploy.deploy_local(project_dir=project_dir, project_name="deploy-fixture", executor=TargetExecutor("0.1.50"))
    assert str(info.value) == f"{check.runtime.message} Nothing was deployed."


@pytest.mark.parametrize(
    ("installed", "verdict"),
    [("0.2.5", "ok"), ("0.2.10", "older"), ("0.2.1", "ok")],
    ids=["same", "newer-installed", "older-installed"],
)
def test_check_target_compares_provider_pins_with_the_manifest(
    installed: str, verdict: str, project_dir: pathlib.Path
) -> None:
    # The fixture pins sim 0.2.5; 0.2.10 is newer numerically.
    executor = TargetExecutor("anolis-runtime 0.1.27\n", _manifest("0.1.27", {"sim": installed}))
    check = deploy.check_target(executor, project_dir=project_dir)
    assert check.manifest == "ok"
    assert [(p.name, p.pinned, p.installed, p.verdict) for p in check.providers] == [
        ("sim", "0.2.5", installed, verdict)
    ]


def test_check_target_flags_a_manifest_from_another_runtime(project_dir: pathlib.Path) -> None:
    executor = TargetExecutor("anolis-runtime 0.1.50\n", _manifest("0.1.43", {"sim": "0.2.5"}))
    check = deploy.check_target(executor, project_dir=project_dir)
    assert check.manifest == "stale"
    assert check.providers[0].installed == "0.2.5"


@pytest.mark.parametrize(
    ("manifest_text", "state"),
    [(None, "missing"), ("not json", "unreadable"), ("[]", "unreadable")],
    ids=["missing", "not-json", "not-an-object"],
)
def test_check_target_without_a_manifest_leaves_providers_unknown(
    manifest_text: str | None, state: str, project_dir: pathlib.Path
) -> None:
    executor = TargetExecutor("anolis-runtime 0.1.27\n", manifest_text)
    check = deploy.check_target(executor, project_dir=project_dir)
    assert check.manifest == state
    assert [(p.name, p.installed, p.verdict) for p in check.providers] == [("sim", None, "unknown")]
    assert check.runtime.verdict == "ok"


def test_check_target_on_a_fresh_target(project_dir: pathlib.Path) -> None:
    executor = TargetExecutor(None)
    check = deploy.check_target(executor, project_dir=project_dir)
    assert (check.runtime.installed, check.runtime.verdict) == (None, "fresh")
    assert [(p.name, p.verdict) for p in check.providers] == [("sim", "fresh")]
    assert check.manifest == "missing"
    assert executor.commands == []


@pytest.mark.parametrize(
    ("recorded", "installed", "verdict"),
    [
        ({"telemetry_export": "0.1.1"}, "0.1.1", "ok"),
        ({"telemetry_export": "0.1.3"}, "0.1.3", "older"),
        (None, None, "unknown"),
    ],
    ids=["same", "newer-installed", "not-recorded"],
)
def test_check_target_lists_optional_components_beside_the_manifest(
    recorded: dict[str, str] | None, installed: str | None, verdict: str, project_dir: pathlib.Path
) -> None:
    # bioreactor-v1 pins components.optional.telemetry_export 0.1.1 (#620).
    _pin_optional(project_dir, "telemetry_export", "0.1.1")
    executor = TargetExecutor("anolis-runtime 0.1.27\n", _manifest("0.1.27", {"sim": "0.2.5"}, recorded))
    check = deploy.check_target(executor, project_dir=project_dir)
    assert [(c.name, c.pinned, c.installed, c.verdict) for c in check.optional] == [
        ("telemetry_export", "0.1.1", installed, verdict)
    ]
    assert [p.name for p in check.providers] == ["sim"]
    _read_only(executor)


def test_check_target_lists_no_optional_components_when_none_are_pinned(project_dir: pathlib.Path) -> None:
    executor = TargetExecutor(
        "anolis-runtime 0.1.27\n", _manifest("0.1.27", {"sim": "0.2.5"}, {"telemetry_export": "0.1.1"})
    )
    assert deploy.check_target(executor, project_dir=project_dir).optional == []


def test_check_target_on_a_fresh_target_lists_optional_pins_as_not_installed(project_dir: pathlib.Path) -> None:
    _pin_optional(project_dir, "telemetry_export", "0.1.1")
    check = deploy.check_target(TargetExecutor(None), project_dir=project_dir)
    assert [(c.name, c.verdict) for c in check.optional] == [("telemetry_export", "fresh")]


def test_check_target_refuses_a_runtime_whose_version_cannot_be_read(project_dir: pathlib.Path) -> None:
    executor = TargetExecutor("", returncode=126)
    with pytest.raises(deploy.DeployError, match="reports no version"):
        deploy.check_target(executor, project_dir=project_dir)


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


@pytest.mark.parametrize("remote", [False, True], ids=["local", "remote"])
def test_deploy_reports_an_unverified_install_sh(
    remote: bool, project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch, warning=_UNVERIFIED)
    events: list[tuple[str, str]] = []
    deploy_fn = deploy.deploy_remote if remote else deploy.deploy_local
    result = deploy_fn(
        project_dir=project_dir,
        project_name="deploy-fixture",
        executor=RecordingExecutor(),
        progress_callback=lambda step, detail: events.append((step, detail)),
    )
    assert result.install_sh_warning == _UNVERIFIED
    assert ("unverified", _UNVERIFIED) in events


def test_deploy_local_has_no_warning_for_a_verified_install_sh(
    project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch)
    result = deploy.deploy_local(project_dir=project_dir, project_name="deploy-fixture", executor=RecordingExecutor())
    assert result.install_sh_warning is None


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


def _author_influx_token(project_dir: pathlib.Path, token: str) -> None:
    # Imported projects skip save-time validation, so write the file directly.
    path = project_dir / "config" / "anolis-runtime.automation.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["telemetry"] = {"enabled": True, "influxdb": {"token": token}}
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def test_deploy_refuses_an_authored_influx_token(project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)
    _author_influx_token(project_dir, "dev-token")
    executor = RecordingExecutor()
    with pytest.raises(deploy.DeployError, match="INFLUXDB_TOKEN"):
        deploy.deploy_local(project_dir=project_dir, project_name="deploy-fixture", executor=executor)
    assert executor.commands == []


def test_bundle_refuses_an_authored_influx_token(
    project_dir: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch)
    _author_influx_token(project_dir, "dev-token")
    with pytest.raises(deploy.DeployError, match="INFLUXDB_TOKEN"):
        deploy.stage_bundle(project_dir=project_dir, project_name="deploy-fixture", out_dir=tmp_path / "out")


def test_deploy_accepts_an_empty_influx_token(project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch)
    _author_influx_token(project_dir, "")
    executor = RecordingExecutor()
    deploy.deploy_local(project_dir=project_dir, project_name="deploy-fixture", executor=executor)
    assert len(executor.commands) == 1


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


def test_run_rollback_reports_an_unverified_install_sh(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_fetch(monkeypatch, warning=_UNVERIFIED)
    events: list[tuple[str, str]] = []
    deploy.run_rollback(RecordingExecutor(), progress_callback=lambda step, detail: events.append((step, detail)))
    assert events == [("unverified", _UNVERIFIED)]


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


def test_stage_bundle_reports_an_unverified_install_sh(
    project_dir: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_fetch(monkeypatch, warning=_UNVERIFIED)
    out_dir = tmp_path / "bundles"

    class _StagingExecutor(RecordingExecutor):
        def run(self, cmd, *, input=None, sudo=False, timeout=None):
            (out_dir / "anolis-deploy-fixture-0.1.27-arm64.tar.gz").write_bytes(b"tar")
            return RunResult(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(deploy, "LocalExecutor", _StagingExecutor)
    events: list[tuple[str, str]] = []
    deploy.stage_bundle(
        project_dir=project_dir,
        project_name="deploy-fixture",
        out_dir=out_dir,
        arch="arm64",
        progress_callback=lambda step, detail: events.append((step, detail)),
    )
    assert ("unverified", _UNVERIFIED) in events


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

    # The reboot check is advisory. The pre-deploy runtime check is not, so
    # the target answers that one (no runtime installed).
    class _Unreachable(_ScriptedExecutor):
        def file_exists(self, path):
            if path == deploy.REBOOT_REQUIRED_PATH:
                raise OSError("connection reset")
            return False

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


# install.sh's own output, captured from anolis tools/install.sh by sourcing
# it with ANOLIS_INSTALL_SH_NO_MAIN=1 and running phase_summary (and, for
# `offline`, phase_observability with its vendor-repo probe failing). Colour
# codes are kept as install.sh prints them.
_INSTALL_SH_FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "install-sh"


def _install_sh_output(name: str) -> str:
    return (_INSTALL_SH_FIXTURES / f"{name}.txt").read_text(encoding="utf-8")


def test_observability_lines_skip_the_rest_of_the_summary() -> None:
    assert deploy.observability_lines(_install_sh_output("pi-loopback")) == [
        "✓ observability: influxdb bootstrapped (org anolis, bucket anolis, retention 30d)",
        "✓ observability: influxdb (:8086) and grafana (:3000) up",
        "  Grafana:   127.0.0.1:3000 on this machine (admin password: /etc/anolis/observability.env)",
        "    from another machine: ssh -L 3000:127.0.0.1:3000 pi@rig.local, then http://localhost:3000",
        "  InfluxDB:  http://127.0.0.1:8086 (org anolis, bucket anolis, retention 30d)",
    ]


def test_observability_lines_show_grafana_on_the_network() -> None:
    lines = deploy.observability_lines(_install_sh_output("pi-network"))
    assert lines[2:] == [
        "  Grafana:   http://rig.local:3000 (admin password: /etc/anolis/observability.env)",
        "  InfluxDB:  http://127.0.0.1:8086 (org anolis, bucket anolis, retention 30d)",
    ]


def test_observability_lines_say_grafana_left_the_network() -> None:
    lines = deploy.observability_lines(_install_sh_output("pi-was-network"))
    assert lines[2].startswith("  Grafana:   now bound to 127.0.0.1")
    assert lines[3].startswith("  Grafana:   127.0.0.1:3000 on this machine")


def test_observability_lines_show_the_bind_unchanged_line() -> None:
    assert deploy.observability_lines(_install_sh_output("offline")) == [
        "⚠ observability: vendor apt repos unreachable (offline?) — skipped",
        "    The runtime install is unaffected; re-run with --with-observability once online.",
        "  Grafana bind unchanged: the observability step did not finish (see the warnings above)",
        "  InfluxDB:  http://127.0.0.1:8086 (org anolis, bucket anolis, retention 30d)",
    ]


# health-*.txt are anolis v0.1.45 tools/install.sh's phase_health followed by
# phase_summary, captured the same way with curl, sleep and hostname stubbed
# as shell functions. Each names the runtime's answers it was given.
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "health-all-running",
            [
                "✓ health: runtime responding (v0.1.45)",
                "✓ health: run registry available",
                "✓ health: providers: 2 of 2 running",
            ],
        ),
        (
            "health-one-down",
            [
                "✓ health: runtime responding (v0.1.45)",
                "✓ health: run registry available",
                "⚠ health: providers: 1 of 2 running",
                "⚠ health: provider io1: UNAVAILABLE",
                "    sudo journalctl -u anolis-runtime --no-pager -n 50",
            ],
        ),
        (
            "health-providers-unknown",
            [
                "✓ health: runtime responding (v0.1.45)",
                "✓ health: run registry available",
                "⚠ health: providers unknown (GET /v0/providers/health did not answer)",
            ],
        ),
        (
            "health-registry-down",
            [
                "✓ health: runtime responding (v0.1.45)",
                "⚠ health: run registry NOT available (GET /v0/runs did not answer 200) -- runs are not being recorded",
                "    sudo journalctl -u anolis-runtime --no-pager | grep 'Run registry'",
                "✓ health: providers: 2 of 2 running",
            ],
        ),
        ("health-no-start", ["→ health: skipped (services not started)"]),
    ],
)
def test_health_lines_are_install_sh_health_report(name: str, expected: list[str]) -> None:
    assert deploy.health_lines(_install_sh_output(name)) == expected


# phase_start runs just before phase_health and restarts the runtime on every
# install (anolis tools/install.sh:1613-1624 at v0.1.51), so its line says
# whether this deploy restarted it (#556).
@pytest.mark.parametrize(
    "start",
    [
        "✓ start: anolis-runtime restarted",
        "⚠ start: failed to restart anolis-runtime",
        "→ start: --no-start specified",
    ],
)
def test_health_lines_lead_with_the_start_line(start: str) -> None:
    output = f"\x1b[0;32m{start}\x1b[0m\n\x1b[0;32m✓ health: runtime responding (v0.1.51)\x1b[0m\n  Hostname:  pi-g1\n"
    assert deploy.health_lines(output) == [start, "✓ health: runtime responding (v0.1.51)"]


# The startup safe-state lines phase_health prints (anolishq/anolis#473): the
# achieved line names the rung, and a run that did not happen is a skip line.
def test_health_lines_carry_the_startup_safe_state() -> None:
    achieved = "\x1b[0;32m✓ health: startup safe state achieved (hooks)\x1b[0m\n"
    not_run = "\x1b[0;33m→ health: startup safe state not run (on_startup: leave)\x1b[0m\n  Hostname:  pi-g1\n"
    not_achieved = (
        "\x1b[1;33m⚠ health: startup safe state NOT achieved\x1b[0m\n"
        "    bread0/dcmt0 set_mode: SET command refused: WATCHDOG_TRIP\n"
        "    bread0/rlht0 set_mode: skipped, device absent at startup\n"
        "  Hostname:  pi-g1\n"
    )
    assert deploy.health_lines(achieved) == ["✓ health: startup safe state achieved (hooks)"]
    assert deploy.health_lines(not_run) == ["→ health: startup safe state not run (on_startup: leave)"]
    assert deploy.health_lines(not_achieved) == [
        "⚠ health: startup safe state NOT achieved",
        "    bread0/dcmt0 set_mode: SET command refused: WATCHDOG_TRIP",
        "    bread0/rlht0 set_mode: skipped, device absent at startup",
    ]


def test_health_and_observability_lines_do_not_overlap() -> None:
    output = _install_sh_output("pi-loopback")
    assert deploy.health_lines(output) == ["✓ health: runtime responding (v0.1.45)"]
    assert not any("health:" in line for line in deploy.observability_lines(output))


_ADMIN_LOGIN_NOTE = "admin password: GRAFANA_ADMIN_PASSWORD in /etc/anolis/observability.env on the target (root only)"


def test_grafana_access_is_a_tunnel_when_grafana_is_on_loopback() -> None:
    assert deploy.grafana_access(_install_sh_output("pi-loopback"), ssh_target="pi@192.168.1.10") == [
        "Grafana listens on 127.0.0.1:3000 on the target. Open a tunnel, then browse http://localhost:3000:",
        "  ssh -L 3000:127.0.0.1:3000 pi@192.168.1.10",
        _ADMIN_LOGIN_NOTE,
    ]


def test_grafana_access_tunnel_names_a_non_default_ssh_port() -> None:
    lines = deploy.grafana_access(_install_sh_output("x86-loopback"), ssh_target="me@box", ssh_port=2222)
    assert lines[1] == "  ssh -p 2222 -L 3000:127.0.0.1:3000 me@box"


def test_grafana_access_is_a_tunnel_after_grafana_left_the_network() -> None:
    lines = deploy.grafana_access(_install_sh_output("pi-was-network"), ssh_target="pi@rig")
    assert lines[1] == "  ssh -L 3000:127.0.0.1:3000 pi@rig"


def test_grafana_access_is_the_address_only_when_install_sh_put_grafana_on_the_network() -> None:
    assert deploy.grafana_access(_install_sh_output("pi-network"), ssh_target="pi@192.168.1.10") == [
        "Grafana: http://192.168.1.10:3000",
        _ADMIN_LOGIN_NOTE,
    ]


def test_grafana_access_on_a_local_install_is_this_machine() -> None:
    """A local install may be an appliance browsed from elsewhere, so say
    whose localhost it is and how to reach it from another machine."""
    assert deploy.grafana_access(_install_sh_output("x86-loopback")) == [
        "Grafana: http://localhost:3000 on this machine",
        "  from another machine: ssh -L 3000:127.0.0.1:3000 <user>@<this machine>, then http://localhost:3000",
        _ADMIN_LOGIN_NOTE,
    ]


def test_grafana_access_on_a_local_install_on_the_network() -> None:
    assert deploy.grafana_access(_install_sh_output("pi-network")) == [
        "Grafana: port 3000 on this machine's network address (http://localhost:3000 on this machine)",
        _ADMIN_LOGIN_NOTE,
    ]


def test_grafana_access_tunnel_names_the_ssh_key() -> None:
    lines = deploy.grafana_access(
        _install_sh_output("pi-loopback"), ssh_target="pi@rig", ssh_port=2222, ssh_key="/home/me/keys/rig key"
    )
    assert lines[1] == "  ssh -p 2222 -i '/home/me/keys/rig key' -L 3000:127.0.0.1:3000 pi@rig"


@pytest.mark.parametrize("host", ["fd00::5", "[fd00::5]"])
def test_grafana_access_brackets_an_ipv6_target(host: str) -> None:
    assert deploy.grafana_access(_install_sh_output("pi-network"), ssh_target=f"pi@{host}")[0] == (
        "Grafana: http://[fd00::5]:3000"
    )
    tunnel = deploy.grafana_access(_install_sh_output("pi-loopback"), ssh_target=f"pi@{host}")[1]
    assert tunnel == "  ssh -L 3000:127.0.0.1:3000 pi@fd00::5"


def test_grafana_access_is_empty_when_the_observability_step_did_not_finish() -> None:
    assert deploy.grafana_access(_install_sh_output("offline"), ssh_target="pi@rig") == []
    assert deploy.grafana_access("✓ health: runtime responding (v0.1.45)\n", ssh_target="pi@rig") == []


def test_a_rejected_sudo_password_is_not_reported_as_an_install_failure(
    project_dir: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sudo's own refusal: install.sh never ran (#230)."""

    class _Rejected(RecordingExecutor):
        def run(self, cmd, *, input=None, sudo=False, timeout=None):
            super().run(cmd, input=input, sudo=sudo, timeout=timeout)
            return RunResult(
                returncode=1,
                stdout="pw-7f3c",
                stderr="Sorry, try again.\nsudo: no password was provided\nsudo: 1 incorrect password attempt\n",
            )

    _stub_fetch(monkeypatch)
    with pytest.raises(deploy.DeployError) as err:
        deploy.deploy_local(project_dir=project_dir, project_name="deploy-fixture", executor=_Rejected())

    message = str(err.value)
    assert message.startswith("sudo rejected the password")
    assert "install.sh failed" not in message
    assert "pw-7f3c" not in message
