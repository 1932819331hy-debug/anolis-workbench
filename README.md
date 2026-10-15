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

Provision with observability (InfluxDB + Grafana installed natively on the
target by the runtime's `install.sh --with-observability`):

```sh
anolis-provision install --with-observability
anolis-provision remote --target user@host --with-observability
```

The provision summary repeats `install.sh`'s observability and Grafana lines.
`install.sh` reports an observability failure (offline target, package
install failed) as a warning and still exits 0, so read that section: the
runtime is installed either way.

Reaching Grafana: on runtime releases that bind Grafana to loopback
(anolishq/anolis#359), it listens on `127.0.0.1:3000` on the target. Open a
tunnel and browse `http://localhost:3000`:

```sh
ssh -L 3000:127.0.0.1:3000 user@host
```

After a remote deploy, the provision summary (and Commission's deploy
result) prints this command for the target it reached. It prints the
network address instead only when `install.sh` reports Grafana on the
network. Operate's Grafana link defaults to `http://localhost:3000`, the
tunnel's end; `ANOLIS_TELEMETRY_URL` overrides it.

`install.sh --with-grafana-network` opens Grafana to the network instead.
`anolis-provision` does not forward that flag; run `install.sh` on the target
yourself to use it. The admin password is `GRAFANA_ADMIN_PASSWORD` in
`/etc/anolis/observability.env` on the target (root only); the workbench
never reads it.

Switching from the old Docker stack: earlier workbench releases ran it from
`~/.anolis/observability` on the machine running the workbench. On a local
install, stop it before provisioning:

```sh
cd ~/.anolis/observability && docker compose down
```

A leftover InfluxDB container on `0.0.0.0:8086` keeps the native `influxd`
from binding, and `install.sh` then finds the container's InfluxDB already
set up and leaves its tokens alone.

To keep InfluxDB and Grafana on a separate monitoring host instead, run the
compose stack from the runtime release asset
`anolis-<version>-observability.tar.gz` on that host; the workbench does not
deploy it.

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

## Trust Boundary

The command allowlist in `anolis_workbench/core/executor.py` guards the
`Executor` call sites only: deploy, bundle staging, rollback, the workbench
service, and remote probes. Dev-launch and its preflight run the runtime and
provider binaries the user names in the project's host paths, and
`anolis-validate --runtime-bin` runs the runtime binary it is given, directly
and by design. Those binaries are the user's own and sit inside the trust
boundary, so the allowlist does not list them.

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
