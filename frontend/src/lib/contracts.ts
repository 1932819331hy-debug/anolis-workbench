export type UnknownRecord = Record<string, unknown>;

export interface ValidationErrorItem extends UnknownRecord {
  source: string;
  code: string;
  path: string;
  message: string;
}

export interface ApiErrorResponse extends UnknownRecord {
  error: string;
  code?: string;
  errors?: ValidationErrorItem[];
}

export interface ProjectSummary extends UnknownRecord {
  name: string;
  /** Every project is a canonical machine-profile project since #255. */
  format?: "machine-profile";
  /** False for imported projects, which are carried verbatim and read-only. */
  authored?: boolean;
  meta: UnknownRecord;
}

export interface TemplateSummary extends UnknownRecord {
  id: string;
  meta: UnknownRecord;
}

export interface RuntimeStatus extends UnknownRecord {
  version?: number;
  active_project: string | null;
  running: boolean;
  pid?: number | null;
  composer?: {
    host: string;
    port: number;
  } & UnknownRecord;
  workbench?: {
    version: number;
  } & UnknownRecord;
}

export interface WorkbenchConfig extends UnknownRecord {
  telemetry_url?: string;
}

export interface PreflightCheck extends UnknownRecord {
  name: string;
  ok: boolean | null;
  error?: string | null;
  hint?: string | null;
  note?: string | null;
}

export interface PreflightResult extends UnknownRecord {
  ok: boolean;
  checks: PreflightCheck[];
  summary: { passed: number; skipped: number; failed: number };
}

export interface ProviderSchemaEnvelope extends UnknownRecord {
  config_schema_version: number;
  provider?: string;
  provider_version?: string;
  schema: UnknownRecord;
}

export interface ProviderSchemasResponse extends UnknownRecord {
  schema_version: number;
  providers: Record<string, ProviderSchemaEnvelope>;
}

/**
 * Any provider kind the server offers, which is whatever envelopes it serves —
 * never a fixed set. The literals that used to be unioned here collapsed to
 * `string` anyway, so they typed nothing and only read like an enum somebody
 * had to extend (#285).
 */
export type ProviderKind = string;

export interface RestartPolicy extends UnknownRecord {
  enabled?: boolean;
  max_attempts?: number;
  backoff_ms?: number[];
  timeout_ms?: number;
  success_reset_ms?: number;
}

/**
 * One entry in a runtime config's `providers[]`.
 *
 * `command` is a deploy TOKEN, not a host path: install.sh rewrites it to
 * `{prefix}/bin/anolis-provider-<kind>` at install time. The real host binary
 * lives in `ProjectDocument.host_paths` and is only used for dev-launch.
 */
export interface RuntimeProviderEntry extends UnknownRecord {
  id: string;
  command?: string;
  args?: string[];
  timeout_ms?: number;
  hello_timeout_ms?: number;
  ready_timeout_ms?: number;
  restart_policy?: RestartPolicy;
}

export interface RuntimeTelemetryInflux extends UnknownRecord {
  url?: string;
  org?: string;
  bucket?: string;
  token?: string;
  batch_size?: number;
  flush_interval_ms?: number;
}

export interface RuntimeTelemetry extends UnknownRecord {
  enabled?: boolean;
  influxdb?: RuntimeTelemetryInflux;
}

export interface RuntimeHttp extends UnknownRecord {
  enabled?: boolean;
  bind?: string;
  port?: number;
  cors_allowed_origins?: string[];
  cors_allow_credentials?: boolean;
}

export interface RuntimeAutomation extends UnknownRecord {
  enabled?: boolean;
  behavior_tree?: string;
}

/**
 * A runtime-config document — the file the runtime itself consumes
 * (`config/anolis-runtime.<variant>.yaml`). The workbench edits it directly
 * now; there is no intermediate shadow document.
 */
export interface RuntimeConfigDoc extends UnknownRecord {
  runtime?: {
    name?: string;
    shutdown_timeout_ms?: number;
    startup_timeout_ms?: number;
  } & UnknownRecord;
  http?: RuntimeHttp;
  providers?: RuntimeProviderEntry[];
  polling?: { interval_ms?: number } & UnknownRecord;
  telemetry?: RuntimeTelemetry;
  logging?: { level?: string } & UnknownRecord;
  automation?: RuntimeAutomation;
}

export interface ComponentPin extends UnknownRecord {
  repo?: string;
  version?: string;
}

/** Parsed `machine-profile.yaml` — the machine's identity and contents. */
export interface MachineProfile extends UnknownRecord {
  schema_version?: number;
  machine_id: string;
  display_name?: string;
  runtime_profiles: Record<string, string>;
  providers: Record<string, { config: string } & UnknownRecord>;
  behaviors?: string[];
  compatibility?: UnknownRecord;
  /** AUTHORED pins. Nothing resolves these from the network. */
  components?: {
    runtime?: ComponentPin;
    providers?: Record<string, ComponentPin>;
  } & UnknownRecord;
  safety?: UnknownRecord;
  validation?: UnknownRecord;
}

/**
 * A provider instance: its kind plus the provider-native config document that
 * the kind's own --config-schema envelope describes (#270). The workbench has
 * no per-kind knowledge of that shape.
 */
export interface ProviderInstance extends UnknownRecord {
  kind: ProviderKind | null;
  config: UnknownRecord;
}

