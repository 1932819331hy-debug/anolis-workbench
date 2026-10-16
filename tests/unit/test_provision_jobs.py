"""The deploy job's events: what a progress view reads back (#417, #228)."""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from anolis_workbench.core import deploy
from anolis_workbench.server.routes import provision


@pytest.fixture()
def calls(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> list[dict[str, Any]]:
    recorded: list[dict[str, Any]] = []
    monkeypatch.setattr(provision, "_prepare_workspace", lambda params, progress: tmp_path)
    return recorded


def test_done_event_carries_the_preflight_and_reboot(calls, monkeypatch: pytest.MonkeyPatch) -> None:
    def _deploy(**kwargs: Any) -> deploy.DeployResult:
        calls.append(kwargs)
        return deploy.DeployResult(
            project_name="rig",
            runtime_version="0.1.43",
            prefix="/opt/anolis",
            output="",
            host_preflight=["⚠ host preflight: continuing (--allow-unmet-host); those providers start up not ready"],
            reboot_pending=True,
            preserved=["→ config (runtime): /opt/anolis/config/runtime.yaml exists (preserved)"],
            runtime_config_kept=True,
        )

    monkeypatch.setattr(deploy, "deploy_local", _deploy)
    job = provision.ProvisionJob(job_id="j1")
    provision._run_install_job(job, {"project": "rig", "allow_unmet_host": True})

    assert job.status == "done"
    assert calls[0]["allow_unmet_host"] is True
    summary = job.events[-1]["summary"]
    assert summary["runtime_version"] == "0.1.43"
    assert summary["host_preflight"][0].startswith("⚠ host preflight: continuing")
    assert summary["reboot_pending"] is True
    assert summary["preserved"] == ["→ config (runtime): /opt/anolis/config/runtime.yaml exists (preserved)"]
    assert summary["runtime_config_kept"] is True


def test_remote_done_event_says_how_to_reach_grafana(calls, monkeypatch: pytest.MonkeyPatch) -> None:
    """The tunnel to the target the job reached, and where the password lives (#241)."""
    output = (pathlib.Path(__file__).parent.parent / "fixtures" / "install-sh" / "pi-loopback.txt").read_text(
        encoding="utf-8"
    )

    def _deploy(**kwargs: Any) -> deploy.DeployResult:
        return deploy.DeployResult(project_name="rig", runtime_version="0.1.45", prefix="/opt/anolis", output=output)

    class _Executor:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(deploy, "deploy_remote", _deploy)
    monkeypatch.setattr(provision, "ParamikoSSHExecutor", _Executor)
    monkeypatch.setattr("anolis_workbench.core.fleet.auto_register_host", lambda **kwargs: None)
    job = provision.ProvisionJob(job_id="j4")
    provision._run_remote_job(
        job, {"project": "rig", "target": "pi@10.0.0.5", "port": 2200, "key_file": "/home/me/.ssh/rig"}
    )

    assert job.events[-1]["summary"]["grafana_access"][1] == (
        "  ssh -p 2200 -i /home/me/.ssh/rig -L 3000:127.0.0.1:3000 pi@10.0.0.5"
    )


def test_local_done_event_has_no_grafana_access_without_observability(calls, monkeypatch: pytest.MonkeyPatch) -> None:
    def _deploy(**kwargs: Any) -> deploy.DeployResult:
        return deploy.DeployResult(project_name="rig", runtime_version="0.1.45", prefix="/opt/anolis", output="")

    monkeypatch.setattr(deploy, "deploy_local", _deploy)
    job = provision.ProvisionJob(job_id="j5")
    provision._run_install_job(job, {"project": "rig"})
    assert job.events[-1]["summary"]["grafana_access"] == []


@pytest.mark.parametrize("remote", [False, True])
def test_done_event_carries_install_sh_observability_lines(
    calls, monkeypatch: pytest.MonkeyPatch, remote: bool
) -> None:
    """install.sh warns and still exits 0 when observability fails, so the
    summary must carry its lines or an offline target reads as a success."""
    output = (pathlib.Path(__file__).parent.parent / "fixtures" / "install-sh" / "offline.txt").read_text(
        encoding="utf-8"
    )

    def _deploy(**kwargs: Any) -> deploy.DeployResult:
        return deploy.DeployResult(project_name="rig", runtime_version="0.1.45", prefix="/opt/anolis", output=output)

    class _Executor:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(deploy, "deploy_local", _deploy)
    monkeypatch.setattr(deploy, "deploy_remote", _deploy)
    monkeypatch.setattr(provision, "ParamikoSSHExecutor", _Executor)
    monkeypatch.setattr("anolis_workbench.core.fleet.auto_register_host", lambda **kwargs: None)
    job = provision.ProvisionJob(job_id="j6")
    if remote:
        provision._run_remote_job(job, {"project": "rig", "target": "pi@rig", "with_observability": True})
    else:
        provision._run_install_job(job, {"project": "rig", "with_observability": True})

    summary = job.events[-1]["summary"]
    assert summary["observability"] == deploy.observability_lines(output)
    assert summary["observability"][0] == "⚠ observability: vendor apt repos unreachable (offline?) — skipped"
    assert summary["grafana_access"] == []


@pytest.mark.parametrize("remote", [False, True])
def test_done_event_carries_install_sh_health_lines(calls, monkeypatch: pytest.MonkeyPatch, remote: bool) -> None:
    """install.sh warns and still exits 0 when a provider is not running, so
    the summary must carry its health lines or the shortfall goes unseen."""
    output = (pathlib.Path(__file__).parent.parent / "fixtures" / "install-sh" / "health-one-down.txt").read_text(
        encoding="utf-8"
    )

    def _deploy(**kwargs: Any) -> deploy.DeployResult:
        return deploy.DeployResult(project_name="rig", runtime_version="0.1.45", prefix="/opt/anolis", output=output)

    class _Executor:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(deploy, "deploy_local", _deploy)
    monkeypatch.setattr(deploy, "deploy_remote", _deploy)
    monkeypatch.setattr(provision, "ParamikoSSHExecutor", _Executor)
    monkeypatch.setattr("anolis_workbench.core.fleet.auto_register_host", lambda **kwargs: None)
    job = provision.ProvisionJob(job_id="j7")
    if remote:
        provision._run_remote_job(job, {"project": "rig", "target": "pi@rig"})
    else:
        provision._run_install_job(job, {"project": "rig"})

    health = job.events[-1]["summary"]["health"]
    assert "⚠ health: providers: 1 of 2 running" in health
    assert "⚠ health: provider io1: UNAVAILABLE" in health


def test_allow_unmet_host_needs_a_literal_true(calls, monkeypatch: pytest.MonkeyPatch) -> None:
    def _deploy(**kwargs: Any) -> deploy.DeployResult:
        calls.append(kwargs)
        return deploy.DeployResult(project_name="rig", runtime_version="0.1.43", prefix="/opt/anolis", output="")

    monkeypatch.setattr(deploy, "deploy_local", _deploy)
    for value in (None, "true", 1):
        provision._run_install_job(provision.ProvisionJob(job_id="j"), {"allow_unmet_host": value})
    assert [c["allow_unmet_host"] for c in calls] == [False, False, False]


def test_error_event_carries_the_preflight_block(calls, monkeypatch: pytest.MonkeyPatch) -> None:
    block = ["✗ host preflight: bread0: host requirements unmet", "✗ host preflight: requirements unmet"]

    def _deploy(**kwargs: Any) -> deploy.DeployResult:
        raise deploy.DeployError("install.sh failed (exit 1)", host_preflight=block, reboot_pending=False)

    monkeypatch.setattr(deploy, "deploy_local", _deploy)
    job = provision.ProvisionJob(job_id="j2")
    provision._run_install_job(job, {})

    assert job.status == "failed"
    event = job.events[-1]
    assert event["stage"] == "error"
    assert event["host_preflight"] == block
    assert event["reboot_pending"] is False


def test_a_project_the_job_seeds_is_marked_as_onboarding_created(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First-run detection skips this project, so a failed install keeps onboarding (#424)."""
    import json

    from anolis_workbench.core import installer
    from anolis_workbench.core import paths as paths_module

    def _seed(template: str, project: str, prefix: pathlib.Path, *, force: bool = False) -> pathlib.Path:
        project_dir = tmp_path / project
        project_dir.mkdir()
        (project_dir / "workbench.json").write_text(json.dumps({"meta": {"name": project, "template": template}}))
        return project_dir

    monkeypatch.setattr(paths_module, "SYSTEMS_ROOT", tmp_path)
    monkeypatch.setattr(installer, "provision_project", _seed)

    project_dir = provision._prepare_workspace({}, lambda *args: None)

    meta = json.loads((project_dir / "workbench.json").read_text())["meta"]
    assert meta == {"name": "sim-quickstart", "template": "sim-quickstart", "created_by": "onboarding"}

    (tmp_path / "my-rig").mkdir()
    (tmp_path / "my-rig" / "workbench.json").write_text(json.dumps({"meta": {"name": "my-rig"}}))
    provision._prepare_workspace({"project": "my-rig"}, lambda *args: None)
    assert "created_by" not in json.loads((tmp_path / "my-rig" / "workbench.json").read_text())["meta"]


def test_remote_job_deploys_the_named_project_with_its_variant(calls, monkeypatch: pytest.MonkeyPatch) -> None:
    """Commission's Deploy to device posts project, variant and target (#275)."""

    def _deploy(**kwargs: Any) -> deploy.DeployResult:
        calls.append(kwargs)
        return deploy.DeployResult(project_name="rig", runtime_version="0.1.43", prefix="/opt/anolis", output="")

    class _Executor:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(deploy, "deploy_remote", _deploy)
    monkeypatch.setattr(provision, "ParamikoSSHExecutor", _Executor)
    monkeypatch.setattr("anolis_workbench.core.fleet.auto_register_host", lambda **kwargs: None)
    job = provision.ProvisionJob(job_id="j3")
    provision._run_remote_job(
        job,
        {
            "project": "rig",
            "variant": "automation",
            "target": "pi@rig",
            "with_telemetry_export": True,
            "with_observability": True,
        },
    )

    assert job.status == "done"
    assert calls[0]["project_name"] == "rig"
    assert calls[0]["variant"] == "automation"
    assert calls[0]["with_telemetry_export"] is True
    assert calls[0]["with_observability"] is True


def test_telemetry_export_needs_a_literal_true(calls, monkeypatch: pytest.MonkeyPatch) -> None:
    """Commission's install option reaches deploy as with_telemetry_export (#229)."""

    def _deploy(**kwargs: Any) -> deploy.DeployResult:
        calls.append(kwargs)
        return deploy.DeployResult(project_name="rig", runtime_version="0.1.43", prefix="/opt/anolis", output="")

    monkeypatch.setattr(deploy, "deploy_local", _deploy)
    for value in (True, None, "true", 1):
        provision._run_install_job(provision.ProvisionJob(job_id="j"), {"with_telemetry_export": value})
    assert [c["with_telemetry_export"] for c in calls] == [True, False, False, False]


def test_observability_needs_a_literal_true(calls, monkeypatch: pytest.MonkeyPatch) -> None:
    """Commission's observability option reaches deploy as with_observability (#229)."""

    def _deploy(**kwargs: Any) -> deploy.DeployResult:
        calls.append(kwargs)
        return deploy.DeployResult(project_name="rig", runtime_version="0.1.43", prefix="/opt/anolis", output="")

    monkeypatch.setattr(deploy, "deploy_local", _deploy)
    for value in (True, None, "true", 1):
        provision._run_install_job(provision.ProvisionJob(job_id="j"), {"with_observability": value})
    assert [c["with_observability"] for c in calls] == [True, False, False, False]
