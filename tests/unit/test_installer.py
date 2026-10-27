"""Unit tests for the local provisioning installer."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from anolis_workbench.core import canonical, installer, machine_profile

# ---------------------------------------------------------------------------
# detect_platform
# ---------------------------------------------------------------------------


class TestProvisionProject:
    def test_creates_project_with_patched_paths(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A provisioned project is the canonical artifact set, re-keyed onto
        its own machine_id, with ONLY the sidecar's host paths pointed at the
        install prefix — the configs keep the deploy tokens install.sh rewrites."""
        systems_root = tmp_path / "systems"
        systems_root.mkdir()
        monkeypatch.setattr("anolis_workbench.core.paths.SYSTEMS_ROOT", systems_root)
        monkeypatch.setattr("anolis_workbench.core.projects.SYSTEMS_ROOT", systems_root)

        prefix = tmp_path / "prefix"
        prefix.mkdir()

        project_dir = installer.provision_project(
            template_name="sim-quickstart",
            project_name="test-project",
            install_prefix=prefix,
        )

        assert project_dir == systems_root / "test-project"
        document = canonical.read_project(project_dir)

        # Host binary paths (the dev-launch residual) point at the prefix...
        host = document["host_paths"]
        assert host["runtime_executable"] == str(prefix / "bin" / "anolis-runtime")
        assert host["providers"]["sim0"]["executable"] == str(prefix / "bin" / "anolis-provider-sim")

        # ...and the canonical configs do NOT: they stay prefix-agnostic.
        manual = document["variants"][canonical.MANUAL_VARIANT]
        for entry in manual["providers"]:
            assert str(prefix) not in entry["command"]
            assert canonical.command_kind(entry["command"]) == "sim"

        # Re-keyed onto the new project, or it would deploy into the template's dir.
        assert document["profile"]["machine_id"] == "test-project"
        assert all(
            "/projects/test-project/" in arg for entry in manual["providers"] for arg in entry["args"] if "../" in arg
        )
        assert document["meta"]["name"] == "test-project"

        # Provider-owned config must survive untouched.
        assert document["providers"]["sim0"]["config"]["provider"]["name"] == "sim0"

        assert (project_dir / canonical.provider_config_relpath("sim", "sim0")).is_file()

    def test_raises_on_existing_project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        systems_root = tmp_path / "systems"
        systems_root.mkdir()
        monkeypatch.setattr("anolis_workbench.core.paths.SYSTEMS_ROOT", systems_root)
        monkeypatch.setattr("anolis_workbench.core.projects.SYSTEMS_ROOT", systems_root)

        # Create a fake existing project
        (systems_root / "existing-project").mkdir()

        with pytest.raises(ValueError, match="already exists"):
            installer.provision_project("sim-quickstart", "existing-project", Path("/opt/anolis"))

    def test_force_overwrites(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        systems_root = tmp_path / "systems"
        systems_root.mkdir()
        monkeypatch.setattr("anolis_workbench.core.paths.SYSTEMS_ROOT", systems_root)
        monkeypatch.setattr("anolis_workbench.core.projects.SYSTEMS_ROOT", systems_root)

        prefix = tmp_path / "prefix"
        prefix.mkdir()

        # Create existing project directory
        (systems_root / "force-test").mkdir()
        (systems_root / "force-test" / "old-file.txt").write_text("old")

        project_dir = installer.provision_project("sim-quickstart", "force-test", prefix, force=True)
        assert (project_dir / machine_profile.PROFILE_FILENAME).exists()
        assert not (project_dir / "old-file.txt").exists()


# ---------------------------------------------------------------------------
# verify_installation (mocked subprocess)
# ---------------------------------------------------------------------------


def test_provision_from_an_existing_canonical_project_carries_every_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `system_path` branch — seeding from an existing canonical directory
    rather than a bundled template. This is the path a real multi-provider
    machine takes now that no multi-provider template ships, and it was the
    coverage lost when bioreactor-manual was deleted."""
    systems_root = tmp_path / "systems"
    systems_root.mkdir()
    monkeypatch.setattr("anolis_workbench.core.paths.SYSTEMS_ROOT", systems_root)
    monkeypatch.setattr("anolis_workbench.core.projects.SYSTEMS_ROOT", systems_root)

    source = Path(__file__).parent.parent / "fixtures" / "mixed-bus-mock"
    prefix = tmp_path / "prefix"

    project_dir = installer.provision_project(
        template_name="unused-when-system-path-given",
        project_name="mixed-rig",
        install_prefix=prefix,
        system_path=source,
        systems_root=systems_root,
    )

    document = canonical.read_project(project_dir)
    host = document["host_paths"]["providers"]
    assert host["bread0"]["executable"] == str(prefix / "bin" / "anolis-provider-bread")
    assert host["ezo0"]["executable"] == str(prefix / "bin" / "anolis-provider-ezo")

    # Re-keyed onto the new project, including the sidecar's deploy directory —
    # without that it would install into the SOURCE's directory.
    assert document["profile"]["machine_id"] == "mixed-rig"
    assert document["meta"]["profile_dir_name"] == "mixed-rig"


# ---------------------------------------------------------------------------
# Re-provisioning from --system without --force (#650)
# ---------------------------------------------------------------------------
# Applying a newer project commit (a pin bump, say) to a provisioned machine
# re-runs `install --system`. It updates the workspace copy when that copy has
# no local edits, and says what changed; a local edit, or a copy with no record
# of what was provisioned, needs --force.


class TestReprovisionFromSystem:
    @pytest.fixture
    def rig(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
        systems_root = tmp_path / "systems"
        systems_root.mkdir()
        monkeypatch.setattr("anolis_workbench.core.paths.SYSTEMS_ROOT", systems_root)
        monkeypatch.setattr("anolis_workbench.core.projects.SYSTEMS_ROOT", systems_root)
        source = tmp_path / "clone"
        shutil.copytree(Path(__file__).parent.parent / "fixtures" / "mixed-bus-mock", source)
        return {"systems_root": systems_root, "source": source, "prefix": tmp_path / "prefix"}

    def provision(self, rig: dict, *, force: bool = False, report: list[str] | None = None) -> Path:
        return installer.provision_project(
            "unused-when-system-path-given",
            "mixed-rig",
            rig["prefix"],
            force=force,
            system_path=rig["source"],
            systems_root=rig["systems_root"],
            report=None if report is None else report.append,
        )

    @staticmethod
    def bump_pin(source: Path) -> None:
        profile = source / machine_profile.PROFILE_FILENAME
        text = profile.read_text(encoding="utf-8")
        assert text.count("version: 0.1.39") == 1
        profile.write_text(text.replace("version: 0.1.39", "version: 0.1.55"), encoding="utf-8")

    def test_an_unchanged_copy_reprovisions_and_says_so(self, rig: dict) -> None:
        project_dir = self.provision(rig)
        before = {p: p.read_bytes() for p in project_dir.rglob("*") if p.is_file()}
        report: list[str] = []

        assert self.provision(rig, report=report) == project_dir

        assert {p: p.read_bytes() for p in project_dir.rglob("*") if p.is_file()} == before
        assert report == [f"Workspace project mixed-rig is up to date with {rig['source']}"]

    def test_a_pin_bump_updates_the_copy_and_names_the_file(self, rig: dict) -> None:
        project_dir = self.provision(rig)
        self.bump_pin(rig["source"])
        report: list[str] = []

        self.provision(rig, report=report)

        assert "version: 0.1.55" in (project_dir / machine_profile.PROFILE_FILENAME).read_text(encoding="utf-8")
        assert report == [f"Updated workspace project mixed-rig from {rig['source']}: machine-profile.yaml"]

    def test_regenerated_sidecar_fields_are_not_local_edits(self, rig: dict) -> None:
        # meta and host_paths are rewritten on every provisioning (and by the
        # workbench, e.g. meta.created_by), so they never count as an edit.
        project_dir = self.provision(rig)
        sidecar = machine_profile.read_sidecar(project_dir) or {}
        sidecar["meta"] = {**sidecar.get("meta", {}), "created_by": "provision"}
        canonical.write_sidecar(project_dir, sidecar)
        self.bump_pin(rig["source"])
        rig["prefix"] = rig["prefix"].parent / "other-prefix"

        self.provision(rig)

        host = canonical.host_paths(machine_profile.read_sidecar(project_dir))
        assert host["runtime_executable"] == str(rig["prefix"] / "bin" / "anolis-runtime")

    def test_a_local_edit_refuses_names_the_file_and_keeps_it(self, rig: dict) -> None:
        project_dir = self.provision(rig)
        edited = project_dir / "config" / "provider-bread.bread0.yaml"
        edited.write_text(edited.read_text(encoding="utf-8") + "# tuned on the rig\n", encoding="utf-8")
        self.bump_pin(rig["source"])

        with pytest.raises(ValueError) as refused:
            self.provision(rig)

        message = str(refused.value)
        assert "config/provider-bread.bread0.yaml" in message
        assert "machine-profile.yaml" not in message
        assert "--force" in message
        assert edited.read_text(encoding="utf-8").endswith("# tuned on the rig\n")
        assert "version: 0.1.39" in (project_dir / machine_profile.PROFILE_FILENAME).read_text(encoding="utf-8")

    def test_force_still_replaces_a_local_edit(self, rig: dict) -> None:
        project_dir = self.provision(rig)
        edited = project_dir / "config" / "provider-bread.bread0.yaml"
        edited.write_text("edited\n", encoding="utf-8")

        self.provision(rig, force=True)

        assert edited.read_text(encoding="utf-8") != "edited\n"

    def test_a_copy_with_no_record_refuses_and_lists_what_differs(self, rig: dict) -> None:
        # Provisioned by an earlier workbench: nothing records what it wrote.
        project_dir = self.provision(rig)
        (project_dir / installer.PROVISIONED_RECORD).unlink()
        self.bump_pin(rig["source"])

        with pytest.raises(ValueError) as refused:
            self.provision(rig)

        message = str(refused.value)
        assert "machine-profile.yaml" in message
        assert "config/" not in message
        assert "--force" in message
        assert "version: 0.1.39" in (project_dir / machine_profile.PROFILE_FILENAME).read_text(encoding="utf-8")

    def test_a_copy_with_no_record_that_matches_the_source_gets_one(self, rig: dict) -> None:
        project_dir = self.provision(rig)
        (project_dir / installer.PROVISIONED_RECORD).unlink()

        self.provision(rig)
        assert (project_dir / installer.PROVISIONED_RECORD).is_file()
        self.bump_pin(rig["source"])
        self.provision(rig)

        assert "version: 0.1.55" in (project_dir / machine_profile.PROFILE_FILENAME).read_text(encoding="utf-8")

    def test_the_record_is_not_deployed(self, rig: dict) -> None:
        project_dir = self.provision(rig)
        entries = set(machine_profile.copy_entries(machine_profile.load_profile(project_dir)))
        assert installer.PROVISIONED_RECORD not in entries
