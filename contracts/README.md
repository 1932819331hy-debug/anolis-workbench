# Contracts

Repository-level contract snapshots and validators.

## Runtime API

1. `runtime-http.openapi.v0.yaml`
2. Structural + example validation remains in the runtime repository (`anolis`).

## Composer Control API

1. `composer-control.openapi.v1.yaml`
2. Validator: `python3 contracts/validate-composer-control-openapi.py`
3. Baseline: `../docs/contracts/composer-control-baseline.md`

## Handoff Package

1. Validator: `anolis-validate <package.anpkg>` (or `python3 contracts/validate-handoff-packages.py <package.anpkg>`)
2. Baseline: `../docs/contracts/handoff-package-baseline.md`
3. v1 format spec: `../docs/contracts/handoff-package-v1.md`

## Upstream locks

`upstream/anolis/*.lock.json` pin the three schemas vendored from `anolishq/anolis`.
`upstream/providers/*.lock.json` pin each provider's `--config-schema` envelope.

Nothing here is packaged: the wheel ships the `anolis_workbench` package plus
`schemas/**`, `templates/**` and `frontend/dist/**`, and `contracts/` is
repo-root — so a lock file cannot reach a target host.
These are the vendoring-time record of where each vendored file came from.

### The provider registry

`upstream/providers/` **is** the provider registry: one lock per kind, and the
single declaration of which providers the workbench vendors. Both schema
scripts and CI discover kinds by globbing it, so adding a provider is a data
change, not a code change.

Two invariants, enforced by `verify-upstream-schema.py --all-providers` (CI) and
by `tests/unit/test_provider_locks.py` (every test run):

- Locks and vendored envelopes correspond **1:1**. A lock with no envelope is a
  kind nothing offers; an envelope with no lock is a kind whose provenance is
  unrecorded.
- Each lock agrees with the envelope it vendored — same `provider_version`, and
  `distribution.templates` must re-render the asset names the lock pinned.

The set of kinds the workbench **offers at runtime** is still the set of
vendored envelopes under `anolis_workbench/schemas/providers/`, which is what
ships in the wheel. The 1:1 invariant is what keeps the two in step.

### Adding or updating a provider

```bash
# Onboard a kind that has no lock yet
python3 scripts/sync-upstream-schema-from-release.py \
    --new-provider foo --repo vendor/anolis-provider-foo --tag v0.1.0

# Update one that already has a lock (repo and asset names come from the lock)
python3 scripts/sync-upstream-schema-from-release.py --schema provider-config-foo --tag v0.2.0

# Verify everything
python3 scripts/verify-upstream-schema.py --all-providers --require-release-artifact
```

Asset names default to the convention
`anolis-provider-<kind>-{version}-config-schema.json` (plus a
`-manifest.json` sidecar). A vendor who names assets differently passes
`--asset-template` / `--manifest-asset-template` **once** at onboarding; the
patterns are stored in the lock and every later sync reads them back.

### When Renovate bumps a lock

Renovate tracks each lock's release tag (`renovate.json`, #369), so a new
upstream release opens a PR that changes the tags only. That PR stays red
until the asset names and sha256s are refreshed. On its branch, run the sync
script once per lock the PR touches, with the new tag:

```bash
# An anolis release bumps all three anolis locks
python3 scripts/sync-upstream-schema-from-release.py --schema runtime-config --tag vX.Y.Z
python3 scripts/sync-upstream-schema-from-release.py --schema machine-profile --tag vX.Y.Z
python3 scripts/sync-upstream-schema-from-release.py --schema runtime-http --tag vX.Y.Z

# A provider release bumps that provider's lock
python3 scripts/sync-upstream-schema-from-release.py --schema provider-config-<kind> --tag vX.Y.Z

# Verify: each anolis lock by name, the provider locks all at once
python3 scripts/verify-upstream-schema.py --schema runtime-config --require-release-artifact
python3 scripts/verify-upstream-schema.py --schema machine-profile --require-release-artifact
python3 scripts/verify-upstream-schema.py --schema runtime-http --require-release-artifact
python3 scripts/verify-upstream-schema.py --all-providers --require-release-artifact
```

Commit the refreshed locks and vendored schemas to the Renovate branch.

### Lock `schema_version`

| Version | Applies to | Shape |
|---|---|---|
| 2 | `upstream/anolis/*` | `source`, `distribution.{mode,release,schema_sha256,asset_sha256}` |
| 3 | `upstream/providers/*` | v2 plus top-level `kind` and `provider_version`, and `distribution.templates.{asset,manifest_asset}`. Provider locks also carry `distribution.asset_format: "raw"`, which they had at v2. |

**v2 → v3 migration** (providers only): add `kind` (must equal the filename
stem before `-config-schema.lock.json`), add `provider_version` copied from the
vendored envelope, and add `distribution.templates` — the asset-name patterns
with the version replaced by `{version}`. Re-running the sync for that kind
produces a v3 lock directly. The anolis locks stay at v2: they are a fixed set
of three, not a registry.
