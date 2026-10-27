"""Stop and restart an installed anolis-runtime service (wb#581).

The workbench could stop only a runtime it launched itself. For one that
install.sh installed as a systemd service, Commission said to stop it and
offered nothing that did: POST /api/projects/<name>/stop answered ok and
stopped nothing.
"""

from __future__ import annotations

import json
from io import BytesIO
from typing import Any
from unittest.mock import patch

import pytest

from anolis_workbench.core import deploy, launcher
from anolis_workbench.core.executor import Executor, RunResult
from anolis_workbench.server.routes import commission, provision


class ScriptedExecutor(Executor):
    """Records each command; answers `systemctl is-active` with `state`."""

    def __init__(self, state: str = "inactive", returncode: int = 0, stderr: str = "") -> None:
        self.state = state
        self.returncode = returncode
        self.stderr = stderr
        self.commands: list[dict[str, Any]] = []
        self.closed = False

    def run(self, cmd, *, input=None, sudo=False, timeout=None):
        self.commands.append({"cmd": list(cmd), "sudo": sudo})
        if list(cmd)[:2] == ["systemctl", "is-active"]:
            return RunResult(returncode=0 if self.state == "active" else 3, stdout=f"{self.state}\n", stderr="")
        return RunResult(returncode=self.returncode, stdout="", stderr=self.stderr)

    def write_file(self, path, data):
        raise AssertionError("no files are written")

    def mkdir(self, path):
        raise AssertionError("no directories are made")

    def file_exists(self, path):
        return False

    def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# deploy.control_runtime_service
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("action", "state"), [("stop", "inactive"), ("restart", "active")])
def test_runs_systemctl_under_sudo_then_reads_the_state(action: str, state: str) -> None:
    executor = ScriptedExecutor(state=state)
    assert deploy.control_runtime_service(executor, action) == state
    assert executor.commands == [
        {"cmd": ["systemctl", action, "anolis-runtime"], "sudo": True},
        {"cmd": ["systemctl", "is-active", "anolis-runtime"], "sudo": False},
    ]


def test_refuses_any_other_action() -> None:
    executor = ScriptedExecutor()
    with pytest.raises(ValueError, match="stop or restart"):
        deploy.control_runtime_service(executor, "disable")
    assert executor.commands == []


def test_a_failing_systemctl_is_an_error_with_its_output() -> None:
    executor = ScriptedExecutor(returncode=5, stderr="Unit anolis-runtime.service not loaded.")
    with pytest.raises(deploy.DeployError) as raised:
        deploy.control_runtime_service(executor, "stop")
    assert "systemctl stop anolis-runtime failed (exit 5)" in str(raised.value)
    assert "Unit anolis-runtime.service not loaded." in str(raised.value)


def test_a_rejected_sudo_password_says_so() -> None:
    executor = ScriptedExecutor(returncode=1, stderr="sudo: 1 incorrect password attempt")
    with pytest.raises(deploy.DeployError, match="sudo rejected the password"):
        deploy.control_runtime_service(executor, "restart")


# ---------------------------------------------------------------------------
# POST /api/provision/service
# ---------------------------------------------------------------------------


class FakeHandler:
    def __init__(self, body: dict[str, Any], client: str = "127.0.0.1") -> None:
        raw = json.dumps(body).encode()
        self.rfile = BytesIO(raw)
        self.headers = {"Content-Length": str(len(raw))}
        self.client_address = (client, 50000)
        self.responses: list[tuple[int, dict[str, Any]]] = []

    def _json(self, status: int, data: dict[str, Any]) -> None:
        self.responses.append((status, data))


