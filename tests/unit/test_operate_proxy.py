"""Unit tests for the operate proxy's remote-runtime override and Bearer auth.

The proxy historically targeted only the locally-launched runtime and
forwarded no Authorization header, so a laptop-hosted workbench received 401
from an auth-enabled device (auth-by-default installs bind 0.0.0.0 with auth
on). ANOLIS_WORKBENCH_RUNTIME_URL / ANOLIS_WORKBENCH_RUNTIME_TOKEN are the
interim remote-operate surface until the device-registry UX
(anolishq/anolis#164).
"""

from __future__ import annotations

import io
import urllib.request
from typing import Any

import pytest

from anolis_workbench.core import workbench_service
from anolis_workbench.server.routes import operate


@pytest.fixture(autouse=True)
def _no_host_token_copy(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never read this machine's real token copy."""
    monkeypatch.setattr(workbench_service, "RUNTIME_TOKEN_FILE", tmp_path / "absent-token")


class _FakeHandler:
    """Just enough of BaseHTTPRequestHandler for proxy_runtime."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {"Content-Type": "application/json"}
        self.rfile = io.BytesIO(b"")
        self.wfile = io.BytesIO()
        self.sent_status: int | None = None
        self.sent_headers: list[tuple[str, str]] = []
        self.json_payloads: list[tuple[int, dict[str, Any]]] = []

    def send_response(self, status: int) -> None:
        self.sent_status = status

    def send_header(self, key: str, value: str) -> None:
        self.sent_headers.append((key, value))

    def end_headers(self) -> None:
        pass

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        self.json_payloads.append((status, payload))


class _FakeResponse:
    status = 200
    headers: dict[str, str] = {}

    def read(self, *_args: Any) -> bytes:
        return b"{}"

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc: Any) -> None:
        pass


def test_runtime_base_env_override_wins_without_local_runtime(monkeypatch) -> None:
    monkeypatch.setenv("ANOLIS_WORKBENCH_RUNTIME_URL", "http://192.0.2.10:8080/")
    # Local launcher reports nothing running; the override must not care.
    monkeypatch.setattr(operate.launcher_module, "get_status", lambda: {"running": False})

    base, error, status = operate._runtime_base()

    assert base == "http://192.0.2.10:8080"  # trailing slash stripped
    assert error is None
    assert status == 200


def test_runtime_base_without_override_requires_local_runtime(monkeypatch) -> None:
    monkeypatch.delenv("ANOLIS_WORKBENCH_RUNTIME_URL", raising=False)
    monkeypatch.setattr(operate.launcher_module, "get_status", lambda: {"running": False})

    base, error, status = operate._runtime_base()

    assert base is None
    assert status == 503


def test_proxy_attaches_bearer_token_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("ANOLIS_WORKBENCH_RUNTIME_URL", "http://192.0.2.10:8080")
    monkeypatch.setenv("ANOLIS_WORKBENCH_RUNTIME_TOKEN", "sekrit-token")

    captured: dict[str, Any] = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float | None = None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        return _FakeResponse()

    monkeypatch.setattr(operate.urllib.request, "urlopen", fake_urlopen)

    handler = _FakeHandler()
    operate.proxy_runtime(handler, "GET", "/v0/runtime/status")

    assert captured["url"] == "http://192.0.2.10:8080/v0/runtime/status"
    assert captured["auth"] == "Bearer sekrit-token"
    assert handler.sent_status == 200


def test_proxy_sends_no_auth_header_without_token(monkeypatch) -> None:
    monkeypatch.setenv("ANOLIS_WORKBENCH_RUNTIME_URL", "http://192.0.2.10:8080")
    monkeypatch.delenv("ANOLIS_WORKBENCH_RUNTIME_TOKEN", raising=False)

    captured: dict[str, Any] = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float | None = None):
        captured["auth"] = req.get_header("Authorization")
        return _FakeResponse()

    monkeypatch.setattr(operate.urllib.request, "urlopen", fake_urlopen)

    handler = _FakeHandler()
    operate.proxy_runtime(handler, "GET", "/v0/runtime/status")

    assert captured["auth"] is None


def test_runtime_base_targets_the_running_variants_port(monkeypatch) -> None:
    """Operate must proxy to the port the RUNNING variant declares (#255).

    A fixed default here is worse than an error: the launcher still reports the
    runtime as running, so the UI shows a live project and then 502s on every
    call to it.
    """
    monkeypatch.delenv("ANOLIS_WORKBENCH_RUNTIME_URL", raising=False)
    monkeypatch.setattr(operate.launcher_module, "get_status", lambda: {"running": True, "active_project": "rig"})
    monkeypatch.setattr(
        operate.projects_module,
        "get_project",
        lambda name: {
            "format": "machine-profile",
            "authored": True,
            "profile": {"machine_id": "rig", "runtime_profiles": {"manual": "x"}, "providers": {}},
            "variants": {"manual": {"http": {"bind": "127.0.0.1", "port": 8085}}},
            "providers": {},
            "launch": {"variant": "manual"},
        },
    )

    base, error, status = operate._runtime_base()
    assert (base, error, status) == ("http://127.0.0.1:8085", None, 200)


def test_runtime_base_follows_the_launch_variant(monkeypatch) -> None:
    monkeypatch.delenv("ANOLIS_WORKBENCH_RUNTIME_URL", raising=False)
    monkeypatch.setattr(operate.launcher_module, "get_status", lambda: {"running": True, "active_project": "rig"})
    monkeypatch.setattr(
        operate.projects_module,
        "get_project",
        lambda name: {
            "format": "machine-profile",
            "authored": True,
            "profile": {"machine_id": "rig", "runtime_profiles": {}, "providers": {}},
            "variants": {
                "manual": {"http": {"port": 8080}},
                "automation": {"http": {"port": 9090}},
            },
            "providers": {},
            "launch": {"variant": "automation"},
        },
    )

    base, _, _ = operate._runtime_base()
    assert base == "http://127.0.0.1:9090"


class _GoneClient(io.BytesIO):
    """A response stream whose client has disconnected."""

    def __init__(self, error: type[OSError]) -> None:
        super().__init__()
        self.error = error

    def write(self, _data: Any) -> int:
        raise self.error()


class _EventStream(_FakeResponse):
    def __init__(self) -> None:
        self.reads = 0
        self.closed = False

    def read(self, *_args: Any) -> bytes:
        self.reads += 1
        return b"data: {}\n\n"

    def __exit__(self, *_exc: Any) -> None:
        self.closed = True


@pytest.mark.parametrize("error", [BrokenPipeError, ConnectionResetError, ConnectionAbortedError])
def test_event_stream_ends_quietly_when_the_client_leaves(monkeypatch, error: type[OSError]) -> None:
    """A closed Operate tab must not raise out of the /v0/events proxy, and
    the proxy must stop reading the runtime's stream for it (#468)."""
    monkeypatch.setenv("ANOLIS_WORKBENCH_RUNTIME_URL", "http://192.0.2.10:8080")
    upstream = _EventStream()
    monkeypatch.setattr(operate.urllib.request, "urlopen", lambda req: upstream)

    handler = _FakeHandler()
    handler.wfile = _GoneClient(error)
    operate.proxy_runtime(handler, "GET", "/v0/events")

    assert upstream.reads == 1
    assert upstream.closed


# install --workbench-service copies the local runtime's token into a file
# the workbench reads; the value below is obviously fake.
_CRED_TOKEN = "fake-credential-token-0000"


def _proxied_auth(monkeypatch: pytest.MonkeyPatch) -> str | None:
    captured: dict[str, Any] = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float | None = None):
        captured["auth"] = req.get_header("Authorization")
        return _FakeResponse()

    monkeypatch.setattr(operate.urllib.request, "urlopen", fake_urlopen)
    operate.proxy_runtime(_FakeHandler(), "GET", "/v0/runtime/status")
    auth: str | None = captured["auth"]
    return auth


@pytest.fixture
def credential(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    copy = tmp_path / "workbench-runtime-token"
    copy.write_text(_CRED_TOKEN + "\n", encoding="utf-8")
    monkeypatch.setattr(workbench_service, "RUNTIME_TOKEN_FILE", copy)
    monkeypatch.delenv("ANOLIS_WORKBENCH_RUNTIME_TOKEN", raising=False)


def _local_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANOLIS_WORKBENCH_RUNTIME_URL", raising=False)
    monkeypatch.setattr(operate, "_runtime_base", lambda: ("http://127.0.0.1:8080", None, 200))


def test_proxy_sends_the_credential_token_to_the_local_runtime(credential: None, monkeypatch) -> None:
    _local_runtime(monkeypatch)
    assert _proxied_auth(monkeypatch) == f"Bearer {_CRED_TOKEN}"


def test_an_explicit_token_wins_over_the_credential(credential: None, monkeypatch) -> None:
    _local_runtime(monkeypatch)
    monkeypatch.setenv("ANOLIS_WORKBENCH_RUNTIME_TOKEN", "explicit-fake-token")
    assert _proxied_auth(monkeypatch) == "Bearer explicit-fake-token"


def test_the_credential_token_never_goes_to_a_remote_override(credential: None, monkeypatch) -> None:
    monkeypatch.setenv("ANOLIS_WORKBENCH_RUNTIME_URL", "http://192.0.2.10:8080")
    assert _proxied_auth(monkeypatch) is None


def test_the_credential_token_never_goes_to_a_loopback_override(credential: None, monkeypatch) -> None:
    """A loopback override may be an ssh tunnel to another machine, so the
    override rule holds even where the host check alone would pass."""
    monkeypatch.setenv("ANOLIS_WORKBENCH_RUNTIME_URL", "http://127.0.0.1:18080")
    assert _proxied_auth(monkeypatch) is None


def test_no_credential_sends_no_token(monkeypatch) -> None:
    _local_runtime(monkeypatch)
    monkeypatch.delenv("ANOLIS_WORKBENCH_RUNTIME_TOKEN", raising=False)
    assert _proxied_auth(monkeypatch) is None


# ---------------------------------------------------------------------------
# The credential token goes only to this machine, whatever a project's
# http.bind says.
# ---------------------------------------------------------------------------


def _project_bind(monkeypatch: pytest.MonkeyPatch, bind: object) -> None:
    """The launcher reports a running project whose runtime binds `bind`."""
    monkeypatch.delenv("ANOLIS_WORKBENCH_RUNTIME_URL", raising=False)
    monkeypatch.setattr(operate.launcher_module, "get_status", lambda: {"running": True, "active_project": "p"})
    monkeypatch.setattr(operate.projects_module, "get_project", lambda name: {})
    monkeypatch.setattr(
        operate.launcher_module, "active_runtime_config", lambda system: {"http": {"bind": bind, "port": 8080}}
    )


@pytest.mark.parametrize(
    "bind",
    [
        "user@evil.example",
        "127.0.0.1@evil.example",
        "evil.example/x",
        "evil.example#x",
        "evil.example?x",
        "127.0.0.1:9999",
        "evil.example:80",
        "[::1]:80",
        "[127.0.0.1]",
        "[evil.example]",
        " 127.0.0.1",
        "127.0.0.1\n",
    ],
)
def test_a_bind_that_is_not_a_bare_address_is_refused(bind: str, monkeypatch) -> None:
    _project_bind(monkeypatch, bind)
    assert operate._runtime_base() == (None, "Runtime bind address is invalid", 500)


@pytest.mark.parametrize(
    ("bind", "base"),
    [
        ("127.0.0.1", "http://127.0.0.1:8080"),
        ("localhost", "http://localhost:8080"),
        ("0.0.0.0", "http://0.0.0.0:8080"),
        ("::1", "http://[::1]:8080"),
        ("[::1]", "http://[::1]:8080"),
        ("runtime.local", "http://runtime.local:8080"),
        ("", "http://127.0.0.1:8080"),
    ],
)
def test_a_bare_bind_builds_the_target(bind: str, base: str, monkeypatch) -> None:
    _project_bind(monkeypatch, bind)
    assert operate._runtime_base() == (base, None, 200)


def test_a_refused_bind_never_reaches_the_network(credential: None, monkeypatch) -> None:
    _project_bind(monkeypatch, "user@evil.example")

    def no_urlopen(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the proxy must not connect")

    monkeypatch.setattr(operate.urllib.request, "urlopen", no_urlopen)
    handler = _FakeHandler()
    operate.proxy_runtime(handler, "GET", "/v0/runtime/status")
    assert handler.json_payloads == [(500, {"error": "Runtime bind address is invalid"})]


@pytest.mark.parametrize(
    "base",
    [
        "http://user@evil.example:80",
        "http://127.0.0.1@evil.example:80",
        "http://evil.example:8080",
        "http://localhost.evil.example:8080",
        "http://127.0.0.1.evil.example:8080",
        "http://192.0.2.10:8080",
        "http://[2001:db8::1]:8080",
        "http://[::ffff:192.0.2.10]:8080",
        "http://[fe80::1]:8080",
    ],
)
def test_the_credential_token_never_goes_to_another_host(base: str, credential: None, monkeypatch) -> None:
    monkeypatch.delenv("ANOLIS_WORKBENCH_RUNTIME_URL", raising=False)
    assert operate.runtime_token(base) == ""


@pytest.mark.parametrize(
    "base",
    [
        "http://127.0.0.1:8080",
        "http://127.5.6.7:8080",
        "http://localhost:8080",
        "http://[::1]:8080",
        "http://0.0.0.0:8080",
        "http://[::]:8080",
    ],
)
def test_the_credential_token_goes_to_this_machine(base: str, credential: None, monkeypatch) -> None:
    monkeypatch.delenv("ANOLIS_WORKBENCH_RUNTIME_URL", raising=False)
    assert operate.runtime_token(base) == _CRED_TOKEN
