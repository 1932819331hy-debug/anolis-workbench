"""Latest-release lookup for anolis components.

Shared by the exporter (machine-profile component pins) and deploy
(install.sh fetch). Network-best-effort: offline returns None and callers
degrade gracefully.

Set ANOLIS_WB_RELEASE_PINS to a JSON object ({"repo": "version", ...}) to
pin lookups without touching the network — for air-gapped use and for
tests that need deterministic exports across processes.
"""

from __future__ import annotations

import json
import os

import requests

GITHUB_API = "https://api.github.com"
RUNTIME_REPO = "anolishq/anolis"

# The architectures release assets are published for, in every spelling the
# workbench accepts: aarch64 is the kernel's name for arm64.
SUPPORTED_ARCHS = ("arm64", "aarch64", "x86_64")


def _seed_from_env() -> "dict[str, str | None]":
    raw = os.environ.get("ANOLIS_WB_RELEASE_PINS")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(repo): (str(ver) if ver is not None else None) for repo, ver in payload.items()}


_RELEASE_CACHE: "dict[str, str | None]" = _seed_from_env()


def latest_release_version(repo: str) -> "str | None":
    """Latest release tag of ``repo`` (without the ``v`` prefix), or None.

    None means offline / no release — the caller degrades gracefully. Cached
    per process so an export makes at most one API call per repo.
    """
    if repo in _RELEASE_CACHE:
        return _RELEASE_CACHE[repo]
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        # Unauthenticated API calls share a tiny per-IP budget (CI runners).
        headers["Authorization"] = f"Bearer {token}"
    version: str | None = None
    try:
        resp = requests.get(
            f"{GITHUB_API}/repos/{repo}/releases/latest",
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            tag = resp.json().get("tag_name", "")
            version = str(tag).lstrip("v") or None
    except requests.RequestException:
        version = None
    _RELEASE_CACHE[repo] = version
    return version
