"""Appliance mode binds loopback and says how to tunnel to it.

The workbench has no login, so no default serves it beyond loopback. An
explicit ANOLIS_WORKBENCH_HOST still wins.
"""

from __future__ import annotations

import pytest

from anolis_workbench.core import appliance
from anolis_workbench.server import app as app_module

TUNNEL = "ssh -L 3010:127.0.0.1:3010 <user>@<host>"


@pytest.fixture
def on_a_pi(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("ANOLIS_WORKBENCH_HOST", "ANOLIS_WORKBENCH_PORT", "ANOLIS_WORKBENCH_OPEN_BROWSER"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(appliance, "is_raspberry_pi", lambda: True)


def test_appliance_mode_binds_loopback(on_a_pi: None) -> None:
    assert appliance.is_appliance_mode() is True
    assert appliance.default_host() == "127.0.0.1"
    cfg = app_module.resolve_config()
    assert cfg.host == "127.0.0.1"


def test_appliance_mode_still_skips_the_browser(on_a_pi: None) -> None:
    assert app_module.resolve_config().open_browser is False


def test_an_explicit_host_still_wins(on_a_pi: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANOLIS_WORKBENCH_HOST", "0.0.0.0")
    assert app_module.resolve_config().host == "0.0.0.0"


def test_appliance_startup_prints_the_tunnel_hint(on_a_pi: None) -> None:
    messages = "\n".join(app_module.startup_messages(app_module.resolve_config()))
    assert TUNNEL in messages


def test_the_hint_uses_the_actual_port() -> None:
    cfg = app_module.ServerConfig(host="127.0.0.1", port=4020, open_browser=False, telemetry_url="")
    assert "ssh -L 4020:127.0.0.1:4020 <user>@<host>" in "\n".join(app_module.startup_messages(cfg))


def test_a_desktop_launch_prints_no_hint() -> None:
    cfg = app_module.ServerConfig(host="127.0.0.1", port=3010, open_browser=True, telemetry_url="")
    assert "ssh -L" not in "\n".join(app_module.startup_messages(cfg))


_SERVE = """
from anolis_workbench.server import app
app.verify_environment = lambda: None
app.projects_module.cleanup_stale_running_files = lambda: None
app.main(host="127.0.0.1", port=0, open_browser=False)
"""


def test_the_startup_hint_reaches_a_pipe_before_the_server_stops(tmp_path) -> None:
    """Under systemd stdout is a pipe, and SIGTERM ends the process without
    flushing a block buffer. The hint must already be in the pipe."""
    import os
    import select
    import signal
    import subprocess
    import sys
    import time

    env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
    proc = subprocess.Popen(
        [sys.executable, "-c", _SERVE], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=tmp_path, env=env
    )
    received = b""
    try:
        deadline = time.monotonic() + 10
        assert proc.stdout is not None
        while b"ssh -L" not in received and time.monotonic() < deadline:
            ready, _, _ = select.select([proc.stdout], [], [], 0.1)
            if ready:
                chunk = os.read(proc.stdout.fileno(), 4096)
                if not chunk:
                    break
                received += chunk
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
    assert b"ssh -L 0:127.0.0.1:0 <user>@<host>" in received
