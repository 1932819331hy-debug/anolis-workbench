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
    provision._run_remote_job(job, {"project": "rig", "variant": "automation", "target": "pi@rig"})

    assert job.status == "done"
    assert calls[0]["project_name"] == "rig"
    assert calls[0]["variant"] == "automation"
