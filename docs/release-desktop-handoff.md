# Desktop Release Handoff

Audience: maintainers preparing native installer bundles for `anolis-workbench`.

## Overview

Desktop packaging is split into two explicit layers:

1. **Python sidecar freeze** (`scripts/freeze_server.py`) -> onefile executable.
2. **Tauri bundle build** (`desktop/src-tauri`) -> platform installers.

The desktop wrapper is a shell only: frontend still talks directly to
`http://127.0.0.1:3010` over localhost HTTP/SSE.

## Required Inputs

1. The release version, bumped in every file listed in `version-locations.txt`.
2. Rust + Node toolchains available in CI runners.

The workflow builds the frontend itself (`npm ci && npm run build` in
`frontend/`) in every freeze and bundle job, so no prebuilt assets are needed.

Version alignment rule:

- The `validate` job's "Validate version alignment across all manifests" step
  in `release.yml` fails unless the requested version is valid semver, its tag
  (`v<version>`) does not exist yet, and the version exactly matches all of:
  - `pyproject.toml` (`project.version`)
  - `desktop/package.json` (`version`)
  - `frontend/package.json` (`version`)
  - `desktop/src-tauri/tauri.conf.json` (`version`)
  - `desktop/src-tauri/Cargo.toml` (`package.version`)

## Workflow

Use:

- `.github/workflows/release.yml` (run manually, `workflow_dispatch`, with the
  `version` input and an optional `prerelease` flag). Desktop and PyPI releases
  share this one workflow.

Desktop job flow:

1. `validate` — semver, tag, and cross-file version alignment (above).
2. `ci` — the reusable `ci.yml` gate.
3. `freeze-server` — freeze the sidecar per target: Linux x64, Windows x64,
   macOS arm64, macOS x64.
4. `package-desktop` — stage the frozen sidecar into the Tauri `externalBin`
   path and run `tauri build` per target (`.msi`, `.deb` + `.AppImage`,
   `.dmg`).
5. `sbom` — Syft CycloneDX SBOMs for the Python, Node and Rust trees. This job
   is `continue-on-error: true`, which keeps a failed SBOM from failing the
   run. `create-release` needs it, though, and GitHub documents that a failed
   job skips the jobs that need it without saying whether `continue-on-error`
   changes that. Treat a failed `sbom` as possibly blocking `create-release`,
   and check the SBOM assets (below).
6. `create-release` — normalize asset names, push the `v<version>` tag, and
   create the GitHub Release with the wheel and sdist, the installers, the
   SBOMs and `metrics.json`. It needs `validate`, `build-python`,
   `smoke-test`, `package-desktop`, `sbom` and `metrics`.

## Sidecar Freeze Guards

`freeze_server.py` enforces:

1. Frontend dist presence check.
2. Minimum executable size threshold check.
3. Executable smoke checks (`--help`, `--version`) unless explicitly skipped.

## Manual Operations

1. Verify desktop app identity in `desktop/src-tauri/tauri.conf.json`:
   - `identifier: org.feastorg.anolis-workbench`
   - `productName: Anolis Workbench`
2. Confirm port `3010` is documented as reserved in user-facing release notes.
3. Confirm release assets include at minimum:
   - Windows `.msi`
   - Linux `.AppImage` and `.deb`
   - macOS `.dmg` (aarch64 and x64)
   - CycloneDX SBOM JSON files

## Notes

1. macOS is built in the same `package-desktop` matrix as Windows and Linux,
   so a failed macOS leg fails that job and `create-release` does not run.
2. `publish-pypi` needs only `validate` and `build-python`, so it runs
   alongside the desktop jobs and never waits for them. A desktop failure does
   not stop the PyPI publish: the version can land on PyPI with no tag and no
   GitHub Release. Re-running the workflow for the same version recovers —
   no tag exists yet, so `validate` passes, and the publish step uses
   `skip-existing: true`.
3. Re-running only the failed jobs of the same run also recovers, without
   rebuilding: GitHub re-runs the failed jobs and the ones they gated. 0.15.0
   went out this way after `smoke-test` failed because PyPI's index had not
   yet listed the new version.
