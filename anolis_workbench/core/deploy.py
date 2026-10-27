"""Deploy a workbench project via the canonical anolis install.sh.

Deployment (/opt/anolis, `anolis` user, systemd) is delegated to the anolis
repo's install.sh — the single provisioning engine. Workbench's job is to
materialize a project config dir (machine-profile + configs + behaviors)
from the workspace and hand it over:

  local  : sudo bash install.sh --project <dir>
  remote : push <dir> + install.sh to the target, run the same there

Authoring / dev-launch (~/.anolis/systems, launcher.py) is unaffected.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import shlex
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests
import yaml

from anolis_workbench.core import canonical, machine_profile, releases
from anolis_workbench.core.executor import Executor, LocalExecutor
from anolis_workbench.core.paths import DATA_ROOT, DEFAULT_INSTALL_PREFIX
from anolis_workbench.core.release_verify import ReleaseVerificationError, verify_install_sh

logger = logging.getLogger(__name__)

# A real install downloads binaries and waits for systemd health.
INSTALL_TIMEOUT_S = 1800.0

# The Debian convention for "a reboot is pending" (tmpfs, so a reboot clears
# it). A project's host prep writes it; the workbench only reports it.
REBOOT_REQUIRED_PATH = "/run/reboot-required"
# Every deploy keeps install.sh's whole output here, on the machine that ran
# the deploy; the summaries show only parts of it (#649).
INSTALL_LOG_DIR = DATA_ROOT / "logs"

# install.sh prefixes every host-preflight line with this, and indents the
# per-requirement detail under it.
_PREFLIGHT_MARK = "host preflight:"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# install.sh keeps this file when it already exists, and then applies neither
# the project's runtime config nor --variant (anolis tools/install.sh,
# phase_config_runtime).
RUNTIME_CONFIG_RELPATH = "config/runtime.yaml"

# install.sh's messages for a config it found on the target and left in place.
# Its other "preserved" lines (the API token, the rollback backup, the
# telemetry-export venv) are kept on every redeploy and say nothing about the
# configs the runtime runs.
_RUNTIME_CONFIG_MESSAGE = "config (runtime):"
_PRESERVED_CONFIG_MESSAGES = (_RUNTIME_CONFIG_MESSAGE, "config (providers):", "telemetry-export: config")

ProgressCallback = Callable[[str, str], None]

# The first runtime whose install.sh refuses to install an older runtime over
# a newer one (anolishq/anolis#366, phase_version_check).
DOWNGRADE_GUARD_VERSION = (0, 1, 45)
_XYZ_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class DeployError(RuntimeError):
    """Raised when a deployment cannot be materialized or install.sh fails."""

    def __init__(self, message: str, *, host_preflight: list[str] | None = None, reboot_pending: bool = False) -> None:
        super().__init__(message)
        self.host_preflight = host_preflight or []
        self.reboot_pending = reboot_pending


@dataclass
class MaterializedProject:
    """A project config dir in the install.sh layout."""

    project_dir: pathlib.Path
    runtime_version: str
    provider_kinds: dict[str, str]  # provider instance id -> kind


@dataclass
class DeployResult:
    project_name: str
    runtime_version: str
    prefix: str
    output: str
    # install.sh's host-preflight block, ANSI stripped; non-empty on success
    # only when --allow-unmet-host let unmet requirements through, or a
    # provider gave no answer.
    host_preflight: list[str] = field(default_factory=list)
    reboot_pending: bool = False
    # install.sh's lines naming the configs an existing install kept, ANSI
    # stripped; runtime_config_kept says whether runtime.yaml is among them.
    preserved: list[str] = field(default_factory=list)
    runtime_config_kept: bool = False
    # Set when install.sh ran without a SHA256SUMS line to check it against.
    install_sh_warning: str | None = None
    # Where install.sh's whole output was kept; None when it could not be written.
    log_path: str | None = None


def materialize_project_dir(
    project_dir: pathlib.Path,
    dest: pathlib.Path,
    *,
    variant: str | None = None,
) -> MaterializedProject:
    """Materialize a canonical project for install.sh (#226, #255).

    Since #255 this is the ONLY materialize path — authored and imported
    projects deploy identically. Every file is copied BYTE-FOR-BYTE — no rendering, no
    profile synthesis, no network. install.sh natively understands this layout
    (components pins, provider-<kind> configs, runtime_profiles variants,
    dev-relative path rewrites), so the workbench's whole job is an honest copy
    plus fast fail-early validation.
    """
    project_dir = project_dir.resolve()
    try:
        profile = machine_profile.load_profile(project_dir)
    except machine_profile.ProfileError as exc:
        raise DeployError(str(exc)) from exc
    errors = machine_profile.schema_errors(profile)
    if errors:
        raise DeployError(f"machine-profile failed schema validation: {'; '.join(errors)}")

    components = profile.get("components")
    if not isinstance(components, dict):
        raise DeployError(
            "machine profile has no components block — deploy requires pinned "
            "components (fill components.runtime/providers before deploying)"
        )
    runtime_component = components.get("runtime")
    runtime_version = runtime_component.get("version") if isinstance(runtime_component, dict) else None
    if not isinstance(runtime_version, str) or runtime_version == "":
        raise DeployError("machine profile components.runtime.version is missing")

    runtime_profiles = profile.get("runtime_profiles")
    if not isinstance(runtime_profiles, dict) or not isinstance(runtime_profiles.get("manual"), str):
        raise DeployError("machine profile has no 'manual' runtime profile — boot-inert install requires one")
    if variant is not None and variant not in runtime_profiles:
        known = ", ".join(sorted(runtime_profiles))
        raise DeployError(f"variant '{variant}' not in the profile's runtime_profiles (known: {known})")

    uncarriable = [
        f"{ref} ({problem})"
        for ref in machine_profile.referenced_files(profile)
        if (problem := machine_profile.containment_error(ref)) is not None
    ]
    if uncarriable:
        raise DeployError(f"machine profile references files it cannot carry: {', '.join(sorted(uncarriable))}")
    missing = [
        ref
        for ref in machine_profile.referenced_files(profile)
        if machine_profile.containment_error(ref) is None and not (project_dir / ref).is_file()
    ]
    if missing:
        raise DeployError(f"machine profile references missing files: {', '.join(sorted(missing))}")

    # install.sh keys its ../anolis-projects/projects/<name>/ path rewrites on
    # the project dir BASENAME — use the original canonical dir name, not the
    # (renamable) workbench project name.
    sidecar = machine_profile.read_sidecar(project_dir) or {}
    raw_meta = sidecar.get("meta")
    meta = raw_meta if isinstance(raw_meta, dict) else {}
    profile_dir_name = str(meta.get("profile_dir_name") or profile.get("machine_id") or project_dir.name)
    # This value builds a filesystem path; the sidecar is workbench-owned but
    # unvalidated, so keep it to a single plain directory name.
    if profile_dir_name in ("", ".", "..") or "/" in profile_dir_name or "\\" in profile_dir_name:
        raise DeployError(f"invalid project directory name {profile_dir_name!r} in the imported project sidecar")

    _assert_path_tokens_name(project_dir, profile, profile_dir_name)
    _assert_no_crlf(project_dir, profile)
    _assert_no_influx_token(project_dir, runtime_profiles)

    # install.sh refuses a non-inert `manual` variant at fresh install. Imported
    # projects never pass through save-time validation, so this is the only
    # place that catches it before the target does.
    manual_rel = runtime_profiles.get(canonical.MANUAL_VARIANT)
    manual_path = project_dir / manual_rel if isinstance(manual_rel, str) else None
    if manual_path is not None and manual_path.is_file():
        violation = canonical.inertness_violation_text(manual_path.read_text(encoding="utf-8"))
        if violation is not None:
            raise DeployError(
                f"the 'manual' runtime variant is not inert ({violation}). install.sh refuses to "
                "install a variant that boots into automation — author automation into a separate "
                "variant."
            )

    out = dest / profile_dir_name
    out.mkdir(parents=True, exist_ok=True)
    for entry in machine_profile.copy_entries(profile):
        src = project_dir / entry
        if src.is_file():
            shutil.copy2(src, out / entry)
        elif src.is_dir():
            shutil.copytree(src, out / entry, copy_function=shutil.copy2, dirs_exist_ok=True)

    provider_kinds = {
        pid: kind or "unknown" for pid, kind in machine_profile.derive_kinds(profile, project_dir).items()
    }
    return MaterializedProject(
        project_dir=out,
        runtime_version=runtime_version,
        provider_kinds=provider_kinds,
    )


def _assert_path_tokens_name(project_dir: pathlib.Path, profile: dict, profile_dir_name: str) -> None:
    """The deploy directory basename must equal what the config tokens name.

    install.sh rewrites `../anolis-projects/projects/<X>/...` using the TOKEN's
    own `<X>`, but installs the project under the directory it is handed. When
    the two disagree the install SUCCEEDS and the rig is broken: configs
    resolve to a path nothing was written to. There is no check for this on the
    install.sh side, so it has to happen here.
    """
    named: set[str] = set()
    # install.sh's renderer is GLOB-driven, not profile-driven, so scan what it
    # scans: a stray config the profile never mentions is still rendered, and
    # its token would still be rewritten.
    for path in _install_sh_config_files(project_dir, profile):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        named.update(match.group(1) for match in canonical.PROJECT_PATH_RE.finditer(text))

    wrong = sorted(named - {profile_dir_name})
    if wrong:
        raise DeployError(
            f"project configs reference project directory {', '.join(repr(w) for w in wrong)} "
            f"but this project deploys as '{profile_dir_name}'. install.sh keys its path rewrites "
            "on the token, so the installed configs would point at a directory that does not exist."
        )


def _assert_no_influx_token(project_dir: pathlib.Path, runtime_profiles: dict) -> None:
    """Deploy and bundle stage the runtime configs verbatim, and an imported
    project never passed save-time validation, so refuse an authored token
    here too."""
    for variant, rel in sorted(runtime_profiles.items()):
        if not isinstance(rel, str) or machine_profile.containment_error(rel) is not None:
            continue
        path = project_dir / rel
        if not path.is_file():
            continue
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        problem = canonical.influx_token_error(doc)
        if problem is not None:
            raise DeployError(f"runtime variant '{variant}' ({rel}): {problem}")


def _install_sh_config_files(project_dir: pathlib.Path, profile: dict) -> list[pathlib.Path]:
    """Every file install.sh will read out of the project: what its config/
    globs pick up, plus anything else the profile references."""
    found: set[pathlib.Path] = set()
    config_dir = project_dir / canonical.CONFIG_DIR
    if config_dir.is_dir():
        for pattern in ("anolis-runtime.*.yaml", "provider-*.yaml"):
            found.update(path for path in config_dir.glob(pattern) if path.is_file())
    for rel in machine_profile.referenced_files(profile):
        if machine_profile.containment_error(rel) is not None:
            continue
        path = project_dir / rel
        if path.is_file():
            found.add(path)
    return sorted(found)


def _assert_no_crlf(project_dir: pathlib.Path, profile: dict) -> None:
    """install.sh reads the profile and configs with awk, which does not strip
    `\r`. A CRLF machine-profile makes it fail to resolve the `manual` variant,
    and a CRLF runtime config silently bypasses the LAN-exposure bind gate. The
    workbench writes LF, but an imported project is carried byte-for-byte."""
    # Only what install.sh reads with awk: the profile (variant lookup) and the
    # runtime configs (bind/auth lookup). Provider configs go through pyyaml and
    # behaviours are byte-copied, so a CR in those is harmless.
    awk_parsed = [project_dir / machine_profile.PROFILE_FILENAME]
    runtime_profiles = profile.get("runtime_profiles")
    if isinstance(runtime_profiles, dict):
        for rel in runtime_profiles.values():
            if isinstance(rel, str) and machine_profile.containment_error(rel) is None:
                awk_parsed.append(project_dir / rel)

    offenders = []
    for path in awk_parsed:
        try:
            if b"\r" in path.read_bytes():
                offenders.append(path.relative_to(project_dir).as_posix())
        except OSError:
            continue
    if offenders:
        raise DeployError(
            f"these files contain carriage returns: {', '.join(sorted(offenders))}. "
            "install.sh reads them with awk, which does not strip a CR — the runtime variant "
            "would not resolve and the LAN-exposure check would be silently skipped. "
            "Convert them to LF before deploying."
        )


def fetch_install_sh(runtime_version: str, dest: pathlib.Path) -> tuple[pathlib.Path, str | None]:
    """Download install.sh from the anolis release the profile pins and check it.

    Returns the script's path and, when the release's SHA256SUMS has no
    line for it, the warning to show. Raises DeployError when it does not
    match, or SHA256SUMS cannot be fetched.
    """
    url = f"https://github.com/{releases.RUNTIME_REPO}/releases/download/v{runtime_version}/install.sh"
    try:
        resp = requests.get(url, timeout=30)
    except requests.RequestException as exc:
        raise DeployError(f"failed to download install.sh from {url}: {exc}") from exc
    if resp.status_code != 200:
        raise DeployError(f"failed to download install.sh from {url}: HTTP {resp.status_code}")
    try:
        warning = verify_install_sh(releases.RUNTIME_REPO, runtime_version, resp.content)
    except ReleaseVerificationError as exc:
        raise DeployError(str(exc)) from exc
    path = dest / "install.sh"
    path.write_bytes(resp.content)
    path.chmod(0o755)
    return path, warning


def run_rollback(
    executor: Executor | None = None,
    *,
    prefix: pathlib.Path = DEFAULT_INSTALL_PREFIX,
    staging: str = "/tmp/anolis-deploy",
    progress_callback: ProgressCallback | None = None,
) -> str:
    """Restore the previous binaries via `install.sh --rollback` on the target.

    Rollback is version-independent (it swaps <prefix>/.prev back), so the
    latest released install.sh is used. The script is staged via the executor,
    which makes the same path work locally and over SSH.
    """
    if executor is None:
        executor = LocalExecutor()

    version = releases.latest_release_version(releases.RUNTIME_REPO)
    if version is None:
        raise DeployError("could not resolve the latest anolis release (offline?)")

    with tempfile.TemporaryDirectory(prefix="anolis-rollback-") as td:
        install_sh, warning = fetch_install_sh(version, pathlib.Path(td))
        if warning and progress_callback:
            progress_callback("unverified", warning)
        target_path = f"{staging}/install.sh"
        executor.mkdir(staging)
        executor.write_file(target_path, install_sh.read_bytes())

    args = ["--rollback"]
    if pathlib.Path(prefix) != DEFAULT_INSTALL_PREFIX:
        args += ["--prefix", str(prefix)]
    result = executor.run(["bash", target_path, *args], sudo=True, timeout=300)
    if result.returncode != 0:
        tail = "\n".join((result.stdout + "\n" + result.stderr).strip().splitlines()[-10:])
        raise DeployError(f"install.sh --rollback failed (exit {result.returncode}):\n{tail}")
    return result.stdout


def _install_args(
    project_dir: str,
    *,
    prefix: pathlib.Path,
    no_start: bool,
    dry_run: bool,
    with_telemetry_export: bool = False,
    with_observability: bool = False,
    variant: str | None = None,
    allow_unmet_host: bool = False,
) -> list[str]:
    args = ["--project", project_dir]
    if pathlib.Path(prefix) != DEFAULT_INSTALL_PREFIX:
        args += ["--prefix", str(prefix)]
    if variant is not None:
        args += ["--variant", variant]
    if no_start:
        args.append("--no-start")
    if dry_run:
        args.append("--dry-run")
    # Telemetry-export provisioning is owned by install.sh (anolishq/anolis#137);
    # workbench just requests it — the service is installed on the target, not
    # on the operator's host.
    if with_telemetry_export:
        args.append("--with-telemetry-export")
    # Likewise observability (anolishq/anolis#162): InfluxDB + Grafana are
    # installed natively on the target, beside the runtime.
    if with_observability:
        args.append("--with-observability")
    if allow_unmet_host:
        args.append("--allow-unmet-host")
    return args


def _push_dir(executor: Executor, local_dir: pathlib.Path, remote_root: str) -> None:
    for local in sorted(p for p in local_dir.rglob("*") if p.is_file()):
        rel = local.relative_to(local_dir).as_posix()
        remote_path = f"{remote_root}/{rel}"
        executor.mkdir(remote_path.rsplit("/", 1)[0])
        executor.write_file(remote_path, local.read_bytes())


def host_preflight_block(stdout: str) -> list[str]:
    """install.sh's host-preflight lines, ANSI stripped: the first line carrying
    the preflight mark, through every following line that carries it or is
    indented detail. Empty when install.sh never reached the preflight."""
    lines = _ANSI_RE.sub("", stdout).splitlines()
    start = next((i for i, line in enumerate(lines) if _PREFLIGHT_MARK in line), None)
    if start is None:
        return []
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if _PREFLIGHT_MARK not in line and not line.startswith(" "):
            break
        block.append(line)
    return block


_OBSERVABILITY_MARKS = ("observability:", "Grafana:", "Grafana bind unchanged", "InfluxDB:")
# install.sh's log_info indents every line by two spaces, so a summary line
# such as "  Hostname:" starts with spaces too. Its detail lines (a log_info
# message that itself starts with two spaces) start with four. A status line
# (a glyph in the first column) keeps every indented line after it: a
# refusal's instructions are plain log_info lines (#649).
_DETAIL_INDENT = "    "
_LOG_INFO_INDENT = "  "


def observability_lines(stdout: str) -> list[str]:
    """install.sh's observability, Grafana and InfluxDB lines, ANSI
    stripped, each with the indented detail that follows it. install.sh
    reports every observability failure as a warning and still exits 0, so
    these lines are the only place a failure shows."""
    return _marked_lines(stdout, lambda line: any(mark in line for mark in _OBSERVABILITY_MARKS))


# install.sh's phase_start and phase_health lines: a status glyph, then
# "start:" or "health:". phase_start restarts the runtime on every install, so
# its line is what says this deploy restarted it (#556).
_HEALTH_RE = re.compile(r"^\S+ (?:start|health):")


def health_lines(stdout: str) -> list[str]:
    """install.sh's start and health lines (runtime restart, runtime, run
    registry, provider liveness), ANSI stripped, each with the indented detail
    that follows it. A provider that is not running is a warning and
    install.sh still exits 0, so these lines are the only place it shows."""
    return _marked_lines(stdout, lambda line: _HEALTH_RE.match(line) is not None)


# install.sh's phase_verify line: the bundle's checksums against
# checksums.sha256, one line per failure.
_VERIFY_RE = re.compile(r"^\S+ verify:")


def verify_lines(stdout: str) -> list[str]:
    """install.sh's checksum result for the bundle it installed, ANSI stripped."""
    return _marked_lines(stdout, lambda line: _VERIFY_RE.match(line) is not None)


