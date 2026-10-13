# Versioning Policy

This document defines versioning for anolis-workbench.

## Scope

- Distribution name: anolis-workbench
- Version source of truth: `pyproject.toml` `project.version`
- Release trigger input version must exactly match `pyproject.toml`

Desktop wrapper alignment:

- Tauri desktop wrapper (`desktop/src-tauri`) follows the same product semver as
  the Python package release.
- Desktop installer artifacts are published under the same Git tag (`v<version>`)
  as Python package releases.
- Desktop release validation enforces cross-file version lockstep across:
  - `pyproject.toml`
  - `desktop/package.json`
  - `desktop/src-tauri/Cargo.toml`
  - `desktop/src-tauri/tauri.conf.json`

## Scheme

anolis-workbench uses Semantic Versioning: MAJOR.MINOR.PATCH.

- MAJOR: breaking changes to commissioning workflows, package behavior, or user-facing compatibility expectations
- MINOR: backward-compatible feature additions
- PATCH: backward-compatible bug fixes, stability improvements, dependency updates, and release pipeline hardening

## Compatibility Boundaries

Runtime compatibility is not encoded only by the workbench package version.
Compatibility with runtime/provider behavior is governed by locked contract artifacts, including:

- anolis_workbench/schemas/runtime-config.schema.json
- anolis_workbench/schemas/machine-profile.schema.json
- contracts/runtime-http.openapi.v0.yaml

Changes to those contracts must follow contract governance and drift checks.

## Release Rules

A release is accepted only when all of the following are true:

1. Requested version is valid semver
2. Requested version equals `pyproject.toml` `project.version`
3. Target tag (`v<version>`) does not already exist
4. Reusable CI gate passes
5. Build artifacts are created successfully
6. PyPI publish succeeds (OIDC trusted publishing)
7. Clean-install smoke checks pass

GitHub release creation occurs only after publish and smoke checks pass.

Desktop release rule:

- Native desktop bundles and SBOM artifacts are attached to the same release tag
  by `release.yml`, the same workflow that publishes to PyPI.

## Pre-release

The release workflow supports a prerelease toggle for GitHub Release metadata.
Pre-release handling still requires valid semver input and the same quality gates.

## Practical Bump Guidance

Use PATCH for:

- bug fixes
- CI/release reliability improvements
- packaging correctness fixes
- non-breaking dependency updates

Use MINOR for:

- new Compose, Commission, or Operate capabilities that remain backward compatible
- new user-visible workflows without breaking existing ones

Use MAJOR for:

- incompatible behavior changes in core commissioning flows
- removals or changes that require user migration

## Notes

- Keep version increments intentional and small.
- Avoid mixing unrelated large changes into one release when possible.
- Always verify release notes and artifact contents before broad announcement.