def test_a_local_stop_uses_the_sudo_password_once_and_zeroes_it() -> None:
    made: list[Any] = []
    executor = ScriptedExecutor(state="inactive")

    def local(*, sudo_password=None):
        made.append(sudo_password)
        return executor

    handler = FakeHandler({"action": "stop", "sudo_password": "pw-7f3c"})
    with patch.object(provision, "LocalExecutor", side_effect=local):
        provision.control_service(handler)

    assert handler.responses == [(200, {"ok": True, "action": "stop", "machine": "this machine", "state": "inactive"})]
    assert made[0] == bytearray(len(b"pw-7f3c")), "the password buffer is zeroed after use"
    assert executor.commands[0] == {"cmd": ["systemctl", "stop", "anolis-runtime"], "sudo": True}


def test_a_sudo_password_from_another_machine_is_refused() -> None:
    handler = FakeHandler({"action": "stop", "sudo_password": "pw"}, client="192.168.1.20")
    with patch.object(provision, "LocalExecutor") as local:
        provision.control_service(handler)
    assert handler.responses[0][0] == 403
    local.assert_not_called()


def test_a_remote_restart_goes_over_ssh_and_closes_it() -> None:
    executor = ScriptedExecutor(state="active")
    handler = FakeHandler({"action": "restart", "target": "pi@rig", "port": 2222})
    with patch.object(provision, "ParamikoSSHExecutor", return_value=executor) as ssh:
        provision.control_service(handler)

    ssh.assert_called_once_with(host="rig", user="pi", key_file=None, port=2222)
    assert handler.responses == [(200, {"ok": True, "action": "restart", "machine": "pi@rig", "state": "active"})]
    assert executor.closed


def test_a_stop_that_leaves_it_running_is_not_ok() -> None:
    executor = ScriptedExecutor(state="active")
    handler = FakeHandler({"action": "stop", "target": "pi@rig"})
    with patch.object(provision, "ParamikoSSHExecutor", return_value=executor):
        provision.control_service(handler)
    status, body = handler.responses[0]
    assert status == 502
    assert body["state"] == "active"
    assert "still active" in body["error"]


@pytest.mark.parametrize(
    "body",
    [
        {"action": "disable"},
        {"action": "stop", "target": "rig"},
        {"action": "stop", "sudo_password": 7},
        {"action": "stop", "target": "pi@rig", "sudo_password": "pw"},
        {},
    ],
    ids=["action", "target", "password-type", "password-over-ssh", "missing"],
)
def test_bad_requests_are_refused_before_anything_runs(body: dict[str, Any]) -> None:
    handler = FakeHandler(body)
    with (
        patch.object(provision, "LocalExecutor") as local,
        patch.object(provision, "ParamikoSSHExecutor") as ssh,
    ):
        provision.control_service(handler)
    assert handler.responses[0][0] == 400
    local.assert_not_called()
    ssh.assert_not_called()


# ---------------------------------------------------------------------------
# The workbench's own status and stop, for an installed runtime
# ---------------------------------------------------------------------------

_EXTERNAL = {"running": True, "project": "rig", "pid": None, "managed": False, "external": True}


def test_status_says_when_the_runtime_is_an_installed_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher, "_current_runtime_snapshot", lambda clean_stale=False: dict(_EXTERNAL))
    assert launcher.get_status()["external"] is True
    monkeypatch.setattr(
        launcher,
        "_current_runtime_snapshot",
        lambda clean_stale=False: {"running": True, "project": "rig", "pid": 42, "managed": True},
    )
    assert launcher.get_status()["external"] is False


def test_stopping_a_project_whose_runtime_is_an_installed_service_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher, "_current_runtime_snapshot", lambda clean_stale=False: dict(_EXTERNAL))
    monkeypatch.setattr(commission.projects_module, "get_project", lambda name: {})
    monkeypatch.setattr(commission, "_reject_imported", lambda handler, name, action: False)
    stopped: list[bool] = []
    monkeypatch.setattr(launcher, "stop", lambda: stopped.append(True))
    handler = FakeHandler({})

    commission.stop_project(handler, "rig")

    status, body = handler.responses[0]
    assert status == 409
    assert "installed service" in body["error"]
    assert stopped == []
