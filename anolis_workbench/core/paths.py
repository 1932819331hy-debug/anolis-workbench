"""Path helpers for Anolis Workbench."""

from __future__ import annotations

import os
import pathlib

CORE_DIR = pathlib.Path(__file__).resolve().parent
PACKAGE_DIR = CORE_DIR.parent

# Default install prefix for provisioned binaries on target machines.
DEFAULT_INSTALL_PREFIX = pathlib.Path("/opt/anolis")

# In editable mode these resolve to source-tree assets. In installed mode they
# resolve to bundled package-data assets under the same package root.
_SOURCE_ASSET_ROOT = PACKAGE_DIR
_PACKAGED_ASSET_ROOT = PACKAGE_DIR

_DEFAULT_SYSTEMS_ROOT = pathlib.Path.home() / ".anolis" / "systems"


def _resolve_systems_root() -> pathlib.Path:
    """Resolve project storage root with optional env override."""
    override = os.getenv("ANOLIS_DATA_DIR")
    if override and override.strip():
        return pathlib.Path(override).expanduser().resolve()
    return _DEFAULT_SYSTEMS_ROOT.resolve()


SYSTEMS_ROOT = _resolve_systems_root()
DATA_ROOT = SYSTEMS_ROOT.parent


def _select_asset_path(source_path: pathlib.Path, packaged_path: pathlib.Path) -> pathlib.Path:
    """Prefer source-tree assets in editable/dev mode, else use package data."""
    if source_path.exists():
        return source_path
    return packaged_path


TEMPLATES_ROOT = _select_asset_path(
    _SOURCE_ASSET_ROOT / "templates",
    _PACKAGED_ASSET_ROOT / "templates",
)


def _resolve_frontend_dir() -> pathlib.Path:
    """Resolve frontend dist dir with optional env override.

    The override is intended for test fixtures and custom deployment layouts.
    When set, ANOLIS_FRONTEND_DIR is used as-is; otherwise the standard
    source-tree / packaged asset fallback applies.
    """
    override = os.getenv("ANOLIS_FRONTEND_DIR")
    if override and override.strip():
        return pathlib.Path(override).expanduser().resolve()
    # In dev: resolved to frontend/dist/ after `npm run build` in the frontend/ source tree.
    # In installed mode: resolved to the bundled anolis_workbench/frontend/dist/ package-data.
    return _select_asset_path(
        _SOURCE_ASSET_ROOT / "frontend" / "dist",
        _PACKAGED_ASSET_ROOT / "frontend" / "dist",
    )


FRONTEND_DIR = _resolve_frontend_dir()
# Vendored provider config-schema envelopes (#270): the set of files here IS
# the set of provider kinds the workbench can compose.
PROVIDER_SCHEMAS_DIR = _select_asset_path(
    _SOURCE_ASSET_ROOT / "schemas" / "providers",
    _PACKAGED_ASSET_ROOT / "schemas" / "providers",
)


def resolve_data_path(path_value: str) -> pathlib.Path:
    """Resolve executable/config paths under ANOLIS_DATA_DIR roots."""
    path = pathlib.Path(path_value).expanduser()
    if path.is_absolute():
        return path

    preferred = (DATA_ROOT / path).resolve()
    if preferred.exists():
        return preferred

    systems_relative = (SYSTEMS_ROOT / path).resolve()
    if systems_relative.exists():
        return systems_relative

    return preferred
