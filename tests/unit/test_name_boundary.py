"""Project and template names are checked where they enter: the provision
routes, project creation, the provision CLI and the fleet file."""

from __future__ import annotations

import json
import pathlib
import sys
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from anolis_workbench.cli import provision_cli
from anolis_workbench.core import fleet, projects
from anolis_workbench.server.routes import compose, provision

BAD_NAMES: list[Any] = ["..", "../escape", "/abs/elsewhere", "a/b", "a\\b", "x" * 65, "has space", 7, ["x"]]
BAD_STRINGS = [n for n in BAD_NAMES if isinstance(n, str)]


class FakeHandler:
    def __init__(self, body: dict[str, Any]) -> None:
        raw = json.dumps(body).encode()
        self.rfile = BytesIO(raw)
        self.headers = {"Content-Length": str(len(raw))}
        self.client_address = ("127.0.0.1", 50000)
        self.body = body
        self.responses: list[tuple[int, dict[str, Any]]] = []

    def _body_json(self) -> dict[str, Any]:
        return self.body

    def _json(self, status: int, data: dict[str, Any]) -> None:
        self.responses.append((status, data))


# ---------------------------------------------------------------------------
# HTTP entries
# ---------------------------------------------------------------------------

PROVISION_ROUTES = [
    (provision.start_install, {}),
    (provision.start_remote, {"target": "pi@10.0.0.5"}),
    (provision.start_bundle, {}),
]


@pytest.mark.parametrize("key", ["project", "template"])
@pytest.mark.parametrize("value", BAD_NAMES, ids=repr)
@pytest.mark.parametrize(("route", "extra"), PROVISION_ROUTES, ids=lambda r: getattr(r, "__name__", ""))
@patch("anolis_workbench.server.routes.provision.threading.Thread")
def test_provision_routes_refuse_an_invalid_name(
    thread: MagicMock, route: Any, extra: dict[str, Any], value: Any, key: str
) -> None:
    handler = FakeHandler({**extra, key: value, "force": True})

    route(handler)

    status, data = handler.responses[0]
    assert status == 400
    assert key in data["error"]
    if isinstance(value, str) and len(value) < 65:
        assert value not in data["error"]
    thread.assert_not_called()


@pytest.mark.parametrize(("route", "extra"), PROVISION_ROUTES, ids=lambda r: getattr(r, "__name__", ""))
@patch("anolis_workbench.server.routes.provision.threading.Thread")
def test_provision_routes_accept_a_valid_or_absent_name(thread: MagicMock, route: Any, extra: dict[str, Any]) -> None:
    for body in ({"project": "rig_A-1", "template": "sim-quickstart"}, {"project": ""}, {}):
        handler = FakeHandler({**extra, **body})

        route(handler)

        assert handler.responses[0][0] == 202, (body, handler.responses)
    assert thread.call_count == 3


@pytest.mark.parametrize("value", BAD_NAMES, ids=repr)
def test_create_project_refuses_an_invalid_template(monkeypatch: pytest.MonkeyPatch, value: Any) -> None:
    create = MagicMock()
    monkeypatch.setattr(projects, "create_project_from_template", create)
    handler = FakeHandler({"name": "rig", "template": value})

    compose.create_project(handler)

    status, data = handler.responses[0]
    assert status == 400
    if isinstance(value, str) and len(value) < 65:
        assert value not in data["error"]
    create.assert_not_called()


def test_create_project_passes_a_valid_template_through(monkeypatch: pytest.MonkeyPatch) -> None:
    create = MagicMock(return_value={"ok": True})
    monkeypatch.setattr(projects, "create_project_from_template", create)
    handler = FakeHandler({"name": "rig", "template": "sim-quickstart"})

    compose.create_project(handler)

    assert handler.responses[0][0] == 201
    create.assert_called_once_with("rig", "sim-quickstart")


# ---------------------------------------------------------------------------
# CLI entries
# ---------------------------------------------------------------------------

CLI_BASES = [
    ["install"],
    ["bundle", "--arch", "x86_64", "--out", "out"],
    ["remote", "--target", "pi@10.0.0.5"],
]


@pytest.mark.parametrize("flag", ["--project", "--template"])
@pytest.mark.parametrize("value", BAD_STRINGS, ids=repr)
@pytest.mark.parametrize("base", CLI_BASES, ids=lambda b: b[0])
def test_provision_cli_refuses_an_invalid_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], base: list[str], value: str, flag: str
) -> None:
    monkeypatch.setattr(sys, "argv", ["anolis-provision", *base, f"{flag}={value}"])

    with pytest.raises(SystemExit) as exc:
        provision_cli._parse_args()

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert flag in err
    if len(value) < 65:
        assert value not in err.split(f"{flag}:", 1)[1]


@pytest.mark.parametrize("base", CLI_BASES, ids=lambda b: b[0])
def test_provision_cli_accepts_a_valid_name(monkeypatch: pytest.MonkeyPatch, base: list[str]) -> None:
    monkeypatch.setattr(
        sys, "argv", ["anolis-provision", *base, "--project", "rig_A-1", "--template", "sim-quickstart"]
    )

    args = provision_cli._parse_args()

    assert (args.project, args.template) == ("rig_A-1", "sim-quickstart")


@pytest.mark.parametrize("key", ["project", "template"])
@pytest.mark.parametrize("value", BAD_NAMES, ids=repr)
def test_fleet_file_refuses_an_invalid_name(tmp_path: pathlib.Path, value: Any, key: str) -> None:
    fleet_file = tmp_path / "fleet.yaml"
    fleet_file.write_text(
        yaml.dump({"targets": [{"name": "a", "host": "pi@10.0.0.5", key: value}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=key) as exc:
        fleet.load_fleet_file(fleet_file)

    if isinstance(value, str) and len(value) < 65:
        assert value not in str(exc.value)


def test_fleet_file_refuses_an_invalid_default(tmp_path: pathlib.Path) -> None:
    fleet_file = tmp_path / "fleet.yaml"
    fleet_file.write_text(
        yaml.dump({"defaults": {"project": ".."}, "targets": [{"name": "a", "host": "pi@10.0.0.5"}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="project"):
        fleet.load_fleet_file(fleet_file)