/**
 * Dev-launch only. Host binary paths live in the workbench sidecar and are
 * never deployed — a Windows path does not even resemble a deploy token.
 */
export interface HostPaths extends UnknownRecord {
  runtime_executable?: string;
  providers?: Record<string, { executable?: string } & UnknownRecord>;
}

/**
 * The whole canonical project as one document (#255). It carries the FULL
 * parsed files rather than a workbench model of them, so keys the workbench
 * does not understand round-trip intact.
 *
 * Imported projects use the same shape with `authored: false`; they are
 * carried verbatim and every write endpoint rejects them.
 */
export interface ProjectDocument extends UnknownRecord {
  format: "machine-profile";
  authored: boolean;
  meta?: UnknownRecord;
  profile: MachineProfile;
  variants: Record<string, RuntimeConfigDoc>;
  providers: Record<string, ProviderInstance>;
  host_paths?: HostPaths;
  launch?: { variant?: string } & UnknownRecord;
  warnings?: string[];
}

export type ProjectDoc = ProjectDocument;

/** Imported projects are read-only; `authored` is the only thing that says so. */
export function isImportedDoc(doc: ProjectDoc | null): boolean {
  return doc !== null && doc.authored === false;
}

export interface RuntimeStatusCode extends UnknownRecord {
  code?: string;
  message?: string;
}

export interface RuntimeStatusProvider {
  provider_id: string;
  state: string;
  device_count: number;
}

export interface RuntimeApiStatus extends UnknownRecord {
  status: RuntimeStatusCode;
  mode: string;
  uptime_seconds: number;
  polling_interval_ms: number;
  providers: RuntimeStatusProvider[];
  device_count: number;
}

/**
 * Which rung of the safe-state ladder a `POST /v0/estop` would run now.
 * "none" = no software safe-state declared (hardware backplane cut only).
 * MUST stay in sync with the `SoftwareSafeState` enum in the vendored
 * runtime-http OpenAPI contract — a unit test guards against drift.
 */
export type SoftwareSafeState = "none" | "hooks" | "setpoints" | "zero";

/** The `estop` block on `GET /v0/runtime/status` (anolis#219). */
export interface EstopStatus extends UnknownRecord {
  latched: boolean;
  software_safe_state: SoftwareSafeState;
  latched_at_epoch_ms: number | null;
  uncovered_actuating_functions: number;
}

export interface TypedValue extends UnknownRecord {
  type: "double" | "int64" | "uint64" | "bool" | "string" | "bytes";
  double?: number;
  int64?: number;
  uint64?: number;
  bool?: boolean;
  string?: string;
  base64?: string;
}

export interface DeviceStateValue extends UnknownRecord {
  signal_id: string;
  value: TypedValue | UnknownRecord | string | number | boolean | null;
  timestamp_ms?: number;
  timestamp_epoch_ms?: number;
  quality?: string;
  age_ms?: number;
}

export interface Device extends UnknownRecord {
  provider_id: string;
  device_id: string;
  display_name: string;
  type: string;
}

export interface FunctionArgSpec extends UnknownRecord {
  name: string;
  type: string;
  required: boolean;
  min?: number | string;
  max?: number | string;
  allowed_values?: Array<string | number>;
}

export interface FunctionSpec extends UnknownRecord {
  function_id: number;
  name: string;
  function_name: string;
  display_name: string;
  label: string;
  description: string;
  args: FunctionArgSpec[];
}

export interface DeviceCapabilities extends UnknownRecord {
  signals: UnknownRecord[];
  functions: FunctionSpec[];
}

export interface ParameterDefinition extends UnknownRecord {
  name: string;
  type: "double" | "int64" | "bool" | "string" | string;
  value: string | number | boolean;
  min?: number;
  max?: number;
  allowed_values?: string[];
}

export type ExecutionStatus = "idle" | "running" | "blocked" | "failed" | "completed" | "unknown";

/** Identity of the loaded automation definition (engine-neutral provenance). */
export interface AutomationVersion extends UnknownRecord {
  engine_kind: string;
  id: string;
  digest: string;
  digest_scope: string;
}

/**
 * Engine-neutral automation status (anolis >= v0.1.24).
 *
 * The workbench reads only the neutral contract. The deprecated behaviour-tree
 * mirrors (`bt_status`, `total_ticks`, `current_tree`, …) are intentionally not
 * modelled here and are removed from the runtime in anolis >= v0.1.26.
 */
export interface AutomationStatus extends UnknownRecord {
  execution_status: ExecutionStatus;
  execution_reason: string | null;
  automation_version: AutomationVersion | null;
  last_evaluation_at_epoch_ms: number | null;
  run_id: string | null;
  last_error: string | null;
}

export interface ProviderSupervision extends UnknownRecord {
  enabled: boolean;
  attempt_count: number;
  max_attempts: number;
  crash_detected: boolean;
  circuit_open: boolean;
  next_restart_in_ms: number | null;
}

export interface ProviderDeviceHealth extends UnknownRecord {
  device_id: string;
  health: string;
  last_poll_ms: number;
  staleness_ms: number;
}

export interface ProviderHealth extends UnknownRecord {
  provider_id: string;
  state: string;
  lifecycle_state: string;
  last_seen_ago_ms: number | null;
  uptime_seconds: number;
  device_count: number;
  supervision: ProviderSupervision;
  devices: ProviderDeviceHealth[];
}
