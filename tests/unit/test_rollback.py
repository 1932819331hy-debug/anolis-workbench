"""Unit tests for the rollback module (install.sh --rollback delegation)."""

from __future__ import annotations

from pathlib import Path

import pytest

from anolis_workbench.core import deploy
from anolis_workbench.core import rollback as rollback_module


def test_rollback_success_wraps_run_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def _fake_run_rollback(executor=None, *, prefix, progress_callback=None):
        calls.append({"executor": executor, "prefix": prefix})
        return "rollback complete"

    monkeypatch.setattr(deploy, "run_rollback", _fake_run_rollback)
    result = rollback_module.rollback(Path("/opt/anolis"))
    assert result.success is True
    assert result.output == "rollback complete"
    assert result.error is None
    assert calls[0]["prefix"] == Path("/opt/anolis")


def test_rollback_failure_surfaces_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_run_rollback(executor=None, *, prefix, progress_callback=None):
        raise deploy.DeployError("nothing to roll back: .prev not found")

    monkeypatch.setattr(deploy, "run_rollback", _fake_run_rollback)
    result = rollback_module.rollback(Path("/opt/anolis"))
    assert result.success is False
    assert result.error is not None
    assert "nothing to roll back" in result.error


def test_rollback_carries_an_unverified_install_sh_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    warning = "WARNING: install.sh not verified: anolis v0.1.49 predates install.sh in SHA256SUMS"

    def _fake_run_rollback(executor=None, *, prefix, progress_callback=None):
        progress_callback("unverified", warning)
        return "rollback complete"

    monkeypatch.setattr(deploy, "run_rollback", _fake_run_rollback)
    result = rollback_module.rollback(Path("/opt/anolis"))
    assert result.success is True
    assert result.warning == warning
