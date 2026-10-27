"""The Frontend CI job's Playwright image matches @playwright/test (#544).

The job runs in Playwright's image so that it never installs browsers or their
system packages. Each @playwright/test version expects particular browser
revisions, which only the image of the same version carries; with any other,
the smoke test fails because the browser executable doesn't exist.
"""

from __future__ import annotations

import json
import pathlib
import re

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
IMAGE = re.compile(r"^mcr\.microsoft\.com/playwright:v(?P<version>[0-9.]+)-[a-z]+@sha256:[0-9a-f]{64}$")


def _frontend_job() -> dict:
    workflow = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    job: dict = workflow["jobs"]["frontend"]
    return job


def test_frontend_job_runs_in_a_digest_pinned_playwright_image() -> None:
    image = _frontend_job()["container"]["image"]
    assert IMAGE.match(image), image


def test_the_image_version_is_playwright_test_s_in_the_lockfile() -> None:
    match = IMAGE.match(_frontend_job()["container"]["image"])
    assert match is not None
    lock = json.loads((REPO / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    locked = lock["packages"]["node_modules/@playwright/test"]["version"]
    assert match["version"] == locked, f"image v{match['version']}, @playwright/test {locked}"


def test_the_job_does_not_install_browsers() -> None:
    commands = " ".join(step.get("run", "") for step in _frontend_job()["steps"])
    assert "smoke:install" not in commands and "playwright install" not in commands, commands
