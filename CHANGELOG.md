# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Historical note:

- This changelog was reconstructed from git history on 2026-04-19.
- `0.1.0`, `0.1.1`, and `0.1.2` were inferred from version-bump commits.
- `0.1.3` is the first tagged release (`v0.1.3`).

## [Unreleased]

## [0.21.0] - 2026-10-26

### Added

- Operate offers a string argument's `allowed_values` as a select (#576,
  #625). anolis 0.1.53 publishes them, and anolis-provider-bread 0.9.1
  declares `set_mode`'s modes. A required argument left on "Choose…"
  sends nothing, and a value outside the set is refused before the call
  is sent, so a form value the restarted runtime no longer offers isn't
  sent.
- Check target lists a project's optional components, such as
  `telemetry_export` (#620, #630), each beside the version the target's
  `manifest.json` records.

### Changed

- The software stop card is one row at button height (#613, #627). The
  readiness, coverage and startup safe state notes moved behind ⓘ
  toggles, and the "Not an emergency stop" disclaimer is a panel
  anchored to the right.
- Contracts synced to anolis 0.1.53 and anolis-provider-bread 0.9.1
  (#634). The sim quickstart template pins runtime 0.1.53. When a stop
  chain refuses a clear, Operate shows the runtime's reason: "Failed to
  clear latch: the emergency stop is in force (…); release it first".

### Fixed

- The Mode card shows the mode when the runtime has no automation layer
  (#582, #624). It said UNKNOWN beside a Runtime Status of MANUAL, and
  offered four modes that each failed with 503. It now shows the status's
  mode, and says "Mode can't be set: Automation layer not enabled".
- Operate handles `device_removed` and no longer subscribes to
  `provider_health_change`, which the runtime never sent (#516, #628,
  #632). A removed device leaves the device list at once, and when it
  returns it fetches its own capabilities again; a reply already in
  flight when it left is dropped.
- Runtime Status and a selected device's signals show "Loading…" until
  their first reply (#623, #629). Before, they showed UNAVAILABLE and
  "No signals available." as fact.

## [0.20.1] - 2026-10-25

### Fixed

- A software stop no longer waits on project discovery (#612, #615).
  Every proxied Operate request re-parsed the running project's YAML,
  and, with a runtime the workbench didn't launch, every project's; on
  a Pi the launcher's status lookup alone took 0.29 s per request, and a
  stop waited behind Operate's polls for about 70 s. A project's parse is now reused while
  its files are unchanged, YAML goes through libyaml where PyYAML has
  it, and `POST /v0/estop` goes to the runtime the proxy last resolved
  without reading a project.
- A status reply sent before a software stop or clear no longer
  overwrites the latch that the stop or clear reply reported (#614,
  #617). The card went back to SOFTWARE STOP while the runtime was
  latched when a slow poll landed after the stop.
- The `/v0/events` proxy forwards each event as it arrives, and closes
  the runtime's stream once the tab has gone (#616, #618). It held
  events until 4 KB had built up and kept the upstream open after the
  tab closed.

## [0.20.0] - 2026-10-25

### Added

- Commission's deploy card has **Check target** (#567, #607). It lists
  each of the project's pins beside the version installed on the target,
  here or over SSH, and gives the runtime verdict a deploy would act on.
  It installs nothing and needs no sudo. The runtime's version is
  measured from its binary; the providers' come from the target's
  `manifest.json`, and the card says when that file is missing,
  unreadable or was written for another runtime version.

### Fixed

- A deploy no longer downgrades a target's runtime unasked (#567, #606).
  Before fetching install.sh, it runs the target's
  `<prefix>/bin/anolis-runtime --version`. A pin older than the
  installed runtime and older than 0.1.45, whose install.sh has no
  downgrade guard, is refused with nothing fetched or pushed. An older
  pin from 0.1.45 on is warned about, and install.sh refuses it itself.
  A runtime that is installed but reports no version is refused too.
  Offline bundles are not checked: they have no target to ask.
- Launching or restarting a project that fails validation is refused,
  and nothing is started or stopped (#414, #604). Restart validates
  before it stops the running runtime. The commission routes answer 422
  with the validation errors.
- Operate shows each function call's result beside its form, "Accepted
  at <time>" or the runtime's error message, and a software stop clears
  the forms of every function except those marked not actuating (#580,
  #603).
- An absent boolean in a provider form shows its schema default (#609).
  A DCMT row from an existing config showed bread 0.9.0's "Encoders
  fitted" unchecked, though bread reads a missing key as true.

### Changed

- The vendored bread config schema is 0.9.0's, which adds
  `devices[].encoders` (anolishq/anolis-provider-bread#179, #609).
- The vendored anolis contract locks are 0.1.52's, and the
  sim-quickstart template pins runtime 0.1.52 (#610). The runtime
  config, machine-profile and HTTP schemas are unchanged from 0.1.51.
- The deploy result's startup safe-state line follows install.sh's
  format from anolis 0.1.52 on: the achieved line names the rung, as in
  `(hooks)`, and a run that did not happen is a `→` skip line
  (anolishq/anolis#473, #605).
- Dependencies: CI's setup-uv action 10.3.2 (#608).

## [0.19.2] - 2026-10-25

### Fixed

- Compose remounts when the project changes, so a failed save's errors no
  longer stay on screen under another project (#561, #592).
- "Continue and discard unsaved changes?" appears only when the edits would
  be lost: on a project switch or leaving the project, not on a tab switch,
  which keeps the draft (#562, #593). "Stay Here" leaves the project
  selector on the project still open.
- A provider's manual address list keeps what was entered (#563, #594).
  "Import from devices" merges instead of replacing the list, switching
  discovery to scan and back restores the list, and duplicate messages
  name the value as entered ("0x13"), not in decimal.
- Operate's software stop panel names the runtime config file the
  project's own `runtime_profiles` maps the variant to, so an imported
  project's file is right (#564, #595).
- With no project open, the workspace tabs look disabled and say a
  project is needed (#569, #596).
- Compose form details (#578, #597). An empty required field shows its
  "Required" line only once it is left empty or a save fails; until then
  its `*` and `aria-required` mark it. A generated device id follows a
  type change, and a typed one is kept. The empty address placeholder
  reads "e.g. 0x0A".
- The header, Home and Operate say they are loading until their first
  replies arrive, instead of showing "Stopped", "No projects yet", "No
  devices found" or "No callable functions declared" (#568, #598).

### Changed

- Dependencies: CI's setup-python action digest 6d4fd22 (#602).

## [0.19.1] - 2026-10-24

### Fixed

- Operate no longer leaves `/v0/events` streams open (#551, #583). A status
  poll that was still in flight when Operate stopped or unmounted opened a
  new stream that nothing closed, and six of them took every browser
  connection to the workbench.
- Selecting another device while a state or capabilities request was in
  flight no longer files the first device's reply, and its function forms,
  under the second (#552, #584).
- Operate's Runtime Status badge reflects the machine, not the status
  request (#557, #585). It reads ATTENTION, with its reasons, while the
  software stop is latched, the startup safe state was not achieved, a
  provider is not AVAILABLE or a device is latched after going unreachable.
  UNAVAILABLE still means the status could not be read.
- Removing the automation variant in Compose also removes the providers
  only it ran (#554, #586). They stayed in the draft with no form showing
  them, and Save failed on their missing fields.
- The deploy result shows install.sh's runtime restart line (part of #556,
  #587). It will also show the startup safe state once install.sh reports
  it (anolishq/anolis#471).
- The software stop card shows one state (#553, #588). It takes the latch
  from the stop or clear reply instead of waiting for the next status read,
  and it is sticky on a long Operate page. The stop button is offered
  before the runtime's status loads. On another project's page the stop
  stays, a latch on the running runtime shows above it, re-read every 5 s,
  with a link to that project's Operate view, where it is cleared.
- The Fleet Dashboard renders at `/fleet`, from Home's button and on a
  direct load (#555, #589).

### Changed

- With no runtime running for the project, Operate names the two ways to
  reach a runtime on another machine: the workbench on the device over an
  ssh tunnel, or a tunnelled runtime port with
  `ANOLIS_WORKBENCH_RUNTIME_URL`, plus `ANOLIS_WORKBENCH_RUNTIME_TOKEN` when
  the runtime requires auth (#590, refs #510).
- The vendored anolis-provider-bread config schema is 0.8.1's (#539). It
  differs from 0.8.0's only in its version.
- Dependencies: setuptools 84.1.0 (#547), happy-dom 21.0.4 (#546), vite
  8.3.5 (#548), `@tauri-apps/cli` 2.12.4 (#549), and CI's setup-uv action
  10.3.1 (#550).

## [0.19.0] - 2026-10-23

### Added

- Local installs from the app work where sudo asks for a password (#230,
  #489). In a browser on the machine itself (127.0.0.1, localhost or
  [::1]), Onboarding and Commission's "This machine" deploy show a sudo
  password field. The password goes to `sudo -k -S` on stdin, never in argv
  or the environment, and its buffers are zeroed after use. The server
  accepts it only from a loopback client and answers 403 otherwise; it is
  never sent to a remote deploy. A wrong password reads "sudo rejected the
  password; install.sh did not run". Bound to all interfaces, the server
  also prints and opens `http://127.0.0.1:<port>`. A sudoers rule that names
  only `/usr/bin/bash` no longer matches the install, which now runs `sh`.
- Operate's startup safe-state line names the calls anolis 0.1.51 skipped
  because their device was absent at startup, each with its provider's
  reason, and any skip makes the line an error (#540,
  anolishq/anolis#461). On a boot with every device absent it used to read
  "ran but issued no call, so nothing was driven". A skipped device is not
  driven when it appears later. A runtime before 0.1.51 reads as before.

### Changed

- The sim-quickstart template pins anolis 0.1.51, and the vendored anolis
  contracts are synced to 0.1.51 (#542). The runtime HTTP OpenAPI gains the
  optional `startup_safe_state.skipped_actions` and its `SkippedAction`
  schema; the other two schemas are unchanged, and the locks record 0.1.51's
  asset digests.
- CI's rust-cache action is at digest d1527c8 (#541).

## [0.18.3] - 2026-10-22

### Security

- `anolis-provision` and the workbench check a downloaded `install.sh`
  against the `SHA256SUMS` of its release before running it, for install,
  remote, bundle and rollback (#530, #534). A mismatch stops the run, and so
  does a `SHA256SUMS` that cannot be fetched for any reason but a 404. A
  release that has no `SHA256SUMS`, or no line for `install.sh`, still runs,
  with `WARNING: install.sh not verified: anolis v<x> predates install.sh in
  SHA256SUMS` in the CLI and in the UI. That covers every anolis release
  through v0.1.49; anolis v0.1.50 is the first whose `SHA256SUMS` lists
  `install.sh`. #533 turns the warning into a refusal. Self-update gets the
  same check, but it cannot download its script yet (#535).

### Changed

- The sim-quickstart template pins anolis 0.1.50, so an install of it
  checks `install.sh` instead of warning, and the vendored anolis contracts
  are synced to 0.1.50 (#536). Only the runtime HTTP OpenAPI changes, in
  two descriptions (anolishq/anolis#453); the locks record 0.1.50's asset
  digests.

## [0.18.2] - 2026-10-21

### Upgrade notes

- **An existing install keeps the certifi it was installed with.** Run
  `pip install --upgrade pip certifi` in the environment the workbench is
  installed in. That's `~/.local/share/anolis-workbench/venv/bin/pip` for an
  install made by anolis-projects' `install-workbench-pi.sh`. For a pipx
  install: `pipx runpip anolis-workbench install --upgrade pip certifi`. A
  fresh `pip install anolis-workbench` already resolves certifi 2026.10.19 or
  later. Desktop installers need this release.
- **Debian's own trust store needs its update too.** System Python and
  Debian's `python3-certifi` read `/etc/ssl/certs`, which `ca-certificates`
  20250419+deb13u1 (DSA-6572-1) fixes on Debian 13:
  `sudo apt update && sudo apt install --only-upgrade ca-certificates`.

### Security

- certifi 2026.10.19 in `uv.lock` (#531), so the frozen server sidecar and
  the desktop bundles carry it. certifi before 2026.10.19 bundles emSign
  root CAs whose intermediate CA keys are compromised (the eMudhra
  incident). requests uses that bundle for every HTTPS call the workbench
  makes, release downloads included. pip before 26.2.2 also vendors it,
  hence the pip upgrade above.

### Changed

- Vendored anolis contracts synced to anolis 0.1.49, and the sim-quickstart
  template pins runtime 0.1.49 (#524). The schemas are unchanged; the locks
  record 0.1.49's release asset digests.
- Vendored bread config-schema envelope synced to anolis-provider-bread
  0.8.0 (#475); the schema is unchanged, only `provider_version` moves.
- The desktop wrapper builds against tauri 2.12.5 and tauri-build 2.7.4
  (#529).

## [0.18.1] - 2026-10-19

### Fixed

- `anolis-provision install` and `anolis-provision remote` list the
  configs `install.sh` found on the target and kept, under "Kept on the
  target (install.sh):", in the summary (#521, closes
  anolishq/anolis#439). When one of those lines is a warning, such as
  anolis 0.1.49's warning that a kept `runtime.yaml` points a provider at
  another project's config, a `WARNING:` line on stderr points at them.

### Changed

- Vendored contracts synced to anolis 0.1.48 (#518). The runtime-http
  description now says loopback clients need the API token unless
  `http.auth_exempt_loopback` is true. The sim-quickstart template pins
  runtime 0.1.48.

## [0.18.0] - 2026-10-18

### Upgrade notes

- The workbench now listens on `127.0.0.1` in Raspberry Pi appliance mode
  and under the `--workbench-service` systemd unit, as it already did
  elsewhere. Reach it from another machine with
  `ssh -L 3010:127.0.0.1:3010 <user>@<host>`, which the server and the
  install now print. An explicit `ANOLIS_WORKBENCH_HOST` still sets the
  bind address.
- Re-run `anolis-provision install --workbench-service` to rewrite an
  installed unit and to give the service its copy of the runtime's API
  token.
- Install this release, and re-run `install --workbench-service`, before
  upgrading the runtime to anolis 0.1.48. That runtime requires its API
  token on loopback, and an older workbench sends none, so Operate would
  get 401.

### Security

- No default serves the workbench beyond loopback. Appliance mode on a
  Raspberry Pi and the `--workbench-service` unit previously bound
  `0.0.0.0`, which let anyone who could reach the port use every page,
  Operate included, with no login.
- `--workbench-service` gives the service the installed runtime's API
  token, and nothing else from the runtime's environment file. The install
  copies it into `/etc/anolis/workbench-runtime-token`, a `0600` file owned
  by the service's user, written atomically; it never appears in the unit,
  a command line or the output, and uninstalling the service removes it.
  Operate sends it only to a runtime on this machine (loopback,
  `localhost`, or `0.0.0.0`/`::`), never to an
  `ANOLIS_WORKBENCH_RUNTIME_URL` override or another host a project's
  `http.bind` names, and an explicit `ANOLIS_WORKBENCH_RUNTIME_TOKEN` still
  wins. Without a runtime token there is no copy and Operate sends none.
- Operate refuses a project whose runtime `http.bind` is not a bare IP
  address or hostname, such as one carrying userinfo, a path or a port.
- Processes the workbench starts (a dev-launched runtime, preflight checks,
  `install.sh`, ssh) no longer get `ANOLIS_WORKBENCH_RUNTIME_TOKEN` in their
  environment. This is defence in depth only: a program the workbench
  launches runs as the service's user and can read the token copy.
- Reaching the workbench means being able to run programs as the service's
  user, since a project's host paths name the executables Launch starts.
  Treat access to the workbench as access to that account.

### Added

- Commission has a "Dev-launch variant" control that shows and sets which
  runtime variant Launch runs (#513, closes #339). Compose's variant
  selector now only picks what is being edited and is never saved. When
  the two differ, Commission and Operate show a banner such as "Editing
  automation; Launch runs manual"; Launch is not blocked. A variant that
  exists only in unsaved Compose edits is listed but cannot be chosen
  until it is saved, and removing a variant in Compose unsets a launch
  variant that named it.

### Changed

- Vendored contracts synced to anolis 0.1.47 and anolis-provider-sim
  0.2.8 (#511, #512). The runtime-http `/v0/events` description lists
  `device_removed` and no longer lists `provider_health_change`; the sim
  envelope changes only its `provider_version`.
- The sim-quickstart template pins runtime 0.1.47 and sim 0.2.8, both
  under `components` and in its `compatibility` entry (#511, #512), so a
  new quickstart project carries no version-skew warning.
- The install summaries' runtime API hint reads the token from
  `/etc/anolis/runtime.env` and passes it to `curl -H @-` on stdin, so it
  never reaches a command line, and points at `/v0/runtime/status`.

### Fixed

- Compose shows `provider.name` read-only, following the provider ID, and
  no longer renders `const` properties as disabled inputs (#507, part of
  #339). The const values are still written and still validate.
- A field that a schema conditional makes required shows why beside the
  required error, from the schema's own titles, for example "Required
  because Discovery mode is 'Manual address list'" (#508, closes #485).
- The "declared in the machine-profile but run by no runtime variant"
  warning is derived when a project is read instead of stored in the
  sidecar, so it clears once fixed (#505, closes #290). A stale copy in an
  existing sidecar is no longer served and is dropped on the next save.
- Discovery of a runtime the workbench did not start counts a `401` from
  its token-less probe as a runtime answering, so a runtime that requires
  its token on loopback is still found.
- Under the systemd unit, the server's startup lines, the tunnel hint
  among them, reach the journal (`PYTHONUNBUFFERED=1` and an explicit
  flush).

### CI

- Renovate's lock manager now moves each lock's release asset names and a
  provider lock's `provider_version` along with its tag, and the template
  manager tracks the sim-quickstart template's sim pins as well as its
  runtime pin (#515).

## [0.17.1] - 2026-10-17

### Upgrade notes

- If you reach the workbench under a name other than an IP address,
  `localhost` or the machine's hostname (plain or `.local`), such as a DNS
  name or a reverse proxy's, set `ANOLIS_WORKBENCH_ALLOWED_HOSTS` to that
  name, or the server answers 403.

### Security

- The server refuses, with 403, a request whose `Host` does not name this
  machine (an IP address, `localhost`, or the hostname, plain or `.local`)
  and a browser request whose `Origin` does not match `Host`. Names listed
  in `ANOLIS_WORKBENCH_ALLOWED_HOSTS` (comma-separated) are also accepted.
  Requests without `Origin`, such as from curl or a script, are unaffected.

## [0.17.0] - 2026-10-17

### Security

- Project and template names are checked at every entry that uses them
  to name a directory. `POST /api/provision/install`, `/remote` and
  `/bundle` answer 400 for an invalid project or template without
  starting a job, and `POST /api/projects` checks the template.
  `anolis-provision install`, `bundle` and `remote` reject an invalid
  `--project` or `--template` as a usage error (exit 2), and a fleet
  file's names are checked when it loads. An absent or empty template
  still falls back to the default.
- Removing a project directory refuses any path that is not a real
  directory strictly inside the workspace root: the root itself, a
  symlink, and a path that resolves outside it.

### Added

- Operate shows what the runtime's startup safe state did (closes #483):
  every call accepted by its provider, which calls failed, no call
  issued, outputs left as they were (`on_startup: leave`), or nothing
  declared. Runtimes older than 0.1.45 do not report it, and Operate
  shows nothing for them.
- The provision summary and Commission's deploy result show
  `install.sh`'s health lines (closes #239), including how many
  providers are running and which are not. `install.sh` reports a
  provider that is not running as a warning and still exits 0, so this
  is where it shows. Needs a project pinned to anolis 0.1.45 or later.
- After a successful local install, Onboarding offers "Open `<project>` in
  Operate" for the project the install seeded (closes #238), and the
  project selector lists that project. A local provision job's `done`
  summary carries `project`. A remote install still ends at "Go to
  projects".

### Changed

- The preflight hint for a missing runtime or provider binary no longer
  names a CMake preset or carries a Windows note for particular provider
  kinds (closes #287). It names the repo and its build docs, the same for
  every kind; how a provider is built is the provider's to document. A
  kind with no vendored envelope gets a hint to set the binary's Executable
  path in Compose.
- The commissioning handoff runbook describes one deploy story (closes
  #240): the machine-profile project is the source of truth, the `.anpkg`
  is an optional provenance record, and the staged bundle is the
  deployable. It documents the facility path (`anolis-provision bundle`,
  then `install.sh --local` on the target) and the online path
  (`anolis-provision install|remote`). Extracting an `.anpkg` and running
  the runtime against it is a validation step, done in a scratch
  directory, and no longer reads as a deploy into `/opt/anolis`. The online
  path needs the target to reach the GitHub release downloads. The handoff
  package v1 format doc states the same purpose; the format is unchanged.
- The README documents pip and a browser as the supported Raspberry Pi
  setup (closes #231): `pipx install anolis-workbench && anolis-workbench`,
  then `http://<pi>:3010` from another machine on a trusted network. It no
  longer labels the desktop app "recommended", says the Linux desktop
  bundles are x86_64 only, and gives the Python requirement as 3.10+, as
  `pyproject.toml` does.

### Fixed

- Commission's preflight panel lists every system-level validation error
  (closes #405). With two or more it threw `each_key_duplicate` and showed
  nothing, because every error is a check with the same name.
- `anolis-provision install --workbench-service` installs the service.
  It wrote the unit into `/etc/systemd/system` without sudo, so a
  non-root run printed a warning, installed nothing and still reported
  success. The unit is now copied into place with sudo, the service
  runs as the invoking user (`SUDO_USER` under sudo, not root), and
  `ExecStart=` is the running install's own `anolis-workbench`. A
  failed step now fails the install with exit 1.

## [0.16.0] - 2026-10-16

### Upgrade notes

- `anolis-provision install|remote --with-observability` now installs
  InfluxDB and Grafana on the target, and `--start-observability` is gone
  (#462). Before a local install, stop a stack the workbench started with
  `docker compose down` in `~/.anolis/observability`.
- With anolis 0.1.45 or later on the target, `install.sh` binds Grafana to
  `127.0.0.1:3000`, so by default Grafana is reached over an ssh tunnel. The
  deploy summary prints the tunnel command (#479). Earlier runtimes put
  Grafana on the target's network address.
- `ANOLIS_TELEMETRY_URL` (Operate's Grafana link) now defaults to
  `http://localhost:3000`, the end of that tunnel (#479). To keep using the
  runtime release's Docker Compose stack, which maps Grafana to host port
  3001, set `ANOLIS_TELEMETRY_URL=http://localhost:3001`.
- A runtime config that sets a non-empty `telemetry.influxdb.token` no longer
  saves, launches, deploys or bundles (#463). Authored projects drop the token
  on load; edit an imported project's source and re-import it.

### Added

- Every GitHub release asset (installers, wheel and sdist, SBOMs,
  `metrics.json`) carries a build-provenance attestation (#488, closes
  #126). Check one with
  `gh attestation verify <asset> -R anolishq/anolis-workbench`. The PyPI
  publish now waits until every asset is built and attested.
- Commission deploys the selected project to a device (#454, closes #275): a
  remote device over SSH or this machine, with a picker over the project's
  runtime variants (default `manual`). A variant that does not boot inert is
  labelled "fresh install refuses this". The section says it deploys the saved
  project, follows the job's stages, and shows the installed version, the host
  preflight, a pending reboot or the failure.
- Commission's Deploy to device offers "Also install telemetry-export" (#455)
  and "Also install observability (InfluxDB + Grafana)" (#480, closes #229).
  They reach `install.sh` as `--with-telemetry-export` and
  `--with-observability`, from the API's `with_telemetry_export: true` and
  `with_observability: true`. The deploy result shows `install.sh`'s
  observability report and heads it "Observability needs attention" when
  `install.sh` warned, since it still exits 0 then.
- After an observability install, the provision summary and Commission's
  deploy result say how to reach Grafana (#479, closes #241): the ssh tunnel
  command for the target (with its port and key), or the network address when
  `install.sh` reports Grafana on the network, and where the admin password
  lives (`/etc/anolis/observability.env` on the target, root only).
- Deploy says when the target already has a runtime config (#452, closes
  #354). `install.sh` keeps it and applies neither the project's runtime
  config nor the variant; the job emits an `existing-config` stage first, and
  its result carries the kept configs (`preserved`) and
  `runtime_config_kept`.
- Compose warns when a runtime variant runs providers and declares no
  `safety.safe_state` (#459, closes #371): the software stop would then latch
  and drive nothing.
- Operate's safe-state note names the runtime version that reported it and
  where to declare a safe state, for dev-launch and for an installed target
  (#460, closes #346 and #372). From runtime 0.1.45 it counts the actuating
  functions the active rung will not drive.
- When the runtime runs another project, Operate's advisory keeps a "Software
  stop: `<project>`" button for the running project (#461, closes #264).
  `GET /api/config` reports `runtime_url_override`.

### Changed

- **BREAKING:** `anolis-provision install|remote --with-observability` now
  means the runtime `install.sh`'s native stack (#462, closes #227): the flag
  is forwarded to `install.sh --with-observability`, which installs InfluxDB
  and Grafana as system services on the target. It used to run the release's
  Docker Compose stack from `~/.anolis/observability` on the machine running
  the workbench. Stop that stack (`docker compose down` there) before a local
  install, or its InfluxDB keeps holding `:8086`. The provision summary
  repeats `install.sh`'s observability and Grafana lines, and only those
  (#478, closes #469), since `install.sh` reports an observability failure as
  a warning and still exits 0. For a separate monitoring host, run the
  release's `anolis-<version>-observability.tar.gz` compose stack on that
  host.
- `ANOLIS_TELEMETRY_URL` (Operate's Grafana link) now defaults to
  `http://localhost:3000` instead of `http://localhost:3001` (#479): the end
  of the ssh tunnel the deploy summary prints, or Grafana itself after a local
  install. The runtime release's Docker Compose observability stack still maps
  Grafana to host port 3001, so set `ANOLIS_TELEMETRY_URL=http://localhost:3001`
  to keep using that stack.
- **BREAKING:** a non-empty `telemetry.influxdb.token` in a runtime config
  fails validation, naming `INFLUXDB_TOKEN` and the file (#463, closes #257).
  Save, dev-launch, deploy and bundle refuse it, because an authored token
  shadowed the target's own `INFLUXDB_TOKEN`. The runtime form has no token
  field. Migration no longer copies a token; an authored project that still
  has one drops it on load with a warning, and the next save removes it.
  Imported projects keep it, warn on import (#478), and are refused at deploy
  until the source is edited and re-imported.
- Launch needs a passing preflight for the project on screen (#444, closes
  #353), and a successful launch clears the result.
- The preflight result counts skipped checks as skipped, not passed, and
  carries `summary {passed, skipped, failed}` (#441).
- The provider form takes what it shows and seeds only from each provider's
  config schema (#466, closes #349). Add Provider needs an explicit kind.
- The e-stop disclaimer is provider-neutral: "If in doubt, isolate power to
  the machine." (#458, closes #450)
- Vendored contracts synced to anolis 0.1.45 and the current provider
  releases (#484). The runtime config's `safety.on_startup` and
  `health.excluded_signals` validate, and the runtime-http contract gains
  `startup_safe_state`, `safe_state_achieved` and signal `unit` and
  `description`.
- The sim-quickstart template pins runtime 0.1.45 instead of 0.1.39 (#477,
  #474, closes #470). Compose's pin fields show `MAJOR.MINOR.PATCH` as their
  placeholder.
- The executor allowlist no longer lists `anolis-runtime` or any
  `anolis-provider-*` binary, which no `Executor` call ran (#464, closes
  #284). The README's Trust Boundary section says what the allowlist guards.

### Removed

- **BREAKING:** `anolis-provision install|remote --start-observability`, with
  the workbench's Docker observability step and `docker` in the executor
  allowlist (#462, closes #227).
- `POST /api/provision/cancel/<job_id>` (#457, closes #348). It reported a
  job cancelled while `install.sh` ran on; no UI called it.

### Fixed

- A failed onboarding install no longer ends first run (#453, closes #424).
  First run lasts until a deploy has succeeded or the user has a project of
  their own; `GET /api/onboarding` gains `has_fleet_entry`.
- Reloading during an onboarding install reattaches to the same job (#457,
  closes #348).
- A client that leaves the provision status stream or the `/v0/events` proxy
  no longer logs a traceback (#476, closes #468).
- Reloading `/fleet` serves the app instead of a JSON 404 (#406, closes
  #347).
- A template that fails to load is listed as unavailable with its error
  instead of being dropped (#446, closes #356).
- The offline bundle builds for the architecture picked in Commission
  (arm64 or x86_64) instead of always arm64 (#442). `POST
  /api/provision/bundle` rejects any other arch with a 400.
- Rollback asks for confirmation before it swaps the binaries and restarts the
  service (#443).

### CI

- Renovate tracks the release tag of every upstream schema lock (#465, closes
  #369) and each template's runtime pin (#477).
- The release workflow checks every location in `version-locations.txt`
  against the requested version (#447, closes #295), and the smoke test waits
  up to about 9 minutes for PyPI's index (#440).
- A message constant is renamed so CodeQL's clear-text-logging check no longer
  flags it; the printed text is unchanged (#481).

## [0.15.0] - 2026-10-12

### Added

- Onboarding shows the install it starts (#425, closes #228). "Set up this
  device" and "Set up a remote device" used to navigate to a route that did
  not exist, so the app fell back to Home and the install ran unseen. The
  view now follows the provision job's stream in place:
  - **Running:** each stage as it arrives.
  - **Success:** the installed runtime version, the host preflight when it
    needs attention, and a pending reboot.
  - **Failure:** the error, including `install.sh`'s host preflight block with
    each fix.
  - **"Install anyway…":** offered, behind an inline confirm, only when the
    preflight's sole failure is unmet requirements. It resubmits with
    `allow_unmet_host: true`.
  - **Other cases:** a dropped stream and a failure to start each say so.
- Host checks in deploy and commission (#421, part of anolishq/anolis#318):
  - **Commission preflight** runs each provider's `--check-host` after
    `--check-config`. Unmet requirements fail the check, with the provider's
    detail and remedy; unknown requirements become a note.
  - **Deploy** reports `install.sh`'s host preflight block when it is what
    failed, and a pending reboot (`/run/reboot-required` on the target) on
    success and on failure.
  - **Installing past unmet requirements** is an explicit opt-in:
    `--allow-unmet-host` on `anolis-provision install` / `remote`, and
    `allow_unmet_host: true` on `POST /api/provision/install|remote`.
  - **The provision job's** `done` summary and `error` event carry
    `host_preflight` and `reboot_pending`.

### Changed

- Vendored contracts synced to anolis 0.1.43, bread 0.5.0 and ezo 0.4.0
  (#420). bread's `hardware.max_bus_hz` and ezo's `sample_interval_ms` are
  accepted, and the runtime-http contract gains `device_loss_latched`. On
  0.14.0, importing a profile that sets `max_bus_hz` warned that it was
  unexpected and failed save-time validation.
- The anolis schemas were resynced so that the runtime config's `safety` and
  `health` blocks are validated (#370). The vendored runtime-config schema had
  been eleven releases behind. A test now fails when the three anolis locks
  pin different tags.
- Provider config schemas resolve by the version the machine pins (#291,
  #283) instead of whichever envelope happens to be vendored. An inexact match
  still validates and warns, naming both versions.
- The provider lock file is the single declaration of provider kinds (#293,
  #285). Adding a provider is one lock plus one envelope, and
  `verify --all-providers` checks that they correspond.
- Operate's stop button reads **SOFTWARE STOP**, not E-STOP, and states inline
  that it is not the hardware emergency stop (#319). The API path stays
  `/v0/estop`.
- `paramiko` is a core dependency (#437). Onboarding offers "Set up a remote
  device" on every install, but a plain `pip install anolis-workbench`
  (including the Raspberry Pi bootstrap) left out the optional `ssh` extra, so
  remote setup failed with `No module named 'paramiko'` after the user had
  entered a target. The `ssh` extra is kept, empty, so existing
  `anolis-workbench[ssh]` and `--extra ssh` installs still resolve.

### Removed

- The workbench's own cross-provider I2C address conflict check (#421, closes
  #417). Providers publish `anolis.claim` keys and the runtime refuses a
  conflict at startup; claims exist only at runtime, so the authoring-time
  check could not see them. `x-anolis-type: i2c_address` still drives the form
  control and within-provider uniqueness.

### Fixed

- The frozen sidecar's PyInstaller hidden imports are derived from the package
  tree (#299). The hand-kept list named three modules that no longer existed
  and omitted ten that did, so a module could be left out of the desktop build
  and fail only at runtime.

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
