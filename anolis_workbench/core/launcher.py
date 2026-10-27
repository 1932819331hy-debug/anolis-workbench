"""Process management for Anolis Workbench.

Preflight checks, process launch, stop, restart, and SSE log streaming.
All public functions are called from server.py HTTP handlers.
"""

import copy
import json
import os
import pathlib
import queue
import signal
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any

import yaml

from anolis_workbench.core import canonical, provider_schemas
from anolis_workbench.core import paths as paths_module
from anolis_workbench.core.child_env import child_env

_state: dict = {
    "project": None,  # active project name
    "process": None,  # subprocess.Popen handle
    "log_file": None,  # open file handle for latest.log
    "log_lines_by_project": {},  # dict[str, list[str]] ring buffers (last 200 lines)
}
_state_lock = threading.Lock()

_sse_subscribers: dict[str, list] = {}  # dict[project_name, list[queue.Queue]]
_sse_lock = threading.Lock()


def running_json_path(name: str) -> pathlib.Path:
    return paths_module.SYSTEMS_ROOT / name / "running.json"


def _resolve_executable_path(path_value: str | None) -> pathlib.Path | None:
    if not path_value:
        return None
    return paths_module.resolve_data_path(path_value)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def get_status() -> dict:
    """Return serialisable status dict for GET /api/status."""
    runtime = _current_runtime_snapshot(clean_stale=True)
    return {
        "version": 1,
        "active_project": runtime["project"],
        "running": runtime["running"],
        "pid": runtime["pid"],
        # A runtime the workbench did not launch, such as the systemd service
        # install.sh installs: its own Stop and Restart cannot reach it (wb#581).
        "external": bool(runtime.get("external")),
    }


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil

        return bool(psutil.pid_exists(pid))
    except ImportError:
        pass

    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)  # type: ignore[attr-defined]
        if handle == 0:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return True

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _terminate_pid(pid: int, timeout_s: float = 5.0) -> None:
    if pid <= 0:
        return

    _psutil: Any = None
    try:
        import psutil as _psutil_import

        _psutil = _psutil_import
    except ImportError:
        pass

    if _psutil is not None:
        try:
            proc = _psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=timeout_s)
            except _psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=timeout_s)
            return
        except _psutil.Error:
            return

    if os.name == "nt":
        import ctypes

        PROCESS_TERMINATE = 0x0001
        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, pid)  # type: ignore[attr-defined]
        if handle == 0:
            return
        try:
            ctypes.windll.kernel32.TerminateProcess(handle, 1)  # type: ignore[attr-defined]
            ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout_s * 1000))  # type: ignore[attr-defined]
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        return

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not _pid_exists(pid):
            return
        time.sleep(0.1)
    if _pid_exists(pid):
        try:
            _sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
            os.kill(pid, _sigkill)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _discover_running_runtime(clean_stale: bool = False) -> dict | None:
    systems_dir = paths_module.SYSTEMS_ROOT
    if not systems_dir.exists():
        return None

    for project_dir in sorted(systems_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        running_path = project_dir / "running.json"
        if not running_path.exists():
            continue

        try:
            data = json.loads(running_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            if clean_stale:
                try:
                    running_path.unlink(missing_ok=True)
                except OSError:
                    pass
            continue

        pid = data.get("pid")
        if not isinstance(pid, int):
            if clean_stale:
                try:
                    running_path.unlink(missing_ok=True)
                except OSError:
                    pass
            continue

        if _pid_exists(pid):
            return {
                "running": True,
                "project": project_dir.name,
                "pid": pid,
                "managed": False,
            }

        if clean_stale:
            try:
                running_path.unlink(missing_ok=True)
            except OSError:
                pass

    return None


def _runtime_alive(bind: str, port: int, timeout_s: float = 0.5) -> bool:
    """True if an anolis-runtime answers /v0/runtime/status on bind:port.

    The probe sends no token. A runtime that requires one on loopback answers
    401, which still proves a runtime is there, so 401 counts as alive. A
    refused connection, a timeout, any other error status, and a 200 without
    a version are not alive.
    """
    import json as _json
    import urllib.error
    import urllib.request

    url = f"http://{bind}:{port}/v0/runtime/status"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:  # noqa: S310 — caller restricts bind to loopback
            if resp.status != 200:
                return False
            return "version" in _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code == 401
    except Exception:
        return False


def _discover_external_runtime() -> dict | None:
    """Detect a runtime the workbench did not launch (e.g. one installed as a
    systemd service by install.sh) by probing each known project's configured
    runtime HTTP port.

    This keeps the install and operate paths coherent: a machine that has been
    set up and is running is operable from the workbench regardless of who
    started its runtime. Such a runtime is reported managed=False/external=True,
    so stop()/restart() (which only act on workbench-launched processes) leave
    it untouched — it is owned by systemd, not the workbench.
    """
    systems_dir = paths_module.SYSTEMS_ROOT
    if not systems_dir.exists():
        return None

    from anolis_workbench.core import projects as projects_module

    for project_dir in sorted(systems_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        try:
            document = projects_module.get_project(project_dir.name)
            runtime_doc = active_runtime_config(document)
        except (FileNotFoundError, ValueError, OSError, AttributeError, TypeError):
            continue
        if not isinstance(runtime_doc, dict):
            continue
        http = runtime_doc.get("http")
        if not isinstance(http, dict):
            continue
        bind = http.get("bind") or "127.0.0.1"
        if bind in ("0.0.0.0", ""):
            bind = "127.0.0.1"
        # Only auto-detect a loopback-bound runtime. A non-loopback bind is a
        # remote/LAN target, reached explicitly via ANOLIS_WORKBENCH_RUNTIME_URL
        # (operate.py) — not something to probe automatically on every status poll.
        if bind not in ("127.0.0.1", "localhost", "::1"):
            continue
        port = http.get("port")
        if not isinstance(port, int):
            continue
        if _runtime_alive(bind, port):
            return {
                "running": True,
                "project": project_dir.name,
                "pid": None,
                "managed": False,
                "external": True,
            }
    return None


def _current_runtime_snapshot(clean_stale: bool = False) -> dict:
    with _state_lock:
        proc = _state["process"]
        if proc is not None and proc.poll() is None:
            return {
                "running": True,
                "project": _state["project"],
                "pid": proc.pid,
                "managed": True,
            }

    discovered = _discover_running_runtime(clean_stale=clean_stale)
    if discovered is not None:
        return discovered

    external = _discover_external_runtime()
    if external is not None:
        return external

    return {"running": False, "project": None, "pid": None, "managed": False}


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def preflight(name: str, system: dict, project_dir: pathlib.Path) -> dict:
    """
    Run preflight checks and return {"ok": bool, "checks": [...], "summary": {...}}
    (see _preflight_result).
    Re-renders YAML to disk before running binary checks.
    """
    from anolis_workbench.core import (
        canonical_validator,  # local import avoids any circular at import time
    )

    checks: list[dict] = []

    # Project the canonical variant to disk first, so --check-config sees the
    # config the runtime will actually be handed.
    try:
        launch_config = materialize_launch_config(project_dir, system)
    except Exception as exc:
        checks.append(
            {
                "name": "Project runtime config for launch",
                "ok": False,
                "error": str(exc),
                "hint": None,
            }
        )
        return _preflight_result(checks)

    # Check 1: Runtime binary
    host_paths = canonical.host_paths({"host_paths": system.get("host_paths")})
    runtime_exe_value = host_paths.get("runtime_executable", "")
    runtime_exe = _resolve_executable_path(runtime_exe_value)
    _exists_check(
        checks,
        "Runtime binary exists",
        runtime_exe,
        hint=_build_binary_hint(runtime_exe, repo="anolis", docs="docs/"),
    )

    # Check 2: Provider binaries
    providers = system.get("providers", {})
    for pid, pcfg in providers.items():
        exe_str = host_paths.get("providers", {}).get(pid, {}).get("executable", "")
        exe = _resolve_executable_path(exe_str)
        kind = pcfg.get("kind", "")
        # A hint string only. The real repo for a kind lives in the machine
        # profile's components.providers pin, which may be any org (#285).
        known_kind = provider_schemas.get_envelope(kind) is not None
        repo = f"anolis-provider-{kind}" if known_kind else None
        docs = "docs/" if known_kind else None
        _exists_check(
            checks,
            f"Provider {pid} binary exists",
            exe,
            hint=_build_binary_hint(exe, repo=repo, docs=docs) if exe is not None else None,
        )

    # Check 3: Output paths writable
    checks.append(_check_writable(project_dir))

    # Check 3b: Runtime port in use
    runtime_doc = active_runtime_config(system) or {}
    rt_port = (runtime_doc.get("http") or {}).get("port")
    if rt_port is not None:
        checks.append(_check_port(rt_port))

    # Check 4: System-level validation
    errors = canonical_validator.validate_project(system)
    if errors:
        for err in errors:
            checks.append({"name": "System-level validation", "ok": False, "error": err, "hint": None})
    else:
        checks.append({"name": "System-level validation", "ok": True, "error": None, "hint": None})

    # Check 5: Runtime --check-config
    checks.append(
        _check_config_binary(
            "Runtime --check-config",
            runtime_exe,
            launch_config,
        )
    )

    # Check 6+: Provider --check-config. The verb is universal per the
    # executable profile (protocol#62); _check_config_binary degrades gracefully
    # ("Not yet available") for a binary that does not recognize the flag.
    for pid in providers:
        exe_str = host_paths.get("providers", {}).get(pid, {}).get("executable", "")
        exe = _resolve_executable_path(exe_str)
        rel = system.get("profile", {}).get("providers", {}).get(pid, {}).get("config", "")
        yaml_path = project_dir / rel if rel else project_dir
        checks.append(
            _check_config_binary(
                f"Provider {pid} --check-config",
                exe,
                yaml_path,
            )
        )
        # The provider says what it needs from this host (executable profile
        # v1 §6); the workbench relays it and knows no transport itself.
        checks.append(_check_host_binary(f"Provider {pid} --check-host", exe, yaml_path))

    return _preflight_result(checks)


def _preflight_result(checks: list[dict]) -> dict:
    """`ok` gates Launch and means "nothing failed". A skipped check (ok None)
    verified nothing, so `summary` counts it apart from the passes."""
    failed = sum(1 for c in checks if c.get("ok") is False)
    passed = sum(1 for c in checks if c.get("ok") is True)
    return {
        "ok": failed == 0,
        "checks": checks,
        "summary": {"passed": passed, "skipped": len(checks) - passed - failed, "failed": failed},
    }


# ---------------------------------------------------------------------------
# Build hints
# ---------------------------------------------------------------------------


def _build_binary_hint(exe: pathlib.Path | None, repo: str | None, docs: str | None) -> str:
    """Return an actionable build hint for a missing binary. It carries no
    per-kind facts: how a provider is built is the provider's to document (#287)."""
    # Detect whether the sibling repo directory exists
    if repo:
        sibling = pathlib.Path("..") / repo
        if sibling.is_dir():
            repo_note = f"Repo '{repo}' found but not built."
        else:
            repo_note = f"Repo not found — clone '{repo}' as a sibling of this repo."
    else:
        repo_note = ""

    parts = []
    if repo_note:
        parts.append(repo_note)
    if docs and repo:
        parts.append(f"Build docs: {repo}/{docs}")
    if not parts:
        parts.append("Set the binary's Executable path in Compose (the project's host paths).")
    return "  ".join(parts)


def _check_port(port: int) -> dict:
    """Check whether a TCP port is already occupied."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        in_use = s.connect_ex(("127.0.0.1", port)) == 0
    if in_use:
        return {
            "name": f"Runtime port {port} available",
            "ok": False,
            "error": f"Port {port} is already in use.",
            "hint": "Stop the existing process or change the runtime port in the config.",
        }
    return {"name": f"Runtime port {port} available", "ok": True, "error": None, "hint": None}


def _exists_check(checks: list, name: str, path: pathlib.Path | None, hint: str | None = None) -> None:
    exists = path is not None and path.exists()
    checks.append(
        {
            "name": name,
            "ok": exists,
            "error": None if exists else f"File not found: {path if path is not None else '<missing>'}",
            "hint": hint if not exists else None,
        }
    )


def _check_writable(project_dir: pathlib.Path) -> dict:
    name = "Output paths writable"
    try:
        project_dir.mkdir(parents=True, exist_ok=True)
        test_file = project_dir / ".composer_write_test"
        test_file.write_text("x", encoding="utf-8")
        test_file.unlink()
        return {"name": name, "ok": True, "error": None, "hint": None}
    except OSError as exc:
        return {
            "name": name,
            "ok": False,
            "error": f"{exc} (path: {project_dir})",
            "hint": "Check directory permissions or available disk space.",
        }


def _check_config_binary(check_name: str, exe: pathlib.Path | None, yaml_path: pathlib.Path) -> dict:
    if exe is None or not exe.exists():
        return {"name": check_name, "ok": None, "note": "Binary missing — skipped"}
    if not yaml_path.exists():
        return {"name": check_name, "ok": None, "note": "Config not yet rendered"}
    try:
        result = subprocess.run(
            [str(exe), "--check-config", str(yaml_path)],
            cwd=str(paths_module.DATA_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
            env=child_env(),
        )
        if result.returncode == 0:
            return {"name": check_name, "ok": True, "error": None, "hint": None}
        stderr_lower = (result.stderr or "").lower()
        if any(kw in stderr_lower for kw in ("unknown", "unrecognized", "invalid option")):
            return {"name": check_name, "ok": None, "note": "Not yet available"}
        error = (result.stderr or result.stdout or "Non-zero exit").strip()[:200]
        return {"name": check_name, "ok": False, "error": error, "hint": None}
    except subprocess.TimeoutExpired:
        return {"name": check_name, "ok": False, "error": "Timed out (10s)", "hint": None}
    except OSError as exc:
        return {"name": check_name, "ok": False, "error": str(exc), "hint": None}


def _check_host_binary(check_name: str, exe: pathlib.Path | None, yaml_path: pathlib.Path) -> dict:
    """Run a provider's --check-host as the user the dev launch runs as.

    Exit 0 is met (unknown requirements do not fail), 1 is unmet, 2 could not
    evaluate. A non-zero exit with no JSON on stdout is no answer — a provider
    that predates the verb — and is skipped, as install.sh's preflight does.
    """
    if exe is None or not exe.exists():
        return {"name": check_name, "ok": None, "note": "Binary missing — skipped"}
    if not yaml_path.exists():
        return {"name": check_name, "ok": None, "note": "Config not yet rendered"}
    try:
        result = subprocess.run(
            [str(exe), "--check-host", str(yaml_path)],
            cwd=str(paths_module.DATA_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
            env=child_env(),
        )
    except subprocess.TimeoutExpired:
        return {"name": check_name, "ok": False, "error": "Timed out (10s)", "hint": None}
    except OSError as exc:
        return {"name": check_name, "ok": False, "error": str(exc), "hint": None}

    try:
        envelope = json.loads(result.stdout)
    except ValueError:
        envelope = None
    requirements = envelope.get("requirements") if isinstance(envelope, dict) else None
    if not isinstance(requirements, list):
        if result.returncode == 0:
            return {"name": check_name, "ok": None, "note": "No host requirements reported"}
        return {"name": check_name, "ok": None, "note": "Not yet available"}

    reqs = [r for r in requirements if isinstance(r, dict)]
    unmet = [r for r in reqs if r.get("status") == "unmet"]
    unknown = [r for r in reqs if r.get("status") == "unknown"]
    note = "; ".join(f"{r.get('id', '?')} unknown: {r.get('detail', '')}" for r in unknown) or None
    if result.returncode == 0:
        return {"name": check_name, "ok": True, "error": None, "hint": None, "note": note}
    if result.returncode == 1:
        return {
            "name": check_name,
            "ok": False,
            "error": "; ".join(f"{r.get('id', '?')}: {r.get('detail', '')}" for r in unmet) or "unmet",
            "hint": "; ".join(str(r["remedy"]) for r in unmet if r.get("remedy")) or None,
            "note": note,
        }
    error = (result.stderr or "").strip()[:200] or f"exit {result.returncode}"
    return {"name": check_name, "ok": False, "error": f"Could not evaluate host requirements: {error}", "hint": None}


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------


class LaunchValidationError(RuntimeError):
    """The project fails the blocking validation preflight runs (#414)."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("Project failed validation; nothing was started or stopped.")
        self.errors = errors


def launch(name: str, system: dict, project_dir: pathlib.Path) -> None:
    """Start the anolis-runtime subprocess."""

    current = _current_runtime_snapshot(clean_stale=True)
    if current["running"]:
        raise RuntimeError("A system is already running.")

    # The same blocking validation as preflight's "System-level validation":
    # requiring a passing preflight is enforced only in the Commission UI, so
    # a direct launch request must not start a project preflight would refuse
    # (#414). Preflight's binary checks stay in preflight.
    from anolis_workbench.core import canonical_validator  # local import, as in preflight

    errors = canonical_validator.validate_project(system)
    if errors:
        raise LaunchValidationError(errors)

    # Project the canonical variant into a runnable host config.
    launch_config = str(materialize_launch_config(project_dir, system))

    # Prepare log file (overwrite)
    log_path = project_dir / "logs" / "latest.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "w", buffering=1, encoding="utf-8")

    # Build command
    host = canonical.host_paths({"host_paths": system.get("host_paths")})
    runtime_exe = _resolve_executable_path(host.get("runtime_executable", ""))
    if runtime_exe is None:
        raise RuntimeError("Runtime executable path is missing (set it in the project's host paths).")
    cmd = [str(runtime_exe), "--config", launch_config]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(paths_module.DATA_ROOT),
        bufsize=1,
        text=True,
        env=child_env(),
    )

    # Write running.json
    running_data = {
        "pid": proc.pid,
        "project": name,
        "started": datetime.now(timezone.utc).isoformat(),
    }
    (project_dir / "running.json").write_text(json.dumps(running_data), encoding="utf-8")

    with _state_lock:
        log_lines_by_project: dict = _state["log_lines_by_project"]
        log_lines_by_project[name] = []
        _state.update(
            {
                "project": name,
                "process": proc,
                "log_file": log_file,
            }
        )
    threading.Thread(target=_read_logs, args=(proc, log_file, name), daemon=True).start()


# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------


def stop() -> None:
    """Gracefully terminate the running process."""
    with _state_lock:
        proc = _state["process"]
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        return

    detached = _discover_running_runtime(clean_stale=False)
    if detached is None:
        return

    _terminate_pid(detached["pid"])
    if not _pid_exists(detached["pid"]):
        try:
            running_json_path(detached["project"]).unlink(missing_ok=True)
        except OSError:
            pass
        _notify_sse_subscribers(detached["project"], None)


# ---------------------------------------------------------------------------
# Restart
# ---------------------------------------------------------------------------


def restart(name: str, project_dir: pathlib.Path) -> None:
    """Kill and relaunch the running project.

    Re-reads the saved project and renders the launch variant's YAML again,
    as launch() does. So the saved project is validated first, as launch()
    does, before the running runtime is stopped (#414).
    """
    current = _current_runtime_snapshot(clean_stale=True)
    if not current["running"]:
        raise RuntimeError(f"Cannot restart '{name}' because no project is running.")
    if current.get("external"):
        raise RuntimeError(
            f"'{name}' is running as an external service (e.g. systemd) that the workbench "
            "does not manage — restart it on the device (e.g. systemctl restart anolis-runtime)."
        )
    if current["project"] != name:
        raise RuntimeError(
            f"Cannot restart '{name}' while '{current['project']}' is running. Stop '{current['project']}' first."
        )

    from anolis_workbench.core import canonical_validator  # local import, as in preflight
    from anolis_workbench.core import projects as projects_module

    system = projects_module.get_project(name)
    # Refuse before stop(), so a refused restart leaves the runtime running.
    errors = canonical_validator.validate_project(system)
    if errors:
        raise LaunchValidationError(errors)
    # Rendered again, as launch() does, so a file an older workbench wrote is
    # never handed to the runtime (it may lack runtime.data_dir, #562). Before
    # stop(), so a project that cannot render leaves the runtime running.
    launch_config = str(materialize_launch_config(project_dir, system))

    stop()
    time.sleep(0.5)  # Allow log reader thread to flush and clean up

    log_path = project_dir / "logs" / "latest.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "w", buffering=1, encoding="utf-8")

    host = canonical.host_paths({"host_paths": system.get("host_paths")})
    runtime_exe = _resolve_executable_path(host.get("runtime_executable", ""))
    if runtime_exe is None:
        raise RuntimeError("Runtime executable path is missing (set it in the project's host paths).")
    cmd = [str(runtime_exe), "--config", launch_config]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(paths_module.DATA_ROOT),
        bufsize=1,
        text=True,
        env=child_env(),
    )

    running_data = {
        "pid": proc.pid,
        "project": name,
        "started": datetime.now(timezone.utc).isoformat(),
    }
    (project_dir / "running.json").write_text(json.dumps(running_data), encoding="utf-8")

    with _state_lock:
        log_lines_by_project: dict = _state["log_lines_by_project"]
        log_lines_by_project[name] = []
        _state.update(
            {
                "project": name,
                "process": proc,
                "log_file": log_file,
            }
        )
    threading.Thread(target=_read_logs, args=(proc, log_file, name), daemon=True).start()


# ---------------------------------------------------------------------------
# Log reader thread
# ---------------------------------------------------------------------------


def _read_logs(proc: subprocess.Popen, log_file, project: str) -> None:
    if proc.stdout is None:
        return
    for line in proc.stdout:
        log_file.write(line)
        with _state_lock:
            lines = _state["log_lines_by_project"].setdefault(project, [])
            lines.append(line)
            if len(lines) > 200:
                lines.pop(0)
        _notify_sse_subscribers(project, line)
    _notify_sse_subscribers(project, None)  # sentinel: process stdout closed
    _cleanup_after_exit(proc)


def _notify_sse_subscribers(project: str, line) -> None:
    with _sse_lock:
        subscribers = list(_sse_subscribers.get(project, []))
    for q in subscribers:
        try:
            q.put_nowait(line)
        except Exception:
            pass


def _cleanup_after_exit(proc: subprocess.Popen) -> None:
    with _state_lock:
        # Guard: don't clobber state if a new process was already started
        if _state["process"] is not proc:
            return
        lf = _state["log_file"]
        project = _state["project"]
        _state.update({"process": None, "log_file": None, "project": None})
    if lf:
        try:
            lf.close()
        except OSError:
            pass
    if project:
        rj = running_json_path(project)
        try:
            rj.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# SSE log stream
# ---------------------------------------------------------------------------


def handle_log_stream(handler, project_name: str) -> None:
    """Stream log output to the browser via Server-Sent Events.

    ``handler`` is the BaseHTTPRequestHandler instance (provides .wfile).
    Blocks until the client disconnects or the process exits.
    """
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("X-Accel-Buffering", "no")
    handler.end_headers()

    # Replay buffered lines first (reconnect support)
    with _state_lock:
        buffered = list(_state["log_lines_by_project"].get(project_name, []))
    for line in buffered:
        try:
            handler.wfile.write(f"data: {line.rstrip()}\n\n".encode("utf-8"))
            handler.wfile.flush()
        except OSError:
            return

    # Subscribe to live output
    q: queue.Queue = queue.Queue(maxsize=500)
    with _sse_lock:
        _sse_subscribers.setdefault(project_name, []).append(q)

    try:
        while True:
            try:
                line = q.get(timeout=15)
            except queue.Empty:
                # Keepalive heartbeat (SSE comment — ignored by EventSource)
                try:
                    handler.wfile.write(b": keepalive\n\n")
                    handler.wfile.flush()
                except OSError:
                    return
                continue

            if line is None:  # sentinel: process exited
                try:
                    handler.wfile.write(b"data: [process exited]\n\n")
                    handler.wfile.flush()
                except OSError:
                    pass
                return

            try:
                handler.wfile.write(f"data: {line.rstrip()}\n\n".encode("utf-8"))
                handler.wfile.flush()
            except OSError:
                return

    finally:
        with _sse_lock:
            try:
                subscribers = _sse_subscribers.get(project_name, [])
                subscribers.remove(q)
                if not subscribers:
                    _sse_subscribers.pop(project_name, None)
            except ValueError:
                pass


# ---------------------------------------------------------------------------
# Dev-launch host projection (#255)
# ---------------------------------------------------------------------------


def launch_variant(document: dict) -> str:
    launch = document.get("launch")
    variant = launch.get("variant") if isinstance(launch, dict) else None
    if isinstance(variant, str) and variant in (document.get("variants") or {}):
        return variant
    variants = document.get("variants") or {}
    return canonical.MANUAL_VARIANT if canonical.MANUAL_VARIANT in variants else next(iter(variants), "")


def active_runtime_config(document: dict) -> dict | None:
    variant = launch_variant(document)
    doc = (document.get("variants") or {}).get(variant)
    return doc if isinstance(doc, dict) else None


def materialize_launch_config(project_dir: pathlib.Path, document: dict) -> pathlib.Path:
    """Project the canonical variant into a runnable host config.

    The canonical files carry DEPLOY TOKENS, which only mean something after
    install.sh rewrites them. Dev-launch needs real host paths, so we write a
    throwaway copy under .workbench/launch/ with the sidecar's host paths
    substituted in. The canonical files are never modified — that separation is
    what lets one artifact set serve both deploy and dev-launch.
    """
    runtime_doc = active_runtime_config(document)
    if runtime_doc is None:
        raise RuntimeError("Project has no runtime variant to launch.")

    host = canonical.host_paths({"host_paths": document.get("host_paths")})
    provider_paths = host.get("providers", {})
    projected = copy.deepcopy(runtime_doc)

    profile_providers = document.get("profile", {}).get("providers", {})
    for entry in projected.get("providers", []):
        if not isinstance(entry, dict):
            continue
        pid = entry.get("id")
        executable = provider_paths.get(pid, {}).get("executable") if isinstance(pid, str) else None
        if not executable:
            raise RuntimeError(f"No host executable configured for provider '{pid}' (set it in the project's paths).")
        entry["command"] = str(_resolve_executable_path(executable) or executable)
        rel = profile_providers.get(pid, {}).get("config") if isinstance(pid, str) else None
        if isinstance(rel, str):
            entry["args"] = ["--config", str((project_dir / rel).resolve())]

    automation = projected.get("automation")
    if isinstance(automation, dict):
        behavior = automation.get("behavior_tree")
        if isinstance(behavior, str) and behavior.startswith("../"):
            match = canonical.PROJECT_PATH_RE.fullmatch(behavior)
            if match is not None:
                automation["behavior_tree"] = str((project_dir / match.group(2) / match.group(3)).resolve())

    # The runtime resolves a default or relative runtime.data_dir against the
    # config file's directory (anolishq/anolis#562), which is inside the
    # project, and a re-provision replaces the project directory. Name the
    # directory a launch has always used, since the runtime starts in
    # DATA_ROOT, so the e-stop latch and the run journal stay where they are.
    runtime_section = projected.get("runtime")
    if not isinstance(runtime_section, dict):
        runtime_section = {}
        projected = {"runtime": runtime_section, **{k: v for k, v in projected.items() if k != "runtime"}}
    data_dir = runtime_section.get("data_dir")
    if not data_dir:
        runtime_section["data_dir"] = str(paths_module.DATA_ROOT / "anolis-data")
    elif not pathlib.Path(str(data_dir)).is_absolute():
        runtime_section["data_dir"] = str(paths_module.DATA_ROOT / str(data_dir))

    out = project_dir / canonical.LAUNCH_DIR / "launch"
    out.mkdir(parents=True, exist_ok=True)
    target = out / canonical.variant_filename(launch_variant(document))
    target.write_text(yaml.safe_dump(projected, default_flow_style=False, sort_keys=False), encoding="utf-8")
    return target