def _marked_lines(stdout: str, is_mark: Callable[[str], bool]) -> list[str]:
    lines = _ANSI_RE.sub("", stdout).splitlines()
    out: list[str] = []
    indent: str | None = None
    for line in lines:
        if is_mark(line):
            out.append(line)
            indent = _DETAIL_INDENT if line.startswith(" ") else _LOG_INFO_INDENT
        elif indent is not None and line.startswith(indent) and line.strip():
            out.append(line)
        else:
            indent = None
    return out


# install.sh's summary line for Grafana on the network, and for Grafana on
# loopback (anolishq/anolis#351). Anything else, "Grafana bind unchanged"
# included, means the observability step did not finish.
_GRAFANA_NETWORK_RE = re.compile(r"^\s*Grafana:\s+http://\S+:3000 ")
_GRAFANA_LOOPBACK_RE = re.compile(r"^\s*Grafana:\s+127\.0\.0\.1:3000 on this machine")
GRAFANA_ADMIN_LOGIN_NOTE = (
    "admin password: GRAFANA_ADMIN_PASSWORD in /etc/anolis/observability.env on the target (root only)"
)


def grafana_access(
    stdout: str, *, ssh_target: str | None = None, ssh_port: int = 22, ssh_key: str | None = None
) -> list[str]:
    """How to reach Grafana after an observability install, from install.sh's
    own summary, and where its admin password lives. The password itself is
    never read. `ssh_target` is the `user@host` a remote deploy reached, with
    the port and key it used; None for a local install. Empty when install.sh
    did not report Grafana up."""
    lines = _ANSI_RE.sub("", stdout).splitlines()
    on_network = any(_GRAFANA_NETWORK_RE.match(line) for line in lines)
    if not on_network and not any(_GRAFANA_LOOPBACK_RE.match(line) for line in lines):
        return []
    if ssh_target is None:
        # A local install may be an appliance browsed from another machine,
        # so say whose localhost this is, as install.sh's summary does.
        if on_network:
            return [
                "Grafana: port 3000 on this machine's network address (http://localhost:3000 on this machine)",
                GRAFANA_ADMIN_LOGIN_NOTE,
            ]
        return [
            "Grafana: http://localhost:3000 on this machine",
            "  from another machine: ssh -L 3000:127.0.0.1:3000 <user>@<this machine>, then http://localhost:3000",
            GRAFANA_ADMIN_LOGIN_NOTE,
        ]
    user, _, host = ssh_target.rpartition("@")
    host = host.removeprefix("[").removesuffix("]")
    if on_network:
        url_host = f"[{host}]" if ":" in host else host
        return [f"Grafana: http://{url_host}:3000", GRAFANA_ADMIN_LOGIN_NOTE]
    options = (f"-p {ssh_port} " if ssh_port != 22 else "") + (f"-i {shlex.quote(ssh_key)} " if ssh_key else "")
    return [
        "Grafana listens on 127.0.0.1:3000 on the target. Open a tunnel, then browse http://localhost:3000:",
        f"  ssh {options}-L 3000:127.0.0.1:3000 {user}@{host}",
        GRAFANA_ADMIN_LOGIN_NOTE,
    ]


