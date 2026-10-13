"""Unit tests for provision route handlers."""

from __future__ import annotations

import json
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock, patch

from anolis_workbench.server.routes import provision


class FakeHandler:
    """Minimal mock of BaseHTTPRequestHandler for testing route handlers."""

    def __init__(self, body: dict[str, Any] | None = None) -> None:
        raw = json.dumps(body).encode() if body else b"{}"
        self.rfile = BytesIO(raw)
        self.headers = {"Content-Length": str(len(raw))}
        self._responses: list[tuple[int, dict[str, Any]]] = []
        self._sse_data: list[str] = []
        self.wfile = BytesIO()

    def _json(self, status: int, data: dict[str, Any]) -> None:
        self._responses.append((status, data))

    def send_response(self, code: int) -> None:
        self._status = code

    def send_header(self, key: str, value: str) -> None:
        pass

    def end_headers(self) -> None:
        pass


class TestStartInstall:
    @patch("anolis_workbench.server.routes.provision.installer")
    def test_returns_202_with_job_id(self, mock_installer: MagicMock) -> None:
        mock_installer.install.return_value = MagicMock(verified_versions={"runtime": "0.1.21"})
        handler = FakeHandler({"project": "bioreactor-v1"})

        provision.start_install(handler)

        assert len(handler._responses) == 1
        status, data = handler._responses[0]
        assert status == 202
        assert "job_id" in data


class TestStartBundle:
    """The arch reaches install.sh --stage --arch verbatim, so it is checked
    against the same allowlist as the provision CLI (#338)."""

    @patch("anolis_workbench.server.routes.provision._run_bundle_job")
    @patch("anolis_workbench.server.routes.provision._create_job")
    def test_rejects_an_unknown_arch_without_starting_a_job(self, create_job: MagicMock, _run: MagicMock) -> None:
        handler = FakeHandler({"project": "demo", "arch": "riscv64"})

        provision.start_bundle(handler)

        status, data = handler._responses[0]
        assert status == 400
        assert "riscv64" in data["error"]
        create_job.assert_not_called()

    @patch("anolis_workbench.server.routes.provision._run_bundle_job")
    @patch("anolis_workbench.server.routes.provision._create_job")
    def test_accepts_the_allowlisted_archs_and_no_arch(self, create_job: MagicMock, _run: MagicMock) -> None:
        create_job.return_value = MagicMock(job_id="job-1")
        # No arch: the job falls back to the host's arch, as before.
        for body in ({"arch": "arm64"}, {"arch": "aarch64"}, {"arch": "x86_64"}, {}):
            handler = FakeHandler({"project": "demo", **body})

            provision.start_bundle(handler)

            status, data = handler._responses[0]
            assert status == 202, (body, data)
        assert create_job.call_count == 4


class TestStartRemote:
    def test_returns_400_without_target(self) -> None:
        handler = FakeHandler({"project": "bioreactor-v1"})

        provision.start_remote(handler)

        status, data = handler._responses[0]
        assert status == 400
        assert "target" in data["error"]

    @patch("anolis_workbench.server.routes.provision.ParamikoSSHExecutor")
    @patch("anolis_workbench.server.routes.provision.installer")
    def test_returns_202_with_target(self, mock_installer: MagicMock, mock_exec: MagicMock) -> None:
        mock_installer.install.return_value = MagicMock(verified_versions={"runtime": "0.1.21"})
        handler = FakeHandler({"target": "pi@192.168.1.10", "project": "bioreactor-v1"})

        provision.start_remote(handler)

        status, data = handler._responses[0]
        assert status == 202
        assert "job_id" in data


class TestCancelJob:
    def test_cancel_running_job(self) -> None:
        job = provision._create_job()
        handler = FakeHandler()

        provision.cancel_job(handler, job.job_id)

        status, data = handler._responses[0]
        assert status == 200
        assert data["status"] == "cancelled"
        assert job.status == "cancelled"

    def test_cancel_nonexistent_job(self) -> None:
        handler = FakeHandler()

        provision.cancel_job(handler, "nonexistent")

        status, data = handler._responses[0]
        assert status == 404

    def test_cancel_already_done_job(self) -> None:
        job = provision._create_job()
        job.status = "done"
        handler = FakeHandler()

        provision.cancel_job(handler, job.job_id)

        status, data = handler._responses[0]
        assert status == 409


class TestGetStatus:
    def test_returns_404_for_nonexistent_job(self) -> None:
        handler = FakeHandler()

        provision.get_status(handler, "bogus")

        status, data = handler._responses[0]
        assert status == 404

    def test_streams_events_for_completed_job(self) -> None:
        job = provision._create_job()
        job.events.append({"stage": "download", "detail": "downloading runtime"})
        job.status = "done"

        handler = FakeHandler()
        provision.get_status(handler, job.job_id)

        output = handler.wfile.getvalue().decode()
        assert "download" in output
        assert "done" in output


class TestJobManagement:
    def test_create_and_get_job(self) -> None:
        job = provision._create_job()
        assert provision._get_job(job.job_id) is job
        assert provision._get_job("missing") is None
