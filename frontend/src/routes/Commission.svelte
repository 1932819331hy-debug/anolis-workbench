<script lang="ts">
  import { onDestroy } from "svelte";
  import {
    ApiResponseError,
    downloadBlob,
    fetchJson,
    fetchResponse,
    filenameFromContentDisposition,
  } from "../lib/api";
  import {
    isImportedDoc,
    type PreflightResult,
    type ProjectDoc,
    type ProviderHealth,
    type RuntimeApiStatus,
    type RuntimeStatus,
  } from "../lib/contracts";
  import { activeVariant, inertnessViolation, MANUAL_VARIANT } from "../lib/canonical";

  let {
    projectName,
    system,
    runtimeStatus,
    commissionRunningForCurrent,
  }: {
    projectName: string | null;
    system: ProjectDoc | null;
    runtimeStatus: RuntimeStatus | null;
    commissionRunningForCurrent: boolean;
  } = $props();

  // Imported projects are deploy-only (#226): no dev-launch, no .anpkg export.
  const imported = $derived(isImportedDoc(system));

  // Every project declares runtime variants now (#255). The bundle carries ALL
  // of them: install.sh's bundle assembly always stages the inert `manual`
  // variant and ignores --variant, so the choice is made at install time on the
  // target. Offering a picker here would claim otherwise; Deploy to device
  // below is where a variant is chosen (#275).
  const variants = $derived(Object.keys(system?.profile?.runtime_profiles ?? {}).sort());

  /** The HTTP port dev-launch serves on, from the variant it will run. */
  const launchPort = $derived(system?.variants?.[activeVariant(system)]?.http?.port ?? null);

  const running = $derived(Boolean(runtimeStatus?.running));
  const runningProject = $derived(
    typeof runtimeStatus?.active_project === "string" ? runtimeStatus.active_project : null,
  );
  const runningForOther = $derived(
    running && Boolean(runningProject) && runningProject !== projectName,
  );

  // ── Launch state ───────────────────────────────────────────────────────────
  let preflightResults = $state<PreflightResult | null>(null);
  let preflightRunning = $state<boolean>(false);
  let launchRunning = $state<boolean>(false);
  let stopRunning = $state<boolean>(false);
  let restartRunning = $state<boolean>(false);
  let actionError = $state<string>("");

  // ── Health badge ──────────────────────────────────────────────────────────
  let healthStatus = $state<"starting" | "healthy" | "unavailable">("starting");
  let healthTimer = $state<ReturnType<typeof setInterval> | null>(null);
  let healthStartedAt = $state<number>(0);

  // ── Log pane ──────────────────────────────────────────────────────────────
  let logLines = $state<string[]>([]);
  let logVisible = $state<boolean>(false);
  let logAutoScroll = true;
  let logEventSource: EventSource | null = null;
  let logEl = $state<HTMLDivElement | null>(null);

  // ── Commission health panel ───────────────────────────────────────────────
  let commissionRuntimeStatus = $state<RuntimeApiStatus | null>(null);
  let commissionProviderHealth = $state<ProviderHealth[]>([]);
  let commissionHealthError = $state<string>("");
  let commissionHealthTimer = $state<ReturnType<typeof setInterval> | null>(null);

  // ── Export ────────────────────────────────────────────────────────────────
  let exportRunning = $state<boolean>(false);
  let exportFeedback = $state<string>("");
  let exportIsError = $state<boolean>(false);

  // ── Bundle Export ─────────────────────────────────────────────────────────
  let bundleRunning = $state<boolean>(false);
  let bundleFeedback = $state<string>("");
  let bundleIsError = $state<boolean>(false);
  // The two architectures release assets exist for. The default is the Pi
  // target, labelled as such, with the alternative always visible (#338).
  let bundleArch = $state<"arm64" | "x86_64">("arm64");

  // ── Deploy to device (#275) ──────────────────────────────────────────────
  type DeployEvent = {
    stage: string;
    detail?: string;
    status?: string;
    error?: string | null;
    summary?: {
      runtime_version?: string;
      host_preflight?: string[];
      reboot_pending?: boolean;
      preserved?: string[];
      runtime_config_kept?: boolean;
      health?: string[];
      observability?: string[];
      grafana_access?: string[];
    };
    host_preflight?: string[];
    reboot_pending?: boolean;
  };
  type DeployOutcome = {
    ok: boolean;
    runtimeVersion: string;
    error: string;
    hostPreflight: string[];
    rebootPending: boolean;
    preserved: string[];
    runtimeConfigKept: boolean;
    health: string[];
    observability: string[];
    grafanaAccess: string[];
  };
  let deployTarget = $state<"local" | "remote">("remote");
  let deployHost = $state<string>("");
  let deployVariantChoice = $state<string>("");
  let deployTelemetryExport = $state<boolean>(false);
  let deployObservability = $state<boolean>(false);
  let deployRunning = $state<boolean>(false);
  let deployProgress = $state<string[]>([]);
  let deployOutcome = $state<DeployOutcome | null>(null);
  let deployError = $state<string>("");
  // The project's own variants; `manual` until the user picks another.
  const deployVariant = $derived(
    variants.includes(deployVariantChoice)
      ? deployVariantChoice
      : variants.includes(MANUAL_VARIANT)
        ? MANUAL_VARIANT
        : (variants[0] ?? ""),
  );

  let commissionLiveDataActive = false;

  // ── Reactive: sync running state when commissionRunningForCurrent changes ─
  $effect(() => {
    if (commissionRunningForCurrent && !commissionLiveDataActive) {
      startHealthPolling();
      connectLogs();
      startCommissionHealthPolling();
      commissionLiveDataActive = true;
    } else if (!commissionRunningForCurrent && commissionLiveDataActive) {
      stopHealthPolling();
      disconnectLogs();
      stopCommissionHealthPolling();
      commissionLiveDataActive = false;
    }
  });

  onDestroy(() => {
    if (commissionLiveDataActive) {
      stopHealthPolling();
      disconnectLogs();
      stopCommissionHealthPolling();
      commissionLiveDataActive = false;
    }
  });

  // ── Health polling ────────────────────────────────────────────────────────
  function startHealthPolling() {
    stopHealthPolling();
    healthStartedAt = Date.now();
    healthStatus = "starting";
    healthTimer = setInterval(async () => {
      if (Date.now() - healthStartedAt < 10_000) {
        healthStatus = "starting";
        return;
      }
      try {
        await fetchJson<RuntimeApiStatus>("/v0/runtime/status");
        healthStatus = "healthy";
      } catch {
        healthStatus = "unavailable";
      }
    }, 2000);
  }

  function stopHealthPolling() {
    if (healthTimer !== null) {
      clearInterval(healthTimer);
      healthTimer = null;
    }
    healthStatus = "starting";
  }

  // ── Log pane ──────────────────────────────────────────────────────────────
  function connectLogs() {
    disconnectLogs();
    if (!projectName) return;
    logLines = [];
    logVisible = true;
    logAutoScroll = true;
    logEventSource = new EventSource(`/api/projects/${encodeURIComponent(projectName)}/logs`);
    logEventSource.onmessage = (e: MessageEvent<string>) => {
      logLines =
        logLines.length >= 1000 ? [...logLines.slice(-999), e.data] : [...logLines, e.data];
      const el = logEl;
      if (logAutoScroll && el)
        setTimeout(() => {
          el.scrollTop = el.scrollHeight;
        }, 0);
    };
    logEventSource.onerror = () => {
      /* browser retries */
    };
  }

  function disconnectLogs() {
    if (logEventSource) {
      logEventSource.close();
      logEventSource = null;
    }
  }

  function handleLogScroll(e: Event): void {
    const el = e.currentTarget as HTMLDivElement;
    logAutoScroll = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
  }

  // ── Commission health panel ───────────────────────────────────────────────
  function startCommissionHealthPolling() {
    stopCommissionHealthPolling();
    void refreshCommissionHealth();
    commissionHealthTimer = setInterval(() => void refreshCommissionHealth(), 2500);
  }

  function stopCommissionHealthPolling() {
    if (commissionHealthTimer !== null) {
      clearInterval(commissionHealthTimer);
      commissionHealthTimer = null;
    }
    commissionRuntimeStatus = null;
    commissionProviderHealth = [];
  }

  async function refreshCommissionHealth() {
    if (!projectName) return;
    let status: RuntimeStatus;
    try {
      status = await fetchJson<RuntimeStatus>("/api/status");
    } catch (err) {
      commissionHealthError = `Control API unavailable: ${err instanceof Error ? err.message : String(err)}`;
      return;
    }
    if (!status.running) {
      commissionHealthError = "Runtime is stopped.";
      commissionRuntimeStatus = null;
      commissionProviderHealth = [];
      return;
    }
    const rp = typeof status.active_project === "string" ? status.active_project : "";
    if (rp !== projectName) {
      commissionHealthError = `Runtime is running for "${rp}". Launch for "${projectName}" is blocked.`;
      return;
    }
    commissionHealthError = "";
    const [rtResult, phResult] = await Promise.allSettled([
      fetchJson<RuntimeApiStatus>("/v0/runtime/status"),
      fetchJson<{ providers: ProviderHealth[] }>("/v0/providers/health"),
    ]);
    commissionRuntimeStatus = rtResult.status === "fulfilled" ? rtResult.value : null;
    commissionProviderHealth =
      phResult.status === "fulfilled" ? (phResult.value?.providers ?? []) : [];
  }

  function stateClass(state: unknown): string {
    const u = String(state || "").toUpperCase();
    if (u === "AVAILABLE" || u === "RUNNING") return "ok";
    if (u === "UNAVAILABLE" || u === "STALE") return "unavailable";
    if (u === "FAULT") return "fault";
    return "unknown";
  }

  // ── Preflight ──────────────────────────────────────────────────────────────
  async function runPreflight() {
    if (!projectName) return;
    preflightRunning = true;
    preflightResults = null;
    actionError = "";
    try {
      const data = await fetchJson<PreflightResult>(
        `/api/projects/${encodeURIComponent(projectName)}/preflight`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        },
      );
      preflightResults = data;
    } catch (err) {
      actionError = `Preflight error: ${err instanceof Error ? err.message : String(err)}`;
    } finally {
      preflightRunning = false;
    }
  }

  // ── Launch ─────────────────────────────────────────────────────────────────
  function deriveLaunchBlockReason(
    status: RuntimeStatus | null | undefined,
    target: string,
  ): string {
    if (!status || typeof status !== "object") return "";
    const r = Boolean(status.running);
    const rp =
      typeof status.active_project === "string" && status.active_project !== ""
        ? status.active_project
        : null;
    if (!r || !rp) return "";
    if (rp === target)
      return `Launch blocked: runtime is already active for "${target}". Use Stop or Restart.`;
    return `Launch blocked: runtime is active for "${rp}". Stop that runtime before launching "${target}".`;
  }

  async function doLaunch() {
    if (!projectName) return;
    launchRunning = true;
    actionError = "";
    try {
      let status: RuntimeStatus | undefined;
      try {
        status = await fetchJson<RuntimeStatus>("/api/status");
      } catch {
        /* non-fatal pre-check */
      }
      if (status) {
        const block = deriveLaunchBlockReason(status, projectName);
        if (block) {
          actionError = block;
          launchRunning = false;
          return;
        }
      }
      await fetchJson(`/api/projects/${encodeURIComponent(projectName)}/launch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      // The result gated this launch only. If the runtime later stops on its
      // own, the idle bar must ask for a fresh preflight, as doStop does.
      preflightResults = null;
      startHealthPolling();
      connectLogs();
      startCommissionHealthPolling();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      actionError = msg.toLowerCase().includes("already running")
        ? "Launch blocked: runtime is already active. Stop current runtime before launching this project."
        : `Launch failed: ${msg}`;
    } finally {
      launchRunning = false;
    }
  }

  // ── Stop ───────────────────────────────────────────────────────────────────
  async function doStop() {
    if (!projectName) return;
    stopRunning = true;
    actionError = "";
    try {
      await fetchJson(`/api/projects/${encodeURIComponent(projectName)}/stop`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
    } catch {
      /* best-effort */
    } finally {
      stopHealthPolling();
      disconnectLogs();
      stopCommissionHealthPolling();
      preflightResults = null;
      stopRunning = false;
    }
  }

  // ── Restart ────────────────────────────────────────────────────────────────
  async function doRestart() {
    if (!projectName) return;
    restartRunning = true;
    actionError = "";
    try {
      await fetchJson(`/api/projects/${encodeURIComponent(projectName)}/restart`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      stopHealthPolling();
      disconnectLogs();
      startHealthPolling();
      connectLogs();
    } catch (err) {
      actionError = `Restart failed: ${err instanceof Error ? err.message : String(err)}`;
    } finally {
      restartRunning = false;
    }
  }

  // ── Export ────────────────────────────────────────────────────────────────
  async function doExport() {
    if (!projectName) return;
    exportRunning = true;
    exportFeedback = "";
    exportIsError = false;
    try {
      const res = await fetchResponse(`/api/projects/${encodeURIComponent(projectName)}/export`, {
        method: "POST",
      });
      if (!res.ok) {
        const payload = (await res.json().catch(() => ({}))) as Record<string, unknown>;
        const message =
          typeof payload.error === "string" && payload.error.trim() !== ""
            ? payload.error
            : `Export failed (HTTP ${res.status})`;
        throw new ApiResponseError(message, res.status, payload);
      }
      const blob = await res.blob();
      const fallback = `${projectName}.anpkg`;
      const filename = filenameFromContentDisposition(
        res.headers.get("Content-Disposition"),
        fallback,
      );
      downloadBlob(blob, filename);
      exportFeedback = `Exported ${filename}`;
    } catch (err) {
      exportFeedback = `Export failed: ${err instanceof Error ? err.message : String(err)}`;
      exportIsError = true;
    } finally {
      exportRunning = false;
    }
  }

  // ── Deploy to device ──────────────────────────────────────────────────────
  function outcomeOf(data: DeployEvent): DeployOutcome | null {
    if (data.stage === "done") {
      return {
        ok: true,
        runtimeVersion: data.summary?.runtime_version ?? "",
        error: "",
        hostPreflight: data.summary?.host_preflight ?? [],
        rebootPending: data.summary?.reboot_pending ?? false,
        preserved: data.summary?.preserved ?? [],
        runtimeConfigKept: data.summary?.runtime_config_kept === true,
        health: data.summary?.health ?? [],
        observability: data.summary?.observability ?? [],
        grafanaAccess: data.summary?.grafana_access ?? [],
      };
    }
    if (data.stage === "error" || data.stage === "status") {
      return {
        ok: data.stage === "status" && data.status === "done",
        runtimeVersion: "",
        error: data.detail ?? data.error ?? `Deploy ${data.status ?? "failed"}`,
        hostPreflight: data.host_preflight ?? [],
        rebootPending: data.reboot_pending ?? false,
        preserved: [],
        runtimeConfigKept: false,
        health: [],
        observability: [],
        grafanaAccess: [],
      };
    }
    return null;
  }

  async function doDeploy() {
    if (!projectName) return;
    const host = deployHost.trim();
    if (deployTarget === "remote" && !host.includes("@")) {
      deployError = "Enter the target as user@host";
      return;
    }
    deployRunning = true;
    deployError = "";
    deployProgress = [];
    deployOutcome = null;
    const body: Record<string, unknown> = { project: projectName };
    if (deployVariant) body.variant = deployVariant;
    if (deployTelemetryExport) body.with_telemetry_export = true;
    if (deployObservability) body.with_observability = true;
    if (deployTarget === "remote") body.target = host;
    try {
      const started = await fetchJson<{ job_id: string }>(
        deployTarget === "remote" ? "/api/provision/remote" : "/api/provision/install",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
      );
      deployOutcome = await new Promise<DeployOutcome>((resolve) => {
        const source = new EventSource(`/api/provision/status/${started.job_id}`);
        source.onmessage = (ev: MessageEvent<string>) => {
          const data = JSON.parse(ev.data) as DeployEvent;
          const outcome = outcomeOf(data);
          if (outcome) {
            source.close();
            resolve(outcome);
          } else {
            deployProgress = [...deployProgress, data.detail || data.stage];
          }
        };
        source.onerror = () => {
          source.close();
          resolve({
            ok: false,
            runtimeVersion: "",
            error:
              "Lost the connection to the workbench while deploying. The install may still be running on the target.",
            hostPreflight: [],
            rebootPending: false,
            preserved: [],
            runtimeConfigKept: false,
            health: [],
            observability: [],
            grafanaAccess: [],
          });
        };
      });
    } catch (err) {
      deployError = `Deploy failed to start: ${err instanceof Error ? err.message : String(err)}`;
    } finally {
      deployRunning = false;
    }
  }

  // ── Bundle Export ─────────────────────────────────────────────────────────
  async function doExportBundle() {
    if (!projectName) return;
    bundleRunning = true;
    bundleFeedback = "";
    bundleIsError = false;
    try {
      // Start bundle job
      const startRes = await fetchJson<{ job_id: string }>("/api/provision/bundle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project: projectName, arch: bundleArch }),
      });
      const jobId = startRes.job_id;
      bundleFeedback = "Building bundle…";

      // Poll for completion via SSE
      const evtSource = new EventSource(`/api/provision/status/${jobId}`);
      await new Promise<void>((resolve, reject) => {
        evtSource.onmessage = (ev) => {
          const data = JSON.parse(ev.data) as {
            stage: string;
            status?: string;
            detail?: string;
            error?: string;
            filename?: string;
          };
          if (data.stage === "done") {
            bundleFeedback = data.detail ?? "Bundle ready";
            evtSource.close();
            resolve();
          } else if (data.stage === "error" || data.status === "failed") {
            evtSource.close();
            reject(new Error(data.error ?? data.detail ?? "Bundle failed"));
          } else if (data.detail) {
            bundleFeedback = data.detail;
          }
        };
        evtSource.onerror = () => {
          evtSource.close();
          reject(new Error("Connection lost"));
        };
      });

      // Download the bundle
      const dlRes = await fetchResponse(`/api/provision/bundle/${jobId}`);
      if (!dlRes.ok) throw new Error(`Download failed (HTTP ${dlRes.status})`);
      const blob = await dlRes.blob();
      const fallback = `anolis-${projectName}-arm64.tar.gz`;
      const filename = filenameFromContentDisposition(
        dlRes.headers.get("Content-Disposition"),
        fallback,
      );
      downloadBlob(blob, filename);
      bundleFeedback = `Downloaded ${filename}`;
    } catch (err) {
      bundleFeedback = `Bundle failed: ${err instanceof Error ? err.message : String(err)}`;
      bundleIsError = true;
    } finally {
      bundleRunning = false;
    }
  }
</script>

{#snippet deployResult(outcome: DeployOutcome)}
  <div id="deploy-result">
    {#if outcome.ok}
      <strong>Deployed</strong>
      {#if outcome.runtimeVersion}
        <p>anolis-runtime v{outcome.runtimeVersion} is installed.</p>
      {/if}
      {#if outcome.runtimeConfigKept}
        <p>The existing runtime config was kept; the runtime runs it, not this project's.</p>
      {/if}
      {#if outcome.preserved.length}
        <p>install.sh left these existing configs in place:</p>
        <pre class="deploy-output">{outcome.preserved.join("\n")}</pre>
      {/if}
      {#if outcome.hostPreflight.length}
        <p>The host preflight needs attention:</p>
        <pre class="deploy-output">{outcome.hostPreflight.join("\n")}</pre>
      {/if}
      {#if outcome.health.length}
        <!-- install.sh warns and still exits 0 when a provider is not
             running, so a "Deployed" without these lines would hide it. -->
        {#if outcome.health.some((line) => line.startsWith("⚠"))}
          <p class="deploy-warning">Health needs attention: install.sh reported a problem.</p>
        {:else}
          <p>install.sh's health check:</p>
        {/if}
        <pre class="deploy-output">{#each outcome.health as line, i (i)}<span
              class="deploy-line"
              class:deploy-warning={line.startsWith("⚠")}>{line}</span
            >{/each}</pre>
      {/if}
      {#if outcome.observability.length}
        <!-- install.sh warns and still exits 0 when observability fails, so
             a "Deployed" without these lines would hide the failure. -->
        {#if outcome.observability.some((line) => line.startsWith("⚠"))}
          <p class="deploy-warning">
            Observability needs attention: install.sh reported a problem.
          </p>
        {:else}
          <p>install.sh's observability report:</p>
        {/if}
        <pre class="deploy-output">{#each outcome.observability as line, i (i)}<span
              class="deploy-line"
              class:deploy-warning={line.startsWith("⚠")}>{line}</span
            >{/each}</pre>
      {/if}
      {#if outcome.grafanaAccess.length}
        <p>Reaching Grafana:</p>
        <pre class="deploy-output">{outcome.grafanaAccess.join("\n")}</pre>
      {/if}
    {:else}
      <strong>Deploy failed</strong>
      <pre class="deploy-output">{outcome.error}</pre>
    {/if}
    {#if outcome.rebootPending}
      <p>
        A reboot is pending on the target (/run/reboot-required). Reboot it before relying on its
        hardware.
      </p>
    {/if}
  </div>
{/snippet}

<section id="workspace-commission" class="workspace visible">
  <div class="workspace-header">
    <h2>Commission</h2>
    <p>Preflight, launch, monitor logs, and supervise runtime bring-up.</p>
  </div>

  {#if runningForOther}
    <div class="workspace-advisory">
      Runtime is currently running for "{runningProject}". Launch for this project is hard-blocked
      until stop.
    </div>
  {/if}

  <!-- Launch panel -->
  <div id="launch-panel" class="launch-panel">
    {#if imported}
      <div class="workspace-advisory">
        Imported machine profile — carried verbatim; dev-launch and preflight are not available.
        Deploy it with install.sh (Deploy to device or Export Bundle below, or the provision CLI).
      </div>
    {:else if !commissionRunningForCurrent}
      <!-- Idle: preflight + launch -->
      <div class="launch-idle-bar">
        <button
          class="btn-secondary"
          type="button"
          disabled={preflightRunning}
          onclick={runPreflight}
        >
          {preflightRunning ? "Checking…" : "Preflight Check"}
        </button>
        <button
          class="btn-primary"
          type="button"
          disabled={launchRunning ||
            runningForOther ||
            preflightRunning ||
            preflightResults === null ||
            !preflightResults.ok}
          onclick={doLaunch}
        >
          {launchRunning ? "Launching…" : "Launch →"}
        </button>
        {#if preflightResults === null && !preflightRunning}
          <span class="field-note">Run a preflight check to enable Launch.</span>
        {/if}
        {#if preflightResults !== null}
          {@const s = preflightResults.summary}
          <span
            class="launch-summary"
            style="color: {s.failed > 0
              ? 'var(--state-fault)'
              : s.skipped > 0
                ? 'var(--state-warning)'
                : 'var(--state-nominal)'}"
          >
            {`${s.passed} passed · ${s.skipped} skipped · ${s.failed} failed`}
          </span>
        {/if}
      </div>

      {#if preflightResults?.checks?.length}
        <div class="preflight-results">
          {#each preflightResults.checks as c, i (i)}
            {@const cls = c.ok === true ? "check-ok" : c.ok === false ? "check-fail" : "check-skip"}
            {@const icon = c.ok === true ? "✓" : c.ok === false ? "✗" : "–"}
            <div class="check-item {cls}">
              <span class="check-icon">{icon}</span>
              <span class="check-name">{c.name}</span>
              {#if c.error || c.hint || c.note}
                <span class="check-extra">
                  {#if c.error}<span class="check-detail">{c.error}</span>{/if}
                  {#if c.hint}<span class="check-hint">{c.hint}</span>{/if}
                  {#if c.note}<span class="check-detail">{c.note}</span>{/if}
                </span>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    {:else}
      <!-- Running state -->
      <div class="launch-running-bar">
        <span class="running-label">
          Running — {projectName} on port {launchPort ?? "?"}
        </span>
        <span class="health-badge {healthStatus}">
          {healthStatus === "healthy"
            ? "Healthy"
            : healthStatus === "unavailable"
              ? "Unavailable"
              : "Starting…"}
        </span>
        <button class="btn-secondary btn-sm" type="button" disabled={stopRunning} onclick={doStop}>
          {stopRunning ? "Stopping…" : "Stop"}
        </button>
        <button
          class="btn-secondary btn-sm"
          type="button"
          disabled={restartRunning}
          title="Restarts with last saved config. Unsaved edits will not be applied."
          onclick={doRestart}
        >
          {restartRunning ? "Restarting…" : "Restart"}
        </button>
      </div>
    {/if}

    {#if actionError}
      <div class="launch-summary" style="color:var(--feedback-error);margin-top:6px;">
        {actionError}
      </div>
    {/if}
  </div>

  <!-- Log pane -->
  {#if logVisible}
    <div id="log-pane" class="log-pane visible">
      <div class="log-toolbar">
        <span>Runtime Log</span>
        <button
          type="button"
          class="btn-secondary btn-sm"
          onclick={() => {
            logLines = [];
          }}>Clear</button
        >
      </div>
      <div class="log-content" bind:this={logEl} onscroll={handleLogScroll}>
        {#each logLines as line, i (i)}
          <div class="log-line">{line}</div>
        {/each}
      </div>
    </div>
  {/if}

  <!-- Commission health panel -->
  <div class="commission-health-section">
    <h3>Runtime Health</h3>
    {#if commissionHealthError}
      <p class="placeholder">{commissionHealthError}</p>
    {:else if commissionRuntimeStatus}
      <div id="commission-runtime-card" class="commission-health-card">
        <div class="runtime-status-row">
          <span>Mode: <strong>{commissionRuntimeStatus.mode ?? "UNKNOWN"}</strong></span>
          <span
            >Uptime: {Number.isFinite(Number(commissionRuntimeStatus.uptime_seconds))
              ? `${commissionRuntimeStatus.uptime_seconds}s`
              : "--"}</span
          >
          <span
            >Poll: {Number.isFinite(Number(commissionRuntimeStatus.polling_interval_ms))
              ? `${commissionRuntimeStatus.polling_interval_ms} ms`
              : "--"}</span
          >
          <span
            >Devices: {Number.isFinite(Number(commissionRuntimeStatus.device_count))
              ? String(commissionRuntimeStatus.device_count)
              : "--"}</span
          >
        </div>
      </div>
    {:else}
      <p class="placeholder">Runtime status unavailable.</p>
    {/if}

    <h3>Provider Health</h3>
    <ul id="commission-provider-health" class="commission-provider-health">
      {#if commissionProviderHealth.length === 0}
        <li class="placeholder">No provider health data.</li>
      {:else}
        {#each commissionProviderHealth as prov, i (i)}
          {@const state = typeof prov?.state === "string" ? prov.state : "UNKNOWN"}
          {@const lifecycle =
            typeof prov?.lifecycle_state === "string" ? prov.lifecycle_state : "--"}
          {@const lastSeenAgo = Number(prov?.last_seen_ago_ms)}
          <li class="commission-provider-row">
            <div class="provider-title">
              <span>{prov?.provider_id ?? "unknown"}</span>
              <span class="badge {stateClass(state)}">{state}</span>
            </div>
            <div class="provider-meta">
              lifecycle: {lifecycle} • last seen: {Number.isFinite(lastSeenAgo)
                ? `${lastSeenAgo} ms`
                : "--"}
            </div>
          </li>
        {/each}
      {/if}
    </ul>
  </div>

  <!-- Export package -->
  {#if !imported}
    <div class="commission-export">
      <button
        type="button"
        class="btn-secondary"
        id="btn-export-package"
        disabled={exportRunning}
        onclick={doExport}
      >
        {exportRunning ? "Exporting…" : "Export .anpkg"}
      </button>
      {#if exportFeedback}
        <span
          id="export-package-feedback"
          class="launch-summary"
          style="color: {exportIsError ? 'var(--feedback-error)' : 'var(--feedback-ok)'}"
        >
          {exportFeedback}
        </span>
      {/if}
    </div>
  {/if}

  <!-- Deploy to device: install.sh --project, here or over SSH (#275) -->
  <div id="deploy-panel" class="commission-deploy">
    <h3>Deploy to device</h3>
    <fieldset class="deploy-target" disabled={deployRunning}>
      <legend>Target</legend>
      <label>
        <input type="radio" name="deploy-target" value="remote" bind:group={deployTarget} />
        A remote device over SSH
      </label>
      <label>
        <input type="radio" name="deploy-target" value="local" bind:group={deployTarget} />
        This machine
      </label>
    </fieldset>
    {#if deployTarget === "remote"}
      <label for="deploy-host">Target (user@host)</label>
      <input
        id="deploy-host"
        type="text"
        placeholder="pi@192.168.1.10"
        autocomplete="off"
        spellcheck="false"
        disabled={deployRunning}
        bind:value={deployHost}
      />
    {/if}
    <label for="deploy-variant">Runtime variant</label>
    <select
      id="deploy-variant"
      value={deployVariant}
      disabled={deployRunning}
      onchange={(e) => (deployVariantChoice = e.currentTarget.value)}
    >
      {#each variants as v (v)}
        <option value={v}>
          {inertnessViolation(system?.variants?.[v]) ? `${v} (fresh install refuses this)` : v}
        </option>
      {/each}
    </select>
    <p class="field-note">Deploys the saved project; unsaved edits are not included.</p>
    <p class="field-note">
      A fresh install accepts only a variant that boots inert. On a device that already has a
      runtime config, install.sh keeps that config: this project's runtime config and the variant
      are not applied over it.
    </p>
    <label>
      <input type="checkbox" disabled={deployRunning} bind:checked={deployTelemetryExport} />
      Also install telemetry-export
    </label>
    <p class="field-note">
      It is installed inert and starts once its tokens are set in /etc/anolis/telemetry-export.env
      on the target.
    </p>
    <label>
      <input type="checkbox" disabled={deployRunning} bind:checked={deployObservability} />
      Also install observability (InfluxDB + Grafana)
    </label>
    <p class="field-note">
      Installed on the target beside the runtime. Grafana listens on port 3000: on the target's
      127.0.0.1 only with a current runtime, on its network address with an older one. The deploy
      result shows install.sh's observability report, and how to reach Grafana.
    </p>
    <button
      type="button"
      class="btn-primary"
      id="btn-deploy"
      disabled={deployRunning || (deployTarget === "remote" && !deployHost.trim())}
      onclick={doDeploy}
    >
      {deployRunning ? "Deploying…" : "Deploy"}
    </button>
    {#if deployError}
      <p class="field-error">{deployError}</p>
    {/if}
    {#if deployProgress.length}
      <ol class="deploy-stages" aria-live="polite">
        {#each deployProgress as step, i (i)}
          <li>{step}</li>
        {/each}
      </ol>
    {/if}
    {#if deployOutcome}
      {@render deployResult(deployOutcome)}
    {/if}
  </div>

  <!-- Export bundle (offline install package) -->
  <div class="commission-export">
    <label for="bundle-arch">Target architecture</label>
    <select
      id="bundle-arch"
      value={bundleArch}
      disabled={bundleRunning}
      onchange={(e) => (bundleArch = e.currentTarget.value as "arm64" | "x86_64")}
    >
      <option value="arm64">Raspberry Pi (arm64)</option>
      <option value="x86_64">x86_64</option>
    </select>
    <button
      type="button"
      class="btn-secondary"
      id="btn-export-bundle"
      disabled={bundleRunning}
      onclick={doExportBundle}
    >
      {bundleRunning ? "Building Bundle…" : "Export Bundle"}
    </button>
    {#if variants.length > 1}
      <span class="field-note">
        Carries every runtime variant ({variants.join(", ")}). Choose one on the target with
        <code>install.sh --variant</code>.
      </span>
    {/if}
    {#if bundleFeedback}
      <span
        id="export-bundle-feedback"
        class="launch-summary"
        style="color: {bundleIsError ? 'var(--feedback-error)' : 'var(--feedback-ok)'}"
      >
        {bundleFeedback}
      </span>
    {/if}
  </div>
</section>