def _message(line: str) -> str:
    """An install.sh log line without its leading mark."""
    return line[1:].strip()


def preserved_lines(stdout: str) -> list[str]:
    """install.sh's skip and warning lines for configs it found and left in
    place, ANSI stripped: the runtime config (with the warning that --variant
    was not applied), provider configs, telemetry-export's config."""
    lines = _ANSI_RE.sub("", stdout).splitlines()
    return [
        line.strip()
        for line in lines
        if line.startswith(("→", "⚠")) and "preserved" in line and _message(line).startswith(_PRESERVED_CONFIG_MESSAGES)
    ]


def runtime_config_kept(preserved: list[str]) -> bool:
    """Whether install.sh kept the target's existing runtime.yaml."""
    return any(_message(line).startswith(_RUNTIME_CONFIG_MESSAGE) for line in preserved)


def _existing_config_notice(executor: Executor, prefix: pathlib.Path, variant: str | None) -> str | None:
    """Say, before install.sh runs, that the target's runtime config will be
    kept. Advisory only: a target we cannot ask gets no notice."""
    path = (pathlib.PurePosixPath(prefix) / RUNTIME_CONFIG_RELPATH).as_posix()
    try:
        exists = executor.file_exists(path)
    except Exception:  # noqa: BLE001 — never mask the deploy's own outcome
        logger.warning("could not check %s on the target", path, exc_info=True)
        return None
    if not exists:
        return None
    notice = (
        f"{path} exists on the target and is kept: install.sh does not replace it with this project's runtime config"
    )
    if variant is not None:
        notice += f", and variant '{variant}' is not applied"
    return notice + "."


