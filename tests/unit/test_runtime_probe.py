"""_runtime_alive: what counts as a runtime answering on bind:port.

A runtime that requires its token on loopback answers the token-less probe
with 401, which still proves a runtime is there. The HTTP layer is stubbed.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from anolis_workbench.core import launcher


class _Response:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status = status
        self._body = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_exc: Any) -> None:
        pass


def _urlopen(result: Any, seen: list[urllib.request.Request | str]):
    def fake(req: Any, timeout: float | None = None) -> Any:
        seen.append(req)
        if isinstance(result, BaseException):
            raise result
        return result

    return fake


def _http_error(code: int) -> urllib.error.HTTPError:
    url = "http://127.0.0.1:8080/v0/runtime/status"
    return urllib.error.HTTPError(url, code, "status", {}, io.BytesIO(b"{}"))  # type: ignore[arg-type]


def test_a_401_is_a_runtime_answering(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen(_http_error(401), seen))
    assert launcher._runtime_alive("127.0.0.1", 8080) is True
    # The probe never carries a token.
    assert all(isinstance(req, str) or req.get_header("Authorization") is None for req in seen)


def test_a_refused_connection_is_not_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    refused = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen(refused, []))
    assert launcher._runtime_alive("127.0.0.1", 8080) is False


def test_a_timeout_is_not_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen(TimeoutError("timed out"), []))
    assert launcher._runtime_alive("127.0.0.1", 8080) is False


@pytest.mark.parametrize("code", [403, 404, 500, 503])
def test_other_error_statuses_are_not_alive(code: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen(_http_error(code), []))
    assert launcher._runtime_alive("127.0.0.1", 8080) is False


def test_a_200_with_a_version_is_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen(_Response(200, {"version": "0.1.47"}), []))
    assert launcher._runtime_alive("127.0.0.1", 8080) is True


def test_a_200_without_a_version_is_not_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen(_Response(200, {"other": 1}), []))
    assert launcher._runtime_alive("127.0.0.1", 8080) is False
