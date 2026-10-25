"""Operate-track proxy handlers for the Workbench server."""

from __future__ import annotations

import ipaddress
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from anolis_workbench.core import launcher as launcher_module
from anolis_workbench.core import projects as projects_module
from anolis_workbench.core import workbench_service

_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def runtime_url_override() -> str:
    """The remote runtime the proxy targets instead of the launched one, or ""."""
    return os.environ.get("ANOLIS_WORKBENCH_RUNTIME_URL", "").strip()


# One DNS label; a bind is an IP address or dot-separated labels, nothing else.
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOSTNAME = re.compile(rf"{_LABEL}(?:\.{_LABEL})*")


def _bind_host(bind: str) -> str | None:
    """A project's http.bind as the host part of a URL, or None if it is not
    a bare address. Userinfo, a path, a fragment or a port would let the
    project point the proxy somewhere the bind does not name."""
    bracketed = bind.startswith("[") and bind.endswith("]")
    try:
        address = ipaddress.ip_address(bind[1:-1] if bracketed else bind)
    except ValueError:
        return bind if not bracketed and _HOSTNAME.fullmatch(bind) else None
    if address.version == 6:
        return f"[{address}]"
    return None if bracketed else str(address)


def is_local_target(base_url: str) -> bool:
    """True when the URL's host is this machine: loopback, localhost, or the
    unspecified address (0.0.0.0, ::), which connects locally."""
    host = urllib.parse.urlsplit(base_url).hostname
    if host is None:
        return False
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def runtime_token(target_base: str) -> str:
    """The bearer token the proxy sends to target_base, or "". Never logged.

    ANOLIS_WORKBENCH_RUNTIME_TOKEN wins. Otherwise the local runtime's token,
    from the copy install --workbench-service writes. That one
    goes only to a target on this machine: never to an
    ANOLIS_WORKBENCH_RUNTIME_URL override, and never to a host a project's
    http.bind names.
    """
    explicit = os.environ.get("ANOLIS_WORKBENCH_RUNTIME_TOKEN", "").strip()
    if explicit:
        return explicit
    if runtime_url_override():
        return ""
    if not is_local_target(target_base):
        return ""
    return workbench_service.read_runtime_token()


# The target _runtime_base last resolved. A software stop goes straight to
# it: resolving afresh reads every project, which on a Pi kept the stop
# waiting behind Operate's polls for more than a minute (#612).
_last_target: str | None = None


def _runtime_base() -> tuple[str | None, str | None, int]:
    # Remote override: point the operate proxy at an already-provisioned
    # device instead of the locally-launched runtime. Pairs with
    # ANOLIS_WORKBENCH_RUNTIME_TOKEN for devices that require a Bearer token
    # (auth-by-default installs bind 0.0.0.0 with auth enabled). Interim
    # surface until the device-registry UX (anolishq/anolis#164).
    override = runtime_url_override()
    if override:
        return override.rstrip("/"), None, 200

    status = launcher_module.get_status()
    active_project = status.get("active_project")
    if not status.get("running") or not isinstance(active_project, str) or active_project == "":
        return None, "Runtime is not running", 503

    try:
        system = projects_module.get_project(active_project)
    except FileNotFoundError:
        return None, f"Running project '{active_project}' not found", 500

    # The proxy target comes from the variant that is actually running (#255).
    # Reading a fixed default here would silently point Operate at :8080 while
    # the runtime listens elsewhere — and the launcher WOULD report it running,
    # so the UI shows a live project whose every call 502s.
    runtime_doc = launcher_module.active_runtime_config(system) or {}
    http = runtime_doc.get("http")
    http = http if isinstance(http, dict) else {}
    bind = http.get("bind", "127.0.0.1")
    port = http.get("port", 8080)

    if not isinstance(bind, str) or bind == "":
        bind = "127.0.0.1"
    host = _bind_host(bind)
    if host is None:
        return None, "Runtime bind address is invalid", 500
    if isinstance(port, str):
        try:
            port = int(port)
        except ValueError:
            return None, "Runtime port is invalid", 500
    if not isinstance(port, int) or isinstance(port, bool):
        return None, "Runtime port is invalid", 500

    global _last_target
    _last_target = f"http://{host}:{port}"
    return _last_target, None, 200


def _open(runtime_base: str, method: str, raw_path: str, body: bytes | None, handler, wants_stream: bool):
    headers: dict[str, str] = {}
    for key in ("Content-Type", "Accept", "Last-Event-ID"):
        value = handler.headers.get(key)
        if value:
            headers[key] = value

    # Bearer auth; never logged. The runtime may require the token on
    # loopback too; runtime_token() hands the copied one only to this machine.
    token = runtime_token(runtime_base)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(f"{runtime_base}{raw_path}", data=body, headers=headers, method=method)
    if wants_stream:
        return urllib.request.urlopen(req)
    return urllib.request.urlopen(req, timeout=15)


def proxy_runtime(handler, method: str, raw_path: str) -> None:
    is_stop = method == "POST" and raw_path.split("?")[0] == "/v0/estop"
    remembered = _last_target if is_stop and not runtime_url_override() else None
    if remembered is not None:
        runtime_base = remembered
    else:
        resolved, error, status_code = _runtime_base()
        if resolved is None:
            handler._json(status_code, {"error": error or "Runtime unavailable"})
            return
        runtime_base = resolved

    body: bytes | None = None
    content_length = int(handler.headers.get("Content-Length", "0") or "0")
    if content_length > 0:
        body = handler.rfile.read(content_length)

    wants_stream = raw_path.split("?")[0] == "/v0/events"

    try:
        try:
            response_ctx = _open(runtime_base, method, raw_path, body, handler, wants_stream)
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError:
            if remembered is None:
                raise
            # Nothing answers at the remembered target: the runtime stopped,
            # or another project's now runs. Resolve it as any request would.
            resolved, error, status_code = _runtime_base()
            if resolved is None:
                handler._json(status_code, {"error": error or "Runtime unavailable"})
                return
            if resolved == remembered:
                raise
            response_ctx = _open(resolved, method, raw_path, body, handler, wants_stream)

        with response_ctx as response:
            handler.send_response(int(response.status))
            for key, value in response.headers.items():
                lower = key.lower()
                if lower in _HOP_BY_HOP_HEADERS:
                    continue
                if lower == "content-length" and wants_stream:
                    continue
                handler.send_header(key, value)
            handler.end_headers()

            if wants_stream:
                while True:
                    chunk = response.read(4096)
                    if not chunk:
                        break
                    try:
                        handler.wfile.write(chunk)
                        handler.wfile.flush()
                    except OSError:
                        # The client went away (a closed tab); stop reading
                        # the runtime's stream for it.
                        handler.close_connection = True
                        break
                return

            data = response.read()
            handler.wfile.write(data)
    except urllib.error.HTTPError as exc:
        body_data = exc.read()
        handler.send_response(int(exc.code))
        for key, value in exc.headers.items():
            lower = key.lower()
            if lower in _HOP_BY_HOP_HEADERS:
                continue
            if lower == "content-length":
                continue
            handler.send_header(key, value)
        handler.send_header("Content-Length", str(len(body_data)))
        handler.end_headers()
        if body_data:
            handler.wfile.write(body_data)
    except urllib.error.URLError as exc:
        handler._json(502, {"error": f"Runtime proxy failed: {exc}"})