def _xyz(text: str) -> tuple[int, int, int] | None:
    match = _XYZ_RE.search(text)
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def _installed_runtime_version(executor: Executor, prefix: pathlib.Path) -> tuple[int, int, int] | None:
    """The X.Y.Z of the runtime installed under prefix, or None when none is.

    Measured by running the binary, as install.sh's own version check does:
    an installed manifest.json can be stale (anolishq/anolis#345). A runtime
    that is there but reports no version is a DeployError, since a downgrade
    could not then be ruled out."""
    path = executor.allow_runtime_version(prefix)
    try:
        if not executor.file_exists(path):
            return None
        result = executor.run([path, "--version"], timeout=30)
    except Exception as exc:  # noqa: BLE001 — reported as the deploy's refusal
        raise DeployError(
            f"could not read the runtime version from {path} on the target ({exc}); nothing was deployed."
        ) from exc
    version = _xyz(result.stdout) if result.returncode == 0 else None
    if version is None:
        detail = (result.stdout + result.stderr).strip().splitlines()[-1:] or ["no output"]
        raise DeployError(
            f"{path} on the target reports no version (exit {result.returncode}: {detail[0]}), "
            "so a downgrade cannot be ruled out; nothing was deployed."
        )
    return version


def _runtime_verdict(installed: tuple[int, int, int], runtime_version: str) -> tuple[str, str]:
    """What deploy does with this pin over this installed runtime: "ok",
    "downgrade" (a warning; install.sh refuses it) or "refuse", and why.
    Deploy and the target check both decide here (#567)."""
    pinned = _xyz(runtime_version)
    if pinned is None:
        return "refuse", f"The project's runtime pin '{runtime_version}' is not X.Y.Z."
    if pinned >= installed:
        return "ok", ""
    have = ".".join(map(str, installed))
    want = ".".join(map(str, pinned))
    guard = ".".join(map(str, DOWNGRADE_GUARD_VERSION))
    if pinned < DOWNGRADE_GUARD_VERSION:
        return "refuse", (
            f"The target runs runtime {have}, newer than this project's pin {want}. install.sh {want} "
            f"predates the downgrade guard (runtime {guard}), so it would install the older runtime "
            f"without asking. Pin runtime {have} or newer to deploy."
        )
    return "downgrade", (
        f"The target runs runtime {have}, newer than this project's pin {want}; "
        f"install.sh {want} refuses to downgrade it. Pin runtime {have} or newer to deploy."
    )


