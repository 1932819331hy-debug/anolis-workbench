"""Unified Workbench HTTP server implementing Compose, Commission, and Operate tracks."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

from anolis_workbench.core import paths as paths_module
from anolis_workbench.core import projects as projects_module
from anolis_workbench.core import provider_schemas
from anolis_workbench.core.appliance import DEFAULT_PORT, default_host, default_open_browser, tunnel_hint
from anolis_workbench.server import request_origin
from anolis_workbench.server.routes import commission, compose, onboarding, operate, provision


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"WARNING: Invalid integer for {name}: {raw!r}; using {default}", file=sys.stderr)
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    print(f"WARNING: Invalid boolean for {name}: {raw!r}; using {default}", file=sys.stderr)
    return default


@dataclass(frozen=True)
class ServerConfig:
    """Resolved server configuration. Built at call time, never at import."""

    host: str
    port: int
    open_browser: bool
    telemetry_url: str


def resolve_config(
    host: str | None = None,
    port: int | None = None,
    open_browser: bool | None = None,
) -> ServerConfig:
    """Resolve config with precedence: explicit arg > env var > default.

    Resolving here (not in module-level globals) is what makes the CLI flags
    take effect — the previous import-time globals froze before cli.main could
    apply --host/--port/--no-browser (anolis-workbench#152).
    """
    return ServerConfig(
        host=host if host is not None else (os.getenv("ANOLIS_WORKBENCH_HOST") or default_host()),
        port=port if port is not None else _env_int("ANOLIS_WORKBENCH_PORT", DEFAULT_PORT),
        open_browser=(
            open_browser
            if open_browser is not None
            else _env_bool("ANOLIS_WORKBENCH_OPEN_BROWSER", default_open_browser())
        ),
        telemetry_url=os.getenv("ANOLIS_TELEMETRY_URL", "http://localhost:3000").rstrip("/"),
    )


FRONTEND_DIR = paths_module.FRONTEND_DIR

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css",
    ".js": "application/javascript",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def verify_environment() -> None:
    if not FRONTEND_DIR.is_dir():
        print("ERROR: Workbench frontend directory not found.", file=sys.stderr)
        print(f"  Expected: {FRONTEND_DIR}", file=sys.stderr)
        sys.exit(1)
    if not provider_schemas.available_kinds():
        print("ERROR: No vendored provider config schemas found.", file=sys.stderr)
        print(f"  Expected envelopes under: {paths_module.PROVIDER_SCHEMAS_DIR}", file=sys.stderr)
        sys.exit(1)


def _open_browser(url: str) -> None:
    time.sleep(0.3)
    webbrowser.open(url)


class _Handler(BaseHTTPRequestHandler):
    @property
    def config(self) -> ServerConfig:
        return cast("_WorkbenchServer", self.server).config

    def log_message(self, fmt, *args):  # suppress noisy per-request logs
        pass

    def _refuse_foreign(self) -> bool:
        reason = request_origin.rejection(self.headers.get("Host"), self.headers.get("Origin"))
        if reason is None:
            return False
        self._json(403, {"error": reason})
        return True

    # ------------------------------------------------------------------
    # HTTP verb dispatch
    # ------------------------------------------------------------------

    def do_GET(self) -> None:
        if self._refuse_foreign():
            return
        path = self.path.split("?")[0]
        if path == "/api/projects":
            compose.list_projects(self)
        elif path.startswith("/api/projects/"):
            name, sub = self._parse_project_path(path)
            if sub is None:
                compose.get_project(self, name)
            elif sub == "logs":
                commission.log_stream(self, name)
            else:
                self._not_found()
        elif path == "/api/status":
            commission.status(self, host=self.config.host, port=self.config.port)
        elif path == "/api/config":
            self._json(
                200,
                {
                    "telemetry_url": self.config.telemetry_url,
                    # Operate must not name the launched project as the target
                    # of a /v0 call that goes elsewhere (#264).
                    "runtime_url_override": bool(operate.runtime_url_override()),
                },
            )
        elif path == "/api/update-check":
            self._check_update()
        elif path == "/api/fleet":
            self._get_fleet()
        elif path == "/api/provider-schemas":
            compose.serve_provider_schemas(self)
        elif path == "/api/templates":
            compose.serve_templates(self)
        elif path == "/api/onboarding":
            onboarding.get_onboarding_status(self)
        elif path.startswith("/api/provision/status/"):
            job_id = path.split("/")[-1]
            provision.get_status(self, job_id)
        elif path.startswith("/api/provision/bundle/"):
            job_id = path.split("/")[-1]
            provision.download_bundle(self, job_id)
        elif path.startswith("/v0/"):
            operate.proxy_runtime(self, "GET", self.path)
        else:
            self._serve_static(path)

    def do_POST(self) -> None:
        if self._refuse_foreign():
            return
        path = self.path.split("?")[0]
        if path == "/api/provision/install":
            provision.start_install(self)
        elif path == "/api/provision/remote":
            provision.start_remote(self)
        elif path == "/api/provision/bundle":
            provision.start_bundle(self)
        elif path == "/api/update":
            self._trigger_update()
        elif path == "/api/rollback":
            self._trigger_rollback()
        elif path == "/api/projects":
            compose.create_project(self)
        elif path == "/api/projects/import":
            # Must match before _parse_project_path, or "import" parses as a name.
            compose.import_project(self)
        elif path.startswith("/api/projects/"):
            name, sub = self._parse_project_path(path)
            if sub == "rename":
                compose.rename_project(self, name)
            elif sub == "duplicate":
                compose.duplicate_project(self, name)
            elif sub == "preflight":
                commission.preflight(self, name)
            elif sub == "launch":
                commission.launch_project(self, name)
            elif sub == "stop":
                commission.stop_project(self, name)
            elif sub == "restart":
                commission.restart_project(self, name)
            elif sub == "export":
                commission.export_project(self, name)
            else:
                self._not_found()
        elif path.startswith("/v0/"):
            operate.proxy_runtime(self, "POST", self.path)
        else:
            self._not_found()

    def do_PUT(self) -> None:
        if self._refuse_foreign():
            return
        path = self.path.split("?")[0]
        if path.startswith("/api/projects/"):
            name, sub = self._parse_project_path(path)
            if sub is None:
                compose.save_project(self, name)
            else:
                self._not_found()
        elif path.startswith("/v0/"):
            operate.proxy_runtime(self, "PUT", self.path)
        else:
            self._not_found()

    def do_DELETE(self) -> None:
        if self._refuse_foreign():
            return
        path = self.path.split("?")[0]
        if path.startswith("/api/projects/"):
            name, sub = self._parse_project_path(path)
            if sub is None:
                compose.delete_project(self, name)
            else:
                self._not_found()
        elif path.startswith("/v0/"):
            operate.proxy_runtime(self, "DELETE", self.path)
        else:
            self._not_found()

    # ------------------------------------------------------------------
    # Static file serving
    # ------------------------------------------------------------------

    def _serve_static(self, path: str) -> None:
        decoded = urllib.parse.unquote(path)
        rel = decoded.lstrip("/")
        if ".." in rel:
            self._json(400, {"error": "Bad request"})
            return

        file_path = FRONTEND_DIR / rel
        if not rel or not file_path.is_file():
            # Any other GET that is not an API call or a file name is a client
            # route: the SPA owns routing and sends a path it does not know
            # back to "/", so the server needs no copy of the route table.
            # Project names cannot contain a dot (projects.NAME_RE).
            if not decoded.startswith("/api/") and "." not in decoded.rsplit("/", 1)[-1]:
                self._serve_index()
            else:
                self._json(404, {"error": "Not found"})
            return

        content_type = _MIME.get(file_path.suffix.lower(), "application/octet-stream")
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_index(self) -> None:
        index_path = FRONTEND_DIR / "index.html"
        data = index_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_project_path(path: str):
        """Return (name, sub) from /api/projects/<name>[/<sub>], sub may be None."""
        tail = path[len("/api/projects/") :]
        parts = tail.split("/", 1)
        name = parts[0]
        sub = parts[1] if len(parts) > 1 else None
        return name, sub

    def _body_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            self._json(400, {"error": "Empty body"})
            return None
        if length > 1_048_576:  # 1 MiB max
            self._json(400, {"error": "Request body too large"})
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            self._json(400, {"error": "Invalid JSON"})
            return None

    def _json(self, status: int, data) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _not_found(self) -> None:
        self._json(404, {"error": "Not found"})

    def _check_update(self) -> None:
        from anolis_workbench.core.updater import check_for_update

        status = check_for_update()
        self._json(
            200,
            {
                "current_version": status.current_version,
                "latest_version": status.latest_version,
                "update_available": status.update_available,
                "error": status.error,
            },
        )

    def _trigger_update(self) -> None:
        from anolis_workbench.core.updater import check_for_update, perform_update

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b"{}"
        params = json.loads(body) if body else {}

        status = check_for_update()
        if not status.update_available or not status.latest_version:
            self._json(200, {"update_available": False, "current_version": status.current_version})
            return

        result = perform_update(
            target_version=status.latest_version,
            dry_run=params.get("dry_run", False),
        )
        self._json(
            200 if result.success else 500,
            {
                "success": result.success,
                "version": result.version,
                "error": result.error,
                "warning": result.warning,
            },
        )

    def _trigger_rollback(self) -> None:
        from anolis_workbench.core.paths import DEFAULT_INSTALL_PREFIX
        from anolis_workbench.core.rollback import rollback

        result = rollback(DEFAULT_INSTALL_PREFIX)
        self._json(
            200 if result.success else 500,
            {
                "success": result.success,
                "output": result.output,
                "error": result.error,
                "warning": result.warning,
            },
        )

    def _get_fleet(self) -> None:
        from anolis_workbench.core.fleet import _FLEET_REGISTRY_PATH

        if not _FLEET_REGISTRY_PATH.is_file():
            self._json(200, {"targets": []})
            return

        import yaml

        raw = yaml.safe_load(_FLEET_REGISTRY_PATH.read_text(encoding="utf-8")) or {}
        targets = raw.get("targets", []) if isinstance(raw, dict) else []
        self._json(200, {"targets": targets})


class _WorkbenchServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that carries the resolved config for handlers."""

    config: ServerConfig


def startup_messages(config: ServerConfig) -> list[str]:
    """What the server prints on start. A headless loopback server (the
    systemd service, appliance mode) also says how to tunnel to it."""
    lines = [f"Anolis Workbench is running at http://{config.host}:{config.port}", "Close this window to stop."]
    if config.host in ("127.0.0.1", "localhost", "::1") and not config.open_browser:
        lines.append(tunnel_hint(config.port))
    return lines


def main(
    host: str | None = None,
    port: int | None = None,
    open_browser: bool | None = None,
) -> None:
    verify_environment()
    projects_module.cleanup_stale_running_files()
    config = resolve_config(host, port, open_browser)
    server = _WorkbenchServer((config.host, config.port), _Handler)
    server.config = config
    url = f"http://{config.host}:{config.port}"
    # flush: under systemd stdout is a pipe to journald, block-buffered, and
    # a stop by SIGTERM would discard the buffer and these lines with it.
    for line in startup_messages(config):
        print(line, flush=True)
    if config.open_browser:
        threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
