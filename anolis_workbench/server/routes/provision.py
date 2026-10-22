"""Provision-track HTTP handlers for the Workbench server.

Exposes endpoints for triggering and monitoring provisioning jobs from the
Tauri desktop UI. Jobs run in background threads; progress is streamed via SSE.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from anolis_workbench.core import canonical, deploy, installer, machine_profile, projects, releases
from anolis_workbench.core import paths as paths_module
from anolis_workbench.core.executor import ParamikoSSHExecutor
from anolis_workbench.core.paths import DEFAULT_INSTALL_PREFIX

DEFAULT_TEMPLATE = "sim-quickstart"

# Sidecar meta.created_by on a project a provision job seeded from a template
# (Onboarding's setup cards). First-run detection ignores such a project, so a
# failed first install keeps the way back into onboarding (#424).
CREATED_BY_PROVISION = "onboarding"

# ---------------------------------------------------------------------------
# Job management
# ---------------------------------------------------------------------------


@dataclass
class ProvisionJob:
    """In-progress or completed provision job."""

    job_id: str
    status: str = "running"  # running | done | failed
    events: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


_jobs: dict[str, ProvisionJob] = {}
_jobs_lock = threading.Lock()


def _get_job(job_id: str) -> ProvisionJob | None:
    with _jobs_lock:
        return _jobs.get(job_id)


def _create_job() -> ProvisionJob:
    job = ProvisionJob(job_id=uuid.uuid4().hex[:12])
    with _jobs_lock:
        _jobs[job.job_id] = job
    return job


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def _prepare_workspace(params: dict[str, Any], progress: Any, *, allow_replace: bool = True) -> Path:
    """Authoring: ensure the local workspace project exists; return its dir.

    Since #255 a workspace project IS the canonical artifact set, so there is
    nothing to render — deploy copies this directory verbatim.

    `allow_replace=False` for read-only operations: `force` deletes and
    re-seeds the project directory, which for a commissioned rig means losing
    its configs, behaviours and the pre-migration backup.
    """
    template = params.get("template") or DEFAULT_TEMPLATE
    # Named after what it actually is: a project called `bioreactor-v1` seeded
    # from a simulator template describes something that does not exist.
    project = params.get("project") or template
    prefix = Path(params.get("install_prefix", str(DEFAULT_INSTALL_PREFIX)))

    project_dir: Path = paths_module.SYSTEMS_ROOT / project
    replace = bool(params.get("force", False)) and allow_replace
    if not project_dir.exists() or replace:
        progress("project", f"Creating workspace project {project} from {template}")
        installer.provision_project(template, project, prefix, force=replace)
        sidecar = machine_profile.read_sidecar(project_dir) or {}
        meta = sidecar.get("meta")
        sidecar["meta"] = {**(meta if isinstance(meta, dict) else {}), "created_by": CREATED_BY_PROVISION}
        canonical.write_sidecar(project_dir, sidecar)
    return project_dir


def _deploy_error_fields(exc: Exception) -> dict[str, Any]:
    """The host-preflight block and reboot flag a failed deploy carries."""
    if not isinstance(exc, deploy.DeployError):
        return {}
    return {"host_preflight": exc.host_preflight, "reboot_pending": exc.reboot_pending}


def _run_install_job(job: ProvisionJob, params: dict[str, Any]) -> None:
    """Run install in a background thread, pushing events to the job."""

    def _progress(step: str, detail: str = "") -> None:
        event = {"stage": step, "detail": detail}
        job.events.append(event)

    try:
        project = params.get("project") or params.get("template") or DEFAULT_TEMPLATE
        project_dir = _prepare_workspace(params, _progress)
        result = deploy.deploy_local(
            project_dir=project_dir,
            project_name=project,
            prefix=Path(params.get("install_prefix", str(DEFAULT_INSTALL_PREFIX))),
            variant=params.get("variant"),
            with_telemetry_export=params.get("with_telemetry_export") is True,
            with_observability=params.get("with_observability") is True,
            allow_unmet_host=params.get("allow_unmet_host") is True,
            progress_callback=_progress,
        )
        job.events.append(
            {
                "stage": "done",
                "summary": {
                    # Onboarding opens it in Operate (#238); local installs only.
                    "project": project,
                    "runtime_version": result.runtime_version,
                    "host_preflight": result.host_preflight,
                    "reboot_pending": result.reboot_pending,
                    "preserved": result.preserved,
                    "runtime_config_kept": result.runtime_config_kept,
                    "install_sh_warning": result.install_sh_warning,
                    "health": deploy.health_lines(result.output),
                    "observability": deploy.observability_lines(result.output),
                    "grafana_access": deploy.grafana_access(result.output),
                },
            }
        )
        job.status = "done"
    except Exception as exc:
        job.error = str(exc)
        job.status = "failed"
        job.events.append({"stage": "error", "detail": str(exc), **_deploy_error_fields(exc)})


def _run_remote_job(job: ProvisionJob, params: dict[str, Any]) -> None:
    """Run remote provision in a background thread using ParamikoSSHExecutor."""

    def _progress(step: str, detail: str = "") -> None:
        event = {"stage": step, "detail": detail}
        job.events.append(event)

    executor = None
    try:
        target = params["target"]
        user, host = target.split("@", 1)
        executor = ParamikoSSHExecutor(
            host=host,
            user=user,
            key_file=params.get("key_file"),
            port=params.get("port", 22),
        )

        # Authoring stays local; the target only receives the deployment.
        project = params.get("project") or params.get("template") or DEFAULT_TEMPLATE
        project_dir = _prepare_workspace(params, _progress)
        result = deploy.deploy_remote(
            executor=executor,
            project_dir=project_dir,
            project_name=project,
            prefix=Path(params.get("install_prefix", str(DEFAULT_INSTALL_PREFIX))),
            variant=params.get("variant"),
            with_telemetry_export=params.get("with_telemetry_export") is True,
            with_observability=params.get("with_observability") is True,
            allow_unmet_host=params.get("allow_unmet_host") is True,
            progress_callback=_progress,
        )
        # Auto-add host to fleet registry
        from anolis_workbench.core.fleet import auto_register_host

        auto_register_host(
            host=host,
            project=params.get("project") or params.get("template") or DEFAULT_TEMPLATE,
            template=params.get("template") or DEFAULT_TEMPLATE,
        )
        job.events.append(
            {
                "stage": "done",
                "summary": {
                    "runtime_version": result.runtime_version,
                    "host_preflight": result.host_preflight,
                    "reboot_pending": result.reboot_pending,
                    "preserved": result.preserved,
                    "runtime_config_kept": result.runtime_config_kept,
                    "install_sh_warning": result.install_sh_warning,
                    "health": deploy.health_lines(result.output),
                    "observability": deploy.observability_lines(result.output),
                    "grafana_access": deploy.grafana_access(
                        result.output,
                        ssh_target=target,
                        ssh_port=params.get("port", 22),
                        ssh_key=params.get("key_file"),
                    ),
                },
            }
        )
        job.status = "done"
    except Exception as exc:
        job.error = str(exc)
        job.status = "failed"
        job.events.append({"stage": "error", "detail": str(exc), **_deploy_error_fields(exc)})
    finally:
        if executor is not None:
            executor.close()


# ---------------------------------------------------------------------------
# HTTP Handlers
# ---------------------------------------------------------------------------


def _reject_invalid_names(handler: Any, params: dict[str, Any]) -> bool:
    """`project` and `template` name directories under the workspace and
    template roots, so both are held to the project-name rule."""
    for key in ("project", "template"):
        value = params.get(key)
        if value in (None, ""):
            continue
        if not isinstance(value, str) or projects.validate_name(value):
            handler._json(
                400, {"error": f"'{key}' must be 1-64 characters: letters, digits, hyphens, underscores only."}
            )
            return True
    return False


def start_install(handler: Any) -> None:
    """POST /api/provision/install — start a local install job."""
    content_length = int(handler.headers.get("Content-Length", 0))
    body = handler.rfile.read(content_length) if content_length else b"{}"
    params = json.loads(body)
    if _reject_invalid_names(handler, params):
        return

    job = _create_job()
    thread = threading.Thread(target=_run_install_job, args=(job, params), daemon=True)
    thread.start()

    handler._json(202, {"job_id": job.job_id})


def start_remote(handler: Any) -> None:
    """POST /api/provision/remote — start a remote provision job."""
    content_length = int(handler.headers.get("Content-Length", 0))
    body = handler.rfile.read(content_length) if content_length else b"{}"
    params = json.loads(body)

    if "target" not in params:
        handler._json(400, {"error": "Missing 'target' field (user@host)"})
        return
    if _reject_invalid_names(handler, params):
        return

    job = _create_job()
    thread = threading.Thread(target=_run_remote_job, args=(job, params), daemon=True)
    thread.start()

    handler._json(202, {"job_id": job.job_id})


def get_status(handler: Any, job_id: str) -> None:
    """GET /api/provision/status/<job_id> — SSE stream of job progress."""
    job = _get_job(job_id)
    if job is None:
        handler._json(404, {"error": f"Job {job_id} not found"})
        return

    # Send as SSE (Server-Sent Events)
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.end_headers()

    sent = 0
    import time

    try:
        while True:
            # Send any new events
            while sent < len(job.events):
                event = job.events[sent]
                data = json.dumps(event)
                handler.wfile.write(f"data: {data}\n\n".encode())
                handler.wfile.flush()
                sent += 1

            if job.status in ("done", "failed"):
                # Send final status event
                final = json.dumps({"stage": "status", "status": job.status, "error": job.error})
                handler.wfile.write(f"data: {final}\n\n".encode())
                handler.wfile.flush()
                break

            time.sleep(0.2)
    except OSError:
        # The client went away (a reload, a closed tab); the job runs on.
        handler.close_connection = True


# ---------------------------------------------------------------------------
# Bundle export
# ---------------------------------------------------------------------------

_bundle_artifacts: dict[str, Path] = {}
_bundle_artifacts_lock = threading.Lock()


def _run_bundle_job(job: ProvisionJob, params: dict[str, Any]) -> None:
    """Run bundle creation in a background thread via install.sh --stage."""
    import platform
    import tempfile

    warnings: list[str] = []

    def _progress(step: str, detail: str = "") -> None:
        if step == "unverified":
            warnings.append(detail)
        job.events.append({"stage": step, "detail": detail})

    try:
        arch = params.get("arch") or ("arm64" if platform.machine() in ("aarch64", "arm64") else "x86_64")
        if arch == "aarch64":
            arch = "arm64"
        project = params.get("project") or params.get("template") or DEFAULT_TEMPLATE

        _progress("resolve", f"Building bundle for {project} ({arch})")

        tmp_dir = Path(tempfile.mkdtemp(prefix="anolis-bundle-"))
        # Building a bundle reads the project; it must never replace it.
        project_dir = _prepare_workspace(params, _progress, allow_replace=False)
        tarball_path = deploy.stage_bundle(
            project_dir=project_dir,
            project_name=project,
            out_dir=tmp_dir,
            arch=arch,
            variant=params.get("variant"),
            progress_callback=_progress,
        )

        with _bundle_artifacts_lock:
            _bundle_artifacts[job.job_id] = tarball_path

        job.events.append(
            {
                "stage": "done",
                "detail": ". ".join([f"Bundle ready: {tarball_path.name}", *warnings]),
                "filename": tarball_path.name,
            }
        )
        job.status = "done"
    except Exception as exc:
        job.error = str(exc)
        job.status = "failed"
        job.events.append({"stage": "error", "detail": str(exc)})


def start_bundle(handler: Any) -> None:
    """POST /api/provision/bundle — start a bundle creation job."""
    content_length = int(handler.headers.get("Content-Length", 0))
    body = handler.rfile.read(content_length) if content_length else b"{}"
    params = json.loads(body)

    # Passed to install.sh --stage --arch verbatim, so it is checked against
    # the arches the CLI accepts too. Absent means "the host's arch".
    arch = params.get("arch")
    if arch is not None and arch not in releases.SUPPORTED_ARCHS:
        expected = ", ".join(releases.SUPPORTED_ARCHS)
        handler._json(400, {"error": f"Unsupported arch {arch!r}; expected one of {expected}"})
        return
    if _reject_invalid_names(handler, params):
        return

    job = _create_job()
    thread = threading.Thread(target=_run_bundle_job, args=(job, params), daemon=True)
    thread.start()

    handler._json(202, {"job_id": job.job_id})


def download_bundle(handler: Any, job_id: str) -> None:
    """GET /api/provision/bundle/<job_id> — download completed bundle tarball."""
    job = _get_job(job_id)
    if job is None:
        handler._json(404, {"error": f"Job {job_id} not found"})
        return

    if job.status != "done":
        handler._json(409, {"error": f"Job not complete (status: {job.status})"})
        return

    with _bundle_artifacts_lock:
        tarball_path = _bundle_artifacts.get(job_id)

    if tarball_path is None or not tarball_path.exists():
        handler._json(404, {"error": "Bundle artifact not found"})
        return

    data = tarball_path.read_bytes()
    filename = tarball_path.name

    handler.send_response(200)
    handler.send_header("Content-Type", "application/gzip")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    handler.end_headers()
    handler.wfile.write(data)