def _check_downgrade(
    executor: Executor, prefix: pathlib.Path, runtime_version: str, progress: ProgressCallback
) -> None:
    """Refuse, before anything is fetched or pushed, a pin whose install.sh
    would downgrade the target's runtime without asking (#567)."""
    installed = _installed_runtime_version(executor, prefix)
    if installed is None:
        return
    verdict, message = _runtime_verdict(installed, runtime_version)
    if verdict == "refuse":
        raise DeployError(f"{message} Nothing was deployed.")
    if verdict == "downgrade":
        progress("downgrade", message)


@dataclass
class ComponentCheck:
    """One pinned component beside what the target has installed."""

    name: str  # "runtime", or a provider kind
    pinned: str | None
    installed: str | None
    # runtime: "fresh" | "ok" | "downgrade" | "refuse"; provider: "fresh" | "ok" | "older" | "unknown"
    verdict: str
    message: str = ""


@dataclass
class TargetCheck:
    runtime: ComponentCheck
    providers: list[ComponentCheck]
    # components.optional (telemetry_export today), recorded in the manifest
    # the same way as providers (#620).
    optional: list[ComponentCheck]
    # Where the providers' installed versions come from: install.sh's
    # <prefix>/manifest.json, which records the last install. "ok", "missing",
    # "unreadable", or "stale" when it lists another runtime than the one
    # measured, so its provider versions may be out of date too.
    manifest: str


