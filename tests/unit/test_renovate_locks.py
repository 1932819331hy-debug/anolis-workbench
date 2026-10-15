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
    found: list[tuple[str, str]] = []
    for manager in managers:
        assert manager["datasourceTemplate"] == "github-releases"
        for pattern in manager["matchStrings"]:
            for match in _js_regex(pattern).finditer(text):
                found.append((match.group("depName"), match.group("currentValue")))

    runtime = yaml.safe_load(text)["components"]["runtime"]
    assert found == [(runtime["repo"], str(runtime["version"]))]
