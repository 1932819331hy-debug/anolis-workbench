"""Directory removal never reaches outside the workspace root.

No test here removes anything: shutil.rmtree is replaced with a recorder.
"""

from __future__ import annotations

import pathlib
import shutil
from typing import Any

import pytest

from anolis_workbench.core import installer, projects
from anolis_workbench.core import paths as paths_module


class _Recorded(Exception):
    pass


@pytest.fixture()
def rmtree_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def _record(path: Any, *args: Any, **kwargs: Any) -> None:
        calls.append(str(path))
        raise _Recorded(str(path))

    monkeypatch.setattr(shutil, "rmtree", _record)
    return calls


@pytest.fixture()
def root(tmp_path: pathlib.Path) -> pathlib.Path:
    systems = tmp_path / "systems"
    (systems / "inside" / "nested").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    (systems / "link").symlink_to(tmp_path / "outside", target_is_directory=True)
    return systems


def test_rmtree_within_removes_a_directory_inside_the_root(root: pathlib.Path, rmtree_calls: list[str]) -> None:
    with pytest.raises(_Recorded):
        paths_module.rmtree_within(root / "inside", root)

    assert rmtree_calls == [str((root / "inside").resolve())]


@pytest.mark.parametrize(
    "target",
    [
        lambda r: r,
        lambda r: r / ".",
        lambda r: r / "..",
        lambda r: r / "inside" / ".." / "..",
        lambda r: r.parent / "outside",
        lambda r: r / str(r.parent / "outside"),
        lambda r: pathlib.Path("/"),
    ],
    ids=["root", "root-dot", "parent", "climb", "sibling", "absolute-join", "filesystem-root"],
)
def test_rmtree_within_refuses_the_root_and_anything_outside_it(
    root: pathlib.Path, rmtree_calls: list[str], target: Any
) -> None:
    with pytest.raises(paths_module.UnsafeDeleteError):
        paths_module.rmtree_within(target(root), root)

    assert rmtree_calls == []


def test_rmtree_within_refuses_a_symlink(root: pathlib.Path, rmtree_calls: list[str]) -> None:
    with pytest.raises(paths_module.UnsafeDeleteError):
        paths_module.rmtree_within(root / "link", root)

    assert rmtree_calls == []


def test_rmtree_within_refuses_a_symlink_to_another_project(root: pathlib.Path, rmtree_calls: list[str]) -> None:
    """Resolving first and deleting the result would remove the link's target."""
    (root / "alias").symlink_to(root / "inside", target_is_directory=True)

    with pytest.raises(paths_module.UnsafeDeleteError):
        paths_module.rmtree_within(root / "alias", root)

    assert rmtree_calls == []


@pytest.mark.parametrize("name", ["..", "ABS"])
def test_forced_provision_never_removes_outside_the_systems_root(
    root: pathlib.Path, rmtree_calls: list[str], name: str
) -> None:
    project = str(root.parent / "outside") if name == "ABS" else name

    with pytest.raises((ValueError, _Recorded)) as exc:
        installer.provision_project(
            "sim-quickstart", project, pathlib.Path("/opt/anolis"), force=True, systems_root=root
        )

    assert rmtree_calls == []
    assert type(exc.value).__name__ == "UnsafeDeleteError"


def test_delete_project_refuses_a_symlinked_project(
    root: pathlib.Path, rmtree_calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects, "SYSTEMS_ROOT", root)

    with pytest.raises((ValueError, _Recorded)) as exc:
        projects.delete_project("link")

    assert rmtree_calls == []
    assert type(exc.value).__name__ == "UnsafeDeleteError"
    assert (root.parent / "outside").is_dir()


def test_delete_project_removes_a_real_project(
    root: pathlib.Path, rmtree_calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects, "SYSTEMS_ROOT", root)

    with pytest.raises(_Recorded):
        projects.delete_project("inside")

    assert rmtree_calls == [str((root / "inside").resolve())]


def _fail(*args: Any, **kwargs: Any) -> None:
    raise RuntimeError("forced failure")


def test_create_cleanup_never_removes_outside_the_systems_root(
    root: pathlib.Path, rmtree_calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects, "SYSTEMS_ROOT", root)
    monkeypatch.setattr(projects.canonical, "retarget_project", _fail)

    with pytest.raises((ValueError, _Recorded, RuntimeError)):
        projects.create_project_from_template("../escape", "sim-quickstart")

    assert rmtree_calls == []


def test_duplicate_cleanup_never_removes_outside_the_systems_root(
    root: pathlib.Path, rmtree_calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects, "SYSTEMS_ROOT", root)
    monkeypatch.setattr(projects.canonical, "write_sidecar", _fail)

    with pytest.raises((ValueError, _Recorded, RuntimeError)):
        projects.duplicate_project("inside", "../escape")

    assert rmtree_calls == []


def test_import_cleanup_never_removes_outside_the_systems_root(
    root: pathlib.Path, rmtree_calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defence in depth: holds even if the name check is bypassed."""
    source = root.parent / "src"
    shutil.copytree(pathlib.Path(__file__).parent.parent / "fixtures" / "imported-profile", source)
    monkeypatch.setenv("ANOLIS_IMPORT_ROOTS", str(root.parent))
    monkeypatch.setattr(projects, "SYSTEMS_ROOT", root)
    monkeypatch.setattr(projects, "validate_name", lambda name: None)
    monkeypatch.setattr(projects, "sidecar_path", _fail)

    with pytest.raises((ValueError, _Recorded, RuntimeError)):
        projects.import_project(str(source), "../escape")

    assert rmtree_calls == []
