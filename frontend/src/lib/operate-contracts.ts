// operate-contracts.ts — data extraction / normalization for the Operate workspace

import type {
  AutomationStatus,
  AutomationVersion,
  Device,
  DeviceCapabilities,
  DeviceStateValue,
  EstopStatus,
  ExecutionStatus,
  FunctionArgSpec,
  FunctionSpec,
  ParameterDefinition,
  ProviderHealth,
  RuntimeApiStatus,
  SoftwareSafeState,
  StartupSafeStateStatus,
  UnknownRecord,
} from "./contracts";

/**
 * Runtime modes accepted by `POST /v0/mode` and reported by `GET /v0/mode`.
 * MUST stay in sync with the `RuntimeMode` enum in the vendored runtime-http
 * OpenAPI contract — a unit test guards against drift.
 */
export const RUNTIME_MODES = ["MANUAL", "AUTO", "IDLE", "FAULT"] as const;

export type RuntimeMode = (typeof RUNTIME_MODES)[number];

const EXECUTION_STATUSES: ReadonlySet<ExecutionStatus> = new Set<ExecutionStatus>([
  "idle",
  "running",
  "blocked",
  "failed",
  "completed",
  "unknown",
]);

export function normalizeExecutionStatus(value: unknown): ExecutionStatus {
  const v = String(value ?? "")
    .trim()
    .toLowerCase() as ExecutionStatus;
  return EXECUTION_STATUSES.has(v) ? v : "unknown";
}

type ParameterType = "double" | "int64" | "bool" | "string";

export type CoerceParameterValueInput = {
  type: unknown;
  rawValue: unknown;
  min?: number | string;
  max?: number | string;
  allowedValues?: Array<string | number>;
};

function asObject(v: unknown): UnknownRecord {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as UnknownRecord) : {};
}

function asArray<T = unknown>(v: unknown): T[] {
  return Array.isArray(v) ? (v as T[]) : [];
}

