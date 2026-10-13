"""Compose-track HTTP handlers for the Workbench server."""

from __future__ import annotations

from anolis_workbench.core import machine_profile, provider_schemas
from anolis_workbench.core import paths as paths_module
from anolis_workbench.core import projects as projects_module


def list_projects(handler) -> None:
    handler._json(200, projects_module.list_projects())


def create_project(handler) -> None:
    body = handler._body_json()
    if body is None:
        return
    name = body.get("name") or ""
    template = body.get("template") or ""
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    if not template:
        handler._json(400, {"error": "template required"})
        return
    try:
        system = projects_module.create_project_from_template(name, template)
        handler._json(201, system)
    except projects_module.ProjectValidationError as exc:
        handler._json(
            500,
            {
                "error": "Template produced invalid system payload",
                "code": "template_validation_failed",
                "errors": exc.errors,
            },
        )
    except FileNotFoundError as exc:
        handler._json(404, {"error": str(exc)})
    except ValueError as exc:
        handler._json(409, {"error": str(exc)})


def import_project(handler) -> None:
    """POST /api/projects/import — import a canonical machine-profile dir (#226).

    The caller-supplied path is canonicalized and allowlist-checked inside
    `projects.import_project`; this handler never builds a filesystem path.
    """
    body = handler._body_json()
    if body is None:
        return
    requested_name = body.get("name")
    if requested_name is not None and not isinstance(requested_name, str):
        handler._json(400, {"error": "name must be a string"})
        return
    try:
        meta, warnings = projects_module.import_project(body.get("path"), requested_name or None)
        name = meta["name"]
        handler._json(
            201,
            {"name": name, "format": projects_module.FORMAT_MACHINE_PROFILE, "meta": meta, "warnings": warnings},
        )
    except machine_profile.ImportSourceError as exc:
        handler._json(400, {"error": str(exc), "code": "import_source_rejected"})
    except projects_module.ImportValidationError as exc:
        handler._json(
            400,
            {
                "error": "Machine-profile directory failed import validation",
                "code": "import_validation_failed",
                "errors": [
                    {"source": "import", "code": "import.validation", "path": "$", "message": m} for m in exc.errors
                ],
            },
        )
    except ValueError as exc:
        handler._json(409, {"error": str(exc)})
    except Exception as exc:  # noqa: BLE001 - HTTP boundary
        handler._json(500, {"error": str(exc)})


def get_project(handler, name: str) -> None:
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    try:
        handler._json(200, projects_module.get_project(name))
    except FileNotFoundError:
        handler._json(404, {"error": f"Project '{name}' not found"})


def save_project(handler, name: str) -> None:
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    system = handler._body_json()
    if system is None:
        return
    try:
        projects_module.save_project(name, system)
        handler._json(200, {"ok": True})
    except projects_module.ReadOnlyProjectError as exc:
        handler._json(409, {"error": str(exc), "code": "read_only_project"})
    except projects_module.ProjectValidationError as exc:
        handler._json(
            400,
            {
                "error": "Project validation failed",
                "code": "validation_failed",
                "errors": exc.errors,
            },
        )
    except Exception as exc:  # noqa: BLE001 - HTTP boundary
        handler._json(500, {"error": str(exc)})


def rename_project(handler, name: str) -> None:
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    body = handler._body_json()
    if body is None:
        return
    new_name = body.get("new_name") or ""
    err = projects_module.validate_name(new_name)
    if err:
        handler._json(400, {"error": err or "new_name required"})
        return
    if projects_module.is_running(name):
        handler._json(409, {"error": "Cannot rename a running project"})
        return
    try:
        projects_module.rename_project(name, new_name)
        handler._json(200, {"ok": True})
    except FileNotFoundError:
        handler._json(404, {"error": f"Project '{name}' not found"})
    except ValueError as exc:
        handler._json(409, {"error": str(exc)})


def duplicate_project(handler, name: str) -> None:
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    body = handler._body_json()
    if body is None:
        return
    new_name = body.get("new_name") or ""
    err = projects_module.validate_name(new_name)
    if err:
        handler._json(400, {"error": err or "new_name required"})
        return
    try:
        system = projects_module.duplicate_project(name, new_name)
        handler._json(201, system)
    except projects_module.ProjectValidationError as exc:
        handler._json(
            500,
            {
                "error": "Duplicated project failed validation",
                "code": "duplicate_validation_failed",
                "errors": exc.errors,
            },
        )
    except FileNotFoundError as exc:
        handler._json(404, {"error": str(exc)})
    except ValueError as exc:
        handler._json(409, {"error": str(exc)})


def delete_project(handler, name: str) -> None:
    err = projects_module.validate_name(name)
    if err:
        handler._json(400, {"error": err})
        return
    if projects_module.is_running(name):
        handler._json(409, {"error": "Cannot delete a running project"})
        return
    try:
        projects_module.delete_project(name)
        handler._json(200, {"ok": True})
    except FileNotFoundError:
        handler._json(404, {"error": f"Project '{name}' not found"})


def serve_provider_schemas(handler) -> None:
    """Provider config-schema envelopes, passed through verbatim (#270).

    kind -> the vendored `--config-schema` envelope (executable profile v1 §2);
    the composer renders provider config forms from these schemas.
    """
    handler._json(200, {"schema_version": 1, "providers": provider_schemas.all_envelopes()})


def serve_templates(handler) -> None:
    """Bundled canonical project templates (#255)."""
    tpl_root = paths_module.TEMPLATES_ROOT
    if not tpl_root.exists():
        handler._json(200, [])
        return
    result = []
    for directory in sorted(tpl_root.iterdir()):
        if not directory.is_dir():
            continue
        try:
            profile = machine_profile.load_profile(directory)
        except machine_profile.ProfileError as exc:
            # Listed with its error rather than dropped, so a broken template
            # is visible instead of silently missing (#356).
            result.append({"id": directory.name, "meta": {"name": directory.name}, "error": str(exc)})
            continue
        result.append(
            {
                "id": directory.name,
                "meta": {"name": profile.get("display_name") or directory.name},
            }
        )
    handler._json(200, result)
