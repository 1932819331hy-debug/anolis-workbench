"""Fleet provisioning — provision multiple targets from a fleet.yaml file.

Supports parallel execution with configurable concurrency, per-target
overrides, and filtering via --only.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from anolis_workbench.core import projects
from anolis_workbench.core.paths import DEFAULT_INSTALL_PREFIX

# The template a fleet entry seeds a MISSING project from. Only used when the
# workspace has no project of that name — an existing one is the source of
# truth, and real machines are imported rather than seeded (#226).
DEFAULT_TEMPLATE = "sim-quickstart"

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class FleetTarget:
    """A single target from the fleet file."""

    name: str
    host: str
    project: str
    template: str = DEFAULT_TEMPLATE
    install_prefix: Path = field(default_factory=lambda: DEFAULT_INSTALL_PREFIX)
    systemd: bool = False
    key: str | None = None


@dataclass
class FleetConfig:
    """Parsed fleet.yaml configuration."""

    defaults: dict[str, Any]
    targets: list[FleetTarget]


@dataclass
class TargetResult:
    """Result of provisioning a single fleet target."""

    name: str
    host: str
    success: bool
    components: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class FleetResult:
    """Aggregate result of a fleet provision run."""

    results: list[TargetResult]

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.success)


# ---------------------------------------------------------------------------
# Fleet file parsing
# ---------------------------------------------------------------------------


def load_fleet_file(fleet_path: Path) -> FleetConfig:
    """Parse a fleet.yaml file into a FleetConfig.

    Args:
        fleet_path: Path to the fleet YAML file.

    Returns:
        FleetConfig with defaults and parsed targets.

    Raises:
        FileNotFoundError: If fleet file doesn't exist.
        ValueError: If fleet file is malformed.
    """
    if not fleet_path.is_file():
        raise FileNotFoundError(f"Fleet file not found: {fleet_path}")

    raw = yaml.safe_load(fleet_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Fleet file must be a YAML mapping: {fleet_path}")

    defaults = raw.get("defaults", {})
    if not isinstance(defaults, dict):
        defaults = {}

    raw_targets = raw.get("targets", [])
    if not isinstance(raw_targets, list):
        raise ValueError("'targets' must be a list in fleet file")

    if not raw_targets:
        raise ValueError("Fleet file has no targets defined")

    targets: list[FleetTarget] = []
    for i, entry in enumerate(raw_targets):
        if not isinstance(entry, dict):
            raise ValueError(f"Target {i} must be a mapping")
        if "name" not in entry or "host" not in entry:
            raise ValueError(f"Target {i} missing required 'name' or 'host' field")
        project = entry.get("project", defaults.get("project", DEFAULT_TEMPLATE))
        template = entry.get("template", defaults.get("template", DEFAULT_TEMPLATE))
        for key, value in (("project", project), ("template", template)):
            if not isinstance(value, str) or projects.validate_name(value):
                raise ValueError(
                    f"Target {i} '{key}' must be 1-64 characters: letters, digits, hyphens, underscores only"
                )

        targets.append(
            FleetTarget(
                name=entry["name"],
                host=entry["host"],
                project=project,
                template=template,
                install_prefix=Path(
                    entry.get("install_prefix", defaults.get("install_prefix", str(DEFAULT_INSTALL_PREFIX)))
                ),
                systemd=entry.get("systemd", defaults.get("systemd", False)),
                key=entry.get("key", defaults.get("key")),
            )
        )

    return FleetConfig(defaults=defaults, targets=targets)


def filter_targets(config: FleetConfig, only: list[str] | None) -> list[FleetTarget]:
    """Filter fleet targets by name.

    Args:
        config: Parsed fleet configuration.
        only: List of target names to include. None means all.

    Returns:
        Filtered list of targets.

    Raises:
        ValueError: If an --only name doesn't match any target.
    """
    if only is None:
        return config.targets

    all_names = {t.name for t in config.targets}
    unknown = set(only) - all_names
    if unknown:
        raise ValueError(f"Unknown target(s) in --only: {', '.join(sorted(unknown))}")

    return [t for t in config.targets if t.name in set(only)]


# ---------------------------------------------------------------------------
# Fleet execution
# ---------------------------------------------------------------------------


def provision_fleet(
    targets: list[FleetTarget],
    *,
    jobs: int = 4,
    dry_run: bool = False,
    provision_fn: Any = None,
) -> FleetResult:
    """Provision multiple targets, optionally in parallel.

    Args:
        targets: List of targets to provision.
        jobs: Max concurrency. 1 = serial (stop on first failure).
        dry_run: If True, don't actually provision.
        provision_fn: Callable(FleetTarget, dry_run=bool) -> TargetResult.
                      Must be provided by caller (avoids circular imports).

    Returns:
        FleetResult with per-target outcomes.
    """
    if provision_fn is None:
        raise ValueError("provision_fn must be provided")

    results: list[TargetResult] = []

    if jobs == 1:
        # Serial mode: stop on first failure
        for target in targets:
            result = provision_fn(target, dry_run=dry_run)
            results.append(result)
            if not result.success:
                break
    else:
        # Parallel mode: continue on error, report all at end
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(provision_fn, t, dry_run=dry_run): t for t in targets}
            for future in as_completed(futures):
                target = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = TargetResult(
                        name=target.name,
                        host=target.host,
                        success=False,
                        error=str(exc),
                    )
                results.append(result)

    # Sort results by original target order
    name_order = {t.name: i for i, t in enumerate(targets)}
    results.sort(key=lambda r: name_order.get(r.name, 999))

    return FleetResult(results=results)


def format_fleet_result(result: FleetResult) -> str:
    """Format fleet result for terminal display."""
    lines: list[str] = []
    lines.append(f"Fleet provision — {len(result.results)} targets")

    for r in result.results:
        if r.success:
            components_str = ", ".join(r.components) if r.components else "ok"
            lines.append(f"  ✓ {r.name:<16} ({r.host})  {components_str}")
        else:
            lines.append(f"  ✗ {r.name:<16} ({r.host})  {r.error or 'unknown error'}")

    lines.append("")
    lines.append(f"{result.succeeded}/{len(result.results)} succeeded, {result.failed} failed.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Auto-registration after successful remote provision
# ---------------------------------------------------------------------------

_FLEET_REGISTRY_PATH = Path.home() / ".anolis" / "fleet.yaml"


def auto_register_host(
    host: str,
    project: str,
    template: str | None = None,
) -> None:
    """Record a successful remote deploy in the fleet registry.

    A host already listed has its row updated to the project just deployed
    (#621); its other keys are kept. `template` is the project's own, and is
    left out for a project not made from one, such as an imported machine.
    """
    _FLEET_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)

    if _FLEET_REGISTRY_PATH.is_file():
        raw = yaml.safe_load(_FLEET_REGISTRY_PATH.read_text(encoding="utf-8")) or {}
    else:
        raw = {}

    if not isinstance(raw, dict):
        raw = {}

    targets = raw.get("targets", [])
    if not isinstance(targets, list):
        targets = []

    entry = next((t for t in targets if isinstance(t, dict) and t.get("host") == host), None)
    if entry is None:
        name = host.replace(".", "-").split("@")[-1] if "@" not in host else host.split("@")[1].replace(".", "-")
        entry = {"name": name, "host": host}
        targets.append(entry)
    entry["project"] = project
    if template:
        entry["template"] = template
    else:
        entry.pop("template", None)
    raw["targets"] = targets
    _FLEET_REGISTRY_PATH.write_text(
        yaml.dump(raw, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
