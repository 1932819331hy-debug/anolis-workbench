<script lang="ts">
  import { onDestroy } from "svelte";
  import { fetchJson } from "../lib/api";
  import type {
    AutomationStatus,
    Device,
    DeviceCapabilities,
    DeviceStateValue,
    EstopStatus,
    FunctionSpec,
    ParameterDefinition,
    ProjectDoc,
    ProviderHealth,
    RuntimeApiStatus,
    RuntimeStatus,
    StartupSafeStateStatus,
    TypedValue,
    UnknownRecord,
    WorkbenchConfig,
  } from "../lib/contracts";
  import {
    activeVariant,
    launchVariantMismatch,
    variantRelpath,
    type SavedLaunch,
  } from "../lib/canonical";
  import {
    coerceParameterValue,
    deriveOperateAvailability,
    DISPATCHABLE_COVERAGE_SINCE,
    extractAutomationStatus,
    extractAutomationTree,
    extractCapabilities,
    extractDeviceStateValues,
    extractDevices,
    extractEstopStatus,
    extractMode,
    extractParameters,
    extractProvidersHealth,
    extractRuntimeStatus,
    extractStartupSafeState,
    normalizeParameterType,
    normalizeProviderHealthQuality,
    renderBtOutline,
    RUNTIME_MODES,
    summarizeEstopResponse,
    summarizeRuntimeHealth,
    summarizeStartupSafeState,
    versionAtLeast,
  } from "../lib/operate-contracts";
  import {
    appendEventTrace,
    buildTraceEvent,
    createOperateEventStreamManager,
  } from "../lib/operate-events";

  let {
    projectName,
    system = null,
    runtimeStatus,
    editingVariant = "",
    saved = null,
    onNavigate = undefined,
  }: {
    projectName: string | null;
    system?: ProjectDoc | null;
    runtimeStatus: RuntimeStatus | null;
    /** The variant open in Compose, for the mismatch banner. */
    editingVariant?: string;
    /** Opens a workbench route, e.g. the running project's Operate view. */
    onNavigate?: (path: string) => void;
    /** The saved project's launch state, shared with Commission. */
    saved?: SavedLaunch | null;
  } = $props();

  function inputTarget(event: Event): HTMLInputElement {
    return event.currentTarget as HTMLInputElement;
  }

  function selectTarget(event: Event): HTMLSelectElement {
    return event.currentTarget as HTMLSelectElement;
  }

  const running = $derived(Boolean(runtimeStatus?.running));
  const runningProject = $derived(
    typeof runtimeStatus?.active_project === "string" ? runtimeStatus.active_project : null,
  );
  const available = $derived(running && runningProject === projectName);
  // Where an operator declares the safe state (#346): the dev-launch copy is
  // the project's own variant file; an installed target keeps its own config,
  // which a redeploy does not overwrite.
  // The file of the variant Launch runs, from the saved project (#339).
  // The project's own variant mapping names the file; an imported project's
  // need not follow the authored naming (wb#564).
  const safeStateFile = $derived.by(() => {
    const variant = saved?.launch ?? activeVariant(system);
    return system?.profile?.runtime_profiles?.[variant] ?? variantRelpath(variant);
  });
  // Against the SAVED launch variant, as Commission does: Launch reads disk.
  const variantMismatch = $derived(
    launchVariantMismatch(system, editingVariant, saved?.launch ?? activeVariant(system)),
  );

  // ── Operate state ─────────────────────────────────────────────────────────
  let mode = $state<string>("--");
  let modeBadgeClass = $state<string>("unknown");
  let modeSelectorValue = $state<string>("");
  let modeSelectorDirty = $state<boolean>(false);
  let modeFeedback = $state<string>("");
  let modeFeedbackClass = $state<string>("field-note");
  let setModeRunning = $state<boolean>(false);

  // ── E-stop state (wb#236) ───────────────────────────────────────────────────
  let estop = $state<EstopStatus | null>(null);
  // Bumped when a stop or clear reply sets the latch. A status reply whose
  // request left before then describes the runtime before that POST, so it
  // never overwrites what the reply reported (wb#614).
  let estopEpoch = 0;
  // Whether a status poll has settled yet: until it has, the stop card says it is
  // loading rather than that the runtime has not reported its safe state.
  let statusSettled = $state<boolean>(false);
  // Whether /v0/devices has answered once: until then the list is loading,
  // not empty (#568).
  let devicesSettled = $state<boolean>(false);
  let startupSafeState = $state<StartupSafeStateStatus | null>(null);
  let estopRunning = $state<boolean>(false);
  let clearRunning = $state<boolean>(false);
  let clearArmed = $state<boolean>(false);
  let estopFeedback = $state<string>("");
  let estopFeedbackClass = $state<string>("field-note");
  // The running project a stop's result belongs to, so the advisory never
  // shows one project's result under another's name (#264).
  let estopFeedbackFor = $state<string | null>(null);

  let providerHealth = $state<ProviderHealth[]>([]);
  let devices = $state<Device[]>([]);
  let selectedKey = $state<string>("");
  let deviceStates = $state<Record<string, DeviceStateValue[]>>({});
  let capabilities = $state<Record<string, DeviceCapabilities>>({});
  let runtimeStatusData = $state<RuntimeApiStatus | null>(null);
  // What `uncovered_actuating_functions` means depends on the runtime
  // version; see DISPATCHABLE_COVERAGE_SINCE.
  const dispatchableCoverage = $derived(
    versionAtLeast(runtimeStatusData?.version, DISPATCHABLE_COVERAGE_SINCE),
  );
  let parameters = $state<ParameterDefinition[]>([]);
  let automationStatus = $state<AutomationStatus | null>(null);
  // Transient automation fault surfaced from an SSE event (a fault is an event,
  // not an execution_status — the next status poll reflects the engine's truth).
  let automationFault = $state<{ message: string; timestamp_ms: number } | null>(null);
  let behaviorTree = $state<string>("");
  let eventTrace = $state<Array<{ type: string; timestamp_ms: number; details: string }>>([]);

  let streamStatus = $state<{
    state: string;
    attempts: number;
    delay_ms?: number;
    idle_ms?: number;
  }>({
    state: "disconnected",
    attempts: 0,
  });
  let streamManager: ReturnType<typeof createOperateEventStreamManager> | null = null;

  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let telemetryLoaded = $state<boolean>(false);
  let telemetryFrame = $state<HTMLIFrameElement | null>(null);
  let workbenchConfig = $state<WorkbenchConfig | null>(null);

  const telemetryUrl = $derived(() => {
    const fromConfig = workbenchConfig?.telemetry_url;
    if (typeof fromConfig === "string" && fromConfig.trim())
      return fromConfig.trim().replace(/\/$/, "");
    const fromGlobal = window.__ANOLIS_COMPOSER__?.telemetryUrl;
    if (typeof fromGlobal === "string" && fromGlobal.trim())
      return fromGlobal.trim().replace(/\/$/, "");
    return "";
  });
  const EVENT_TRACE_LIMIT = 100;

  // ── Availability: react to prop changes ───────────────────────────────────
  $effect(() => {
    if (available) {
      startOperate();
    } else {
      stopOperate();
    }
  });

  // The advisory's stop label depends on whether /v0 goes to an override.
  void loadWorkbenchConfig();

  // On another project's page the Operate poll is off, so read the running
  // runtime's latch on the same 5 s cadence while the page shows it (wb#553).
  // A reply that lands after the page moved on writes nothing, as in #551.
  let otherLatchTimer: ReturnType<typeof setInterval> | null = null;
  $effect(() => {
    if (available || !running || !runningProject) return;
    const target = runningProject;
    estop = null;
    const read = (): void => {
      const epoch = estopEpoch;
      void fetchJson<UnknownRecord>("/v0/runtime/status")
        .then((p) => {
          if (otherLatchTimer === timer && runningProject === target && epoch === estopEpoch)
            estop = extractEstopStatus(p);
        })
        .catch(() => {});
    };
    const timer = setInterval(read, 5000);
    otherLatchTimer = timer;
    read();
    return () => {
      clearInterval(timer);
      if (otherLatchTimer === timer) otherLatchTimer = null;
    };
  });

  // The "confirm clear" arm is scoped to a single latched episode: any time the
  // latch is not engaged (cleared here, by another client, or before a fresh
  // e-stop), disarm so a new latch can never reappear already in the confirm
  // state and be released by a single click (wb#236 two-step safety).
  $effect(() => {
    if (!estop?.latched) clearArmed = false;
  });

  onDestroy(() => stopOperate());

  // ── Core activate/deactivate ──────────────────────────────────────────────
  function startOperate(): void {
    if (pollTimer !== null) return; // already running
    void loadWorkbenchConfig();
    ensureTelemetryLoaded();
    void refreshOperate();
    pollTimer = setInterval(() => void refreshOperate(), 5000);
    ensureEventStream();
  }

  function stopOperate(): void {
    if (pollTimer !== null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    closeEventStream();
    clearOperateData();
  }

  function clearOperateData(): void {
    statusSettled = false;
    devicesSettled = false;
    devices = [];
    selectedKey = "";
    deviceStates = {};
    capabilities = {};
    parameters = [];
    runtimeStatusData = null;
    automationStatus = null;
    automationFault = null;
    behaviorTree = "";
    modeSelectorDirty = false;
    streamStatus = { state: "disconnected", attempts: 0 };
    mode = "--";
    modeBadgeClass = "unknown";
    estop = null;
    startupSafeState = null;
    estopFeedback = "";
    estopFeedbackClass = "field-note";
    clearArmed = false;
    providerHealth = [];
    eventTrace = [];
    telemetryLoaded = false;
  }

  function ensureTelemetryLoaded(): void {
    const url = telemetryUrl();
    if (!telemetryFrame || telemetryLoaded || !url) return;
    telemetryFrame.src = url;
    telemetryLoaded = true;
  }

  async function loadWorkbenchConfig(): Promise<void> {
    try {
      const config = await fetchJson<WorkbenchConfig>("/api/config");
      workbenchConfig = config;
      const next = { ...(window.__ANOLIS_COMPOSER__ ?? {}) };
      if (typeof config.telemetry_url === "string" && config.telemetry_url.trim()) {
        next.telemetryUrl = config.telemetry_url.trim().replace(/\/$/, "");
      }
      window.__ANOLIS_COMPOSER__ = next;
      telemetryLoaded = false;
      ensureTelemetryLoaded();
    } catch {
      // non-fatal; leave telemetry panel in unavailable state when not configured
    }
  }

  // ── Poll ──────────────────────────────────────────────────────────────────
  async function refreshOperate(): Promise<void> {
    if (!projectName) return;
    let status: RuntimeStatus;
    try {
      status = await fetchJson<RuntimeStatus>("/api/status");
    } catch {
      mode = "--";
      modeBadgeClass = "unknown";
      return;
    }
    // Operate stopped (or unmounted) while this poll was in flight: opening the
    // event stream now would leave one that nothing closes (wb#551).
    if (pollTimer === null) return;
    const avail = deriveOperateAvailability(status, projectName);
    if (!avail.available) {
      mode = "--";
      modeBadgeClass = "unknown";
      clearOperateData();
      return;
    }

    ensureEventStream();
    ensureTelemetryLoaded();

    const epoch = estopEpoch;
    const [modeRes, phRes, devRes, rtRes, parRes, asRes, atRes] = await Promise.allSettled([
      fetchJson<UnknownRecord>("/v0/mode"),
      fetchJson<UnknownRecord>("/v0/providers/health"),
      fetchJson<UnknownRecord>("/v0/devices"),
      fetchJson<UnknownRecord>("/v0/runtime/status"),
      fetchJson<UnknownRecord>("/v0/parameters"),
      fetchJson<UnknownRecord>("/v0/automation/status"),
      fetchJson<UnknownRecord>("/v0/automation/tree"),
    ]);

    if (modeRes.status === "fulfilled") {
      const m = extractMode(modeRes.value) ?? "UNKNOWN";
      mode = m;
      modeBadgeClass = "ok";
      if (!modeSelectorDirty) modeSelectorValue = m;
    } else {
      mode = "UNKNOWN";
      modeBadgeClass = "unavailable";
    }

    providerHealth = phRes.status === "fulfilled" ? extractProvidersHealth(phRes.value) : [];

    devicesSettled = true;
    if (devRes.status === "fulfilled") {
      devices = extractDevices(devRes.value);
      if (devices.length && !selectedKey) {
        selectedKey = `${devices[0].provider_id}/${devices[0].device_id}`;
      }
      await ensureSelectedDeviceLoaded();
    } else {
      devices = [];
    }

    runtimeStatusData = rtRes.status === "fulfilled" ? extractRuntimeStatus(rtRes.value) : null;
    // Keep the last-known estop state on a transient status-poll failure so the
    // safety control never disappears mid-session (wb#236), and the state a
    // stop or clear reply reported since this poll left (wb#614).
    if (rtRes.status === "fulfilled" && epoch === estopEpoch)
      estop = extractEstopStatus(rtRes.value);
    statusSettled = true;
    startupSafeState =
      rtRes.status === "fulfilled" ? extractStartupSafeState(rtRes.value) : startupSafeState;
    parameters = parRes.status === "fulfilled" ? extractParameters(parRes.value) : [];
    automationStatus = asRes.status === "fulfilled" ? extractAutomationStatus(asRes.value) : null;
    behaviorTree = atRes.status === "fulfilled" ? extractAutomationTree(atRes.value) : "";
  }

  async function refreshParametersOnly(): Promise<void> {
    try {
      const p = await fetchJson<UnknownRecord>("/v0/parameters");
      parameters = extractParameters(p);
    } catch {
      /* best-effort */
    }
  }

  async function refreshProviderHealthOnly(): Promise<void> {
    try {
      const p = await fetchJson<UnknownRecord>("/v0/providers/health");
      providerHealth = extractProvidersHealth(p);
    } catch {
      /* best-effort */
    }
  }

  async function refreshModeOnly(): Promise<void> {
    try {
      const p = await fetchJson<UnknownRecord>("/v0/mode");
      const m = extractMode(p) ?? "UNKNOWN";
      mode = m;
      modeBadgeClass = "ok";
      if (!modeSelectorDirty) modeSelectorValue = m;
    } catch {
      mode = "UNKNOWN";
      modeBadgeClass = "unavailable";
    }
  }

  // ── Device detail ─────────────────────────────────────────────────────────
  async function ensureSelectedDeviceLoaded(): Promise<void> {
    if (!selectedKey || !devices.length) return;
    const dev = getSelectedDevice();
    if (!dev) return;
    // File each reply under the device it was requested for: the selection can
    // change while these requests are in flight (wb#552).
    const key = selectedKey;
    try {
      const vp = await fetchJson<UnknownRecord>(
        `/v0/state/${encodeURIComponent(dev.provider_id)}/${encodeURIComponent(dev.device_id)}`,
      );
      deviceStates = { ...deviceStates, [key]: extractDeviceStateValues(vp) };
    } catch {
      deviceStates = { ...deviceStates, [key]: [] };
    }
    if (!capabilities[key]) {
      try {
        const cp = await fetchJson<UnknownRecord>(
          `/v0/devices/${encodeURIComponent(dev.provider_id)}/${encodeURIComponent(dev.device_id)}/capabilities`,
        );
        capabilities = { ...capabilities, [key]: extractCapabilities(cp) };
      } catch {
        capabilities = { ...capabilities, [key]: { functions: [], signals: [] } };
      }
    }
  }

  function getSelectedDevice(): Device | null {
    if (!selectedKey) return null;
    return devices.find((d) => `${d.provider_id}/${d.device_id}` === selectedKey) ?? null;
  }

  async function selectDevice(key: string): Promise<void> {
    selectedKey = key;
    await ensureSelectedDeviceLoaded();
  }

  // ── Mode set ──────────────────────────────────────────────────────────────
  async function setMode(): Promise<void> {
    if (!modeSelectorValue) return;
    setModeRunning = true;
    modeFeedback = "";
    modeFeedbackClass = "field-note";
    try {
      await fetchJson<UnknownRecord>("/v0/mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: modeSelectorValue }),
      });
      modeSelectorDirty = false;
      modeFeedback = "";
      await refreshModeOnly();
    } catch (err) {
      modeFeedback = `Failed to set mode: ${err instanceof Error ? err.message : String(err)}`;
      modeFeedbackClass = "field-error";
    } finally {
      setModeRunning = false;
    }
  }

  // ── Software e-stop (wb#236) — thin trigger over POST /v0/estop ─────────────
  async function refreshEstopOnly(): Promise<void> {
    const epoch = estopEpoch;
    try {
      const p = await fetchJson<UnknownRecord>("/v0/runtime/status");
      if (epoch === estopEpoch) estop = extractEstopStatus(p);
    } catch {
      // Best-effort; the 5s status poll corrects the display shortly.
    }
  }

  async function triggerEstop(): Promise<void> {
    estopRunning = true;
    estopFeedback = "";
    estopFeedbackClass = "field-note";
    estopFeedbackFor = runningProject;
    try {
      const res = await fetchJson<UnknownRecord>("/v0/estop", { method: "POST" });
      const summary = summarizeEstopResponse(res);
      estopFeedback = summary.text;
      estopFeedbackClass = summary.ok ? "field-note" : "field-error";
      // Show the latch the reply reports now; the status read after it only
      // fills in the latch time (wb#553).
      if (res.latched === true) {
        estopEpoch += 1;
        estop = estop
          ? { ...estop, latched: true }
          : extractEstopStatus({
              estop: {
                latched: true,
                software_safe_state: res.software_safe_state,
                latched_at_epoch_ms: null,
              },
            });
      }
      estopRunning = false;
      void refreshEstopOnly();
    } catch (err) {
      estopFeedback = `Failed to engage software stop: ${err instanceof Error ? err.message : String(err)}. If the machine is not safe, use the hardware e-stop now.`;
      estopFeedbackClass = "field-error";
    } finally {
      estopRunning = false;
    }
  }

  async function clearEstop(): Promise<void> {
    // First click arms; second click actually clears (re-enables actuation).
    if (!clearArmed) {
      clearArmed = true;
      return;
    }
    clearRunning = true;
    estopFeedback = "";
    estopFeedbackClass = "field-note";
    try {
      const res = await fetchJson<UnknownRecord>("/v0/estop/clear", { method: "POST" });
      // Only claim success if the response confirms the latch actually released.
      if (res && (res as UnknownRecord).latched === true) {
        estopFeedback = "Clear requested, but the runtime still reports the latch engaged.";
        estopFeedbackClass = "field-error";
      } else {
        estopFeedback = "Latch cleared. Actuation re-enabled.";
        estopFeedbackClass = "field-note";
        estopEpoch += 1;
        if (estop) estop = { ...estop, latched: false, latched_at_epoch_ms: null };
      }
      clearRunning = false;
      clearArmed = false;
      void refreshEstopOnly();
    } catch (err) {
      estopFeedback = `Failed to clear latch: ${err instanceof Error ? err.message : String(err)}`;
      estopFeedbackClass = "field-error";
    } finally {
      clearRunning = false;
      clearArmed = false;
    }
  }

  // ── Function call ──────────────────────────────────────────────────────────
  let functionFeedback = $state<Record<string, { text: string; cls: string }>>({});

  async function callFunction(
    deviceKey: string,
    func: FunctionSpec,
    formData: Record<string, unknown>,
  ): Promise<void> {
    const fbKey = `${deviceKey}:${func.function_id}`;
    const [providerId, deviceId] = deviceKey.split("/");
    functionFeedback = { ...functionFeedback, [fbKey]: { text: "Executing...", cls: "" } };
    try {
      const argsPayload = buildArgsPayload(func, formData);
      await fetchJson<UnknownRecord>("/v0/call", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider_id: providerId,
          device_id: deviceId,
          function_id: func.function_id,
          args: argsPayload,
        }),
      });
      // The result stays beside its form until the next call (#580).
      functionFeedback = {
        ...functionFeedback,
        [fbKey]: { text: `Accepted at ${new Date().toLocaleTimeString()}`, cls: "success" },
      };
    } catch (err) {
      functionFeedback = {
        ...functionFeedback,
        [fbKey]: {
          text: `Error: ${err instanceof Error ? err.message : String(err)}`,
          cls: "error",
        },
      };
    }
  }

  function buildArgsPayload(
    func: FunctionSpec,
    formData: Record<string, unknown>,
  ): Record<string, TypedValue> {
    const INT64_MIN = -9223372036854775808n;
    const INT64_MAX = 9223372036854775807n;
    const JS_SAFE_MAX = BigInt(Number.MAX_SAFE_INTEGER);
    const args: Record<string, TypedValue> = {};
    for (const [i, argDef] of (func.args ?? []).entries()) {
      const argType = argDef.type || "string";
      const raw = formData[`arg_${i}`];
      if (argType === "bool") {
        args[argDef.name] = { type: "bool", bool: Boolean(raw) };
        continue;
      }
      const value = typeof raw === "string" ? raw.trim() : "";
      if (value === "") {
        if (argDef.required !== false) throw new Error(`Missing required argument: ${argDef.name}`);
        continue;
      }
      if (argType === "double") {
        const n = Number(value);
        if (isNaN(n)) throw new Error(`Invalid double: ${argDef.name}`);
        args[argDef.name] = { type: "double", double: n };
        continue;
      }
      if (argType === "int64") {
        let n: bigint;
        try {
          n = BigInt(value);
        } catch {
          throw new Error(`Invalid int64: ${argDef.name}`);
        }
        if (n < INT64_MIN || n > INT64_MAX) throw new Error(`Out-of-range int64: ${argDef.name}`);
        if (n > JS_SAFE_MAX || n < -JS_SAFE_MAX) {
          throw new Error(`int64 out of browser-safe range: ${argDef.name}`);
        }
        args[argDef.name] = { type: "int64", int64: Number(n) };
        continue;
      }
      if (argType === "uint64") {
        const UINT64_MAX = 18446744073709551615n;
        let n: bigint;
        try {
          n = BigInt(value);
        } catch {
          throw new Error(`Invalid uint64: ${argDef.name}`);
        }
        if (n < 0n || n > UINT64_MAX) throw new Error(`Out-of-range uint64: ${argDef.name}`);
        if (n > JS_SAFE_MAX) throw new Error(`uint64 out of browser-safe range: ${argDef.name}`);
        args[argDef.name] = { type: "uint64", uint64: Number(n) };
        continue;
      }
      if (argType === "bytes") {
        args[argDef.name] = { type: "bytes", base64: value };
        continue;
      }
      args[argDef.name] = { type: "string", string: value };
    }
    return args;
  }

  // ── Parameter update ──────────────────────────────────────────────────────
  let paramFeedback = $state<Record<string, { text: string; cls: string }>>({});

  async function updateParameter(param: ParameterDefinition, rawValue: unknown): Promise<void> {
    const type = normalizeParameterType(param.type);
    paramFeedback = { ...paramFeedback, [param.name]: { text: "", cls: "" } };
    try {
      const value = coerceParameterValue({
        type,
        rawValue,
        min: param.min,
        max: param.max,
        allowedValues: param.allowed_values,
      });
      await fetchJson<UnknownRecord>("/v0/parameters", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: param.name, value }),
      });
      paramFeedback = { ...paramFeedback, [param.name]: { text: "Updated", cls: "success" } };
      setTimeout(() => {
        paramFeedback = { ...paramFeedback, [param.name]: { text: "", cls: "" } };
      }, 2000);
      await refreshParametersOnly();
    } catch (err) {
      paramFeedback = {
        ...paramFeedback,
        [param.name]: {
          text: `Error: ${err instanceof Error ? err.message : String(err)}`,
          cls: "error",
        },
      };
    }
  }

  // ── SSE Event stream ──────────────────────────────────────────────────────
  function ensureEventStream(): void {
    if (streamManager) return;
    streamManager = createOperateEventStreamManager({
      url: "/v0/events",
      onEvent: handleSseEvent,
      onConnectionStatus: (s) => {
        const prev = streamStatus.state;
        streamStatus = s;
        if (s.state !== prev) {
          const details =
            s.state === "reconnecting"
              ? `Reconnecting in ${Math.max(1, Math.ceil((s.delay_ms || 0) / 1000))}s`
              : s.state === "stale"
                ? `Stale for ${Math.floor((s.idle_ms || 0) / 1000)}s`
                : s.state === "connected"
                  ? "Connected"
                  : "Disconnected";
          addTraceEvent("stream_status", { timestamp_ms: Date.now() }, details);
        }
      },
      onParseError: (err, et) =>
        addTraceEvent(
          "stream_error",
          { timestamp_ms: Date.now() },
          `${et}: ${err instanceof Error ? err.message : String(err)}`,
        ),
    });
    streamManager.connect();
  }

  function closeEventStream(): void {
    if (streamManager) {
      streamManager.disconnect();
      streamManager = null;
    }
    streamStatus = { state: "disconnected", attempts: 0 };
  }

  function handleSseEvent(eventType: string, payload: UnknownRecord): void {
    if (eventType === "state_update") consumeStateEvent(payload);
    else if (eventType === "quality_change") consumeQualityEvent(payload);
    else if (eventType === "mode_change") consumeModeChangeEvent(payload);
    else if (eventType === "parameter_change") void refreshParametersOnly();
    else if (eventType === "automation_fault") consumeAutomationFaultEvent(payload);
    else if (eventType === "provider_health_change") void refreshProviderHealthOnly();
    addTraceEvent(eventType, payload);
  }

  function addTraceEvent(
    type: string,
    payload: UnknownRecord,
    detailsOverride: string | null = null,
  ): void {
    const ev = buildTraceEvent(type, payload, Date.now());
    if (detailsOverride) ev.details = detailsOverride;
    const buf = [...eventTrace];
    appendEventTrace(buf, ev, EVENT_TRACE_LIMIT);
    eventTrace = buf;
  }

  function consumeStateEvent(payload: UnknownRecord): void {
    const providerId = typeof payload.provider_id === "string" ? payload.provider_id : "";
    const deviceId = typeof payload.device_id === "string" ? payload.device_id : "";
    const signalId = typeof payload.signal_id === "string" ? payload.signal_id : "";
    if (!providerId || !deviceId || !signalId) return;
    const key = `${providerId}/${deviceId}`;
    const values = [...(deviceStates[key] ?? [])];
    const idx = values.findIndex((e) => e.signal_id === signalId);
    const normalized = {
      ...payload,
      signal_id: signalId,
      timestamp_ms: Number(payload.timestamp_ms) || Number(payload.timestamp_epoch_ms) || 0,
    } as DeviceStateValue;
    if (idx >= 0) values[idx] = normalized;
    else values.push(normalized);
    deviceStates = { ...deviceStates, [key]: values };
  }

  function consumeQualityEvent(payload: UnknownRecord): void {
    const providerId = typeof payload.provider_id === "string" ? payload.provider_id : "";
    const deviceId = typeof payload.device_id === "string" ? payload.device_id : "";
    const signalId = typeof payload.signal_id === "string" ? payload.signal_id : "";
    if (!providerId || !deviceId || !signalId) return;
    const key = `${providerId}/${deviceId}`;
    const values = deviceStates[key];
    if (!values) return;
    const updated = values.map((e) =>
      e.signal_id === signalId ? { ...e, quality: payload.new_quality as string } : e,
    );
    deviceStates = { ...deviceStates, [key]: updated };
  }

  function consumeModeChangeEvent(payload: UnknownRecord): void {
    const m = typeof payload.new_mode === "string" ? payload.new_mode : "UNKNOWN";
    mode = m;
    modeBadgeClass = "ok";
    if (!modeSelectorDirty) modeSelectorValue = m;
  }

  function consumeAutomationFaultEvent(payload: UnknownRecord): void {
    // A fault is an event, not a status. Record it for display but never
    // fabricate an execution_status — the polled /v0/automation/status is the
    // single source of truth for the engine's state.
    const errorText =
      typeof payload.error === "string" && payload.error.trim() !== ""
        ? payload.error
        : "Unknown automation fault";
    automationFault = {
      message: errorText,
      timestamp_ms: Number(payload.timestamp_ms) || Date.now(),
    };
  }

  // ── Formatters ────────────────────────────────────────────────────────────
  function formatAutomationVersion(version: AutomationStatus["automation_version"]): string {
    if (!version || !version.engine_kind) return "none loaded";
    const digest = version.digest ? ` ${version.digest.slice(0, 12)}` : "";
    return `${version.engine_kind}${digest}`;
  }

  function formatEpochMs(ms: number | null): string {
    if (ms === null || !Number.isFinite(ms) || ms <= 0) return "--";
    return new Date(ms).toLocaleTimeString();
  }

  function formatValue(v: unknown): string {
    if (v === null || v === undefined) return "--";
    if (typeof v === "object") {
      const value = v as UnknownRecord;
      if (typeof value.type === "string") {
        if (value.type === "double" && value.double !== undefined) return String(value.double);
        if (value.type === "int64" && value.int64 !== undefined) return String(value.int64);
        if (value.type === "uint64" && value.uint64 !== undefined) return String(value.uint64);
        if (value.type === "bool" && value.bool !== undefined) return value.bool ? "true" : "false";
        if (value.type === "string" && value.string !== undefined) return String(value.string);
        if (value.type === "bytes" && value.base64 !== undefined) return String(value.base64);
      }
      return JSON.stringify(v);
    }
    return String(v);
  }

  function formatBtOutline(tree: string): string {
    if (!tree || !tree.trim()) return "No behavior tree loaded.";
    try {
      const parser = new DOMParser();
      const xmlDoc = parser.parseFromString(tree, "text/xml");
      if (xmlDoc.querySelector("parsererror")) return tree;
      return renderBtOutline(xmlDoc);
    } catch {
      return tree;
    }
  }

  // Function form state keyed by deviceKey:functionId
  let funcFormData = $state<Record<string, Record<string, unknown>>>({});

  // When the software stop latches, clear the forms of actuating functions,
  // so one Execute after the clear cannot resend the last values (#580). The
  // runtime's `actuating` flag decides: it is true unless the category is
  // READ, so CONFIG counts too, which it must, since bread's setpoints are
  // CONFIG. Only a form marked actuating: false keeps its values; a missing
  // flag counts as actuating.
  let wasLatched = false;
  $effect(() => {
    const latched = estop?.latched === true;
    if (latched && !wasLatched) {
      const next = { ...funcFormData };
      for (const [deviceKey, caps] of Object.entries(capabilities)) {
        for (const func of caps.functions ?? []) {
          if (func.actuating !== false) delete next[getFuncFormKey(deviceKey, func)];
        }
      }
      funcFormData = next;
    }
    wasLatched = latched;
  });
  function getFuncFormKey(deviceKey: string, func: FunctionSpec): string {
    return `${deviceKey}:${func.function_id}`;
  }
  function getFuncFormData(deviceKey: string, func: FunctionSpec): Record<string, unknown> {
    return funcFormData[getFuncFormKey(deviceKey, func)] ?? {};
  }
  function setFuncArg(deviceKey: string, func: FunctionSpec, argIdx: number, value: unknown): void {
    const k = getFuncFormKey(deviceKey, func);
    funcFormData = {
      ...funcFormData,
      [k]: { ...(funcFormData[k] ?? {}), [`arg_${argIdx}`]: value },
    };
  }

  // Parameter input state
  let paramInputs = $state<Record<string, string>>({});
  function getParamInput(name: string): string {
    return paramInputs[name] ?? "";
  }
  function setParamInput(name: string, value: string): void {
    paramInputs = { ...paramInputs, [name]: value };
  }