def _installed_manifest(executor: Executor, prefix: pathlib.Path) -> tuple[str, dict]:
    path = (pathlib.PurePosixPath(prefix) / "manifest.json").as_posix()
    try:
        if not executor.file_exists(path):
            return "missing", {}
        result = executor.run(["cat", path], timeout=30)
        doc = json.loads(result.stdout) if result.returncode == 0 else None
    except Exception:  # noqa: BLE001 — informational: provider versions are then unknown
        logger.warning("could not read %s on the target", path, exc_info=True)
        return "unreadable", {}
    if not isinstance(doc, dict):
        return "unreadable", {}
    return "ok", doc


def check_target(
    executor: Executor, *, project_dir: pathlib.Path, prefix: pathlib.Path = DEFAULT_INSTALL_PREFIX
) -> TargetCheck:
    """The project's pins beside the target's installed versions, and what a
    deploy would do about the runtime (#567). Read-only: it runs the
    installed runtime's --version, as deploy does, and reads manifest.json.

    A runtime that cannot be read is a DeployError, the same refusal deploy
    gives."""
    try:
        profile = machine_profile.load_profile(project_dir.resolve())
    except machine_profile.ProfileError as exc:
        raise DeployError(str(exc)) from exc
    components = profile.get("components")
    if not isinstance(components, dict):
        components = {}
    runtime_entry = components.get("runtime")
    runtime_pin = runtime_entry.get("version") if isinstance(runtime_entry, dict) else None
    provider_pins = _pins(components.get("providers"))
    optional_pins = _pins(components.get("optional"))

    installed = _installed_runtime_version(executor, prefix)
    if installed is None:
        runtime = ComponentCheck("runtime", runtime_pin, None, "fresh")
    else:
        verdict, message = _runtime_verdict(installed, str(runtime_pin or ""))
        runtime = ComponentCheck("runtime", runtime_pin, ".".join(map(str, installed)), verdict, message)

    manifest, doc = _installed_manifest(executor, prefix)
    if manifest == "ok" and installed is not None and _xyz(str(doc.get("version", ""))) != installed:
        manifest = "stale"
    listed = doc.get("components")
    if not isinstance(listed, dict):
        listed = {}
    return TargetCheck(
        runtime=runtime,
        providers=_recorded_checks(provider_pins, listed.get("providers"), installed),
        optional=_recorded_checks(optional_pins, listed.get("optional"), installed),
        manifest=manifest,
    )


def _pins(entries: Any) -> dict[str, Any]:
    """A components.providers or components.optional block's version pins."""
    if not isinstance(entries, dict):
        return {}
    return {name: entry.get("version") for name, entry in entries.items() if isinstance(entry, dict)}


