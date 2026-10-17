"""Host and Origin checks on every request.

The server has no authentication. Without these checks a page on another
site could POST to it (a text/plain body needs no CORS preflight), and a DNS
rebinding page could reach it under the attacker's own name. The /v0 proxy
would forward either request to the runtime with the workbench's token.

The end-to-end tests run the real handler in-process against a stub runtime
that records what reaches it, with a scratch workspace.
"""

from __future__ import annotations

import http.client
import http.server
import json
import pathlib
import threading
from collections.abc import Generator
from typing import Any

import pytest

from anolis_workbench.server import request_origin
from anolis_workbench.server.request_origin import rejection

# ---------------------------------------------------------------------------
# rejection()
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fixed_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_origin, "_own_hostname", lambda: "benchpi")
    monkeypatch.delenv(request_origin.ALLOWED_HOSTS_ENV, raising=False)


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1:3010",
        "127.0.0.1",
        "[::1]:3010",
        "192.168.1.40:3010",
        "10.0.0.5",
        "localhost:3010",
        "LOCALHOST:3010",
        "app.localhost:5173",
        "benchpi:3010",
        "BenchPi.local:3010",
        "benchpi.local.:3010",
    ],
)
def test_host_naming_this_machine_is_served(host: str) -> None:
    assert rejection(host, None) is None


@pytest.mark.parametrize(
    "host",
    [
        "rebind.attacker.example:3010",
        "attacker.example",
        "127.0.0.1.attacker.example:3010",
        "localhost.attacker.example",
        "benchpi.attacker.example:3010",
        "otherpi.local:3010",
    ],
)
def test_foreign_host_is_refused(host: str) -> None:
    reason = rejection(host, None)
    assert reason is not None
    assert request_origin.ALLOWED_HOSTS_ENV in reason


@pytest.mark.parametrize("host", ["", "user@127.0.0.1", "127.0.0.1:notaport", "127.0.0.1/x", "127.0.0.1:99999"])
def test_malformed_host_is_refused(host: str) -> None:
    assert rejection(host, None) is not None


def test_allowed_hosts_env_adds_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(request_origin.ALLOWED_HOSTS_ENV, " workbench.lab.example , Other.Example ")
    assert rejection("workbench.lab.example:3010", None) is None
    assert rejection("other.example", None) is None
    assert rejection("third.example", None) is not None


def test_missing_host_without_origin_is_served() -> None:
    assert rejection(None, None) is None


def test_missing_host_with_origin_is_refused() -> None:
    assert rejection(None, "http://127.0.0.1:3010") is not None


@pytest.mark.parametrize(
    ("host", "origin"),
    [
        ("127.0.0.1:3010", "http://127.0.0.1:3010"),
        ("localhost:5173", "http://localhost:5173"),
        ("benchpi.local:3010", "http://BenchPi.local:3010"),
        ("[::1]:3010", "http://[::1]:3010"),
        ("192.168.1.40", "http://192.168.1.40"),
        ("192.168.1.40", "http://192.168.1.40:80"),
        ("benchpi", "https://benchpi"),
    ],
)
def test_same_origin_is_served(host: str, origin: str) -> None:
    assert rejection(host, origin) is None


@pytest.mark.parametrize(
    ("host", "origin"),
    [
        ("127.0.0.1:3010", "http://attacker.example"),
        ("127.0.0.1:3010", "https://example.com"),
        ("127.0.0.1:3010", "http://127.0.0.1:5173"),
        ("127.0.0.1:3010", "http://localhost:3010"),
        ("127.0.0.1:3010", "null"),
        ("127.0.0.1:3010", "file://"),
        ("127.0.0.1:3010", "chrome-extension://abc"),
        ("127.0.0.1:3010", "http://127.0.0.1:bad"),
    ],
)
def test_cross_origin_is_refused(host: str, origin: str) -> None:
    assert rejection(host, origin) is not None


# ---------------------------------------------------------------------------
# End to end through the real handler
# ---------------------------------------------------------------------------


class _RecordingRuntime(http.server.BaseHTTPRequestHandler):
    received: list[str] = []

    def log_message(self, *_args: Any) -> None:
        pass

    def _answer(self) -> None:
        self.received.append(f"{self.command} {self.path}")
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_DELETE = _answer


