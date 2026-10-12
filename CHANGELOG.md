# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Historical note:

- This changelog was reconstructed from git history on 2026-04-19.
- `0.1.0`, `0.1.1`, and `0.1.2` were inferred from version-bump commits.
- `0.1.3` is the first tagged release (`v0.1.3`).

## [Unreleased]

### Changed

- `paramiko` is a core dependency (#437). Onboarding offers "Set up a remote
  device" on every install, but a plain `pip install anolis-workbench`
  (including the Raspberry Pi bootstrap) left out the optional `ssh` extra, so
  remote setup failed with `No module named 'paramiko'` after the user had
  entered a target. The `ssh` extra is kept, empty, so existing
  `anolis-workbench[ssh]` and `--extra ssh` installs still resolve.

## [0.14.0] - 2026-08-02

### Added

- Import an existing machine-profile project (#226): `POST /api/projects/import`
  (plus a Home-page card and the previously dead Onboarding entry) copies a
  canonical machine-profile directory (`machine-profile.yaml` + `config/` +
  `behaviors/`) into the workspace **byte-for-byte** with a `workbench.json`
  sidecar — a second project format the workbench carries verbatim and never
  re-renders. `safety.estop_topology` and ALL `runtime_profiles` variants
  survive untouched (the renderer safety-drop, variant collapse and
  invented-pins defects cannot occur on this path). Deploys hand the directory
  straight to install.sh — component pins are read verbatim from the profile
  (no release lookups, offline-clean materialize) — and the Commission bundle
  export gains a **runtime-variant selector** (`install.sh --variant`, the
  workbench half of #225). Import-time validation hard-fails on
  schema/missing-reference errors and surfaces everything else (non-inert
  manual variant, unknown kinds, envelope mismatches, project-dir-name
  mismatch) as persisted warnings. Imported projects are read-only in the
  composer (`ProfileView`); save/preflight/dev-launch/.anpkg return 409 —
  edit the profile in its source repository and re-import.
  Import sources are confined to an allowlist of roots (default: the
  operator's home directory plus the workbench data dir; override with
  `ANOLIS_IMPORT_ROOTS`), checked on the fully-resolved path so a symlink
  cannot escape them — the workbench can be operated over LAN, so a
  caller-supplied path must not reach anywhere on the host.

- `GET /api/provider-schemas` (#270, part of the #271 arc): serves the vendored
  provider `--config-schema` envelopes (executable profile v1 §2) verbatim —
  the provider-owned contracts the composer will render config forms from.
  New `core/provider_schemas.py` registry; the set of known provider kinds is
  now exactly the set of vendored envelopes.
- Schema-driven provider composer (#270 part 2b): the per-kind provider forms
  are replaced by a generic `SchemaForm` rendered from the vendored
  `--config-schema` envelopes (`frontend/src/lib/schema-form/`, no new
  runtime dependencies). Conditional fields follow the schema
  (`allOf`/`if`/`then` + `dependentRequired`): forbidden keys are hidden and
  pruned on discriminator change, titled `oneOf` consts become selects,
  `x-anolis-type: i2c_address` fields validate int-or-hex, and device-list
  keys a schema revision does not describe are preserved and shown read-only.
  Generic "Import from devices" fills i2c address arrays from the device
  address column.
- `system.json` schema v2 (#270 part 2b): provider config is stored
  provider-natively — each `topology.providers` entry is `{kind, config}`
  where `config` is exactly the document the provider's `--config-schema`
  describes; `paths.providers[].bus_path` moved into
  `config.hardware.bus_path`. v1 documents migrate on load (new
  `core/migrations.py`, persisted by `projects.get_project`, applied
  defensively in the exporter, deploy, installer and provision loaders).
  Save-time validation now checks each provider config against its envelope
  (Draft 2020-12) plus `x-anolis-unique` annotations (i2c-normalized, so
  `10` and `"0x0A"` collide), with error paths anchored under
  `$.topology.providers.<id>.config`.

### Changed

- **A project IS its deployed artifacts (#255).** `system.json` is gone as the
  source of truth: a project on disk is now a canonical machine-profile
  directory (`machine-profile.yaml` + `config/anolis-runtime.<variant>.yaml` +
  `config/provider-<kind>.<id>.yaml` + `behaviors/`) — the same layout imports
  have used since #226, and exactly what install.sh consumes. Nothing is
  rendered, synthesised or resolved at deploy time; deploying is a byte-copy.
  Existing projects migrate automatically and in place on first sight (and
  eagerly at server start), keeping the untouched original as
  `system.json.pre255.bak`.

  What this fixes, both of which could reach a bench rig:

  - **Deploys no longer invent component pins.** They were resolved from live
    GitHub releases at materialize time, so an unchanged project could ship a
    newer runtime than the one it was commissioned with — under a running rig —
    and deploying offline was impossible. Pins are authored data now, edited in
    Compose. A migrated project therefore refuses to deploy until its pins are
    filled in; that is deliberate.
  - **Composer projects with automation can be deployed at all.** The composer
    wrote `automation.enabled: true` into its only runtime config, deploy filed
    that as the `manual` variant, and install.sh refuses a non-inert `manual`
    variant — so every such deploy was rejected at the target. Automation is now
    a separate `automation` variant, and `manual` is kept inert.

  Save-time validation mirrors install.sh's three hard gates (inert `manual`
  variant, provider commands resolving to pinned components, path tokens
  carrying the profile's `machine_id`), so a project that install.sh would
  refuse is refused in the composer with a message naming the fix.

  API note: `GET`/`PUT /api/projects/{name}` now speak one `ProjectDocument`
  (profile + variants + provider configs + host paths) instead of a
  system-document/imported-document union. Every project reports
  `format: "machine-profile"`; `authored` distinguishes workbench-authored
  projects from imported read-only ones.
- Handoff packages record `exported_variant` and `omitted_variants` in
  `meta/provenance.json`. Package format v1 holds a single runtime config, so a
  multi-variant project exports its inert `manual` variant only — the recipient
  can now see that rather than assume the package is the whole machine.
- `core/renderer.py` renders provider configs as a verbatim YAML passthrough
  of the native config; the per-kind `_render_sim`/`_render_bread`/
  `_render_ezo` transforms are deleted (they live on only as the v1→v2
  migration). I2C addresses keep their authored hex-string form in rendered
  YAML (provider binaries parse both).
- `core/validator.py` keeps only cross-provider concerns; the I2C bus/address
  conflict check is capability-driven (any provider whose config declares
  `hardware.bus_path` plus addressed devices), and per-provider checks
  (required fields, duplicate device ids, `custom` kind) are subsumed by the
  envelope/unknown-kind validation in `projects.py`.
- Bundled templates store provider config in the native shape; the old v1
  templates are kept as migration test fixtures.

### Removed

- The `bioreactor-manual` and `mixed-bus-mock` templates. Since a template is
  now literally a canonical project directory, one that mirrors a real machine
  is a **fork** of it — and this pair had already drifted: the bioreactor
  template was missing `command_watchdog_ms` on all three bread devices, which
  the real `anolis-projects/projects/bioreactor-v1` carries (the firmware
  command watchdog; "0 never arms"). A fork like that cannot be kept honest by a
  test, because the source of truth lives in another repo CI cannot see.

  Real machines come from **Import** (#226), which carries every variant, the
  safety block and the docs verbatim, with no drift possible. `sim-quickstart`
  remains the one shipped template: it is synthetic, has no hardware and no
  counterpart, so there is nothing for it to drift from. `mixed-bus-mock` moved
  to `tests/fixtures/` — it was a test artifact that happened to ship in the
  wheel; nothing user-facing should offer to create a mock-bus machine.

  The default template for `anolis-provision`, the provision routes and fleet
  entries is now `sim-quickstart`.
- `catalog/providers.json`, its loaders, and `GET /api/catalog` (#270): the
  catalog's form-field data (`topology_fields`/`device_types`/`path_fields`)
  had zero consumers, and its live fields are replaced — kind display labels
  come from the schema envelopes, build hints from the
  `anolis-provider-<kind>` org convention, and provider `--check-config` now
  runs unconditionally in preflight (the verb is universal per protocol#62;
  unknown-flag binaries still degrade gracefully to "Not yet available").

### Fixed

- Operate proxied to a hardcoded `127.0.0.1:8080` instead of the port the
  running variant declares. The launcher still reported the runtime as running,
  so the UI showed a live project and then 502'd on every call to it.
- A legacy project that could not be migrated took the whole project list down
  with it (500), making every OTHER project unreachable. Migration failures are
  now reported per project and the project stays listed.
- Save no longer deletes files under `config/` that the project never
  referenced — staged provider configs and notes kept alongside the ones in use
  survive. It still reclaims the config of a provider that was renamed or
  removed, which install.sh would otherwise install for a provider the runtime
  never launches.
- Migration carries a provider config that existed only as a hand-written
  `providers/<id>.yaml` (the retired deploy path fell back to it), relocates a
  behaviour tree stored outside `behaviors/`, drops a provider with no kind from
  the runtime variants as well as the profile, and moves `providers/` aside
  rather than deleting it. A behaviour tree that cannot be found now drops the
  automation variant with a warning instead of leaving the profile referencing a
  missing file — which would have blocked every deploy of the project.
- Canonical files are written with LF endings. On Windows they were CRLF, and
  install.sh does not strip `\r`: the machine profile failed to resolve its
  `manual` variant, and the LAN-exposure rewrite silently did not match, so a
  rig meant to be reachable shipped loopback-only with no warning.
- Save-time validation now matches install.sh's inertness gate exactly (an empty
  `automation:` block, `enabled: 0`, and flags nested below the top level all
  count), rejects an off-host `http.bind` with authentication disabled (install.sh
  refuses it too, but only AFTER replacing the binaries on the target), and
  rejects a behaviour tree the project does not contain.
- Deploy refuses a project whose config path tokens name a different directory
  than it installs into. install.sh reports success in that case and the rig is
  left with configs pointing at a path nothing was written to.
- Two projects can no longer share a machine ID (`Rig_A` and `rig-a` slugify
  identically). install.sh removes `{prefix}/projects/<machine_id>` before
  installing, so deploying either would have destroyed the other's config.
- `anolis-provision bundle` and fleet provisioning worked again: both still
  looked for `templates/<name>/system.json`. Building a bundle no longer
  re-creates the workspace project, which with `force` deleted the user's
  commissioned project directory.
- Dev-launch, preflight, stop, restart and `.anpkg` export are no longer
  refused for every project. The guard that makes imported projects deploy-only
  keyed on the project FORMAT, which after #255 is `machine-profile` for
  everything; it now keys on `authored`.
- Packaged wheels ship the bundled templates again: the include glob was
  `templates/*/system.json`, which matched nothing once templates became
  canonical directories.
- Packaged wheels now include `schemas/providers/*.json` (the vendored
  provider envelopes) — the include glob previously missed the subdirectory.


## [0.13.0] - 2026-07-30

### Added

- Software E-stop button in Operate (#236): a prominent one-click control that
  engages the runtime's latching software safe-state via `POST /v0/estop`,
  independent of runtime mode. Bound to the `estop{}` block on
  `/v0/runtime/status` — shows which safe-state ladder rung a press will run, an
  honest "latch only — outputs NOT driven" warning when no software safe-state
  is declared, a latched banner with a two-step Clear, and clear labeling that
  it is **not** a substitute for the hardware backplane-cut e-stop.

### Changed

- Re-synced the vendored runtime-http OpenAPI contract to anolis v0.1.39 (#262);
  it was pinned to v0.1.30. Device health now carries `FAULT` plus
  `stale_signal_count` / `fault_signal_count` (anolishq/anolis#220) and the
  `registered` / `reported` fields (anolishq/anolis#185), so the workbench no
  longer rejects a current-runtime `/v0/providers/health` response.

### Removed

- **BREAKING:** the collapsed `--profile {manual,telemetry,automation,full}`
  provisioning taxonomy (#235). It reduced to two behaviors
  (`manual`≡`automation`, `telemetry`≡`full`) and only gated auxiliary services —
  use the orthogonal `--with-observability` / `--with-telemetry-export` flags
  (and `--variant` for the runtime variant, anolishq/anolis#225). The vestigial,
  never-consumed `FleetTarget.profile` field is removed too; a `profile:` key in
  `fleet.yaml` is now ignored.

## [0.12.0] - 2026-07-23

### Changed

- **BREAKING:** deployment is delegated entirely to `install.sh`; the workbench's
  second, in-process deploy engine was removed (#170). Rollback likewise
  delegates to `install.sh --rollback` (#171). Provisioning is now a single
  engine (`deploy.py` → `install.sh --project`).
- Telemetry-export provisioning is now delegated to `install.sh`
  (`--with-telemetry-export`) instead of a workbench-side step. Deploys request
  it via the flag/profile and install.sh provisions the service **on the target**
  (venv + config + inert-until-secrets unit) — fixing the previous behavior where
  the interim step ran on the operator's host and never configured the deployed
  service. Removed `core/telemetry_config.py`. Observability provisioning is
  unchanged (still workbench-side until anolishq/anolis#162).

### Added

- Operate can target a remote, auth-enabled device: `ANOLIS_WORKBENCH_RUNTIME_URL`
  points the `/v0/*` proxy at an already-provisioned runtime instead of the
  locally-launched one, and `ANOLIS_WORKBENCH_RUNTIME_TOKEN` attaches the
  `Authorization: Bearer` header auth-by-default installs require. Interim
  surface until the device-registry UX (anolishq/anolis#164); previously a
  laptop-hosted workbench always received 401 from a provisioned device.

### Fixed

- Operate mode selector now offers the runtime-accepted modes
  (`MANUAL`/`AUTO`/`IDLE`/`FAULT`) instead of stale execution-state labels
  (`ACTIVE`/`SAFE`/…) that the runtime rejected with HTTP 400, so operators can
  actually climb the IDLE→MANUAL→AUTO ladder from the UI (#145).
- Operate recognizes an externally-running runtime — e.g. one installed by
  `install.sh` as a systemd service — by probing the active project's loopback
  runtime port, so a machine that is set up and running is operable even though
  the workbench did not launch it (#224, #233).

### CI

- Add CI OK aggregator gate: removed `paths-ignore`, added `dorny/paths-filter`
  to detect code-vs-docs changes, gated all jobs behind the filter, and added a
  final `ok` job as the sole required status check for `main` branch protection.

## [0.11.1] - 2026-05-18

### Fixed

- Desktop release workflow: add macOS builds (`.dmg` for Apple Silicon + Intel).
- Desktop release workflow: add frontend build step to `package-desktop` job
  (Tauri requires `frontendDist` at build time).
- Desktop release workflow: fix artifact attachment — only desktop bundles and
  SBOMs are attached (no intermediate sidecar binaries).
- Normalize release asset filenames to `anolis-workbench-{version}-{arch}.{ext}`
  (no spaces, lowercase, consistent across platforms).
- Updater manifest (`latest.json`) now covers all platforms (macOS, Windows,
  Linux) with correct normalized filenames.

## [0.11.0] - 2026-05-18

### Added

- **First-run onboarding endpoint** (`GET /api/onboarding`): detects whether
  the system has projects or runtime installed; returns `first_run` flag.
- **Onboarding UI** (`Onboarding.svelte`): guided wizard with three paths —
  local install, remote SSH provisioning, and import/skip.
- **Tauri auto-updater**: `tauri-plugin-updater` integrated with GitHub
  Releases endpoint (`latest.json`).
- `scripts/generate-update-manifest.py` for CI release manifest generation.
- Updater manifest step added to `desktop-release.yml` workflow.

### Fixed

- `happy-dom` version specifier aligned to `^20.9.0` (was `^20.10.0` which
  doesn't exist, breaking `npm ci`).

## [0.10.0] - 2026-05-18

### Added

- **ParamikoSSHExecutor** for server/UI mode SSH connections: lazy-imports
  paramiko, loads system known_hosts, rejects unknown hosts (RejectPolicy).
- **`create_ssh_executor()` factory** for choosing SSH backend (CLI uses
  subprocess, server uses paramiko).
- **Provision route handlers**: `start_install`, `start_remote`, `get_status`
  (SSE progress streaming), `cancel_job`.
- Provision endpoints wired into server app.py routing.
- `paramiko` as optional `[ssh]` dependency group.
- `types-paramiko` in dev dependencies.
- Unit tests for ParamikoSSHExecutor and provision routes.

## [0.9.0] - 2026-05-18

### Added

- **Fleet provisioning** (`provision fleet` subcommand): parallel multi-target
  provisioning with configurable concurrency and per-host status tracking.
- **Rollback support** (`provision rollback` subcommand): reverts a target to
  the previous installed version using snapshot metadata.
- `FleetTarget` dataclass and `FleetResult` for structured fleet outcomes.
- `--concurrency` flag for fleet operations.
- Fleet and rollback unit tests.

## [0.8.0] - 2026-05-18

### Added

- **Observability stack deployment** (`--with-observability`): downloads and
  extracts the Docker Compose observability stack (InfluxDB + Grafana) from
  the runtime release. Optional `--start-observability` auto-starts the stack.
- **Telemetry export service** (`--with-telemetry-export`): installs the
  `anolis-telemetry-export` package, renders configuration YAML, and
  optionally installs a systemd unit.
- **Provisioning profiles** (`--profile manual|telemetry|automation|full`):
  compose observability and telemetry features into predefined bundles.
- `--behavior-tree` flag for automation/full profiles.
- `anolis_workbench.core.observability` module.
- `anolis_workbench.core.telemetry_config` module.
- `observability` entry in `compatibility-matrix.yaml` optional_components.

## [0.7.0] - 2026-05-18

### Added

- `--system` flag on install/bundle/remote subcommands: load a custom
  `system.json` instead of a template (mutually exclusive with `--template`).
- `--include-wheels` flag on bundle subcommand: downloads all transitive
  Python wheels for true air-gap installs.
- `get_system_provider_names()` in installer for component filtering from
  custom system.json files.
- `_download_wheels()` and offline `pip install` section in bundle's
  `install.sh`.
- `optional_components` section in `compatibility-matrix.yaml`.

## [0.6.0] - 2026-05-18

### Added

- **Preflight checks** (`anolis_workbench.core.preflight`): verifies target
  readiness (architecture, I2C, disk space, Python version, sudo) before
  install. Runs via the Executor abstraction for local and remote targets.
- **Systemd service management** (`anolis_workbench.core.systemd`): renders
  unit files, installs/enables/starts services, and polls health endpoint.
- `--systemd` and `--wait-ready` flags on install and remote subcommands.
- `--no-preflight` flag to skip preflight checks.

## [0.5.0] - 2026-05-18

### Added

- `anolis-provision bundle` CLI command for creating offline install bundles:
  downloads pinned binaries for a specified architecture, renders project
  configs, and assembles a self-contained directory with `install.sh` for
  air-gapped deployment to Raspberry Pi targets.
- `anolis_workbench.core.bundler` module with the bundle assembly logic.
- `anolis-provision remote` CLI command for SSH-based remote provisioning:
  downloads binaries locally, transfers and installs them on a remote target
  via SSH, and writes project configs to the remote `~/.anolis/systems/`.
- `anolis_workbench.core.executor` module: `Executor` ABC with
  `LocalExecutor` and `SubprocessSSHExecutor` implementations for
  local/remote command dispatch.

### Changed

- Refactored `installer.py` to accept an optional `Executor` for all I/O
  boundary operations (install, verify, project write). Pass 3 behavior
  unchanged — defaults to `LocalExecutor`.

## [0.4.0] - 2026-05-18

### Added

- `anolis-provision install` CLI command for local provisioning: downloads
  pinned runtime + provider binaries from GitHub Releases, verifies SHA256
  integrity, installs to a configurable prefix (default `/usr/local`), and
  creates a ready-to-launch workbench project from a bundled template.
- `anolis_workbench.core.installer` module with the provisioning domain logic.
- `ANOLIS_FRONTEND_DIR` environment variable; when set, the workbench server
  uses the specified directory as the frontend dist root instead of the
  packaged or source-tree default.  Intended for custom deployment layouts
  and test fixtures.

### CI

- `workbench_server` test fixture now creates a minimal HTML stub and directs
  the server at it via `ANOLIS_FRONTEND_DIR`, so the shell-route integration
  tests pass in local development without requiring a prior `npm run build`.

## [0.3.2] - 2026-05-18

### Security

- Sanitize `Content-Disposition` header filename in the export handler to
  prevent HTTP response-splitting; CR, LF, double-quote, and backslash are
  stripped before the value is written into the header.
- Validate project name in the `anolis-package` CLI command via
  `validate_name()`, matching the guard applied by the HTTP export handler
  and closing a path-traversal gap when the name is supplied on the command
  line.
- Replace the user-derived temp-file path in `export_project` with a fixed
  constant (`package.anpkg`), eliminating user-controlled data from all
  filesystem path operations inside the handler; the user-visible filename
  continues to be set only in the sanitized `Content-Disposition` header.
- Canonicalize `out_path` at entry to `build_package` and
  `_write_zip_deterministic` so all downstream path operations work on a
  canonical absolute path regardless of call site.

### CI

- Add explicit `permissions: read-all` to `docs.yml` and `metrics.yml`
  workflow files; previously no permissions block was declared, leaving the
  `GITHUB_TOKEN` with repository-default (potentially write-all) access.

## [0.3.1] - 2026-04-23

### Changed

- `anolis_workbench/schema/` merged into `anolis_workbench/schemas/`; `paths.py`
  and `pyproject.toml` package glob updated accordingly.
- Upstream schema locks synced to anolis `v0.1.5`.
- `desktop/src-tauri/tauri.conf.json` and `desktop/src-tauri/Cargo.toml` version
  strings aligned to `0.3.0` (were stale at `0.2.0`).

### CI

- Version-sync check wired: `version-locations.txt` now tracks `pyproject.toml`,
  `desktop/package.json`, `frontend/package.json`,
  `desktop/src-tauri/tauri.conf.json`, and `desktop/src-tauri/Cargo.toml`; CI
  calls reusable `version-sync` workflow from `anolishq/.github`.
- `frontend/package.json` version aligned to `0.3.0` (was stale scaffold value `0.0.1`).
- `.anpkg` added to `.gitignore`.

## [0.3.0] - 2026-04-21

### Added

- Upstream schema sync script (`scripts/sync-schemas.py`, W-3/W-4): fetches and updates all contract schemas from a pinned anolis release.
- Release-artifact lock verify script (W-5): CI gate that confirms committed contracts match the pinned anolis release asset; replaces the earlier contract-drift schema-diff step.
- Runtime-HTTP OpenAPI contract locked to anolis `v0.1.3` release artifact (W-6).

### Changed

- Corrected schema `$id` URIs throughout from `schemas.anolishq.dev` to `anolishq.github.io`.
- Synced `runtime-config`, `machine-profile`, and `runtime-http` contracts from anolis `v0.1.4`.
- Removed redundant top-level `schemas/` copies; all contracts now live under `contracts/`.
- Corrected contract-drift sparse-checkout paths for the post-`v0.1.2` schema layout.
- Migrated all org references from `FEASTorg` to `anolishq`.
- Frontend formatting cleanup post-`0.2.0` baseline.

### CI

- Add shared org docs-check workflow.
- Add markdownlint config; tighten lint rules.
- Pin org reusable workflow refs from `@main` to `@v1`.
- Add metrics collection to release workflow; `metrics.json` uploaded as release asset on each release.
- Bump `pytest` dependency via Dependabot.

## [0.2.0] - 2026-04-19

### Added

- Browser smoke contract tests and frontend bundle-size reporting (`c8b9345`).
- Desktop Tauri wrapper scaffold and desktop release pipeline foundation (`6adddd7`).
- Component testing lane with Vitest + Testing Library for Home/Compose/Commission, coverage wiring, and Firefox Playwright smoke target (`8b4a94d`).
- Lightweight contributor/release docs and uv-first setup guidance (`e635d2d`).

### Changed

- Finalized cutover from legacy composer assets/tests; normalized docs and runtime/control port validation (`dda1ac4`).
- Completed frontend TypeScript migration and hardening, including contract-backed types and stricter linting (`c1baca5`, `1d1373e`, `e5a83b8`).
- Hardened frontend behavior by replacing `window.confirm`, moving defaults to `/api/config`, and removing raw component `fetch`/localhost assumptions (`6522ee4`, `682fce3`).
- Established formatting and linting standards with `.editorconfig`, Prettier, and ESLint flat config (`7aeaf03`, `9f30a9d`, `d73852d`, `e24c8e1`, `77b241a`, `deb5735`).

### CI

- Added Dependabot for npm + GitHub Actions and enabled rebase strategy (`fca946b`, `8f2ba43`).
- Hardened frontend CI with audit, svelte-check, ESLint, Playwright retry policy, and component coverage (`da2f9f7`, `c1d8aa5`, `1ec0a5e`, `1d0e2dd`, `8b4a94d`).
- Standardized Python CI on `uv sync --locked` with dev extras and pinned release jobs to Ubuntu 24.04 (`e0c5e94`, `c9cc89e`, `e3fa2c2`).
- Applied dependency upgrades via Dependabot across GitHub Actions and frontend dev dependencies (`2e24c04`, `ef22a2b`, `10f842f`, `91c4815`, `0b484b1`, `bc9b22c`, `bb5d580`, `076ff94`, `c247322`, merged via `1b33f66`, `c1f7119`, `301f5ee`, `554e606`, `a6e4b7f`, `b5b7021`, `93de2a1`, `eb62b6e`, `51c0fe9`).

### Docs

- Added explicit versioning policy (`9c9ccdf`).

### Version

- Bumped project and desktop lockstep versions from `0.1.3` to `0.2.0` (`2993f68`).

## [0.1.3] - 2026-04-18

### Changed

- Added `argparse`-based CLI `--version` handling and tightened smoke timeout behavior (`44aa1de`).

### Release

- Tagged as `v0.1.3` (tag points to `44aa1de`).

## [0.1.2] - 2026-04-18

### Changed

- Improved smoke-test observability with verbose output and pip timeout handling (`523760a`).

## [0.1.1] - 2026-04-18

### Fixed

- Ensured built frontend `dist/` is packaged in wheel artifacts (`367983e`).
- Added exponential backoff for PyPI propagation during smoke install (`a34f76c`).
- Allowed release workflow reruns by skipping already-existing PyPI artifacts (`3cb5536`).

## [0.1.0] - 2026-04-18 (inferred)

### Added

- Initial extraction of `anolis-workbench` from monorepo and repository normalization (`77304b6`, `72af0f8`, `424e4a1`, `d4693e8`, `ed4212e`, `40579a2`).
- Workbench API OpenAPI v1 contract, validator, and CI wiring (`01548b2`).
- Unified `anolis_workbench` package structure replacing split composer/workbench layout (`aedc9b8`).
- Python packaging and publish foundation with hatchling, uv, and release/smoke workflows (`9303832`).
- Svelte 5 + Vite frontend scaffold and full SPA implementation (`ac3a2b6`, `96a9e07`).

### Fixed

- Early CI correctness and portability fixes for mypy/ruff, Linux `ctypes.windll`, and workflow/frontend build ordering (`59c5d1a`, `480f76e`, `97c01aa`, `8fc1cea`, `8fcb9e6`, `3c61c84`, `4a599dc`).
