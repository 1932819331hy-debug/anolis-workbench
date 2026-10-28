"""Unit tests for deterministic handoff package export core."""

from __future__ import annotations

import errno
import io
import json
import os
import pathlib
import zipfile
from typing import Any, BinaryIO, Callable

import pytest
import requests
import yaml

from anolis_workbench.core import canonical, exporter, releases


@pytest.fixture(autouse=True)
def _stub_release_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed the release cache and block network so tests never hit GitHub."""
    monkeypatch.setattr(
        releases,
        "_RELEASE_CACHE",
        {"anolishq/anolis": "0.1.26", "anolishq/anolis-provider-sim": "0.2.1"},
    )

    def _no_network(*args: object, **kwargs: object) -> None:
        raise requests.RequestException("network disabled in tests")

    monkeypatch.setattr(releases.requests, "get", _no_network)


EXPORT_CREATED = "2026-04-16T19:01:02.999999+00:00"


@pytest.fixture()
def project_dir(canonical_project: Callable[..., pathlib.Path], tmp_path: pathlib.Path) -> pathlib.Path:
    """A canonical project carrying an automation variant and a telemetry token.

    Since #255 the exporter sources from DISK rather than re-rendering, so the
    fixture is the real thing: whatever these files say is what ships.
    """
    pdir = canonical_project(
        tmp_path / "export-deterministic",
        machine_id="export-deterministic",
        behavior="behaviors/local.xml",
        created=EXPORT_CREATED,
    )
    _patch_variant(
        pdir,
        canonical.MANUAL_VARIANT,
        telemetry={
            "enabled": True,
            "influxdb": {
                "url": "http://localhost:8086",
                "org": "anolis",
                "bucket": "anolis",
                "token": "super-secret",
            },
        },
    )
    return pdir


def _patch_variant(project_dir: pathlib.Path, variant: str, **updates: object) -> dict:
    path = project_dir / canonical.variant_relpath(variant)
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc.update(updates)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return doc  # type: ignore[no-any-return]


def test_build_package_is_deterministic_and_rewrites_runtime_paths(
    project_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    out_a = tmp_path / "a.anpkg"
    out_b = tmp_path / "b.anpkg"

    exporter.build_package(project_dir=project_dir, out_path=out_a)
    exporter.build_package(project_dir=project_dir, out_path=out_b)

    data_a = out_a.read_bytes()
    assert data_a == out_b.read_bytes()

    with zipfile.ZipFile(io.BytesIO(data_a), mode="r") as archive:
        assert sorted(archive.namelist()) == sorted(
            [
                "machine-profile.yaml",
                "meta/checksums.sha256",
                "meta/provenance.json",
                "providers/sim0.yaml",
                "runtime/anolis-runtime.yaml",
            ]
        )

        runtime_payload = yaml.safe_load(archive.read("runtime/anolis-runtime.yaml"))
        assert runtime_payload["providers"][0]["args"] == ["--config", "providers/sim0.yaml"]
        assert "token" not in runtime_payload.get("telemetry", {}).get("influxdb", {})
        assert "influx_token" not in runtime_payload.get("telemetry", {})

        profile = yaml.safe_load(archive.read("machine-profile.yaml"))
        assert profile["runtime_profiles"] == {"manual": "runtime/anolis-runtime.yaml"}
        assert profile["providers"]["sim0"]["config"] == "providers/sim0.yaml"

        provenance = json.loads(archive.read("meta/provenance.json").decode("utf-8"))
        assert provenance["exported_at"] == "2026-04-16T19:01:02Z"
        assert provenance["package_format_version"] == 1
        assert provenance["source_project"] == "export-deterministic"


def test_export_carries_the_projects_authored_pins(project_dir: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """The pins in the package are the project's own. The old exporter resolved
    them from live GitHub lookups, so two exports of the same unchanged project
    could disagree — and exporting offline produced a package with no
    components block at all."""
    out = tmp_path / "out.anpkg"
    exporter.build_package(project_dir=project_dir, out_path=out)
    with zipfile.ZipFile(out) as archive:
        profile = yaml.safe_load(archive.read("machine-profile.yaml"))
    assert profile["components"]["runtime"] == {"repo": "anolishq/anolis", "version": "0.1.27"}
    assert profile["components"]["providers"]["sim"] == {
        "repo": "anolishq/anolis-provider-sim",
        "version": "0.2.5",
    }


def test_export_records_the_variants_it_could_not_carry(project_dir: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """Package format v1 holds ONE runtime config, so a project with an
    automation variant exports only the inert manual one. The recipient must
    be able to tell that from the package instead of assuming it is whole."""
    out = tmp_path / "out.anpkg"
    exporter.build_package(project_dir=project_dir, out_path=out)
    with zipfile.ZipFile(out) as archive:
        provenance = json.loads(archive.read("meta/provenance.json").decode("utf-8"))
    assert provenance["exported_variant"] == "manual"
    assert provenance["omitted_variants"] == ["automation"]


def test_export_carries_a_behavior_tree_the_exported_variant_references(
    project_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """An imported profile's `manual` variant may legitimately reference a
    behavior tree (#226 only warns about inertness), and it has to be carried
    and re-pointed at the package layout."""
    _patch_variant(
        project_dir,
        canonical.MANUAL_VARIANT,
        automation={"enabled": True, "behavior_tree": "behaviors/local.xml"},
    )
    out = tmp_path / "out.anpkg"
    exporter.build_package(project_dir=project_dir, out_path=out)

    with zipfile.ZipFile(out) as archive:
        assert "runtime/behaviors/local.xml" in archive.namelist()
        runtime_payload = yaml.safe_load(archive.read("runtime/anolis-runtime.yaml"))
        assert runtime_payload["automation"]["behavior_tree"] == "runtime/behaviors/local.xml"
        profile = yaml.safe_load(archive.read("machine-profile.yaml"))
        assert profile["behaviors"] == ["runtime/behaviors/local.xml"]


def test_missing_machine_profile_raises_export_error(tmp_path: pathlib.Path) -> None:
    project_dir = tmp_path / "no-profile"
    project_dir.mkdir()
    with pytest.raises(exporter.ExportError, match="Project file not found"):
        exporter.build_package(project_dir=project_dir, out_path=tmp_path / "out.anpkg")


def test_malformed_machine_profile_raises_export_error(project_dir: pathlib.Path, tmp_path: pathlib.Path) -> None:
    (project_dir / "machine-profile.yaml").write_text("{not: valid: yaml", encoding="utf-8")
    with pytest.raises(exporter.ExportError):
        exporter.build_package(project_dir=project_dir, out_path=tmp_path / "out.anpkg")


def test_absolute_behavior_tree_path_raises_export_error(project_dir: pathlib.Path, tmp_path: pathlib.Path) -> None:
    abs_bt = str((tmp_path / "behaviors" / "local.xml").resolve())
    _patch_variant(
        project_dir,
        canonical.MANUAL_VARIANT,
        automation={"enabled": True, "behavior_tree": abs_bt},
    )
    with pytest.raises(exporter.ExportError, match="must be a relative path"):
        exporter.build_package(project_dir=project_dir, out_path=tmp_path / "out.anpkg")


def test_missing_behavior_tree_file_raises_export_error(project_dir: pathlib.Path, tmp_path: pathlib.Path) -> None:
    _patch_variant(
        project_dir,
        canonical.MANUAL_VARIANT,
        automation={"enabled": True, "behavior_tree": "behaviors/ghost.xml"},
    )
    with pytest.raises(exporter.ExportError, match="Behavior tree file not found"):
        exporter.build_package(project_dir=project_dir, out_path=tmp_path / "out.anpkg")


def test_secret_leak_raises_export_error() -> None:
    with pytest.raises(exporter.ExportError, match="Secret-like token value leaked"):
        exporter._assert_no_secret_leak(
            {
                "providers/test.yaml": b"connection:\n  token: 'leaked-secret'\n",
            }
        )


@pytest.mark.parametrize("existing_output", [False, True])
@pytest.mark.parametrize("error", [OSError(28, "No space left on device"), KeyboardInterrupt()])
def test_write_zip_cleans_up_after_mid_write_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, existing_output: bool, error: BaseException
) -> None:
    out = tmp_path / "out.anpkg"
    if existing_output:
        out.write_bytes(b"previous package")
    writestr = zipfile.ZipFile.writestr

    def fail_after_first_entry(archive: zipfile.ZipFile, info: zipfile.ZipInfo, data: bytes) -> None:
        if info.filename == "b.txt":
            assert archive.namelist() == ["a.txt"]
            raise error
        writestr(archive, info, data)

    monkeypatch.setattr(zipfile.ZipFile, "writestr", fail_after_first_entry)
    with pytest.raises(type(error)) as raised:
        exporter._write_zip_deterministic(files={"a.txt": b"first", "b.txt": b"second"}, out_path=out)

    assert raised.value is error
    if existing_output:
        assert out.read_bytes() == b"previous package"
    else:
        assert not out.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_zip_preserves_another_exports_temporary_file(tmp_path: pathlib.Path) -> None:
    out = tmp_path / "out.anpkg"
    other_tmp = tmp_path / "out.anpkg.tmp"
    other_tmp.write_bytes(b"another export in progress")

    exporter._write_zip_deterministic(files={"a.txt": b"new package"}, out_path=out)

    assert other_tmp.read_bytes() == b"another export in progress"
    assert list(tmp_path.glob("*.tmp")) == [other_tmp]
    with zipfile.ZipFile(out) as archive:
        assert archive.read("a.txt") == b"new package"


def test_write_zip_cleans_up_after_replace_failure(tmp_path: pathlib.Path) -> None:
    out = tmp_path / "out.anpkg"
    out.mkdir()
    marker = out / "keep.txt"
    marker.write_bytes(b"keep")

    with pytest.raises(OSError):
        exporter._write_zip_deterministic(files={"a.txt": b"new package"}, out_path=out)

    assert marker.read_bytes() == b"keep"
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_zip_preserves_original_error_if_cleanup_fails(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out.anpkg"
    out.write_bytes(b"previous package")
    error = OSError(28, "No space left on device")
    unlink = pathlib.Path.unlink

    def fail_write(archive: zipfile.ZipFile, info: zipfile.ZipInfo, data: bytes) -> None:
        raise error

    def fail_unlink(path: pathlib.Path, missing_ok: bool = False) -> None:
        raise PermissionError("temporary file cannot be removed")

    monkeypatch.setattr(zipfile.ZipFile, "writestr", fail_write)
    monkeypatch.setattr(pathlib.Path, "unlink", fail_unlink)
    with pytest.raises(OSError) as raised:
        exporter._write_zip_deterministic(files={"a.txt": b"new package"}, out_path=out)

    assert raised.value is error
    assert out.read_bytes() == b"previous package"
    for leftover in tmp_path.glob("*.tmp"):
        unlink(leftover)


def test_write_zip_closes_descriptor_if_fdopen_fails(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "out.anpkg"
    out.write_bytes(b"previous package")
    error = MemoryError("file handle allocation failed")
    allocated_fd: int | None = None

    def fail_fdopen(fd: int, *args: Any, **kwargs: Any) -> BinaryIO:
        nonlocal allocated_fd
        allocated_fd = fd
        raise error

    monkeypatch.setattr(os, "fdopen", fail_fdopen)
    with pytest.raises(MemoryError) as raised:
        exporter._write_zip_deterministic(files={"a.txt": b"first"}, out_path=out)

    assert raised.value is error
    assert out.read_bytes() == b"previous package"
    assert list(tmp_path.glob("*.tmp")) == []
    assert allocated_fd is not None
    try:
        os.fstat(allocated_fd)
    except OSError as closed:
        assert closed.errno == errno.EBADF
    else:
        os.close(allocated_fd)  # Do not leak the test's descriptor on regression.
        pytest.fail("temporary archive descriptor was not closed")


def test_write_zip_preserves_write_error_when_finalization_also_fails(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out.anpkg"
    out.write_bytes(b"previous package")
    primary = OSError(28, "first write failed")
    closing = OSError(5, "archive finalization failed")
    original_open = io.open

    class WriteFault:
        def __init__(self, raw: BinaryIO) -> None:
            self.raw = raw
            self.writes = 0

        def __getattr__(self, name: str) -> Any:
            return getattr(self.raw, name)

        def write(self, data: bytes) -> int:
            self.writes += 1
            # The first member's header, payload and updated header are real writes.
            if self.writes == 4:
                raise primary
            if self.writes > 4:
                raise closing
            return self.raw.write(data)

        def __enter__(self) -> WriteFault:
            return self

        def __exit__(self, *args: Any) -> None:
            self.raw.__exit__(*args)

    handles: list[WriteFault] = []

    def failing_open(*args: Any, **kwargs: Any) -> WriteFault:
        handle = WriteFault(original_open(*args, **kwargs))
        handles.append(handle)
        return handle

    with monkeypatch.context() as io_patch:
        io_patch.setattr(io, "open", failing_open)
        with pytest.raises(OSError) as raised:
            exporter._write_zip_deterministic(files={"a.txt": b"first", "b.txt": b"second"}, out_path=out)

    assert raised.value is primary
    assert len(handles) == 1
    assert handles[0].writes >= 5  # Both the member write and finalization failed.
    assert handles[0].raw.closed
    assert out.read_bytes() == b"previous package"
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_zip_replaces_output_with_a_deterministic_archive(tmp_path: pathlib.Path) -> None:
    out = tmp_path / "out.anpkg"
    other = tmp_path / "other.anpkg"
    out.write_bytes(b"previous package")
    exporter._write_zip_deterministic(files={"z.txt": b"last", "a.txt": b"first"}, out_path=out)
    exporter._write_zip_deterministic(files={"a.txt": b"first", "z.txt": b"last"}, out_path=other)

    assert out.read_bytes() == other.read_bytes()
    assert list(tmp_path.glob("*.tmp")) == []
    with zipfile.ZipFile(out) as archive:
        assert archive.namelist() == ["a.txt", "z.txt"]
        assert archive.read("a.txt") == b"first"
        assert archive.read("z.txt") == b"last"
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())


# ---------------------------------------------------------------------------
# _latest_release_version
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def test_latest_release_version_strips_v_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(releases, "_RELEASE_CACHE", {})
    monkeypatch.setattr(releases.requests, "get", lambda *a, **k: _FakeResponse(200, {"tag_name": "v1.2.3"}))
    assert releases.latest_release_version("anolishq/some-repo") == "1.2.3"


def test_latest_release_version_none_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(releases, "_RELEASE_CACHE", {})
    monkeypatch.setattr(releases.requests, "get", lambda *a, **k: _FakeResponse(404, {}))
    assert releases.latest_release_version("anolishq/no-releases") is None


def test_latest_release_version_none_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(releases, "_RELEASE_CACHE", {})
    # The autouse fixture already makes requests.get raise RequestException.
    assert releases.latest_release_version("anolishq/offline") is None


def test_latest_release_version_caches_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(releases, "_RELEASE_CACHE", {})
    calls: list[str] = []

    def _get(url: str, **kwargs: object) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(200, {"tag_name": "v2.0.0"})

    monkeypatch.setattr(releases.requests, "get", _get)
    assert releases.latest_release_version("anolishq/cached") == "2.0.0"
    assert releases.latest_release_version("anolishq/cached") == "2.0.0"
    assert len(calls) == 1


def test_release_pins_env_seeds_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANOLIS_WB_RELEASE_PINS", '{"anolishq/anolis": "1.2.3", "anolishq/x": null}')
    assert releases._seed_from_env() == {"anolishq/anolis": "1.2.3", "anolishq/x": None}
    monkeypatch.setenv("ANOLIS_WB_RELEASE_PINS", "not-json")
    assert releases._seed_from_env() == {}
    monkeypatch.delenv("ANOLIS_WB_RELEASE_PINS")
    assert releases._seed_from_env() == {}
