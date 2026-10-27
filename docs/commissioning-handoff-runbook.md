# Commissioning Handoff Runbook

Status: Active.
Audience: Machine integrators commissioning in Workbench and handing a
machine off for deployment with the runtime's `install.sh`.

## 1) Preconditions

1. Project validates and launches cleanly in Commission workspace.
2. Operate checks are complete for target behavior.
3. Runtime contracts and Workbench tests are green locally.

## 2) Three Artifacts, Three Roles

A commissioned machine is carried by three artifacts. They compose; none
replaces another.

1. **Machine-profile project** (`machine-profile.yaml` and the configs it
   references): the source of truth. It is what you author and commission
   in the workbench.
2. **Handoff package** (`.anpkg`, optional): a provenance record of what
   was commissioned and validated. It is never a step on the install path.
3. **Staged bundle** (an `anolis-*.tar.gz` from `install.sh --stage`):
   the deployable. It carries the runtime, the providers, the project's
   configs and every runtime variant, and `install.sh --local` installs it
   on the target as a systemd service.

## 3) Deploy

The runtime's `install.sh` performs every deploy. Each path below installs
binaries, providers, configs and the systemd service.

Facility path (air-gapped baseline): stage a bundle, carry it to the target
(USB or any offline transfer), and install it there.

```bash
anolis-provision bundle --project <project-name> --arch arm64 --out <dir>
```

Commission's bundle export builds the same bundle. `anolis-provision bundle`
also writes `<bundle>.tar.gz.sha256` beside it; Commission's Export Bundle
download is the tarball alone, so skip the `sha256sum` step on that path. On
the target, verify and unpack the bundle, then run the `install.sh` it carries:

```bash
sha256sum -c <bundle>.tar.gz.sha256   # anolis-provision bundle only
tar xzf <bundle>.tar.gz
sudo <bundle>/install.sh --local <bundle>.tar.gz
```

`anolis-provision bundle` prints `sudo ./install.sh --local <bundle>.tar.gz`,
which assumes an `install.sh` beside the tarball; the bundle step leaves none
there.

Online convenience path: when the target can reach the GitHub release
downloads, deploy straight from the workbench, with Commission's Deploy to
device or:

```bash
anolis-provision install --project <project-name>
anolis-provision remote --target user@host --project <project-name>
```

These run `install.sh --project` on the target, which downloads the runtime
and provider releases from GitHub there. For `remote`, the machine running
the workbench must reach GitHub as well (it fetches `install.sh` and copies
it over SSH). They are a convenience over the facility path, not a
replacement for it.

On a target that already has a runtime or provider config, `install.sh`
keeps it in place and the deploy result says so.

Before it fetches `install.sh`, the deploy runs the target's
`<prefix>/bin/anolis-runtime --version`. When the project pins an older
runtime, the deploy:

- refuses, with nothing fetched or pushed, if the pin is older than
  0.1.45, because that `install.sh` would downgrade without asking;
- warns otherwise, and `install.sh` refuses the downgrade itself.

A runtime that is installed but reports no version is refused as well. A
target with no runtime installed is a fresh install.

To see this before deploying, use Commission's **Check target**. It
installs nothing and needs no sudo. It lists each of the project's pins
beside the version installed on the target, and gives the runtime verdict
the deploy would act on. The runtime's installed version is measured from
its binary. The providers' versions come from the target's
`<prefix>/manifest.json`, which records the last install, and the card says
when that file is missing, unreadable or was written for another runtime
version.

The installed runtime is the `anolis-runtime` systemd service, which the
workbench did not launch, so Commission's own Stop and Restart cannot reach
it. Under **Deploy to device**, **Stop the installed runtime…** and
**Restart the installed runtime…** run `systemctl stop` or `restart` on the
deploy target, over SSH or here with the sudo password above. Each asks for a
confirm naming the machine, then shows what `systemctl is-active` reports.
Without the workbench, run `sudo systemctl stop anolis-runtime` on the
target.

## 4) Export Package (Provenance Record)

Workbench path:

1. Open project in `Commission`.
2. Click `Export .anpkg`.
3. Save generated `.anpkg` artifact.

CLI path:

```bash
anolis-package <project-name> [output.anpkg]
```

## 5) Validate Package

Static package validation:

```bash
python3 contracts/validate-handoff-packages.py <path-to-package.anpkg>
```

Replay hardening with runtime binary:

```bash
python3 contracts/validate-handoff-packages.py \
  <path-to-package.anpkg> \
  --runtime-bin <path-to-anolis-runtime>
```

Manual replay, as a validation step only: extract the package into a
scratch directory and start a runtime against it. This checks that the
package's configs are self-contained. It does not deploy: nothing is installed, no
service is created, and the target's `/opt/anolis` is not touched.

```bash
unzip <machine>.anpkg -d <scratch-dir>
cd <scratch-dir>
export INFLUXDB_TOKEN='<token-value>'   # only if telemetry is enabled
<path-to-anolis-runtime> --config runtime/anolis-runtime.yaml
```

Expected: the runtime loads its config from inside the package, with no
dependency on `systems/<project>/`. The exported `manual` variant has
automation disabled, so the runtime loads no behavior tree. Each provider
process reads `providers/<id>.yaml` through its `--config` argument once it
starts. Provider binaries are not in the package: each
provider's `command` is carried as authored and a relative one resolves
against `<scratch-dir>`, so providers start only if their binaries exist at
those paths.

## 6) Operator Handoff Package Contents

Deliver all of:

1. The staged bundle that was deployed
2. `.anpkg` artifact, as the provenance record
3. Runtime/provider binary provenance (build IDs/refs)
4. Validation output (`validate-handoff-packages.py` logs)
5. Mode/operation checklist used during commissioning
6. Deployment env requirements (`INFLUXDB_TOKEN` policy)

## 7) Rollback Guidance

1. Keep the prior known-good bundle and its `.anpkg` in deployment
   storage. The `.anpkg` records the project's `manual` runtime variant
   (other variants are listed as omitted in `meta/provenance.json`), its
   provider configs and the behavior tree it references, if any, with
   the runtime config's InfluxDB token stripped (an export carrying any
   other token-like value in its YAML or JSON is refused). It is not a
   record of every config the bundle shipped or of what the target kept.
2. If a new rollout fails, restore the previous binaries on the target:

   ```bash
   anolis-provision rollback --target user@host
   ```

   Omit `--target` on the target itself. This runs `install.sh
   --rollback`, which swaps the previous binaries (and their
   `manifest.json`) back and restarts the service. It does not revert
   runtime or provider configs; they stay as they are on the target.
3. Record failure cause and attach validation output before next rollout attempt.

## 8) Troubleshooting

1. Checksum mismatch:
   - Artifact tampered/corrupted after export.
   - Re-export and re-validate package.
2. Secret leakage validation failure:
   - Token-like value exists in package content.
   - Remove embedded token and re-export.
3. Path escape/reference failure:
   - Runtime/provider/behavior path is not package-relative.
   - Fix project config, re-export, and validate.
4. Replay `--check-config` failure:
   - Review runtime error output and runtime schema expectations.
   - Re-run static validator first, then replay validator.
