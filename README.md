# anolis-workbench

Commissioning shell repository for Anolis.

Repository structure:

1. `anolis_workbench/core` — pure domain library (projects, canonical artifacts, launcher, exporter, validators, paths)
2. `anolis_workbench/server` — unified HTTP server (Compose + Commission + Operate `/v0/*` proxy)
3. `anolis_workbench/cli` — CLI entry points (`anolis-workbench`, `anolis-package`, `anolis-validate`)

Developer references:

1. Contributing guide: `CONTRIBUTING.md`
2. Changelog: `CHANGELOG.md`

## Quick Start

### Download and run (recommended)

**Desktop app (recommended for commissioning):**

Download the latest Tauri desktop installer from
[Releases](https://github.com/anolishq/anolis-workbench/releases/latest).
Run the installer — the desktop wrapper bundles the Python runtime and starts automatically.

**CLI / server via PyPI:**

```sh
pip install anolis-workbench
anolis-workbench          # starts the commissioning server
anolis-package <project>  # build a handoff package
anolis-validate <pkg>     # validate a handoff package
```

Requires Python 3.11+. Open `http://127.0.0.1:3010` in a browser after starting.

### Build from source (contributors)

```sh
uv sync --locked --extra dev
./start.sh
```

Or via module entry point:

```sh
python -m anolis_workbench.server.app
```

Windows launcher: `start.cmd`.

## CLI

Build handoff package:

```sh
anolis-package <project-name> [output.anpkg]
```

Validate handoff package:

```sh
anolis-validate <package.anpkg>
# or
python contracts/validate-handoff-packages.py <package.anpkg>
```

## Frontend Tests

```sh
cd frontend
npm run test:unit:coverage
npm run test:components:coverage
```

The Vitest lanes enforce coverage thresholds for `frontend/src/lib/*`.

## Desktop Wrapper

A native Tauri wrapper now lives under `desktop/`.

Runtime model:

1. Tauri launches a frozen Python sidecar.
2. Sidecar serves Workbench at `http://127.0.0.1:3010`.
3. WebView consumes the same localhost HTTP/SSE surface as browser mode.

Important system requirement:

- Port `3010` is reserved by the desktop wrapper. If another process is bound to
  that port, desktop startup fails with a native error dialog.

Desktop workflows/docs:

1. Desktop release workflow: `.github/workflows/release.yml` (`package-desktop` job)
2. Desktop wrapper source: `desktop/src-tauri/`
3. Desktop handoff guide: `docs/release-desktop-handoff.md`

## Repository Contracts

1. Runtime HTTP snapshot: `contracts/runtime-http.openapi.v0.yaml`
2. Workbench API OpenAPI: `contracts/workbench-api.openapi.v1.yaml`
3. Handoff/control baselines: `docs/contracts/*.md`
4. Runtime config schema: `anolis_workbench/schemas/runtime-config.schema.json`
5. Machine profile schema: `anolis_workbench/schemas/machine-profile.schema.json`

## Handoff Docs

1. Commissioning handoff runbook: `docs/commissioning-handoff-runbook.md`
2. Handoff package v1 format: `docs/contracts/handoff-package-v1.md`
3. PyPI/OIDC release handoff: `docs/release-pypi-handoff.md`
4. Desktop release handoff: `docs/release-desktop-handoff.md`