</script>

{#snippet safeStateFix()}
  <span class="field-note" data-testid="safe-state-fix">
    Declared under <code>safety.safe_state</code> in the project's
    <code>{safeStateFile}</code> for dev-launch, or in
    <code>/opt/anolis/config/runtime.yaml</code> on an installed target, which survives redeploys.
  </span>
{/snippet}

<section id="workspace-operate" class="workspace visible">
  <div class="workspace-header">
    <h2>Operate</h2>
  </div>

  {#if variantMismatch}
    <div class="workspace-advisory" data-testid="launch-variant-mismatch">{variantMismatch}</div>
  {/if}

  {#if !available}
    <div class="workspace-advisory">
      {#if running && runningProject}
        Runtime is running for "{runningProject}", not this project.
        <!-- The /v0 proxy targets the running project's runtime, so its stop
             stays reachable here (#264). Clearing the latch belongs to that
             project's own Operate view. -->
        <!-- The stop stays offered whatever the latch reads: stopping a latched
             runtime is harmless, and a stale read must never hide it (wb#553). -->
        {#if estop?.latched}
          <div class="estop-latched-banner" data-testid="other-project-latch">
            <span class="estop-latched-title">SAFE-STATE ENGAGED — actuation latched</span>
            <span class="estop-latched-meta">
              Latched at {formatEpochMs(estop.latched_at_epoch_ms)} on the runtime running "{runningProject}".
              Clear it from
              <a
                href={`/projects/${encodeURIComponent(runningProject)}/operate`}
                onclick={(e: MouseEvent) => {
                  if (!onNavigate) return;
                  e.preventDefault();
                  onNavigate(`/projects/${encodeURIComponent(runningProject)}/operate`);
                }}>{runningProject}'s Operate view</a
              >.
            </span>
          </div>
        {/if}
        <div class="estop-row">
          <button type="button" class="btn-estop" disabled={estopRunning} onclick={triggerEstop}>
            {estopRunning
              ? "ENGAGING…"
              : workbenchConfig?.runtime_url_override
                ? "Software stop: configured runtime URL"
                : `Software stop: ${runningProject}`}
          </button>
        </div>
        <div class="estop-disclaimer">
          Not an emergency stop. For any physical hazard use the hardware emergency stop.
        </div>
        {#if estopFeedback && estopFeedbackFor === runningProject}
          <div class={estopFeedbackClass}>{estopFeedback}</div>
        {/if}
      {:else}
        Runtime is not running for this project.
        <!-- Until Operate can pick a deployed runtime itself (#510), name the
             two ways to reach one on another machine. -->
        <div class="field-note" data-testid="operate-remote-routes">
          To operate a runtime deployed to another machine, either:
          <ul>
            <li>
              run the workbench on that machine and reach it over an ssh tunnel:
              <code>ssh -L 3010:127.0.0.1:3010 &lt;user&gt;@&lt;device&gt;</code>; or
            </li>
            <li>
              tunnel the runtime's port (<code
                >ssh -L 8080:127.0.0.1:8080 &lt;user&gt;@&lt;device&gt;</code
              >) and start this workbench with
              <code>ANOLIS_WORKBENCH_RUNTIME_URL=http://127.0.0.1:8080</code>. A runtime with
              <code>auth_enabled: true</code> also needs
              <code>ANOLIS_WORKBENCH_RUNTIME_TOKEN</code>, the <code>ANOLIS_API_TOKEN</code> in
              <code>/etc/anolis/runtime.env</code> on an installed device.
            </li>
          </ul>
        </div>
      {/if}
    </div>
  {:else}
    <!-- Software stop (wb#236) — thin trigger over POST /v0/estop. NOT an
         emergency stop: it commands devices that stay powered. -->
    <div class="operate-section operate-estop-section">
      <h3>Software Stop</h3>
      {#if !estop}
        <!-- POST /v0/estop needs no prior status, so the stop is offered before
             the first status reply and when the runtime never reports its rung
             (wb#553). -->
        <div class="estop-row">
          <button type="button" class="btn-estop" disabled={estopRunning} onclick={triggerEstop}>
            {estopRunning ? "ENGAGING…" : "SOFTWARE STOP"}
          </button>
          <div class="estop-info">
            <p class="placeholder">
              {statusSettled
                ? "Software safe-state: not observed. The runtime has not reported it."
                : "Software safe-state: loading the runtime's status…"}
            </p>
            {@render safeStateFix()}
          </div>
        </div>
      {:else if estop.latched}
        <div class="estop-latched-banner">
          <span class="estop-latched-title">SAFE-STATE ENGAGED — actuation latched</span>
          <span class="estop-latched-meta">
            Latched at {formatEpochMs(estop.latched_at_epoch_ms)}. Actuating commands are refused
            until cleared.
          </span>
          <div class="estop-clear-row">
            {#if clearArmed}
              <button
                type="button"
                class="btn-secondary btn-sm"
                disabled={clearRunning}
                onclick={clearEstop}
              >
                {clearRunning ? "Clearing…" : "Confirm clear — re-enables actuation"}
              </button>
              <button
                type="button"
                class="btn-secondary btn-sm"
                disabled={clearRunning}
                onclick={() => (clearArmed = false)}
              >
                Cancel
              </button>
            {:else}
              <button type="button" class="btn-secondary btn-sm" onclick={clearEstop}>
                Clear latch…
              </button>
            {/if}
          </div>
        </div>
      {:else}
        <div class="estop-row">
          <button type="button" class="btn-estop" disabled={estopRunning} onclick={triggerEstop}>
            {estopRunning
              ? "ENGAGING…"
              : estop.software_safe_state === "none"
                ? "SOFTWARE STOP (latch only)"
                : "SOFTWARE STOP"}
          </button>
          <div class="estop-info">
            {#if estop.software_safe_state === "hooks"}
              <span class="field-note"
                >Latches actuation, then runs the declared safe-state hooks.</span
              >
            {:else if estop.software_safe_state === "setpoints"}
              <span class="field-note"
                >Latches actuation, then drives the declared safe setpoints.</span
              >
            {:else if estop.software_safe_state === "zero"}
              <span class="field-note"
                >The project declares zero_is_safe: latches actuation, then tries to drive every
                actuating function to zero.</span
              >
            {:else}
              <span class="field-error">
                No software safe-state is declared for this machine. The e-stop drives nothing: it
                only latches actuation (blocks new commands). Use the hardware e-stop to make the
                machine safe.
              </span>
            {/if}
            <span class="field-note">
              As reported by {runtimeStatusData?.version
                ? `runtime v${runtimeStatusData.version}`
                : "the runtime"}.
            </span>
            {@render safeStateFix()}
            {#if dispatchableCoverage}
              <!-- The functions the active rung will not drive. Under none it
                   drives nothing, which the note above already says. -->
              {#if estop.software_safe_state !== "none" && estop.uncovered_actuating_functions > 0}
                <span class="field-error">
                  {estop.uncovered_actuating_functions} actuating function(s) the e-stop will not drive.
                </span>
              {/if}
            {:else if estop.software_safe_state === "none" && estop.uncovered_actuating_functions > 0}
              <!-- An older runtime counts functions with no matching setpoint,
                   which is only meaningful as the reason setpoints were not chosen. -->
              <span class="field-error">
                {estop.uncovered_actuating_functions} actuating function(s) have no declared safe setpoint,
                which is why setpoints are not used.
              </span>
            {/if}
          </div>
        </div>
      {/if}
      {#if startupSafeState}
        <!-- What safety.on_startup did when this runtime started (#483). The
             verdict is the runtime's; a runtime older than 0.1.45 omits it. -->
        {@const startup = summarizeStartupSafeState(startupSafeState)}
        <div class={startup.ok ? "field-note" : "field-error"} data-testid="startup-safe-state">
          {startup.text}
        </div>
      {/if}
      {#if estopFeedback}
        <div class={estopFeedbackClass}>{estopFeedback}</div>
      {/if}
      <div class="estop-disclaimer">
        Not an emergency stop — it commands devices that stay powered, and needs the runtime,
        providers and bus all working. For any physical hazard use the hardware emergency stop. If
        in doubt, isolate power to the machine.
      </div>
    </div>

    <!-- Mode control -->
    <div class="operate-section operate-mode-section">
      <h3>Mode</h3>
      <div class="mode-row">
        <span>Current: <span class="badge {modeBadgeClass}">{mode}</span></span>
        <select
          value={modeSelectorValue}
          onchange={(e: Event) => {
            modeSelectorValue = selectTarget(e).value;
            modeSelectorDirty = true;
          }}
        >
          {#each RUNTIME_MODES as runtimeMode (runtimeMode)}
            <option value={runtimeMode}>{runtimeMode}</option>
          {/each}
        </select>
        <button
          type="button"
          class="btn-secondary btn-sm"
          disabled={setModeRunning}
          onclick={setMode}
        >
          {setModeRunning ? "Setting…" : "Set Mode"}
        </button>
        {#if modeFeedback}
          <span class={modeFeedbackClass}>{modeFeedback}</span>
        {/if}
      </div>
    </div>

    <!-- Runtime status -->
    <div class="operate-section">
      <h3>Runtime Status</h3>
      {#if runtimeStatusData}
        {@const health = summarizeRuntimeHealth(runtimeStatusData, estop, startupSafeState)}
        <span class="badge {health.cls}" data-testid="runtime-health">{health.label}</span>
        {#if health.reasons.length > 0}
          <span class="operate-meta">{health.reasons.join("; ")}.</span>
        {/if}
        <span class="operate-meta">
          Mode {runtimeStatusData.mode} | Devices {runtimeStatusData.device_count} | Poll {runtimeStatusData.polling_interval_ms}ms
          | Uptime {runtimeStatusData.uptime_seconds}s
        </span>
      {:else}
        <span class="badge unavailable">UNAVAILABLE</span>
        <span class="operate-meta">Runtime status unavailable.</span>
      {/if}
    </div>

    <!-- Event stream status -->
    <div class="operate-section">
      <h3>Event Stream</h3>
      {#if streamStatus.state === "connected"}
        <span class="badge ok">CONNECTED</span> <span class="operate-meta">SSE stream active.</span>
      {:else if streamStatus.state === "reconnecting"}
        <span class="badge unavailable">RECONNECTING</span>
        <span class="operate-meta"
          >Attempt {streamStatus.attempts}, retry in {Math.max(
            1,
            Math.ceil((streamStatus.delay_ms || 0) / 1000),
          )}s.</span
        >
      {:else if streamStatus.state === "stale"}
        <span class="badge stale">STALE</span>
        <span class="operate-meta"
          >No events for {Math.floor((streamStatus.idle_ms || 0) / 1000)}s.</span
        >
      {:else}
        <span class="badge unavailable">DISCONNECTED</span>
        <span class="operate-meta">Stream disconnected.</span>
      {/if}
    </div>

    <!-- Provider health -->
    <div class="operate-section">
      <h3>Provider Health</h3>
      <ul class="operate-provider-health">
        {#if providerHealth.length === 0}
          <li class="placeholder">No provider health available.</li>
        {:else}
          {#each providerHealth as entry (entry.provider_id)}
            {@const pid = typeof entry?.provider_id === "string" ? entry.provider_id : "unknown"}
            {@const q = normalizeProviderHealthQuality(entry?.state || "UNKNOWN")}
            {@const lc = typeof entry?.lifecycle_state === "string" ? entry.lifecycle_state : "--"}
            {@const dc = Number.isFinite(Number(entry?.device_count))
              ? Number(entry.device_count)
              : 0}
            <li>
              {pid} <span class="badge {q.toLowerCase()}">{q}</span>
              <div class="provider-row-meta">{lc} | {dc} device(s)</div>
            </li>
          {/each}
        {/if}
      </ul>
    </div>

    <!-- Devices -->
    <div class="operate-section operate-devices-layout">
      <div class="operate-device-list-col">
        <h3>Devices</h3>
        <ul class="operate-list">
          {#if !devicesSettled}
            <li class="placeholder">Loading devices…</li>
          {:else if devices.length === 0}
            <li class="placeholder">No devices found.</li>
          {:else}
            {#each devices as device (`${device.provider_id}/${device.device_id}`)}
              {@const key = `${device.provider_id}/${device.device_id}`}
              <li class:selected={key === selectedKey}>
                <button
                  type="button"
                  class="operate-device-select"
                  onclick={() => selectDevice(key)}
                >
                  {device.display_name || device.device_id || key} ({key})
                </button>
              </li>
            {/each}
          {/if}
        </ul>
      </div>

      <div class="operate-device-detail-col">
        <h3 id="device-title">
          {#if selectedKey && getSelectedDevice()}
            {getSelectedDevice()?.display_name || getSelectedDevice()?.device_id} ({selectedKey})
          {:else}
            Device Detail
          {/if}
        </h3>

        {#if !selectedKey || !getSelectedDevice()}
          <p class="placeholder">Select a device to inspect signals and execute functions.</p>
        {:else}
          {@const signals = deviceStates[selectedKey] ?? []}
          {@const capsLoaded = capabilities[selectedKey] !== undefined}
          {@const caps = capabilities[selectedKey] ?? { functions: [] }}

          {#if signals.length === 0}
            <p class="placeholder">No signals available.</p>
          {:else}
            <table class="signal-table">
              <thead><tr><th>Signal</th><th>Value</th><th>Quality</th><th>Timestamp</th></tr></thead
              >
              <tbody>
                {#each signals as sig (sig.signal_id)}
                  <tr>
                    <td>{sig.signal_id || "-"}</td>
                    <td>{formatValue(sig.value)}</td>
                    <td>{sig.quality ?? "--"}</td>
                    <td
                      >{(sig.timestamp_ms ?? 0) > 0
                        ? new Date(sig.timestamp_ms ?? 0).toLocaleTimeString()
                        : "--"}</td
                    >
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}

          {#if !capsLoaded}
            <p class="placeholder">Loading functions…</p>
          {:else if caps.functions?.length === 0}
            <p class="placeholder">No callable functions declared.</p>
          {:else}
            {#each caps.functions as func (func.function_id)}
              {@const fbKey = `${selectedKey}:${func.function_id}`}
              <div class="function-card">
                <h4>{func.display_name || func.name || `Function ${func.function_id}`}</h4>
                {#if func.description}
                  <p class="function-description">{func.description}</p>
                {/if}
                <form
                  onsubmit={(e: Event) => {
                    e.preventDefault();
                    void callFunction(selectedKey, func, getFuncFormData(selectedKey, func));
                  }}
                >
                  {#each func.args ?? [] as arg, ai (ai)}
                    <div class="arg-row">
                      <label for="fn-{fbKey}-{ai}">
                        {arg.name}{arg.required !== false ? " *" : ""}
                        {#if arg.min !== undefined && arg.max !== undefined}
                          <span class="constraint-hint">[{arg.min} - {arg.max}]</span>
                        {/if}
                      </label>
                      {#if arg.type === "bool"}
                        <input
                          id="fn-{fbKey}-{ai}"
                          type="checkbox"
                          checked={Boolean(getFuncFormData(selectedKey, func)[`arg_${ai}`])}
                          onchange={(e: Event) =>
                            setFuncArg(selectedKey, func, ai, inputTarget(e).checked)}
                        />
                      {:else if arg.type === "double"}
                        <input
                          id="fn-{fbKey}-{ai}"
                          type="number"
                          step="any"
                          value={getFuncFormData(selectedKey, func)[`arg_${ai}`] ?? ""}
                          oninput={(e: Event) =>
                            setFuncArg(selectedKey, func, ai, inputTarget(e).value)}
                        />
                      {:else}
                        <input
                          id="fn-{fbKey}-{ai}"
                          type="text"
                          inputmode={arg.type === "int64" || arg.type === "uint64"
                            ? "numeric"
                            : "text"}
                          placeholder={arg.type === "bytes" ? "Base64 encoded" : ""}
                          value={getFuncFormData(selectedKey, func)[`arg_${ai}`] ?? ""}
                          oninput={(e: Event) =>
                            setFuncArg(selectedKey, func, ai, inputTarget(e).value)}
                        />
                      {/if}
                    </div>
                  {/each}
                  <div class="function-actions">
                    <button type="submit" class="btn-secondary btn-sm">Execute</button>
                    {#if functionFeedback[fbKey]?.text}
                      <span class="function-feedback {functionFeedback[fbKey].cls}"
                        >{functionFeedback[fbKey].text}</span
                      >
                    {/if}
                  </div>
                </form>
              </div>
            {/each}
          {/if}
        {/if}
      </div>
    </div>

    <!-- Parameters -->
    <div class="operate-section">
      <h3>Parameters</h3>
      {#if parameters.length === 0}
        <p class="placeholder">No parameters available.</p>
      {:else}
        <div class="parameters-grid">
          {#each parameters as param (param.name)}
            {@const ptype = normalizeParameterType(param.type)}
            <div class="parameter-card">
              <div class="parameter-header">
                <span class="parameter-name">{param.name}</span>
                <span class="parameter-type">{ptype ?? String(param.type)}</span>
              </div>
              <div class="parameter-value"><strong>{String(param.value)}</strong></div>
              {#if param.min !== undefined || param.max !== undefined}
                <div class="parameter-range">
                  Range: [{param.min ?? "-inf"}, {param.max ?? "+inf"}]
                </div>
              {/if}
              {#if ptype}
                <div class="parameter-controls">
                  {#if ptype === "bool"}
                    <select
                      value={getParamInput(param.name) || String(param.value).toLowerCase()}
                      onchange={(e: Event) => setParamInput(param.name, selectTarget(e).value)}
                    >
                      <option value="true">true</option>
                      <option value="false">false</option>
                    </select>
                  {:else if ptype === "string" && Array.isArray(param.allowed_values) && param.allowed_values.length > 0}
                    <select
                      value={getParamInput(param.name) || String(param.value)}
                      onchange={(e: Event) => setParamInput(param.name, selectTarget(e).value)}
                    >
                      {#each param.allowed_values as av (av)}
                        <option value={String(av)}>{String(av)}</option>
                      {/each}
                    </select>
                  {:else if ptype === "double"}
                    <input
                      type="number"
                      step="any"
                      placeholder="New value"
                      value={getParamInput(param.name)}
                      oninput={(e: Event) => setParamInput(param.name, inputTarget(e).value)}
                    />
                  {:else}
                    <input
                      type="text"
                      placeholder="New value"
                      value={getParamInput(param.name)}
                      oninput={(e: Event) => setParamInput(param.name, inputTarget(e).value)}
                    />
                  {/if}
                  <button
                    type="button"
                    class="btn-secondary btn-sm"
                    onclick={() => updateParameter(param, getParamInput(param.name))}>Set</button
                  >
                  {#if paramFeedback[param.name]?.text}
                    <span class="feedback {paramFeedback[param.name].cls}"
                      >{paramFeedback[param.name].text}</span
                    >
                  {/if}
                </div>
              {:else}
                <span class="feedback error">Unsupported parameter type: {String(param.type)}</span>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    </div>

    <!-- Automation status -->
    <div class="operate-section">
      <h3>Automation</h3>
      {#if !automationStatus}
        <p class="placeholder">Automation status unavailable.</p>
      {:else}
        {@const execStatus = automationStatus.execution_status}
        <div class="automation-status-grid">
          <span
            >Status: <span class="badge exec-{execStatus}">{execStatus.toUpperCase()}</span></span
          >
          {#if automationStatus.execution_reason}
            <span>Reason: {automationStatus.execution_reason}</span>
          {/if}
          <span>Run: {automationStatus.run_id ?? "--"}</span>
          <span>Definition: {formatAutomationVersion(automationStatus.automation_version)}</span>
          <span>Last evaluation: {formatEpochMs(automationStatus.last_evaluation_at_epoch_ms)}</span
          >
          {#if automationStatus.last_error}
            <span
              class="error-text {execStatus === 'failed'
                ? 'alarm'
                : execStatus === 'blocked'
                  ? 'warning'
                  : ''}"
            >
              Last error: {automationStatus.last_error}
            </span>
          {/if}
          {#if automationFault}
            <span class="error-text alarm">
              Fault ({new Date(automationFault.timestamp_ms).toLocaleTimeString()}): {automationFault.message}
            </span>
          {/if}
        </div>
        <pre class="bt-viewer">{formatBtOutline(behaviorTree)}</pre>
      {/if}
    </div>

    <!-- Event trace -->
    <div class="operate-section">
      <h3>Event Trace</h3>
      {#if eventTrace.length === 0}
        <p class="placeholder">No events yet.</p>
      {:else}
        <div class="event-items">
          {#each [...eventTrace].reverse() as ev (ev.timestamp_ms)}
            {@const ts = Number.isFinite(Number(ev.timestamp_ms))
              ? new Date(Number(ev.timestamp_ms)).toLocaleTimeString()
              : "--"}
            {@const typeClass = String(ev.type || "event").replaceAll("_", "-")}
            <div class="event-item {typeClass}">
              <span class="event-time">{ts}</span>
              <span class="event-type">{ev.type}</span>
              <span class="event-details">{ev.details}</span>
            </div>
          {/each}
        </div>
      {/if}
    </div>

    <!-- Telemetry iframe -->
    <div class="operate-section">
      <h3>Telemetry</h3>
      {#if telemetryUrl()}
        <div class="telemetry-bar">
          <a href={telemetryUrl()} target="_blank" rel="noopener noreferrer"
            >Open Grafana in new tab →</a
          >
        </div>
        <iframe
          bind:this={telemetryFrame}
          title="Telemetry"
          class="telemetry-frame"
          frameborder="0"
          allowfullscreen
        ></iframe>
      {:else}
        <p class="placeholder">Telemetry URL is not configured.</p>
      {/if}
    </div>
  {/if}
</section>
