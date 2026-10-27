# Composer Control Baseline

Status: Locked.

## Purpose

Freeze Workbench control API behavior used by commissioning flows so UI and tooling integrations remain stable.

## Canonical Artifacts

1. Backend implementation: `anolis_workbench/server/app.py`
2. OpenAPI contract: `contracts/composer-control.openapi.v1.yaml`
3. OpenAPI validator: `contracts/validate-composer-control-openapi.py`
4. Contract tests: `tests/unit/test_control_contract.py`
5. Tooling docs:
   - `README.md`
   - `contracts/runtime-http.openapi.v0.yaml` (contract workflow context)

## Locked Behavior Summary

### Endpoint Inventory

1. `GET /api/status`
2. `GET /api/config`
3. `POST /api/projects/{name}/preflight`
4. `POST /api/projects/{name}/launch`
5. `POST /api/projects/{name}/stop`
6. `POST /api/projects/{name}/restart`
7. `GET /api/projects/{name}/logs` (SSE)

### Response and Error Shape

1. Control endpoints return stable JSON success payloads.
2. Error payloads expose stable top-level `error` text.
3. Project save and import validation failures include structured `errors[]` entries (`source`, `code`, `path`, `message`); launch and restart validation failures return `422` with an `errors[]` list of strings.
4. Expected status semantics:
   - `400` invalid project names
   - `404` unknown project
   - `409` launch conflict
   - `500` backend failure

### Runtime/Path Decoupling

1. Composer path resolution is package-anchored (`anolis_workbench/core/paths.py`), not caller-CWD dependent. Project data defaults to `~/.anolis/systems`, or `ANOLIS_DATA_DIR` if set; a relative override resolves against the CWD.

2. `ANOLIS_*` environment variables the Workbench server reads:

   - `ANOLIS_WORKBENCH_HOST`
   - `ANOLIS_WORKBENCH_PORT`
   - `ANOLIS_TELEMETRY_URL`
   - `ANOLIS_WORKBENCH_OPEN_BROWSER`
   - `ANOLIS_WORKBENCH_ALLOWED_HOSTS`
   - `ANOLIS_WORKBENCH_RUNTIME_URL`
   - `ANOLIS_WORKBENCH_RUNTIME_TOKEN`
   - `ANOLIS_DATA_DIR`
   - `ANOLIS_FRONTEND_DIR`
   - `ANOLIS_IMPORT_ROOTS`
   - `ANOLIS_RUNTIME_PATH`
   - `ANOLIS_WB_RELEASE_PINS`

## Validation Gates

1. `python3 contracts/validate-composer-control-openapi.py`
2. `python3 -m pytest tests -q`

## Drift Notes and Change Rule

1. Additive response fields are allowed.
2. Existing fields must keep semantics.
3. Status-code or payload-shape changes require synchronized updates to implementation, OpenAPI, tests, and this baseline. No check enforces this: the OpenAPI validator checks document structure and that the seven operations exist, not their status codes against the implementation. `contracts/composer-control.openapi.v1.yaml` does not list the `422` launch and restart validation failure or the `409` imported-project refusal on preflight and stop, and its `ErrorResponse.errors` schema requires object items with `source`, `code`, `path` and `message`, which matches the save and import validation failures but not the `422` response, which returns strings.