function toFinite(v: unknown): number | null {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

const PARAMETER_TYPES = new Set<ParameterType>(["double", "int64", "bool", "string"]);
const INT64_MIN = -9223372036854775808n;
const INT64_MAX = 9223372036854775807n;
const JS_SAFE_MIN = BigInt(Number.MIN_SAFE_INTEGER);
const JS_SAFE_MAX = BigInt(Number.MAX_SAFE_INTEGER);

export function extractDevices(payload: unknown): Device[] {
  return asArray(asObject(payload).devices) as Device[];
}

export function extractProvidersHealth(payload: unknown): ProviderHealth[] {
  return asArray(asObject(payload).providers) as ProviderHealth[];
}

export function extractCapabilities(payload: unknown): DeviceCapabilities {
  const root = asObject(payload);
  const caps = asObject(root.capabilities);
  return {
    ...caps,
    signals: asArray(caps.signals) as UnknownRecord[],
    functions: normalizeFunctionSpecs(caps.functions),
  } as DeviceCapabilities;
}

export function normalizeFunctionSpecs(functions: unknown): FunctionSpec[] {
  if (!Array.isArray(functions)) return [];
  return (functions as UnknownRecord[])
    .map((func, i) => {
      const functionId = toFinite(func.function_id) ?? i + 1;
      const name =
        (typeof func.name === "string" && func.name.trim()) ||
        (typeof func.function_name === "string" && func.function_name.trim()) ||
        `Function ${functionId}`;
      const description =
        (typeof func.label === "string" && func.label.trim()) ||
        (typeof func.description === "string" && func.description.trim()) ||
        "";
      return {
        ...func,
        function_id: functionId,
        name,
        function_name: name,
        display_name: name,
        label: description,
        description,
        args: normalizeFunctionArgs(func.args),
      } as FunctionSpec;
    })
    .sort((a, b) =>
      a.function_id !== b.function_id
        ? a.function_id - b.function_id
        : String(a.display_name).localeCompare(String(b.display_name)),
    );
}

export function normalizeFunctionArgs(args: unknown): FunctionArgSpec[] {
  if (Array.isArray(args)) {
    return (args as UnknownRecord[])
      .map((a, i) => normalizeArgSpec(asObject(a), `arg_${i + 1}`))
      .filter((a) => a.name !== "");
  }
  if (args && typeof args === "object") {
    return Object.entries(args as UnknownRecord)
      .map(([name, spec]) => normalizeArgSpec(asObject(spec), name))
      .filter((a) => a.name !== "")
      .sort((a, b) => a.name.localeCompare(b.name));
  }
  return [];
}

export function normalizeArgSpec(arg: UnknownRecord, fallback = ""): FunctionArgSpec {
  const name = (typeof arg.name === "string" && arg.name.trim()) || fallback;
  if (!name.trim()) return { name: "", type: "string", required: true };
  return {
    name: name.trim(),
    type: (typeof arg.type === "string" && arg.type.trim()) || "string",
    required: arg.required !== false,
    min: arg.min as number | string | undefined,
    max: arg.max as number | string | undefined,
    allowed_values: arg.allowed_values as Array<string | number> | undefined,
  };
}

export function extractDeviceStateValues(payload: unknown): DeviceStateValue[] {
  return asArray(asObject(payload).values).map((s) => {
    const src = asObject(s);
    return {
      ...src,
      timestamp_ms: toFinite(src.timestamp_ms) ?? toFinite(src.timestamp_epoch_ms) ?? 0,
    } as DeviceStateValue;
  });
}

export function extractMode(payload: unknown): string | null {
  const r = asObject(payload);
  return typeof r.mode === "string" ? r.mode : null;
}

export function extractRuntimeStatus(payload: unknown): RuntimeApiStatus {
  const r = asObject(payload);
  return {
    status: asObject(r.status) as RuntimeApiStatus["status"],
    version: typeof r.version === "string" ? r.version : "",
    mode: typeof r.mode === "string" ? r.mode : "UNKNOWN",
    uptime_seconds: toFinite(r.uptime_seconds) ?? 0,
    polling_interval_ms: toFinite(r.polling_interval_ms) ?? 0,
    device_count: toFinite(r.device_count) ?? 0,
    providers: asArray(r.providers) as RuntimeApiStatus["providers"],
    device_loss_latched: asArray(r.device_loss_latched).map(String),
  };
}

export function extractAutomationVersion(payload: unknown): AutomationVersion | null {
  if (payload === null || typeof payload !== "object") return null;
  const r = payload as UnknownRecord;
  return {
    engine_kind: typeof r.engine_kind === "string" ? r.engine_kind : "",
    id: typeof r.id === "string" ? r.id : "",
    digest: typeof r.digest === "string" ? r.digest : "",
    digest_scope: typeof r.digest_scope === "string" ? r.digest_scope : "",
  };
}

export function extractAutomationStatus(payload: unknown): AutomationStatus {
  const r = asObject(payload);
  return {
    execution_status: normalizeExecutionStatus(r.execution_status),
    execution_reason:
      typeof r.execution_reason === "string" && r.execution_reason.trim()
        ? r.execution_reason
        : null,
    automation_version: extractAutomationVersion(r.automation_version),
    last_evaluation_at_epoch_ms: toFinite(r.last_evaluation_at_epoch_ms) ?? null,
    run_id: typeof r.run_id === "string" && r.run_id.trim() ? r.run_id : null,
    last_error: typeof r.last_error === "string" && r.last_error.trim() ? r.last_error : null,
  };
}

export function extractAutomationTree(payload: unknown): string {
  const r = asObject(payload);
  return typeof r.tree === "string" ? r.tree : "";
}

export function normalizeParameterType(type: unknown): ParameterType | null {
  const t = String(type ?? "").trim() as ParameterType;
  return PARAMETER_TYPES.has(t) ? t : null;
}

export function extractParameters(payload: unknown): ParameterDefinition[] {
  return asArray(asObject(payload).parameters)
    .filter((p) => p && typeof (p as UnknownRecord).name === "string")
    .map((p) => {
      const record = asObject(p);
      return {
        ...record,
        name: String(record.name).trim(),
        type: normalizeParameterType(record.type) ?? String(record.type ?? ""),
      } as ParameterDefinition;
    })
    .filter((p) => p.name !== "")
    .sort((a, b) => a.name.localeCompare(b.name));
}

export function normalizeProviderHealthQuality(
  quality: unknown,
): "OK" | "FAULT" | "UNAVAILABLE" | "UNKNOWN" {
  const v = String(quality || "UNKNOWN").toUpperCase();
  if (v === "OK" || v === "READY" || v === "AVAILABLE") return "OK";
  if (v === "FAULT") return "FAULT";
  if (v === "UNAVAILABLE" || v === "STALE") return "UNAVAILABLE";
  return "UNKNOWN";
}

/**
 * Software safe-state ladder rungs, mirroring the `SoftwareSafeState` enum in
 * the vendored runtime-http OpenAPI contract. A unit test guards against drift.
 */
export const SOFTWARE_SAFE_STATES = ["none", "hooks", "setpoints", "zero"] as const;

/**
 * The first runtime whose `uncovered_actuating_functions` counts the
 * actuating functions the active rung will not drive (anolishq/anolis#253,
 * anolishq/anolis PR #369). Before it, the field counts functions with no
 * matching setpoint, which overstates under hooks and is false under zero.
 */
export const DISPATCHABLE_COVERAGE_SINCE = "0.1.45";

const SEMVER = /^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/;

/**
 * Whether `version` is at least `min` (a release `X.Y.Z`) by semver
 * precedence. A pre-release of `min` is older than `min`. An unknown or
 * unparsable version is never at least anything.
 */
export function versionAtLeast(version: string | null | undefined, min: string): boolean {
  const v = SEMVER.exec((version ?? "").trim());
  const m = SEMVER.exec(min);
  if (!v || !m) return false;
  for (let i = 1; i <= 3; i += 1) {
    const diff = Number(v[i]) - Number(m[i]);
    if (diff !== 0) return diff > 0;
  }
  return v[4] === undefined;
}

/**
 * Normalize a software-safe-state value. An unrecognized value degrades to the
 * *weakest* claim ("none") — the UI must never advertise a stop rung the runtime
 * did not declare.
 */
export function normalizeSoftwareSafeState(value: unknown): SoftwareSafeState {
  const v = String(value ?? "")
    .trim()
    .toLowerCase() as SoftwareSafeState;
  return (SOFTWARE_SAFE_STATES as readonly string[]).includes(v) ? v : "none";
}

/**
 * Extract the `estop` block from a `GET /v0/runtime/status` body. Returns null
 * when the block is absent or not an object (the contract keeps it optional) —
 * the UI must not render a stop control it cannot describe.
 */
export function extractEstopStatus(payload: unknown): EstopStatus | null {
  const root = asObject(payload);
  if (!root.estop || typeof root.estop !== "object" || Array.isArray(root.estop)) {
    return null;
  }
  const r = asObject(root.estop);
  return {
    latched: r.latched === true,
    software_safe_state: normalizeSoftwareSafeState(r.software_safe_state),
    // toFinite(null) is 0, but an absent latch time must stay null.
    latched_at_epoch_ms: r.latched_at_epoch_ms == null ? null : toFinite(r.latched_at_epoch_ms),
    uncovered_actuating_functions: toFinite(r.uncovered_actuating_functions) ?? 0,
  };
}

/**
 * Extract the `startup_safe_state` block from a `GET /v0/runtime/status` body.
 * Returns null when it is absent (a runtime older than anolis 0.1.45) or not an
 * object, so the UI shows nothing rather than guess.
 */
export function extractStartupSafeState(payload: unknown): StartupSafeStateStatus | null {
  const root = asObject(payload);
  const raw = root.startup_safe_state;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const r = asObject(raw);
  return {
    on_startup: r.on_startup === "leave" ? "leave" : "safe_state",
    ran: r.ran === true,
    software_safe_state: normalizeSoftwareSafeState(r.software_safe_state),
    safe_state_achieved: r.safe_state_achieved === true,
    failed_actions: asArray(r.failed_actions)
      .map(asObject)
      .map((a) => ({
        device_handle: String(a.device_handle ?? "?"),
        function: String(a.function ?? "?"),
        success: a.success === true,
        ...(typeof a.error === "string" ? { error: a.error } : {}),
      })),
    skipped_actions: asArray(r.skipped_actions)
      .map(asObject)
      .map((a) => ({
        device_handle: String(a.device_handle ?? "?"),
        function: String(a.function ?? "?"),
        reason: String(a.reason ?? "absent"),
      })),
  };
}

/**
 * Format the runtime's startup safe-state report into operator-facing text.
 * `safe_state_achieved` means the rung issued at least one call and every
 * call was accepted by its provider, not that the outputs reached a safe
 * state, so the text says no more than that. `ok` is false when a rung ran
 * and the runtime's verdict is false, or when any call was skipped.
 */
export function summarizeStartupSafeState(s: StartupSafeStateStatus): {
  text: string;
  ok: boolean;
} {
  const head = `Startup safe state (${s.software_safe_state})`;
  // A skipped call's device was absent at startup, and is not driven when it
  // appears later (anolis#461): never ok, whatever the calls that ran did.
  const skipped =
    s.skipped_actions.length === 0
      ? ""
      : `${s.skipped_actions.length} call(s) skipped, device absent at startup: ` +
        s.skipped_actions.map((a) => `${a.device_handle}/${a.function}: ${a.reason}`).join("; ");
  if (s.ran && s.safe_state_achieved) {
    const text = `${head}: every call it issued was accepted by its provider.`;
    return skipped ? { text: `${text} ${skipped}`, ok: false } : { text, ok: true };
  }
  if (s.ran && s.failed_actions.length === 0) {
    if (skipped) {
      return { text: `${head} issued no call: ${skipped}`, ok: false };
    }
    // Otherwise only the zero rung with no actuating function issues no call.
    return { text: `${head} ran but issued no call, so nothing was driven.`, ok: false };
  }
  if (s.ran) {
    const detail = s.failed_actions
      .map((a) => `${a.device_handle}/${a.function}: ${a.error ?? "failed"}`)
      .join("; ");
    const text = `${head}: ${s.failed_actions.length} call(s) failed: ${detail}`;
    return { text: skipped ? `${text}. ${skipped}` : text, ok: false };
  }
  if (s.on_startup === "leave") {
    return { text: "Outputs were left as they were at startup (on_startup: leave).", ok: true };
  }
  return { text: "No software safe state is declared, so nothing ran at startup.", ok: true };
}

/**
 * The Runtime Status badge, from machine state (wb#557). `status.code` is the
 * response envelope: it says the status request succeeded, so it decides only
 * between loaded and UNAVAILABLE, never whether the machine is OK.
 */
export function summarizeRuntimeHealth(
  status: RuntimeApiStatus | null,
  estop: EstopStatus | null,
  startup: StartupSafeStateStatus | null,
): {
  label: "OK" | "ATTENTION" | "UNAVAILABLE";
  cls: "ok" | "fault" | "unavailable";
  reasons: string[];
} {
  if (!status || String(status.status?.code || "").toUpperCase() !== "OK") {
    return { label: "UNAVAILABLE", cls: "unavailable", reasons: [] };
  }
  const reasons: string[] = [];
  if (estop?.latched) reasons.push("software stop latched");
  if (startup && !summarizeStartupSafeState(startup).ok) {
    reasons.push("startup safe state not achieved");
  }
  for (const p of status.providers) {
    if (p.state !== "AVAILABLE") reasons.push(`provider ${p.provider_id} ${p.state}`);
  }
  for (const handle of status.device_loss_latched) reasons.push(`device ${handle} lost (latched)`);
  return reasons.length > 0
    ? { label: "ATTENTION", cls: "fault", reasons }
    : { label: "OK", cls: "ok", reasons };
}

/**
 * Format a `POST /v0/estop` response (EstopResponse) into operator-facing text.
 * `ok` is false whenever the operator should not trust that the machine is safe
 * (an action failed, the latch did not engage, or a FAULT transition failed).
 */
export function summarizeEstopResponse(payload: unknown): { text: string; ok: boolean } {
  const r = asObject(payload);
  const latched = r.latched === true;
  const rung = normalizeSoftwareSafeState(r.software_safe_state);
  const actions = asArray(r.actions).map(asObject);
  const failures = actions.filter((a) => a.success === false);
  const parts: string[] = [];
  let ok = true;

  // Never assert "engaged"/"latched" when the latch did not actually engage —
  // this defensive branch exists precisely to be honest about a failed stop.
  if (latched) {
    parts.push(`Software stop engaged (${rung}).`);
  } else {
    parts.push("WARNING: the latch did not engage — the machine may not be safe.");
    ok = false;
  }

  if (actions.length === 0 && rung === "none") {
    if (latched) {
      const warning = typeof r.warning === "string" && r.warning.trim() ? r.warning.trim() : "";
      parts.push(
        warning ||
          "Actuation is latched, but no software safe-state is declared — outputs were NOT driven.",
      );
    }
    // When the latch failed, the leading warning already conveys nothing is safe.
  } else if (failures.length > 0) {
    ok = false;
    const detail = failures
      .map((a) => `${a.device_handle ?? "?"}/${a.function ?? "?"}: ${a.error ?? "failed"}`)
      .join("; ");
    parts.push(`${failures.length} of ${actions.length} action(s) FAILED: ${detail}`);
  } else if (actions.length > 0) {
    parts.push(`${actions.length} safe-state action(s) succeeded.`);
  }

  if (r.fault_engaged === false) {
    parts.push("FAULT transition did not engage.");
    ok = false;
  }

  if (!ok) {
    parts.push("Use the hardware e-stop to make the machine safe.");
  }

  return { text: parts.join(" "), ok };
}

export function deriveOperateAvailability(
  statusPayload: UnknownRecord | null | undefined,
  projectName: string,
): {
  available: boolean;
  reason: "stopped" | "different_project" | "available";
  message: string;
  runningProject: string;
} {
  const running = Boolean(statusPayload?.running);
  const runningProject =
    typeof statusPayload?.active_project === "string" ? statusPayload.active_project : "";
  if (!running) {
    return {
      available: false,
      reason: "stopped",
      message: "Runtime is stopped. Start runtime from Commission to operate this project.",
      runningProject,
    };
  }
  if (runningProject !== projectName) {
    return {
      available: false,
      reason: "different_project",
      message: `Runtime is running for project "${runningProject}". Stop it before operating "${projectName}".`,
      runningProject,
    };
  }
  return { available: true, reason: "available", message: "", runningProject };
}

export function coerceParameterValue(input: CoerceParameterValueInput): number | boolean | string {
  const { type, rawValue, min, max, allowedValues } = input;
  const ntype = normalizeParameterType(type);
  if (!ntype) throw new Error("Unsupported parameter type");
  const src = String(rawValue ?? "").trim();
  let value: number | boolean | string;

  if (ntype === "int64") {
    if (!/^-?\d+$/.test(src)) throw new Error("Invalid integer");
    let n: bigint;
    try {
      n = BigInt(src);
    } catch {
      throw new Error("Invalid integer");
    }
    if (n < INT64_MIN || n > INT64_MAX) throw new Error("Out-of-range int64");
    if (n < JS_SAFE_MIN || n > JS_SAFE_MAX) throw new Error("int64 exceeds browser-safe range");
    value = Number(n);
  } else if (ntype === "double") {
    value = Number(src);
    if (Number.isNaN(value)) throw new Error("Invalid number");
  } else if (ntype === "bool") {
    value = src.toLowerCase() === "true";
  } else {
    value = src;
  }

  if ((ntype === "int64" || ntype === "double") && typeof value === "number") {
    const mn = min !== undefined ? Number(min) : undefined;
    const mx = max !== undefined ? Number(max) : undefined;
    if (Number.isFinite(mn) && value < (mn as number)) {
      throw new Error(`Value below minimum (${mn})`);
    }
    if (Number.isFinite(mx) && value > (mx as number)) {
      throw new Error(`Value above maximum (${mx})`);
    }
  }

  if (ntype === "string" && Array.isArray(allowedValues) && allowedValues.length > 0) {
    const allowed = allowedValues.map(String);
    if (!allowed.includes(String(value))) {
      throw new Error(`Value must be one of: ${allowed.join(", ")}`);
    }
  }
  return value;
}

export function renderBtOutline(
  xmlDoc: Document,
  node: Element | null = null,
  indent = 0,
  isLast = true,
): string {
  if (!node) {
    const root = xmlDoc.querySelector("BehaviorTree");
    if (!root) return "No BehaviorTree found.";
    return renderBtOutline(xmlDoc, root, 0, true);
  }
  const prefix = indent === 0 ? "" : " ".repeat((indent - 1) * 2) + (isLast ? "\\- " : "|- ");
  const name = node.getAttribute("name") || "";
  let out = `${prefix}${node.tagName}${name ? ` "${name}"` : ""}\n`;
  const children = Array.from(node.children) as Element[];
  for (let i = 0; i < children.length; i += 1) {
    out += renderBtOutline(xmlDoc, children[i], indent + 1, i === children.length - 1);
  }
  return out;
}