def _recorded_checks(
    pins: dict[str, Any], recorded: Any, installed: tuple[int, int, int] | None
) -> list[ComponentCheck]:
    """Each pin beside the version the target's manifest records for it."""
    if not isinstance(recorded, dict):
        recorded = {}
    checks = []
    for name, pin in sorted(pins.items()):
        entry = recorded.get(name)
        have = entry.get("version") if isinstance(entry, dict) else None
        if installed is None:
            checks.append(ComponentCheck(name, pin, None, "fresh"))
        elif not isinstance(have, str) or _xyz(have) is None or _xyz(str(pin or "")) is None:
            checks.append(ComponentCheck(name, pin, have if isinstance(have, str) else None, "unknown"))
        else:
            older = _xyz(str(pin)) < _xyz(have)  # type: ignore[operator]
            checks.append(ComponentCheck(name, pin, have, "older" if older else "ok"))
    return checks


def _preflight_needs_attention(block: list[str]) -> bool:
    return any(line.startswith(("✗", "⚠")) for line in block)


def _reboot_pending(executor: Executor) -> bool:
    """Advisory only: a target we cannot ask is reported as not pending."""
    try:
        return executor.file_exists(REBOOT_REQUIRED_PATH)
    except Exception:  # noqa: BLE001 — never mask the deploy's own outcome
        logger.warning("could not check %s on the target", REBOOT_REQUIRED_PATH, exc_info=True)
        return False


