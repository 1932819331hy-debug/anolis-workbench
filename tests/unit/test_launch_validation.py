"""Launch runs the same blocking validation as preflight (#414).

Requiring a passing preflight is enforced in the Commission UI only, so a
direct POST /api/projects/{name}/launch used to start a project preflight
would refuse.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from anolis_workbench.core import canonical, launcher
from anolis_workbench.server.routes import commission


class _Handler:
    def __init__(self) -> None:
        self.status: int | None = None
        self.body: dict[str, Any] | None = None

    def _json(self, status: int, data: dict[str, Any]) -> None:
        self.status = status
        self.body = data


def _non_inert(document: dict[str, Any]) -> dict[str, Any]:
    """A blocking error preflight reports: the manual variant boots into automation."""
    document["variants"][canonical.MANUAL_VARIANT]["automation"] = {"enabled": True, "behavior_tree": "b.xml"}
    return document


@pytest.fixture()
def no_spawn(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Record any attempt to start the runtime instead of starting it."""
    spawned: list[Any] = []

    def _popen(*args: Any, **kwargs: Any) -> None:
        spawned.append(args)
        pytest.fail("launch started the runtime")

    monkeypatch.setattr(launcher.subprocess, "Popen", _popen)
    monkeypatch.setattr(
        launcher, "_current_runtime_snapshot", lambda clean_stale=False: {"running": False, "project": None}
    )
    return spawned


def test_launch_refuses_a_project_that_fails_validation(
    canonical_document, tmp_path: pathlib.Path, no_spawn: list[Any]
) -> None:
    with pytest.raises(launcher.LaunchValidationError) as raised:
        launcher.launch("rig", _non_inert(canonical_document()), tmp_path)

    assert any("not inert" in e for e in raised.value.errors), raised.value.errors
    assert no_spawn == []
    assert not (tmp_path / "running.json").exists()


def test_launch_endpoint_answers_422_with_the_errors(
    canonical_document, tmp_path: pathlib.Path, no_spawn: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    document = _non_inert(canonical_document())
    monkeypatch.setattr(commission.projects_module, "is_authored", lambda name: True)
    monkeypatch.setattr(commission.projects_module, "get_project", lambda name: document)
    monkeypatch.setattr(commission.projects_module, "project_dir", lambda name: tmp_path)
    handler = _Handler()

    commission.launch_project(handler, "rig")

    assert handler.status == 422
    assert handler.body is not None
    assert any("not inert" in e for e in handler.body["errors"]), handler.body
    assert no_spawn == []


@pytest.fixture()
def running_rig(monkeypatch: pytest.MonkeyPatch, canonical_document) -> dict[str, Any]:
    """`rig` is running, launched while valid; its saved project is now invalid."""
    state: dict[str, Any] = {"stopped": 0, "spawned": []}
    monkeypatch.setattr(
        launcher, "_current_runtime_snapshot", lambda clean_stale=False: {"running": True, "project": "rig"}
    )

    def _stop() -> None:
        state["stopped"] += 1

    def _popen(*args: Any, **kwargs: Any) -> None:
        state["spawned"].append(args)
        pytest.fail("restart started the runtime")

    monkeypatch.setattr(launcher, "stop", _stop)
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)
    monkeypatch.setattr(launcher.subprocess, "Popen", _popen)
    document = _non_inert(canonical_document())
    from anolis_workbench.core import projects as projects_module

    monkeypatch.setattr(projects_module, "get_project", lambda name: document)
    return state


def test_restart_refuses_an_invalid_saved_project_and_leaves_the_runtime_running(
    running_rig: dict[str, Any], tmp_path: pathlib.Path
) -> None:
    with pytest.raises(launcher.LaunchValidationError) as raised:
        launcher.restart("rig", tmp_path)

    assert any("not inert" in e for e in raised.value.errors), raised.value.errors
    assert running_rig["stopped"] == 0
    assert running_rig["spawned"] == []


def test_restart_endpoint_answers_422_with_the_errors(
    running_rig: dict[str, Any], tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(commission.projects_module, "is_authored", lambda name: True)
    monkeypatch.setattr(commission.projects_module, "project_dir", lambda name: tmp_path)
    handler = _Handler()

    commission.restart_project(handler, "rig")

    assert handler.status == 422
    assert handler.body is not None
    assert any("not inert" in e for e in handler.body["errors"]), handler.body
    assert running_rig["stopped"] == 0
