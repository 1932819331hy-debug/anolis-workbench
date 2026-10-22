"""The self-update path checks install.sh against its release's SHA256SUMS (#530)."""

from __future__ import annotations

import hashlib
import pathlib
import re
import subprocess
import tempfile

import pytest
import requests

from anolis_workbench.core import updater

_RELEASE = "https://github.com/anolishq/anolis-workbench/releases/download/v9.9.9"
_SCRIPT = b"#!/usr/bin/env bash\n"
_UNVERIFIED = "WARNING: install.sh not verified: anolis-workbench v9.9.9 predates install.sh in SHA256SUMS"


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes = b"") -> None:
        self.status_code = status_code
        self.content = content

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


@pytest.fixture(autouse=True)
def _temp_in_tmp_path(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """download_install_script writes a NamedTemporaryFile; keep it in tmp_path."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def _serve_release(monkeypatch: pytest.MonkeyPatch, sums: _FakeResponse | Exception) -> list[str]:
    """install.sh at v9.9.9, and ``sums`` for its SHA256SUMS."""
    urls: list[str] = []

    def _get(url, **kwargs):
        urls.append(url)
        if url == f"{_RELEASE}/install.sh":
            return _FakeResponse(200, _SCRIPT)
        assert url == f"{_RELEASE}/SHA256SUMS"
        if isinstance(sums, Exception):
            raise sums
        return sums

    monkeypatch.setattr(updater.requests, "get", _get)
    return urls


def _sums(digest: str) -> _FakeResponse:
    return _FakeResponse(200, f"{'0' * 64}  anolis_workbench-9.9.9.tar.gz\n{digest}  install.sh\n".encode())


_MATCH = _sums(hashlib.sha256(_SCRIPT).hexdigest())
_MISMATCH = _sums(hashlib.sha256(b"something else").hexdigest())
_NO_LINE = _FakeResponse(200, f"{'0' * 64}  anolis_workbench-9.9.9.tar.gz\n".encode())


def test_download_install_script_checks_a_match(monkeypatch: pytest.MonkeyPatch) -> None:
    urls = _serve_release(monkeypatch, _MATCH)
    path, warning = updater.download_install_script("9.9.9")
    assert path.read_bytes() == _SCRIPT
    assert warning is None
    assert urls == [f"{_RELEASE}/install.sh", f"{_RELEASE}/SHA256SUMS"]


@pytest.mark.parametrize("sums", [pytest.param(_NO_LINE, id="no-line"), pytest.param(_FakeResponse(404), id="404")])
def test_download_install_script_warns_when_the_release_cannot_check_it(
    sums: _FakeResponse, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve_release(monkeypatch, sums)
    path, warning = updater.download_install_script("9.9.9")
    assert path.read_bytes() == _SCRIPT
    assert warning == _UNVERIFIED


@pytest.mark.parametrize(
    "sums, reason",
    [
        pytest.param(_MISMATCH, "does not match its SHA256SUMS", id="mismatch"),
        pytest.param(requests.ConnectionError("connection reset"), "could not fetch SHA256SUMS", id="fetch-error"),
        pytest.param(_FakeResponse(500), "could not fetch SHA256SUMS.*HTTP 500", id="http-500"),
    ],
)
def test_perform_update_refuses_and_never_runs_the_script(
    sums: _FakeResponse | Exception, reason: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve_release(monkeypatch, sums)
    ran: list[list[str]] = []
    monkeypatch.setattr(updater.subprocess, "run", lambda cmd, **kwargs: ran.append(cmd))
    result = updater.perform_update("9.9.9")
    assert result.success is False
    assert result.error is not None
    assert result.error.startswith("Install script not verified: ")
    assert re.search(reason, result.error)
    assert ran == []
    assert list(tmp_path.glob("anolis-install-*")) == []


@pytest.mark.parametrize(
    "sums, warning",
    [
        pytest.param(_MATCH, None, id="match"),
        pytest.param(_NO_LINE, _UNVERIFIED, id="no-line"),
        pytest.param(_FakeResponse(404), _UNVERIFIED, id="404"),
    ],
)
def test_perform_update_runs_the_script_and_carries_the_warning(
    sums: _FakeResponse, warning: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve_release(monkeypatch, sums)
    ran: list[list[str]] = []

    def _run(cmd, **kwargs):
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(updater.subprocess, "run", _run)
    result = updater.perform_update("9.9.9")
    assert result.success is True
    assert result.warning == warning
    assert len(ran) == 1 and ran[0][0] == "sudo"


def test_perform_update_keeps_the_warning_when_the_script_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_release(monkeypatch, _FakeResponse(404))

    def _run(cmd, **kwargs):
        raise subprocess.CalledProcessError(2, cmd, output="", stderr="boom")

    monkeypatch.setattr(updater.subprocess, "run", _run)
    result = updater.perform_update("9.9.9")
    assert result.success is False
    assert result.warning == _UNVERIFIED
