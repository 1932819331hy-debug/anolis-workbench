<script lang="ts">
  import { fetchJson } from "../lib/api";

  let {
    onNavigate,
  }: {
    onNavigate: (path: string) => void;
  } = $props();

  type OnboardingStatus = {
    first_run: boolean;
    has_projects: boolean;
    has_runtime: boolean;
    runtime_path: string;
  };

  // install.sh's host-preflight block ends on this line when the only thing
  // wrong is unmet requirements, the one failure --allow-unmet-host can get past.
  const PREFLIGHT_UNMET = "✗ host preflight: requirements unmet";

  type ProvisionEvent = {
    stage: string;
    detail?: string;
    status?: string;
    error?: string | null;
    summary?: { runtime_version?: string; host_preflight?: string[]; reboot_pending?: boolean };
    host_preflight?: string[];
    reboot_pending?: boolean;
  };

  type ProvisionResult = {
    ok: boolean;
    runtimeVersion: string;
    error: string;
    hostPreflight: string[];
    rebootPending: boolean;
  };

  let status = $state<OnboardingStatus | null>(null);
  let provisionMode = $state<"local" | "remote" | null>(null);
  let remoteTarget = $state<string>("");
  let remoteError = $state<string>("");
  let startError = $state<string>("");
  let starting = $state<boolean>(false);

  // choose -> running -> result. Onboarding is an overlay, not a route: the job
  // lives in this component only, so a reload loses the view (#348).
  let phase = $state<"choose" | "running" | "result">("choose");
  let progress = $state<{ stage: string; detail: string }[]>([]);
  let result = $state<ProvisionResult | null>(null);
  let lastRequest = $state<{ url: string; body: Record<string, unknown> } | null>(null);
  let confirmingUnmet = $state<boolean>(false);
  let source: EventSource | null = null;

  async function loadStatus(): Promise<void> {
    try {
      status = await fetchJson<OnboardingStatus>("/api/onboarding");
    } catch {
      // If endpoint fails, don't block — just show onboarding
      status = { first_run: true, has_projects: false, has_runtime: false, runtime_path: "" };
    }
  }

  function finish(next: ProvisionResult): void {
    result = next;
    phase = "result";
    source?.close();
    source = null;
  }

  function onEvent(data: ProvisionEvent): void {
    if (result) return;
    if (data.stage === "done") {
      finish({
        ok: true,
        runtimeVersion: data.summary?.runtime_version ?? "",
        error: "",
        hostPreflight: data.summary?.host_preflight ?? [],
        rebootPending: data.summary?.reboot_pending ?? false,
      });
    } else if (data.stage === "error") {
      finish({
        ok: false,
        runtimeVersion: "",
        error: data.detail ?? "Install failed",
        hostPreflight: data.host_preflight ?? [],
        rebootPending: data.reboot_pending ?? false,
      });
    } else if (data.stage === "status") {
      // The stream's closing frame; reached without done/error only when a job
      // ended some other way (cancelled).
      finish({
        ok: data.status === "done",
        runtimeVersion: "",
        error: data.error ?? `Install ${data.status ?? "ended"}`,
        hostPreflight: [],
        rebootPending: false,
      });
    } else {
      progress = [...progress, { stage: data.stage, detail: data.detail ?? "" }];
    }
  }

  async function startJob(url: string, body: Record<string, unknown>): Promise<void> {
    starting = true;
    startError = "";
    try {
      const started = await fetchJson<{ job_id: string }>(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      lastRequest = { url, body };
      progress = [];
      result = null;
      confirmingUnmet = false;
      phase = "running";
      source = new EventSource(`/api/provision/status/${started.job_id}`);
      source.onmessage = (ev: MessageEvent<string>) =>
        onEvent(JSON.parse(ev.data) as ProvisionEvent);
      source.onerror = () => {
        // A closed stream after done/error is the normal end, not a loss.
        if (result) return;
        finish({
          ok: false,
          runtimeVersion: "",
          error:
            "Lost the connection to the workbench while installing. The install may still be running on the target.",
          hostPreflight: [],
          rebootPending: false,
        });
      };
    } catch (err) {
      startError = err instanceof Error ? err.message : "Failed to start provisioning";
    } finally {
      starting = false;
    }
  }

  function startLocalInstall(): Promise<void> {
    return startJob("/api/provision/install", {});
  }

  function showRemoteForm(): void {
    provisionMode = "remote";
  }

  async function startRemoteInstall(): Promise<void> {
    remoteError = "";
    if (!remoteTarget.includes("@")) {
      remoteError = "Enter target as user@hostname";
      return;
    }
    await startJob("/api/provision/remote", { target: remoteTarget });
    if (startError) remoteError = startError;
  }

  // Offered only when the preflight's sole failure is unmet requirements.
  function canInstallAnyway(r: ProvisionResult): boolean {
    return (
      !r.ok &&
      r.hostPreflight.at(-1) === PREFLIGHT_UNMET &&
      lastRequest?.body.allow_unmet_host !== true
    );
  }

  function installAnyway(): Promise<void> {
    if (!lastRequest) return Promise.resolve();
    return startJob(lastRequest.url, { ...lastRequest.body, allow_unmet_host: true });
  }

  function backToChoose(): void {
    phase = "choose";
    result = null;
    progress = [];
    confirmingUnmet = false;
  }

  function skip(): void {
    onNavigate("/");
  }

  // Load status on component init
  loadStatus();
</script>

<section id="onboarding" class="workspace visible">
  <div class="onboarding-container">
    <div class="onboarding-header">
      <h1>Welcome to Anolis Workbench</h1>
      <p>No system is set up yet. Let's get started.</p>
    </div>

    {#if phase === "running"}
      <div class="onboarding-progress" aria-live="polite">
        <h2>Installing…</h2>
        <p class="onboarding-note">
          Keep this window open. Reloading loses this view; the install keeps running.
        </p>
        <ol class="onboarding-stages">
          {#each progress as step, i (i)}
            <li>{step.detail || step.stage}</li>
          {/each}
        </ol>
      </div>
    {:else if phase === "result" && result}
      <div class="onboarding-result" class:failed={!result.ok}>
        {#if result.ok}
          <h2>Installed</h2>
          {#if result.runtimeVersion}
            <p>anolis-runtime v{result.runtimeVersion} is installed.</p>
          {/if}
          {#if result.hostPreflight.length}
            <p>The host preflight needs attention:</p>
            <pre class="onboarding-preflight">{result.hostPreflight.join("\n")}</pre>
          {/if}
        {:else}
          <h2>Install failed</h2>
          <pre class="onboarding-preflight">{result.error}</pre>
        {/if}

        {#if result.rebootPending}
          <p class="onboarding-reboot">
            A reboot is pending on the target (/run/reboot-required). Reboot it before relying on
            its hardware.
          </p>
        {/if}

        <div class="onboarding-actions">
          {#if result.ok}
            <button type="button" class="btn-primary" onclick={skip}>Go to projects</button>
          {:else}
            {#if canInstallAnyway(result)}
              {#if confirmingUnmet}
                <p class="onboarding-warning">
                  The providers above will start with no devices until the host is fixed and the
                  service restarted.
                </p>
                <button
                  type="button"
                  class="btn-primary"
                  disabled={starting}
                  onclick={installAnyway}
                >
                  Confirm: install anyway
                </button>
              {:else}
                <button
                  type="button"
                  class="btn-secondary"
                  onclick={() => {
                    confirmingUnmet = true;
                  }}
                >
                  Install anyway…
                </button>
              {/if}
            {/if}
            <button type="button" class="btn-secondary" onclick={backToChoose}>Back</button>
          {/if}
        </div>
      </div>
    {:else if provisionMode === null}
      <div class="onboarding-options">
        <button
          type="button"
          class="onboarding-card"
          disabled={starting}
          onclick={startLocalInstall}
        >
          <h2>Set up this device</h2>
          <p>Install runtime + providers directly on this machine.</p>
        </button>

        <button type="button" class="onboarding-card" disabled={starting} onclick={showRemoteForm}>
          <h2>Set up a remote device</h2>
          <p>Provision a Raspberry Pi or other target over SSH.</p>
        </button>

        <button
          type="button"
          class="onboarding-card"
          disabled={starting}
          onclick={() => onNavigate("/")}
        >
          <h2>Import a project</h2>
          <p>Import a canonical machine-profile project directory (carried verbatim).</p>
        </button>
      </div>

      {#if startError}
        <p class="field-error">{startError}</p>
      {/if}

      <div class="onboarding-skip">
        <button type="button" class="btn-secondary" onclick={skip}>
          Skip — go to project list
        </button>
      </div>
    {:else if provisionMode === "remote"}
      <div class="onboarding-remote-form">
        <h2>Remote Device Setup</h2>
        <p>Enter the SSH target for provisioning.</p>

        <div class="form-group">
          <label for="remote-target">Target (user@host)</label>
          <input
            id="remote-target"
            type="text"
            placeholder="pi@192.168.1.10"
            autocomplete="off"
            spellcheck="false"
            bind:value={remoteTarget}
          />
        </div>

        {#if remoteError}
          <p class="field-error">{remoteError}</p>
        {/if}

        <div class="onboarding-actions">
          <button
            type="button"
            class="btn-primary"
            disabled={starting || !remoteTarget}
            onclick={startRemoteInstall}
          >
            {starting ? "Starting…" : "Start Provisioning"}
          </button>
          <button
            type="button"
            class="btn-secondary"
            disabled={starting}
            onclick={() => {
              provisionMode = null;
            }}
          >
            Back
          </button>
        </div>
      </div>
    {/if}
  </div>
</section>

<style>
  .onboarding-container {
    max-width: 600px;
    margin: 2rem auto;
    padding: 0 1rem;
  }

  .onboarding-header {
    text-align: center;
    margin-bottom: 2rem;
  }

  .onboarding-header h1 {
    margin-bottom: 0.5rem;
  }

  .onboarding-header p {
    color: var(--text-secondary, #666);
  }

  .onboarding-options {
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }

  .onboarding-card {
    display: block;
    width: 100%;
    padding: 1.25rem 1.5rem;
    border: 1px solid var(--border-color, #ddd);
    border-radius: 8px;
    background: var(--card-bg, #fff);
    cursor: pointer;
    text-align: left;
    transition:
      border-color 0.15s,
      box-shadow 0.15s;
  }

  .onboarding-card:hover:not(:disabled) {
    border-color: var(--accent-color, #2563eb);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  }

  .onboarding-card:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  .onboarding-card h2 {
    margin: 0 0 0.25rem;
    font-size: 1.1rem;
  }

  .onboarding-card p {
    margin: 0;
    color: var(--text-secondary, #666);
    font-size: 0.9rem;
  }

  .onboarding-skip {
    margin-top: 1.5rem;
    text-align: center;
  }

  .onboarding-remote-form {
    margin-top: 1rem;
  }

  .onboarding-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem;
    margin-top: 1rem;
  }

  .onboarding-note,
  .onboarding-warning {
    color: var(--text-secondary, #666);
    font-size: 0.9rem;
  }

  .onboarding-stages {
    padding-left: 1.25rem;
  }

  .onboarding-preflight {
    white-space: pre-wrap;
    overflow-x: auto;
    padding: 0.75rem;
    border: 1px solid var(--border-color, #ddd);
    border-radius: 6px;
    font-size: 0.85rem;
  }

  .onboarding-reboot {
    font-weight: 600;
  }
</style>
