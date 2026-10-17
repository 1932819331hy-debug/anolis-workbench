"""Refuse requests addressed to a foreign name or sent from a foreign page.

The server has no authentication, so a request is only as trustworthy as the
page that sent it. Two checks stand in for that:

- Host must name this machine: an IP literal, localhost, this host's name
  (plain or .local), or a name in ANOLIS_WORKBENCH_ALLOWED_HOSTS. A DNS
  rebinding page reaches the server under the attacker's own name, so its
  Host fails here even though the browser treats it as same-origin.
- Origin, when the browser sends one, must match Host. A browser sends Origin
  on every cross-origin request and on every same-origin POST, PUT and DELETE,
  so a page elsewhere cannot get a state-changing request past this.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import urllib.parse

ALLOWED_HOSTS_ENV = "ANOLIS_WORKBENCH_ALLOWED_HOSTS"

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _split_host(value: str) -> tuple[str, int | None] | None:
    try:
        parts = urllib.parse.urlsplit(f"//{value}")
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname or parts.username is not None or parts.password is not None:
        return None
    if parts.path or parts.query or parts.fragment:
        return None
    return parts.hostname.rstrip("."), port


def _own_hostname() -> str:
    return socket.gethostname()


def _host_is_allowed(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    if name == "localhost" or name.endswith(".localhost"):
        return True
    own = _own_hostname().lower().rstrip(".")
    if own and name in (own, f"{own}.local"):
        return True
    extra = os.environ.get(ALLOWED_HOSTS_ENV, "")
    return name in {h.strip().lower().rstrip(".") for h in extra.split(",") if h.strip()}


def rejection(host: str | None, origin: str | None) -> str | None:
    """Return why a request must be refused, or None to serve it."""
    if host is None:
        # HTTP/1.0 clients may omit Host; browsers always send it.
        if origin is not None:
            return "Origin without Host"
        return None
    split = _split_host(host.strip())
    if split is None:
        return "Malformed Host header"
    name, port = split
    if not _host_is_allowed(name):
        return f"Host is not an address or name of this machine; add the name to {ALLOWED_HOSTS_ENV} to allow it"
    if origin is None:
        return None
    o = urllib.parse.urlsplit(origin.strip())
    try:
        o_port = o.port
    except ValueError:
        return "Cross-origin request refused"
    if o.scheme not in _DEFAULT_PORTS or not o.hostname:
        return "Cross-origin request refused"
    default = _DEFAULT_PORTS[o.scheme]
    if (o.hostname.rstrip("."), o_port or default) != (name, port or default):
        return "Cross-origin request refused"
    return None
