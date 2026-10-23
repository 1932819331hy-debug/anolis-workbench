"""Regression tests for anolis-workbench#152.

The sidecar CLI flags (--host/--port/--no-browser) silently had no effect because
server.app resolved HOST/PORT/OPEN_BROWSER into module-level globals at import
time, before cli.main translated the flags. These tests pin the call-time
resolution (resolve_config) and the cli -> run_server wiring.
"""

from __future__ import annotations

import sys

import pytest

from anolis_workbench.cli import main as cli_main
from anolis_workbench.server import app as app_module


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "ANOLIS_WORKBENCH_HOST",
        "ANOLIS_WORKBENCH_PORT",
        "ANOLIS_WORKBENCH_OPEN_BROWSER",
        "ANOLIS_TELEMETRY_URL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_resolve_config_defaults():
    cfg = app_module.resolve_config()
    assert cfg.port == 3010
    assert cfg.host in ("127.0.0.1", "0.0.0.0")  # appliance-dependent
    assert isinstance(cfg.open_browser, bool)
    # Grafana on the target, through the tunnel the deploy summary prints,
    # or on this machine after a local install (#241).
    assert cfg.telemetry_url == "http://localhost:3000"


def test_resolve_config_explicit_args_win_over_env(monkeypatch):
    monkeypatch.setenv("ANOLIS_WORKBENCH_HOST", "10.0.0.1")
    monkeypatch.setenv("ANOLIS_WORKBENCH_PORT", "1111")
    monkeypatch.setenv("ANOLIS_WORKBENCH_OPEN_BROWSER", "1")
    cfg = app_module.resolve_config(host="192.168.1.5", port=9999, open_browser=False)
    assert (cfg.host, cfg.port, cfg.open_browser) == ("192.168.1.5", 9999, False)


def test_resolve_config_env_used_when_arg_none(monkeypatch):
    monkeypatch.setenv("ANOLIS_WORKBENCH_HOST", "0.0.0.0")
    monkeypatch.setenv("ANOLIS_WORKBENCH_PORT", "2222")
    monkeypatch.setenv("ANOLIS_WORKBENCH_OPEN_BROWSER", "0")
    cfg = app_module.resolve_config()
    assert (cfg.host, cfg.port, cfg.open_browser) == ("0.0.0.0", 2222, False)


def _capture_run_server(monkeypatch):
    called: dict = {}

    def fake_run_server(host=None, port=None, open_browser=None):
        called.update(host=host, port=port, open_browser=open_browser)

    monkeypatch.setattr(cli_main, "run_server", fake_run_server)
    return called


def test_cli_forwards_flags(monkeypatch):
    called = _capture_run_server(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["anolis-workbench", "--host", "0.0.0.0", "--port", "8080", "--no-browser"])
    cli_main.main()
    assert called == {"host": "0.0.0.0", "port": 8080, "open_browser": False}


def test_cli_defaults_forward_none(monkeypatch):
    # No flags -> all None so resolve_config falls back to env/defaults.
    called = _capture_run_server(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["anolis-workbench"])
    cli_main.main()
    assert called == {"host": None, "port": None, "open_browser": None}


class _Server:
    def __init__(self, address, handler):
        self.address = address

    def serve_forever(self):
        raise KeyboardInterrupt


class _InlineThread:
    def __init__(self, target, args, daemon):
        self._run = lambda: target(*args)

    def start(self):
        self._run()


@pytest.mark.parametrize(
    ("host", "local_url"),
    [("0.0.0.0", "http://127.0.0.1:3010"), ("::", "http://127.0.0.1:3010"), ("127.0.0.1", "http://127.0.0.1:3010")],
)
def test_startup_names_the_loopback_url_for_this_machine(monkeypatch, capsys, host, local_url):
    """A browser on the device must open a loopback URL, the only kind that
    offers the sudo password field (#230); http://0.0.0.0 is not one."""
    opened: list[str] = []
    monkeypatch.setattr(app_module, "verify_environment", lambda: None)
    monkeypatch.setattr(app_module.projects_module, "cleanup_stale_running_files", lambda: None)
    monkeypatch.setattr(app_module, "_WorkbenchServer", _Server)
    monkeypatch.setattr(app_module, "_open_browser", opened.append)
    monkeypatch.setattr(app_module.threading, "Thread", _InlineThread)

    app_module.main(host=host, port=3010, open_browser=True)

    out = capsys.readouterr().out
    assert f"is running at http://{host}:3010" in out
    if host == "127.0.0.1":
        assert "On this machine" not in out
    else:
        assert f"On this machine, open {local_url}" in out
    assert opened == [local_url]