@pytest.fixture
def server(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Generator[dict[str, Any], None, None]:
    from anolis_workbench.core import projects as projects_module
    from anolis_workbench.server import app

    systems = tmp_path / "systems"
    systems.mkdir()
    monkeypatch.setattr(projects_module, "SYSTEMS_ROOT", systems)

    _RecordingRuntime.received = []
    runtime = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RecordingRuntime)
    threading.Thread(target=runtime.serve_forever, daemon=True).start()
    monkeypatch.setenv("ANOLIS_WORKBENCH_RUNTIME_URL", f"http://127.0.0.1:{runtime.server_address[1]}")
    monkeypatch.setenv("ANOLIS_WORKBENCH_RUNTIME_TOKEN", "test-token")

    wb = app._WorkbenchServer(("127.0.0.1", 0), app._Handler)
    wb.config = app.resolve_config("127.0.0.1", 0, False)
    threading.Thread(target=wb.serve_forever, daemon=True).start()
    try:
        yield {"port": wb.server_address[1], "systems": systems, "runtime": _RecordingRuntime.received}
    finally:
        wb.shutdown()
        runtime.shutdown()
        wb.server_close()
        runtime.server_close()


def _send(port: int, method: str, path: str, headers: dict[str, str], body: str | None = None) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.putrequest(method, path, skip_host=True)
        for key, value in headers.items():
            conn.putheader(key, value)
        data = body.encode() if body is not None else b""
        if body is not None:
            conn.putheader("Content-Length", str(len(data)))
        conn.endheaders(data if body is not None else None)
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


_CREATE = json.dumps({"name": "planted", "template": "sim-quickstart"})
_CALL = json.dumps({"device_id": "relay0", "function_id": 1, "args": {}})


def test_cross_site_post_creates_no_project(server: dict[str, Any]) -> None:
    status = _send(
        server["port"],
        "POST",
        "/api/projects",
        {"Host": f"127.0.0.1:{server['port']}", "Origin": "https://example.com", "Content-Type": "text/plain"},
        _CREATE,
    )
    assert status == 403
    assert list(server["systems"].iterdir()) == []


def test_cross_site_post_does_not_reach_the_runtime(server: dict[str, Any]) -> None:
    status = _send(
        server["port"],
        "POST",
        "/v0/call",
        {"Host": f"127.0.0.1:{server['port']}", "Origin": "https://example.com", "Content-Type": "text/plain"},
        _CALL,
    )
    assert status == 403
    assert server["runtime"] == []


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/projects", None),
        ("GET", "/v0/devices", None),
        ("POST", "/api/projects", _CREATE),
        ("POST", "/v0/call", _CALL),
        ("PUT", "/api/projects/planted", "{}"),
        ("DELETE", "/api/projects/planted", None),
    ],
)
def test_rebinding_host_is_refused(server: dict[str, Any], method: str, path: str, body: str | None) -> None:
    host = f"rebind.attacker.example:{server['port']}"
    headers = {"Host": host, "Content-Type": "application/json"}
    if method != "GET":
        headers["Origin"] = f"http://{host}"
    assert _send(server["port"], method, path, headers, body) == 403
    assert server["runtime"] == []
    assert list(server["systems"].iterdir()) == []


def test_same_origin_post_is_served(server: dict[str, Any]) -> None:
    host = f"127.0.0.1:{server['port']}"
    status = _send(
        server["port"],
        "POST",
        "/api/projects",
        {"Host": host, "Origin": f"http://{host}", "Content-Type": "application/json"},
        _CREATE,
    )
    assert status == 201
    assert [p.name for p in server["systems"].iterdir()] == ["planted"]


def test_same_origin_proxy_call_reaches_the_runtime(server: dict[str, Any]) -> None:
    host = f"127.0.0.1:{server['port']}"
    status = _send(
        server["port"],
        "POST",
        "/v0/call",
        {"Host": host, "Origin": f"http://{host}", "Content-Type": "application/json"},
        _CALL,
    )
    assert status == 200
    assert server["runtime"] == ["POST /v0/call"]


def test_request_without_origin_is_served(server: dict[str, Any]) -> None:
    # curl and scripts send no Origin.
    host = f"127.0.0.1:{server['port']}"
    status = _send(server["port"], "GET", "/api/projects", {"Host": host})
    assert status == 200


def test_hostname_of_this_machine_is_served(server: dict[str, Any]) -> None:
    host = f"benchpi.local:{server['port']}"
    status = _send(
        server["port"],
        "POST",
        "/v0/call",
        {"Host": host, "Origin": f"http://{host}", "Content-Type": "application/json"},
        _CALL,
    )
    assert status == 200
    assert server["runtime"] == ["POST /v0/call"]
