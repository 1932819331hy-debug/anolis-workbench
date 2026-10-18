"""Renovate tracks every upstream schema lock's release tag (#369).

The locks can agree with each other and still all be stale. A Renovate
regex manager on their tags turns a new upstream release into a PR here,
which stays red until the sync script refreshes the sha256s.

The lock set is globbed, never enumerated, so a new lock is covered
without editing this test.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LOCKS = sorted((REPO_ROOT / "contracts" / "upstream").rglob("*.lock.json"))
TEMPLATES = sorted((REPO_ROOT / "anolis_workbench" / "templates").glob("*/machine-profile.yaml"))


def _js_regex(pattern: str) -> re.Pattern[str]:
    """Renovate's regexes are JavaScript; named groups are the only syntax
    these use that Python spells differently."""
    if pattern.startswith("/") and pattern.endswith("/"):
        pattern = pattern[1:-1]
    return re.compile(pattern.replace("(?<", "(?P<"))


def _managers_for(rel_path: str) -> list[dict]:
    config = json.loads((REPO_ROOT / "renovate.json").read_text(encoding="utf-8"))
    return [
        manager
        for manager in config.get("customManagers", [])
        if any(_js_regex(p).search(rel_path) for p in manager.get("managerFilePatterns", []))
    ]


def _deps(rel_path: str, text: str) -> list[tuple[str, str, str]]:
    """(depName, currentValue, matched text) for every dep Renovate extracts.

    A manager's depNameTemplate, when set, names the dep instead of a group."""
    deps = []
    for manager in _managers_for(rel_path):
        for pattern in manager["matchStrings"]:
            for match in _js_regex(pattern).finditer(text):
                name = manager.get("depNameTemplate") or match.group("depName")
                deps.append((name, match.group("currentValue"), match.group(0)))
    return deps


def _bump(rel_path: str, text: str, dep_name: str, new_value: str) -> str:
    """Model Renovate's update: inside each matched text for the dep, every
    occurrence of the current value becomes the new one."""
    for name, current, matched in _deps(rel_path, text):
        if name == dep_name:
            text = text.replace(matched, matched.replace(current, new_value), 1)
    return text


def test_there_are_locks_to_track() -> None:
    assert LOCKS


@pytest.mark.parametrize("lock", LOCKS, ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_every_lock_tag_is_tracked(lock: pathlib.Path) -> None:
    rel_path = lock.relative_to(REPO_ROOT).as_posix()
    managers = _managers_for(rel_path)
    assert managers, f"no Renovate custom manager covers {rel_path}"

    text = lock.read_text(encoding="utf-8")
    found: list[tuple[str, str]] = []
    for manager in managers:
        assert manager["datasourceTemplate"] == "github-releases"
        for pattern in manager["matchStrings"]:
            for match in _js_regex(pattern).finditer(text):
                found.append((match.group("depName"), match.group("currentValue")))

    data = json.loads(text)
    # Both tags, in file order: the source block, then the release block.
    expected = [
        (data["source"]["repo"], data["source"]["tag"].removeprefix("v")),
        (data["distribution"]["release"]["repo"], data["distribution"]["release"]["tag"].removeprefix("v")),
    ]
    assert found == expected


def test_there_are_templates_to_track() -> None:
    assert TEMPLATES


@pytest.mark.parametrize("profile", TEMPLATES, ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_every_template_runtime_pin_is_tracked(profile: pathlib.Path) -> None:
    """Onboarding deploys these templates, so a stale runtime pin installs an
    old runtime, or downgrades an installed one (#470)."""
    rel_path = profile.relative_to(REPO_ROOT).as_posix()
    managers = _managers_for(rel_path)
    assert managers, f"no Renovate custom manager covers {rel_path}"

    text = profile.read_text(encoding="utf-8")
    for manager in managers:
        assert manager["datasourceTemplate"] == "github-releases"

    runtime = yaml.safe_load(text)["components"]["runtime"]
    found = [(name, value) for name, value, _ in _deps(rel_path, text) if name == runtime["repo"]]
    assert found == [(runtime["repo"], str(runtime["version"]))]


def test_a_sim_release_moves_both_sim_quickstart_pins() -> None:
    """The template pins sim twice, under compatibility and components; a
    bump that moved one would leave the two disagreeing."""
    path = REPO_ROOT / "anolis_workbench" / "templates" / "sim-quickstart" / "machine-profile.yaml"
    rel_path = path.relative_to(REPO_ROOT).as_posix()
    text = path.read_text(encoding="utf-8")
    before = yaml.safe_load(text)

    after = yaml.safe_load(_bump(rel_path, text, "anolishq/anolis-provider-sim", "9.9.9"))

    assert after["compatibility"]["providers"]["sim0"]["version"] == "9.9.9"
    assert after["components"]["providers"]["sim"]["version"] == "9.9.9"
    assert after["components"]["runtime"] == before["components"]["runtime"]


@pytest.mark.parametrize("lock", LOCKS, ids=lambda p: p.relative_to(REPO_ROOT).as_posix())
def test_a_release_moves_every_version_in_the_lock(lock: pathlib.Path) -> None:
    """A bump that moved the tag but not the asset names fetched the old
    asset from the new release and 404'd. Only the sha256s are left for the
    sync script."""
    rel_path = lock.relative_to(REPO_ROOT).as_posix()
    text = lock.read_text(encoding="utf-8")
    data = json.loads(text)
    old = data["source"]["tag"].removeprefix("v")

    bumped = _bump(rel_path, text, data["source"]["repo"], "9.9.9")

    stale = [line.strip() for line in bumped.splitlines() if old in line and "sha256" not in line]
    assert stale == []