def _keep_install_log(project_name: str, stdout: str, stderr: str) -> str | None:
    """Write install.sh's whole output to INSTALL_LOG_DIR, owner-only. Never
    fails the deploy: a log that cannot be written is reported as None."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", project_name)
    try:
        INSTALL_LOG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = INSTALL_LOG_DIR / f"install-{safe_name}-{stamp}.log"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(stdout)
            if stderr:
                fh.write("\n--- stderr ---\n" + stderr)
    except OSError:
        logger.warning("could not keep install.sh's output in %s", INSTALL_LOG_DIR, exc_info=True)
        return None
    return str(path)


def _run_install_sh(
    executor: Executor,
    install_sh: str,
    args: list[str],
    project_name: str,
    progress: ProgressCallback | None,
) -> tuple[str, list[str], bool, str | None]:
    """Run install.sh; return (stdout, host-preflight block worth showing,
    reboot pending, the log holding its whole output)."""
    result = executor.run(["bash", install_sh, *args], sudo=True, timeout=INSTALL_TIMEOUT_S)
    log_path = _keep_install_log(project_name, result.stdout, result.stderr)
    block = host_preflight_block(result.stdout)
    reboot = _reboot_pending(executor)
    if result.returncode != 0:
        # sudo's own refusal: install.sh never ran. The message carries none
        # of the command's output.
        if "incorrect password attempt" in result.stderr:
            raise DeployError("sudo rejected the password; install.sh did not run. Check it and deploy again.")
        if block and _preflight_needs_attention(block):
            detail = "\n".join(block)
        else:
            detail = "\n".join(_ANSI_RE.sub("", result.stdout + "\n" + result.stderr).strip().splitlines()[-15:])
        message = f"install.sh failed (exit {result.returncode}):\n{detail}"
        if reboot:
            message += f"\nA reboot is pending on the target ({REBOOT_REQUIRED_PATH}); reboot it, then deploy again."
        if log_path:
            message += f"\nFull install.sh output: {log_path}"
        raise DeployError(message, host_preflight=block, reboot_pending=reboot)
    if progress:
        progress("install", "install.sh completed")
    return result.stdout, (block if _preflight_needs_attention(block) else []), reboot, log_path


def deploy_local(
    *,
    project_dir: pathlib.Path,
    project_name: str,
    prefix: pathlib.Path = DEFAULT_INSTALL_PREFIX,
    no_start: bool = False,
    dry_run: bool = False,
    with_telemetry_export: bool = False,
    with_observability: bool = False,
    variant: str | None = None,
    allow_unmet_host: bool = False,
    executor: Executor | None = None,
    progress_callback: ProgressCallback | None = None,
) -> DeployResult:
    """Copy a canonical project verbatim and run install.sh --project."""
    if executor is None:
        executor = LocalExecutor()

    def _progress(step: str, detail: str = "") -> None:
        if progress_callback:
            progress_callback(step, detail)

    with tempfile.TemporaryDirectory(prefix="anolis-deploy-") as td:
        tmp = pathlib.Path(td)
        _progress("materialize", "Copying machine-profile project for install.sh")
        mat = materialize_project_dir(project_dir, tmp, variant=variant)
        _check_downgrade(executor, prefix, mat.runtime_version, _progress)
        _progress("fetch", f"Fetching install.sh v{mat.runtime_version}")
        install_sh, warning = fetch_install_sh(mat.runtime_version, tmp)
        if warning:
            _progress("unverified", warning)
        if notice := _existing_config_notice(executor, prefix, variant):
            _progress("existing-config", notice)
        _progress("install", f"Running install.sh --project (runtime v{mat.runtime_version})")
        output, preflight, reboot, log_path = _run_install_sh(
            executor,
            str(install_sh),
            _install_args(
                str(mat.project_dir),
                prefix=prefix,
                no_start=no_start,
                dry_run=dry_run,
                with_telemetry_export=with_telemetry_export,
                with_observability=with_observability,
                variant=variant,
                allow_unmet_host=allow_unmet_host,
            ),
            project_name,
            progress_callback,
        )
    preserved = preserved_lines(output)
    return DeployResult(
        project_name=project_name,
        runtime_version=mat.runtime_version,
        prefix=str(prefix),
        output=output,
        host_preflight=preflight,
        reboot_pending=reboot,
        preserved=preserved,
        runtime_config_kept=runtime_config_kept(preserved),
        install_sh_warning=warning,
        log_path=log_path,
    )


def deploy_remote(
    *,
    executor: Executor,
    project_dir: pathlib.Path,
    project_name: str,
    prefix: pathlib.Path = DEFAULT_INSTALL_PREFIX,
    no_start: bool = False,
    dry_run: bool = False,
    with_telemetry_export: bool = False,
    with_observability: bool = False,
    variant: str | None = None,
    allow_unmet_host: bool = False,
    remote_staging: str = "/tmp/anolis-deploy",
    progress_callback: ProgressCallback | None = None,
) -> DeployResult:
    """Copy a canonical project verbatim, push it, run install.sh remotely."""

    def _progress(step: str, detail: str = "") -> None:
        if progress_callback:
            progress_callback(step, detail)

    with tempfile.TemporaryDirectory(prefix="anolis-deploy-") as td:
        tmp = pathlib.Path(td)
        _progress("materialize", "Copying machine-profile project for install.sh")
        mat = materialize_project_dir(project_dir, tmp, variant=variant)
        _check_downgrade(executor, prefix, mat.runtime_version, _progress)
        install_sh, warning = fetch_install_sh(mat.runtime_version, tmp)
        if warning:
            _progress("unverified", warning)

        remote_root = f"{remote_staging}/{mat.project_dir.name}"
        _progress("push", f"Pushing project config to {remote_root}")
        _push_dir(executor, mat.project_dir, remote_root)
        remote_install = f"{remote_staging}/install.sh"
        executor.write_file(remote_install, install_sh.read_bytes())

        if notice := _existing_config_notice(executor, prefix, variant):
            _progress("existing-config", notice)
        _progress("install", f"Running install.sh --project on target (runtime v{mat.runtime_version})")
        output, preflight, reboot, log_path = _run_install_sh(
            executor,
            remote_install,
            _install_args(
                remote_root,
                prefix=prefix,
                no_start=no_start,
                dry_run=dry_run,
                with_telemetry_export=with_telemetry_export,
                with_observability=with_observability,
                variant=variant,
                allow_unmet_host=allow_unmet_host,
            ),
            project_name,
            progress_callback,
        )
    preserved = preserved_lines(output)
    return DeployResult(
        project_name=project_name,
        runtime_version=mat.runtime_version,
        prefix=str(prefix),
        output=output,
        host_preflight=preflight,
        reboot_pending=reboot,
        preserved=preserved,
        runtime_config_kept=runtime_config_kept(preserved),
        install_sh_warning=warning,
        log_path=log_path,
    )


def stage_bundle(
    *,
    project_dir: pathlib.Path,
    project_name: str,
    out_dir: pathlib.Path,
    arch: str | None = None,
    prefix: pathlib.Path = DEFAULT_INSTALL_PREFIX,
    variant: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> pathlib.Path:
    """Build an offline bundle from a canonical project via install.sh --stage."""

    def _progress(step: str, detail: str = "") -> None:
        if progress_callback:
            progress_callback(step, detail)

    out_dir.mkdir(parents=True, exist_ok=True)
    bundle_glob = "anolis-*.tar.gz"
    before = {p.name for p in out_dir.glob(bundle_glob)}
    with tempfile.TemporaryDirectory(prefix="anolis-stage-") as td:
        tmp = pathlib.Path(td)
        _progress("materialize", "Copying machine-profile project for install.sh --stage")
        mat = materialize_project_dir(project_dir, tmp, variant=variant)
        install_sh, warning = fetch_install_sh(mat.runtime_version, tmp)
        if warning:
            _progress("unverified", warning)
        cmd = ["bash", str(install_sh), "--stage", str(out_dir), "--project", str(mat.project_dir)]
        if pathlib.Path(prefix) != DEFAULT_INSTALL_PREFIX:
            cmd += ["--prefix", str(prefix)]
        # NOTE: --variant is deliberately NOT passed. install.sh's bundle
        # assembly always stages the `manual` variant and ignores the flag, so
        # passing it would tell the operator they had chosen something they had
        # not. The bundle carries every variant; the choice is made at install
        # time on the target.
        if arch:
            cmd += ["--arch", arch]
        _progress("stage", f"Building offline bundle (runtime v{mat.runtime_version})")
        result = LocalExecutor().run(cmd, timeout=INSTALL_TIMEOUT_S)
        if result.returncode != 0:
            tail = "\n".join((result.stdout + "\n" + result.stderr).strip().splitlines()[-15:])
            raise DeployError(f"install.sh --stage failed (exit {result.returncode}):\n{tail}")
    produced = [p for p in out_dir.glob(bundle_glob) if p.name not in before]
    if not produced:
        raise DeployError(f"install.sh --stage produced no bundle tarball in {out_dir}")
    return max(produced, key=lambda p: p.stat().st_mtime)
