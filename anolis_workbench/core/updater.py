"""Update detection and self-update: compare installed version against latest GitHub release."""

from __future__ import annotations

import importlib.metadata
import logging
import os
import platform
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from anolis_workbench.core.child_env import child_env

logger = logging.getLogger(__name__)

GITHUB_REPO = "anolishq/anolis-workbench"
RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
REQUEST_TIMEOUT = 10  # seconds


@dataclass
class UpdateStatus:
    current_version: str
    latest_version: Optional[str]
    update_available: bool
    error: Optional[str] = None


def get_current_version() -> str:
    """Return the installed workbench version."""
    return importlib.metadata.version("anolis-workbench")


def fetch_latest_version() -> Optional[str]:
    """Query GitHub API for the latest release tag. Returns version string or None on failure."""
    try:
        resp = requests.get(
            RELEASES_URL,
            headers={"Accept": "application/vnd.github+json"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        tag = resp.json().get("tag_name", "")
        # Strip leading 'v' if present
        return tag.lstrip("v") if tag else None
    except (requests.RequestException, ValueError, KeyError) as exc:
        logger.warning("Failed to check for updates: %s", exc)
        return None


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a semver-like string into a tuple of ints for comparison."""
    parts = []
    for segment in v.split("."):
        # Handle pre-release suffixes like 0.12.0-rc1
        num = ""
        for ch in segment:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    return tuple(parts)


def check_for_update() -> UpdateStatus:
    """Check if an update is available. Safe to call from any context."""
    current = get_current_version()
    latest = fetch_latest_version()

    if latest is None:
        return UpdateStatus(
            current_version=current,
            latest_version=None,
            update_available=False,
            error="Could not reach GitHub to check for updates",
        )

    update_available = _parse_version(latest) > _parse_version(current)
    return UpdateStatus(
        current_version=current,
        latest_version=latest,
        update_available=update_available,
    )


@dataclass
class UpdateResult:
    success: bool
    version: Optional[str] = None
    error: Optional[str] = None


def _detect_arch() -> str:
    """Map platform.machine() to the arch string used in release assets."""
    machine = platform.machine()
    if machine in ("aarch64", "arm64"):
        return "arm64"
    return "x86_64"


def download_install_script(version: str) -> Path:
    """Download install.sh from the given release version to a temp file."""
    url = f"https://github.com/{GITHUB_REPO}/releases/download/v{version}/install.sh"
    resp = requests.get(url, timeout=60, allow_redirects=True)
    resp.raise_for_status()

    tmp = tempfile.NamedTemporaryFile(prefix="anolis-install-", suffix=".sh", delete=False)
    tmp.write(resp.content)
    tmp.close()
    os.chmod(tmp.name, os.stat(tmp.name).st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return Path(tmp.name)


def perform_update(
    target_version: str,
    install_prefix: Optional[Path] = None,
    dry_run: bool = False,
) -> UpdateResult:
    """Download and execute the install script for the target version.

    This downloads install.sh from the release and executes it with sudo.
    The install script handles downloading binaries and updating in place.
    """
    url = f"https://github.com/{GITHUB_REPO}/releases/download/v{target_version}/install.sh"
    cmd_preview = f"curl -fsSL {url} | sudo bash"
    if install_prefix:
        cmd_preview += f" -s -- --prefix {install_prefix}"

    if dry_run:
        return UpdateResult(
            success=True,
            version=target_version,
            error=f"DRY RUN: would execute: {cmd_preview}",
        )

    try:
        script = download_install_script(target_version)
    except requests.RequestException as exc:
        return UpdateResult(success=False, error=f"Failed to download install script: {exc}")

    cmd = ["sudo", str(script)]
    if install_prefix:
        cmd.extend(["--prefix", str(install_prefix)])

    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
            env=child_env(),
        )
        logger.info("Update output: %s", result.stdout)
        return UpdateResult(success=True, version=target_version)
    except subprocess.CalledProcessError as exc:
        return UpdateResult(
            success=False,
            error=f"Install script failed (exit {exc.returncode}): {exc.stderr[:500]}",
        )
    except subprocess.TimeoutExpired:
        return UpdateResult(success=False, error="Install script timed out (5 min)")
    finally:
        try:
            script.unlink()
        except OSError:
            pass
