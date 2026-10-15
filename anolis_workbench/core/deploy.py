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

import logging
import pathlib
import re
import shlex
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Callable

import requests
import yaml

from anolis_workbench.core import canonical, machine_profile, releases
from anolis_workbench.core.executor import Executor, LocalExecutor
from anolis_workbench.core.paths import DEFAULT_INSTALL_PREFIX

logger = logging.getLogger(__name__)

# A real install downloads binaries and waits for systemd health.
INSTALL_TIMEOUT_S = 1800.0

# The Debian convention for "a reboot is pending" (tmpfs, so a reboot clears
# it). A project's host prep writes it; the workbench only reports it.
REBOOT_REQUIRED_PATH = "/run/reboot-required"

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


def fetch_install_sh(runtime_version: str, dest: pathlib.Path) -> pathlib.Path:
    """Download install.sh from the anolis release the profile pins."""
    url = f"https://github.com/{releases.RUNTIME_REPO}/releases/download/v{runtime_version}/install.sh"
    try:
        resp = requests.get(url, timeout=30)
    except requests.RequestException as exc:
        raise DeployError(f"failed to download install.sh from {url}: {exc}") from exc
    if resp.status_code != 200:
        raise DeployError(f"failed to download install.sh from {url}: HTTP {resp.status_code}")
    path = dest / "install.sh"
    path.write_bytes(resp.content)
    path.chmod(0o755)
    return path


def run_rollback(
    executor: Executor | None = None,
    *,
    prefix: pathlib.Path = DEFAULT_INSTALL_PREFIX,
    staging: str = "/tmp/anolis-deploy",
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
        install_sh = fetch_install_sh(version, pathlib.Path(td))
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
# message that itself starts with two spaces) start with four.
_DETAIL_INDENT = "    "


def observability_lines(stdout: str) -> list[str]:
    """install.sh's observability, Grafana and InfluxDB lines, ANSI
    stripped, each with the indented detail that follows it. install.sh
    reports every observability failure as a warning and still exits 0, so
    these lines are the only place a failure shows."""
    lines = _ANSI_RE.sub("", stdout).splitlines()
    out: list[str] = []
    in_block = False
    for line in lines:
        if any(mark in line for mark in _OBSERVABILITY_MARKS):
            out.append(line)
            in_block = True
        elif in_block and line.startswith(_DETAIL_INDENT) and line.strip():
            out.append(line)
        else:
            in_block = False
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


def _preflight_needs_attention(block: list[str]) -> bool:
    return any(line.startswith(("✗", "⚠")) for line in block)


def _reboot_pending(executor: Executor) -> bool:
    """Advisory only: a target we cannot ask is reported as not pending."""
    try:
        return executor.file_exists(REBOOT_REQUIRED_PATH)
    except Exception:  # noqa: BLE001 — never mask the deploy's own outcome
        logger.warning("could not check %s on the target", REBOOT_REQUIRED_PATH, exc_info=True)
        return False


def _run_install_sh(
    executor: Executor,
    install_sh: str,
    args: list[str],
    progress: ProgressCallback | None,
) -> tuple[str, list[str], bool]:
    """Run install.sh; return (stdout, host-preflight block worth showing, reboot pending)."""
    result = executor.run(["bash", install_sh, *args], sudo=True, timeout=INSTALL_TIMEOUT_S)
    block = host_preflight_block(result.stdout)
    reboot = _reboot_pending(executor)
    if result.returncode != 0:
        if block and _preflight_needs_attention(block):
            detail = "\n".join(block)
        else:
            detail = "\n".join(_ANSI_RE.sub("", result.stdout + "\n" + result.stderr).strip().splitlines()[-15:])
        message = f"install.sh failed (exit {result.returncode}):\n{detail}"
        if reboot:
            message += f"\nA reboot is pending on the target ({REBOOT_REQUIRED_PATH}); reboot it, then deploy again."
        raise DeployError(message, host_preflight=block, reboot_pending=reboot)
    if progress:
        progress("install", "install.sh completed")
    return result.stdout, (block if _preflight_needs_attention(block) else []), reboot


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
        _progress("fetch", f"Fetching install.sh v{mat.runtime_version}")
        install_sh = fetch_install_sh(mat.runtime_version, tmp)
        if notice := _existing_config_notice(executor, prefix, variant):
            _progress("existing-config", notice)
        _progress("install", f"Running install.sh --project (runtime v{mat.runtime_version})")
        output, preflight, reboot = _run_install_sh(
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
        install_sh = fetch_install_sh(mat.runtime_version, tmp)

        remote_root = f"{remote_staging}/{mat.project_dir.name}"
        _progress("push", f"Pushing project config to {remote_root}")
        _push_dir(executor, mat.project_dir, remote_root)
        remote_install = f"{remote_staging}/install.sh"
        executor.write_file(remote_install, install_sh.read_bytes())

        if notice := _existing_config_notice(executor, prefix, variant):
            _progress("existing-config", notice)
        _progress("install", f"Running install.sh --project on target (runtime v{mat.runtime_version})")
        output, preflight, reboot = _run_install_sh(
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
        install_sh = fetch_install_sh(mat.runtime_version, tmp)
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
