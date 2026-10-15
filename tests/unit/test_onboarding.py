"""Unit tests for onboarding detection endpoint."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from anolis_workbench.server.routes import onboarding


class FakeHandler:
    """Minimal mock of BaseHTTPRequestHandler."""

    def __init__(self) -> None:
        self._responses: list[tuple[int, dict[str, Any]]] = []

    def _json(self, status: int, data: dict[str, Any]) -> None:
        self._responses.append((status, data))


class TestGetOnboardingStatus:
    def test_first_run_when_nothing_exists(self, tmp_path: Path) -> None:
        handler = FakeHandler()
        with (
            patch.object(onboarding, "_systems_root", return_value=tmp_path / "missing"),
            patch.object(onboarding, "_has_runtime", return_value=False),
            patch.object(onboarding, "_fleet_registry_path", return_value=tmp_path / "fleet.yaml"),
        ):
            onboarding.get_onboarding_status(handler)

        status, data = handler._responses[0]
        assert status == 200
        assert data["first_run"] is True
        assert data["has_projects"] is False
        assert data["has_runtime"] is False

    def test_still_first_run_when_a_failed_install_left_a_project(self, tmp_path: Path) -> None:
        # The install job creates the workspace project before install.sh
        # runs, so a project alone is no sign that a deploy succeeded (#424).
        systems = tmp_path / "systems"
        (systems / "sim-quickstart").mkdir(parents=True)
        (systems / "sim-quickstart" / "workbench.json").write_text(
            json.dumps({"meta": {"name": "sim-quickstart", "created_by": "onboarding"}})
        )

        handler = FakeHandler()
        with (
            patch.object(onboarding, "_systems_root", return_value=systems),
            patch.object(onboarding, "_has_runtime", return_value=False),
            patch.object(onboarding, "_fleet_registry_path", return_value=tmp_path / "fleet.yaml"),
        ):
            onboarding.get_onboarding_status(handler)

        status, data = handler._responses[0]
        assert status == 200
        assert data["first_run"] is True
        assert data["has_projects"] is True

    def test_not_first_run_with_a_user_authored_project(self, tmp_path: Path) -> None:
        systems = tmp_path / "systems"
        (systems / "my-rig").mkdir(parents=True)
        (systems / "my-rig" / "workbench.json").write_text(json.dumps({"meta": {"name": "my-rig"}}))

        handler = FakeHandler()
        with (
            patch.object(onboarding, "_systems_root", return_value=systems),
            patch.object(onboarding, "_has_runtime", return_value=False),
            patch.object(onboarding, "_fleet_registry_path", return_value=tmp_path / "fleet.yaml"),
        ):
            onboarding.get_onboarding_status(handler)

        _, data = handler._responses[0]
        assert data["first_run"] is False

    def test_not_first_run_after_a_remote_deploy_registered_its_host(self, tmp_path: Path) -> None:
        registry = tmp_path / "fleet.yaml"
        registry.write_text("targets:\n  - name: rig\n    host: pi@rig\n    project: sim-quickstart\n")

        handler = FakeHandler()
        with (
            patch.object(onboarding, "_systems_root", return_value=tmp_path / "missing"),
            patch.object(onboarding, "_has_runtime", return_value=False),
            patch.object(onboarding, "_fleet_registry_path", return_value=registry),
        ):
            onboarding.get_onboarding_status(handler)

        _, data = handler._responses[0]
        assert data["first_run"] is False
        assert data["has_fleet_entry"] is True

    def test_not_first_run_when_runtime_exists(self, tmp_path: Path) -> None:
        handler = FakeHandler()
        with (
            patch.object(onboarding, "_systems_root", return_value=tmp_path / "missing"),
            patch.object(onboarding, "_has_runtime", return_value=True),
            patch.object(onboarding, "_fleet_registry_path", return_value=tmp_path / "fleet.yaml"),
        ):
            onboarding.get_onboarding_status(handler)

        status, data = handler._responses[0]
        assert status == 200
        assert data["first_run"] is False
        assert data["has_runtime"] is True

    def test_runtime_path_included(self) -> None:
        handler = FakeHandler()
        with (
            patch.object(onboarding, "_systems_root", return_value=Path("/nonexistent")),
            patch.object(onboarding, "_has_runtime", return_value=False),
            patch.object(onboarding, "_runtime_path", return_value="/opt/anolis/bin/anolis-runtime"),
            patch.object(onboarding, "_fleet_registry_path", return_value=Path("/nonexistent/fleet.yaml")),
        ):
            onboarding.get_onboarding_status(handler)

        _, data = handler._responses[0]
        assert data["runtime_path"] == "/opt/anolis/bin/anolis-runtime"


class TestHelpers:
    def test_has_projects_empty_dir(self, tmp_path: Path) -> None:
        with patch.object(onboarding, "_systems_root", return_value=tmp_path):
            assert onboarding._has_projects() is False

    def test_has_projects_with_subdirs(self, tmp_path: Path) -> None:
        (tmp_path / "project1").mkdir()
        with patch.object(onboarding, "_systems_root", return_value=tmp_path):
            assert onboarding._has_projects() is True

    def test_has_projects_nonexistent_dir(self) -> None:
        with patch.object(onboarding, "_systems_root", return_value=Path("/no/such/path")):
            assert onboarding._has_projects() is False

    def test_has_runtime_false(self) -> None:
        with patch.object(onboarding, "_runtime_path", return_value="/no/such/binary"):
            assert onboarding._has_runtime() is False

    def test_has_runtime_true(self, tmp_path: Path) -> None:
        binary = tmp_path / "anolis-runtime"
        binary.touch()
        with patch.object(onboarding, "_runtime_path", return_value=str(binary)):
            assert onboarding._has_runtime() is True

    def test_has_fleet_entry_missing_or_empty_registry(self, tmp_path: Path) -> None:
        registry = tmp_path / "fleet.yaml"
        with patch.object(onboarding, "_fleet_registry_path", return_value=registry):
            assert onboarding._has_fleet_entry() is False
            registry.write_text("targets: []\n")
            assert onboarding._has_fleet_entry() is False
            registry.write_text("targets: [\n")
            assert onboarding._has_fleet_entry() is False

    def test_has_fleet_entry_with_a_target(self, tmp_path: Path) -> None:
        registry = tmp_path / "fleet.yaml"
        registry.write_text("targets:\n  - name: rig\n    host: pi@rig\n")
        with patch.object(onboarding, "_fleet_registry_path", return_value=registry):
            assert onboarding._has_fleet_entry() is True
