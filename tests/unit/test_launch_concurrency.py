"""Overlapping launches start one runtime (anolishq/anolis-workbench#671).

The server runs each request on its own thread. Two launches that overlap
used to both pass the "already running" check and both spawn a runtime; the
workbench then tracked only the second, and the first ran on, untracked and
out of Stop's reach.
"""

from __future__ import annotations

import pathlib
import threading
import time
from typing import Any

import pytest

from anolis_workbench.core import launcher


class _FakeRuntime:
    """Enough of a Popen handle for launch(), stop() and the status check."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.stdout = None
        self.alive = True

    def poll(self) -> int | None:
        return None if self.alive else 0

    def terminate(self) -> None:
        self.alive = False

    def kill(self) -> None:
        self.alive = False

    def wait(self, timeout: float | None = None) -> int:
        return 0


@pytest.fixture
def spawned(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> list[_FakeRuntime]:
    exe = tmp_path / "anolis-runtime"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    config = tmp_path / "launch.yaml"
    config.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr("anolis_workbench.core.paths.DATA_ROOT", tmp_path)
    monkeypatch.setattr("anolis_workbench.core.paths.SYSTEMS_ROOT", tmp_path / "systems")
    monkeypatch.setattr(launcher, "materialize_launch_config", lambda project_dir, system: config)
    monkeypatch.setattr("anolis_workbench.core.canonical_validator.validate_project", lambda system: [])
    monkeypatch.setattr(launcher.canonical, "host_paths", lambda doc: {"runtime_executable": str(exe)})
    # No port probe: only the workbench's own state says whether one runs.
    monkeypatch.setattr(launcher, "_discover_running_runtime", lambda clean_stale=False: None)
    monkeypatch.setattr(launcher, "_state", dict(launcher._state, process=None, project=None, log_lines_by_project={}))
    monkeypatch.setattr(launcher, "_read_logs", lambda proc, log_file, project: None)

    children: list[_FakeRuntime] = []

    def _popen(cmd: Any, *args: Any, **kwargs: Any) -> _FakeRuntime:
        time.sleep(0.2)  # a real spawn takes time; hold the window open
        child = _FakeRuntime(pid=1000 + len(children))
        children.append(child)
        return child

    monkeypatch.setattr(launcher.subprocess, "Popen", _popen)
    return children


def test_two_overlapping_launches_start_one_runtime(spawned: list[_FakeRuntime], tmp_path: pathlib.Path) -> None:
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def _launch(project: str) -> None:
        barrier.wait()
        try:
            launcher.launch(project, {}, tmp_path / project)
            outcomes.append("ok")
        except RuntimeError as error:
            outcomes.append(str(error))

    threads = [threading.Thread(target=_launch, args=(name,)) for name in ("demo", "other")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(spawned) == 1, f"{len(spawned)} runtimes started"
    assert sorted(outcomes) == ["A system is already running.", "ok"]

    launcher.stop()
    assert not spawned[0].alive, "Stop did not reach the runtime the workbench started"


def test_a_launch_that_fails_after_spawning_stops_its_runtime(
    spawned: list[_FakeRuntime], tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # running.json, written after the spawn, cannot be written.
    real_write_text = pathlib.Path.write_text

    def _write_text(self: pathlib.Path, *args: Any, **kwargs: Any) -> int:
        if self.name == "running.json":
            raise OSError("disk full")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "write_text", _write_text)
    with pytest.raises(OSError, match="disk full"):
        launcher.launch("demo", {}, tmp_path / "demo")

    assert len(spawned) == 1
    assert not spawned[0].alive, "a failed launch left its runtime running"
    assert launcher._state["process"] is None


def test_restart_and_launch_do_not_overlap(
    spawned: list[_FakeRuntime], tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("anolis_workbench.core.projects.get_project", lambda name: {})
    launcher.launch("demo", {}, tmp_path / "demo")
    first = spawned[0]
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def _restart() -> None:
        barrier.wait()
        try:
            launcher.restart("demo", tmp_path / "demo")
            outcomes.append("restarted")
        except RuntimeError as error:
            outcomes.append(f"restart: {error}")

    def _launch() -> None:
        barrier.wait()
        time.sleep(0.05)  # inside restart's stop-then-spawn window
        try:
            launcher.launch("demo", {}, tmp_path / "demo")
            outcomes.append("launched")
        except RuntimeError as error:
            outcomes.append(f"launch: {error}")

    threads = [threading.Thread(target=_restart), threading.Thread(target=_launch)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not first.alive
    alive = [child for child in spawned if child.alive]
    assert len(alive) == 1, f"{len(alive)} runtimes running after a restart and a launch overlapped"
    assert "restarted" in outcomes
    assert "launch: A system is already running." in outcomes
