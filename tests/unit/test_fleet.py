"""Unit tests for the fleet module."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from anolis_workbench.core.fleet import (
    FleetConfig,
    FleetTarget,
    TargetResult,
    filter_targets,
    format_fleet_result,
    load_fleet_file,
    provision_fleet,
)

# ---------------------------------------------------------------------------
# Fleet file parsing
# ---------------------------------------------------------------------------


class TestLoadFleetFile:
    def test_loads_valid_fleet(self, tmp_path: Path) -> None:
        fleet = {
            "defaults": {"template": "sim-quickstart", "systemd": True},
            "targets": [
                {"name": "rpi-a", "host": "pi@192.168.1.10", "project": "bioreactor-v1"},
                {"name": "rpi-b", "host": "pi@192.168.1.11", "project": "bioreactor-v1"},
            ],
        }
        fleet_file = tmp_path / "fleet.yaml"
        fleet_file.write_text(yaml.dump(fleet), encoding="utf-8")

        config = load_fleet_file(fleet_file)
        assert len(config.targets) == 2
        assert config.targets[0].name == "rpi-a"
        assert config.targets[0].host == "pi@192.168.1.10"
        assert config.targets[0].systemd is True  # from defaults
        assert config.targets[0].template == "sim-quickstart"

    def test_target_overrides_defaults(self, tmp_path: Path) -> None:
        fleet = {
            "defaults": {"install_prefix": "/usr/local"},
            "targets": [
                {"name": "rpi-a", "host": "pi@10.0.0.1", "project": "x", "install_prefix": "/opt/anolis"},
            ],
        }
        fleet_file = tmp_path / "fleet.yaml"
        fleet_file.write_text(yaml.dump(fleet), encoding="utf-8")

        config = load_fleet_file(fleet_file)
        assert config.targets[0].install_prefix == Path("/opt/anolis")

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_fleet_file(tmp_path / "missing.yaml")

    def test_empty_targets_raises(self, tmp_path: Path) -> None:
        fleet_file = tmp_path / "fleet.yaml"
        fleet_file.write_text(yaml.dump({"targets": []}), encoding="utf-8")

        with pytest.raises(ValueError, match="no targets"):
            load_fleet_file(fleet_file)

    def test_missing_name_raises(self, tmp_path: Path) -> None:
        fleet_file = tmp_path / "fleet.yaml"
        fleet_file.write_text(yaml.dump({"targets": [{"host": "pi@x"}]}), encoding="utf-8")

        with pytest.raises(ValueError, match="missing required"):
            load_fleet_file(fleet_file)


# ---------------------------------------------------------------------------
# Target filtering
# ---------------------------------------------------------------------------


class TestFilterTargets:
    def test_no_filter_returns_all(self) -> None:
        config = FleetConfig(
            defaults={},
            targets=[
                FleetTarget(name="a", host="pi@1", project="p"),
                FleetTarget(name="b", host="pi@2", project="p"),
            ],
        )
        result = filter_targets(config, None)
        assert len(result) == 2

    def test_filters_by_name(self) -> None:
        config = FleetConfig(
            defaults={},
            targets=[
                FleetTarget(name="a", host="pi@1", project="p"),
                FleetTarget(name="b", host="pi@2", project="p"),
                FleetTarget(name="c", host="pi@3", project="p"),
            ],
        )
        result = filter_targets(config, ["a", "c"])
        assert len(result) == 2
        assert result[0].name == "a"
        assert result[1].name == "c"

    def test_unknown_name_raises(self) -> None:
        config = FleetConfig(defaults={}, targets=[FleetTarget(name="a", host="pi@1", project="p")])
        with pytest.raises(ValueError, match="Unknown target"):
            filter_targets(config, ["nonexistent"])


# ---------------------------------------------------------------------------
# Fleet execution
# ---------------------------------------------------------------------------


class TestProvisionFleet:
    def test_serial_stops_on_first_failure(self) -> None:
        targets = [
            FleetTarget(name="a", host="pi@1", project="p"),
            FleetTarget(name="b", host="pi@2", project="p"),
            FleetTarget(name="c", host="pi@3", project="p"),
        ]

        call_count = {"n": 0}

        def mock_fn(target: FleetTarget, *, dry_run: bool = False) -> TargetResult:
            call_count["n"] += 1
            if target.name == "b":
                return TargetResult(name=target.name, host=target.host, success=False, error="SSH failed")
            return TargetResult(name=target.name, host=target.host, success=True)

        result = provision_fleet(targets, jobs=1, provision_fn=mock_fn)
        assert result.succeeded == 1
        assert result.failed == 1
        assert call_count["n"] == 2  # stopped after 'b' failed

    def test_parallel_continues_on_failure(self) -> None:
        targets = [
            FleetTarget(name="a", host="pi@1", project="p"),
            FleetTarget(name="b", host="pi@2", project="p"),
            FleetTarget(name="c", host="pi@3", project="p"),
        ]

        def mock_fn(target: FleetTarget, *, dry_run: bool = False) -> TargetResult:
            if target.name == "b":
                return TargetResult(name=target.name, host=target.host, success=False, error="SSH failed")
            return TargetResult(name=target.name, host=target.host, success=True, components=["runtime 0.1.21"])

        result = provision_fleet(targets, jobs=4, provision_fn=mock_fn)
        assert result.succeeded == 2
        assert result.failed == 1
        assert len(result.results) == 3

    def test_no_provision_fn_raises(self) -> None:
        with pytest.raises(ValueError, match="provision_fn"):
            provision_fleet([], provision_fn=None)


# ---------------------------------------------------------------------------
# Format output
# ---------------------------------------------------------------------------


class TestFormatFleetResult:
    def test_format_mixed_results(self) -> None:
        from anolis_workbench.core.fleet import FleetResult

        result = FleetResult(
            results=[
                TargetResult(name="rpi-a", host="pi@10.0.0.1", success=True, components=["runtime 0.1.21"]),
                TargetResult(name="rpi-b", host="pi@10.0.0.2", success=False, error="SSH connection failed"),
            ]
        )
        output = format_fleet_result(result)
        assert "✓ rpi-a" in output
        assert "✗ rpi-b" in output
        assert "1/2 succeeded, 1 failed" in output


def test_target_without_a_template_defaults_to_the_shipped_one() -> None:
    """A fleet entry that omits `template:` seeds a MISSING project from this.
    An existing workspace project is the source of truth and is used as-is, so
    the default only matters on first provision of a new name."""
    from anolis_workbench.core import fleet as fleet_module

    path = Path(tempfile.mkdtemp()) / "fleet.yaml"
    path.write_text(
        yaml.safe_dump({"targets": [{"name": "t1", "host": "pi@10.0.0.1", "project": "rig"}]}),
        encoding="utf-8",
    )
    config = fleet_module.load_fleet_file(path)
    assert config.targets[0].template == fleet_module.DEFAULT_TEMPLATE == "sim-quickstart"


def test_auto_register_keeps_the_rest_of_an_existing_row(monkeypatch, tmp_path) -> None:
    """A row an operator edited keeps its keys when a deploy updates it (#621)."""
    import yaml

    from anolis_workbench.core import fleet

    registry = tmp_path / "fleet.yaml"
    registry.write_text(
        yaml.safe_dump(
            {
                "defaults": {"systemd": True},
                "targets": [
                    {
                        "name": "rig-a",
                        "host": "10.0.0.5",
                        "project": "old",
                        "template": "sim-quickstart",
                        "key": "~/.ssh/rig",
                    },
                    {"name": "rig-b", "host": "10.0.0.6", "project": "other"},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fleet, "_FLEET_REGISTRY_PATH", registry)

    fleet.auto_register_host(host="10.0.0.5", project="new", template="bioreactor-manual")

    raw = yaml.safe_load(registry.read_text(encoding="utf-8"))
    assert raw["defaults"] == {"systemd": True}
    assert raw["targets"] == [
        {"name": "rig-a", "host": "10.0.0.5", "project": "new", "template": "bioreactor-manual", "key": "~/.ssh/rig"},
        {"name": "rig-b", "host": "10.0.0.6", "project": "other"},
    ]
