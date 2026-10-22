"""Check a downloaded install.sh against its release's SHA256SUMS (#530).

install.sh runs as root, locally under sudo or on the target over ssh, so the
workbench checks it the way install.sh checks the tarballs it downloads
(anolishq/anolis#408): against the SHA256SUMS published with the same release.
"""

from __future__ import annotations

import hashlib

import requests

INSTALL_SH = "install.sh"


class ReleaseVerificationError(Exception):
    """install.sh does not match its release's SHA256SUMS, or SHA256SUMS could not be read."""


def _expected_digest(sums: str, asset: str) -> str | None:
    """The digest SHA256SUMS lists for ``asset`` (standard sha256sum output), or None."""
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].lstrip("*") == asset:
            return parts[0].lower()
    return None


def _unverified(repo: str, version: str) -> str:
    # TODO(anolishq/anolis-workbench#533): refuse instead of warning once the
    # oldest supported anolis release lists install.sh in its SHA256SUMS.
    # Releases through anolis v0.1.49 do not (anolishq/anolis#446).
    product = repo.rsplit("/", 1)[-1]
    return f"WARNING: install.sh not verified: {product} v{version} predates install.sh in SHA256SUMS"


def verify_install_sh(repo: str, version: str, content: bytes, *, timeout: float = 30) -> str | None:
    """Check ``content`` against the install.sh line of ``repo``'s v``version`` SHA256SUMS.

    Returns None when it matches, or the warning to show when the release has
    no SHA256SUMS (HTTP 404) or no line for install.sh. Raises
    ReleaseVerificationError on a mismatch, or when SHA256SUMS cannot be
    fetched for any other reason.
    """
    url = f"https://github.com/{repo}/releases/download/v{version}/SHA256SUMS"
    try:
        resp = requests.get(url, timeout=timeout)
    except requests.RequestException as exc:
        raise ReleaseVerificationError(
            f"could not fetch SHA256SUMS for {repo} v{version} ({exc}); refusing to run install.sh unverified"
        ) from exc
    if resp.status_code == 404:
        return _unverified(repo, version)
    if resp.status_code != 200:
        raise ReleaseVerificationError(
            f"could not fetch SHA256SUMS for {repo} v{version} (HTTP {resp.status_code}); "
            "refusing to run install.sh unverified"
        )
    expected = _expected_digest(resp.content.decode("utf-8", errors="replace"), INSTALL_SH)
    if expected is None:
        return _unverified(repo, version)
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise ReleaseVerificationError(
            f"install.sh from {repo} v{version} does not match its SHA256SUMS: expected {expected}, got {actual}"
        )
    return None
