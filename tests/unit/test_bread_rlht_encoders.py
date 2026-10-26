"""bread 0.9.2's schema forbids `encoders` on an RLHT device (#640).

Workbench 0.20.x wrote `encoders: true` on every new bread device row, RLHT
included. bread's own loader drops the key from an RLHT with a warning
(anolishq/anolis-provider-bread#188); the workbench drops it the same way
before validating, so an existing project keeps loading, saving and launching.
"""

from __future__ import annotations

import copy
import pathlib
import shutil
from typing import Any

import pytest
import yaml

from anolis_workbench.core import canonical, canonical_validator, projects

FIXTURE = pathlib.Path(__file__).parent.parent / "fixtures" / "mixed-bus-mock"
BREAD_CONFIG = pathlib.Path("config") / "provider-bread.bread0.yaml"


@pytest.fixture(autouse=True)
def _systems_root(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "systems"
    monkeypatch.setattr(projects, "SYSTEMS_ROOT", root)
    return root


def _as_composed_by_020(devices: list[dict[str, Any]]) -> None:
    """encoders on every row, as 0.20.x's "+ Add" seeded it; a DCMT says false."""
    for device in devices:
        device["encoders"] = device["type"] != "dcmt"


def _by_id(devices: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {device["id"]: device for device in devices}


def test_save_drops_encoders_from_rlht_rows_and_keeps_a_dcmt_value(_systems_root: pathlib.Path) -> None:
    document = copy.deepcopy(canonical.read_project(FIXTURE))
    _as_composed_by_020(document["providers"]["bread0"]["config"]["devices"])

    projects.save_project("old-rlht", document)

    written = yaml.safe_load((_systems_root / "old-rlht" / BREAD_CONFIG).read_text(encoding="utf-8"))
    devices = _by_id(written["devices"])
    assert "encoders" not in devices["rlht0"]
    assert devices["dcmt0"]["encoders"] is False


def test_load_drops_encoders_from_rlht_rows_and_says_so(_systems_root: pathlib.Path) -> None:
    pdir = _systems_root / "old-rlht"
    shutil.copytree(FIXTURE, pdir)
    config = yaml.safe_load((pdir / BREAD_CONFIG).read_text(encoding="utf-8"))
    _as_composed_by_020(config["devices"])
    (pdir / BREAD_CONFIG).write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    document = projects.get_project("old-rlht")

    devices = _by_id(document["providers"]["bread0"]["config"]["devices"])
    assert "encoders" not in devices["rlht0"]
    assert devices["dcmt0"]["encoders"] is False
    notes = [w for w in document["warnings"] if "encoders" in w]
    assert len(notes) == 1, document["warnings"]
    assert "bread0" in notes[0] and "rlht0" in notes[0] and "dcmt0" not in notes[0], notes[0]
    # What the UI saves back is valid against the 0.9.2 schema.
    assert projects.validate_project_payload(document, project_dir=pdir) == []


def test_an_old_project_passes_launch_validation(_systems_root: pathlib.Path) -> None:
    """Launch never checked provider schemas (#604 runs canonical_validator only),
    and bread drops the key itself, so this held before the fix too."""
    pdir = _systems_root / "old-rlht"
    shutil.copytree(FIXTURE, pdir)
    config = yaml.safe_load((pdir / BREAD_CONFIG).read_text(encoding="utf-8"))
    _as_composed_by_020(config["devices"])
    (pdir / BREAD_CONFIG).write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    assert canonical_validator.validate_project(projects.get_project("old-rlht"), pdir) == []


def test_a_project_without_the_key_has_no_note(_systems_root: pathlib.Path) -> None:
    pdir = _systems_root / "clean"
    shutil.copytree(FIXTURE, pdir)

    document = projects.get_project("clean")

    assert not [w for w in document["warnings"] if "encoders" in w], document["warnings"]
