"""Commission-track HTTP handlers for the Workbench server."""

from __future__ import annotations

import pathlib
import tempfile

from anolis_workbench.core import exporter as exporter_module
from anolis_workbench.core import launcher as launcher_module
from anolis_workbench.core import projects as projects_module


def _reject_imported(handler, name: str, action: str) -> bool:
    """Imported projects are deploy-only in v1 (#226): dev-launch needs
    host-local binary paths that a canonical profile deliberately doesn't carry.

    Keyed on `authored`, NOT on format: since #255 every project is
    machine-profile format, so a format check here would reject them all.
    """
    try:
        authored = projects_module.is_authored(name)
    except FileNotFoundError:
        handler._json(404, {"error": f"Project '{name}' not found"})
        return True
    if not authored:
        handler._json(
            409,
            {
                "error": f"Project '{name}' is an imported machine profile — {action} is not "
                "available for imported projects. Use Deploy (install.sh) instead.",
                "code": "imported_project",
            },
        )
        return True
    return False


def status(handler, *, host: str, port: int) -> None:
    payload = launcher_module.get_status()
    payload["composer"] = {
        "host": host,
        "port": port,
    }
    payload["workbench"] = {
        "version": 1,
    }
    handler._json(200, payload)


def preflight(handler, name: str) -> None:
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    if _reject_imported(handler, name, "preflight"):
        return
    try:
        system = projects_module.get_project(name)
    except FileNotFoundError:
        handler._json(404, {"error": f"Project '{name}' not found"})
        return
    project_dir = projects_module.project_dir(name)
    result = launcher_module.preflight(name, system, project_dir)
    handler._json(200, result)


def launch_project(handler, name: str) -> None:
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    if _reject_imported(handler, name, "dev-launch"):
        return
    try:
        system = projects_module.get_project(name)
    except FileNotFoundError:
        handler._json(404, {"error": f"Project '{name}' not found"})
        return
    project_dir = projects_module.project_dir(name)
    try:
        launcher_module.launch(name, system, project_dir)
        handler._json(200, {"ok": True})
    except launcher_module.LaunchValidationError as exc:
        handler._json(422, {"error": str(exc), "errors": exc.errors})
    except RuntimeError as exc:
        handler._json(409, {"error": str(exc)})
    except Exception as exc:  # noqa: BLE001 - HTTP boundary
        handler._json(500, {"error": str(exc)})


def stop_project(handler, name: str) -> None:
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    if _reject_imported(handler, name, "stop"):
        return
    try:
        projects_module.get_project(name)
    except FileNotFoundError:
        handler._json(404, {"error": f"Project '{name}' not found"})
        return
    launcher_module.stop()
    handler._json(200, {"ok": True})


def restart_project(handler, name: str) -> None:
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    if _reject_imported(handler, name, "restart"):
        return
    try:
        projects_module.get_project(name)
    except FileNotFoundError:
        handler._json(404, {"error": f"Project '{name}' not found"})
        return
    project_dir = projects_module.project_dir(name)
    try:
        launcher_module.restart(name, project_dir)
        handler._json(200, {"ok": True})
    except launcher_module.LaunchValidationError as exc:
        handler._json(422, {"error": str(exc), "errors": exc.errors})
    except RuntimeError as exc:
        handler._json(409, {"error": str(exc)})
    except Exception as exc:  # noqa: BLE001 - HTTP boundary
        handler._json(500, {"error": str(exc)})


def export_project(handler, name: str) -> None:
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    if _reject_imported(handler, name, ".anpkg export"):
        return
    try:
        projects_module.get_project(name)
    except FileNotFoundError:
        handler._json(404, {"error": f"Project '{name}' not found"})
        return

    project_dir = projects_module.project_dir(name)
    filename = f"{name}.anpkg"
    try:
        with tempfile.TemporaryDirectory(prefix="anolis-workbench-export-") as tmp_dir:
            tmp_resolved = pathlib.Path(tmp_dir).resolve()
            # Use a fixed temp filename; the user-visible name is set only in
            # the Content-Disposition header below, keeping user-derived data
            # out of filesystem path operations entirely.
            out_path = tmp_resolved / "package.anpkg"
            exporter_module.build_package(project_dir=project_dir, out_path=out_path)
            payload = out_path.read_bytes()

        # Sanitize filename for embedding in a quoted Content-Disposition header
        # value: strip CR, LF, double-quote and backslash to prevent header
        # injection regardless of upstream validation.
        safe_filename = filename.replace("\r", "").replace("\n", "").replace('"', "").replace("\\", "")

        handler.send_response(200)
        handler.send_header("Content-Type", "application/zip")
        handler.send_header("Content-Disposition", f'attachment; filename="{safe_filename}"')
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)
    except exporter_module.ExportError as exc:
        handler._json(400, {"error": str(exc)})
    except Exception as exc:  # noqa: BLE001 - HTTP boundary
        handler._json(500, {"error": str(exc)})


def log_stream(handler, name: str) -> None:
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    try:
        projects_module.get_project(name)
    except FileNotFoundError:
        handler._json(404, {"error": f"Project '{name}' not found"})
        return
    launcher_module.handle_log_stream(handler, name)
