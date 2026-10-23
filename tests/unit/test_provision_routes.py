"""Unit tests for provision route handlers."""

from __future__ import annotations

import json
import socket
import struct
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from anolis_workbench.server.routes import provision


class FakeHandler:
    """Minimal mock of BaseHTTPRequestHandler for testing route handlers."""

    def __init__(self, body: dict[str, Any] | None = None) -> None:
        raw = json.dumps(body).encode() if body else b"{}"
        self.rfile = BytesIO(raw)
        self.headers = {"Content-Length": str(len(raw))}
        self.client_address = ("127.0.0.1", 50000)
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


class TestStartInstallSudoPassword:
    """The password leaves the request as a bytearray for the job, and is
    never kept in the job's params (#230)."""

    @patch("anolis_workbench.server.routes.provision.threading.Thread")
    def test_password_goes_to_the_job_apart_from_its_params(self, thread: MagicMock) -> None:
        handler = FakeHandler({"project": "rig", "sudo_password": "pw-7f3c"})
        handler.client_address = ("127.0.0.1", 50000)

        provision.start_install(handler)

        assert handler._responses[0][0] == 202
        _job, params, password = thread.call_args.kwargs["args"]
        assert params == {"project": "rig"}
        assert isinstance(password, bytearray)
        assert password == bytearray(b"pw-7f3c")

    @patch("anolis_workbench.server.routes.provision.threading.Thread")
    def test_no_password_hands_the_job_none(self, thread: MagicMock) -> None:
        handler = FakeHandler({"project": "rig", "sudo_password": ""})
        handler.client_address = ("127.0.0.1", 50000)

        provision.start_install(handler)

        assert thread.call_args.kwargs["args"][2] is None

    @patch("anolis_workbench.server.routes.provision.threading.Thread")
    def test_password_is_zeroed_when_the_job_cannot_start(self, thread: MagicMock) -> None:
        thread.return_value.start.side_effect = RuntimeError("can't start new thread")
        handler = FakeHandler({"project": "rig", "sudo_password": "pw-7f3c"})

        with pytest.raises(RuntimeError):
            provision.start_install(handler)

        password = thread.call_args.kwargs["args"][2]
        assert password == bytearray(len(b"pw-7f3c"))

    @pytest.mark.parametrize(
        ("client", "value", "code"),
        [("192.168.1.20", "pw-7f3c", 403), ("127.0.0.1", 1234, 400)],
    )
    @patch("anolis_workbench.server.routes.provision.threading.Thread")
    def test_refused_off_loopback_or_when_not_a_string(
        self, thread: MagicMock, client: str, value: object, code: int
    ) -> None:
        handler = FakeHandler({"project": "rig", "sudo_password": value})
        handler.client_address = (client, 50000)

        provision.start_install(handler)

        status, data = handler._responses[0]
        assert status == code
        assert "pw-7f3c" not in json.dumps(data)
        thread.assert_not_called()


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

    @pytest.mark.parametrize("error", [BrokenPipeError, ConnectionResetError, ConnectionAbortedError])
    def test_any_disconnect_error_ends_the_stream(self, error: type[OSError]) -> None:
        class GoneClient(BytesIO):
            def write(self, _data: Any) -> int:
                raise error()

        job = provision._create_job()
        job.events.append({"stage": "download", "detail": "downloading runtime"})
        handler = FakeHandler()
        handler.wfile = GoneClient()
        try:
            provision.get_status(handler, job.job_id)
        finally:
            job.status = "done"

    def test_client_gone_before_next_event_ends_quietly(self) -> None:
        """A closed EventSource (reload, closed tab) must not log a traceback
        or keep the handler waiting on the job (#468)."""
        errors: list[BaseException | None] = []
        returned: list[bool] = []
        finished = threading.Event()

        class Server(ThreadingHTTPServer):
            def handle_error(self, request: Any, client_address: Any) -> None:
                errors.append(sys.exc_info()[1])

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                try:
                    provision.get_status(self, self.path.split("/")[-1])
                    returned.append(True)
                finally:
                    finished.set()

        job = provision._create_job()
        server = Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            client = socket.create_connection(("127.0.0.1", server.server_port), timeout=5)
            client.sendall(f"GET /api/provision/status/{job.job_id} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
            received = b""
            while b"\r\n\r\n" not in received:
                received += client.recv(1024)
            # Reset rather than FIN, so the server's next write fails at once.
            client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            client.close()

            job.events.append({"stage": "download", "detail": "downloading runtime"})
            job.events.append({"stage": "verify", "detail": "verifying runtime"})

            assert finished.wait(5), "handler still waiting on the job"
        finally:
            job.status = "done"
            server.shutdown()
            server.server_close()
        assert errors == []
        assert returned == [True]


class TestJobManagement:
    def test_create_and_get_job(self) -> None:
        job = provision._create_job()
        assert provision._get_job(job.job_id) is job
        assert provision._get_job("missing") is None
