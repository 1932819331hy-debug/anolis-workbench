<script lang="ts" module>
  // The running provision job outlives a reload; this lets the view reattach
  // to its stream, which replays from the first event (#348).
  const PENDING_JOB_KEY = "anolis-workbench.provision-job";

  export type PendingJob = { jobId: string; url: string; body: Record<string, unknown> };

  export function readPendingJob(): PendingJob | null {
    try {
      const raw = sessionStorage.getItem(PENDING_JOB_KEY);
      return raw ? (JSON.parse(raw) as PendingJob) : null;
    } catch {
      return null;
    }
  }
</script>

<script lang="ts">
  import { fetchJson } from "../lib/api";
  import { isLoopbackHost } from "../lib/guards";

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

  const LOCAL_INSTALL_URL = "/api/provision/install";

  type ProvisionEvent = {
    stage: string;
    detail?: string;
    status?: string;
    error?: string | null;
    summary?: {
      project?: string;
      runtime_version?: string;
      host_preflight?: string[];
      reboot_pending?: boolean;
      preserved?: string[];
      runtime_config_kept?: boolean;
    };
    host_preflight?: string[];
    reboot_pending?: boolean;
  };

  type ProvisionResult = {
    ok: boolean;
    // Named by a local install's done summary only (#238).
    project: string;
    runtimeVersion: string;
    error: string;
    hostPreflight: string[];
    rebootPending: boolean;
    preserved: string[];
    runtimeConfigKept: boolean;
  };

  let status = $state<OnboardingStatus | null>(null);
  let provisionMode = $state<"local" | "remote" | null>(null);
  let remoteTarget = $state<string>("");
  // Sent once in a local job's request body, then cleared. It never joins
  // the job kept in sessionStorage or the request "Install anyway" repeats.
  let localSudoPassword = $state<string>("");
  const loopbackClient = isLoopbackHost(window.location.hostname);
  let remoteError = $state<string>("");
  let startError = $state<string>("");
  let starting = $state<boolean>(false);

  // choose -> running -> result. Onboarding is an overlay, not a route; a
  // reload reattaches to the job kept under PENDING_JOB_KEY (#348).
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

  // Storage is best-effort: an install already started must still get its
  // view when sessionStorage refuses a write; only the reload path is lost.
  function rememberJob(job: PendingJob): void {
    try {
      sessionStorage.setItem(PENDING_JOB_KEY, JSON.stringify(job));
    } catch {
      /* the view attaches anyway; a reload just cannot reattach */
    }
  }

  function forgetJob(): void {
    try {
      sessionStorage.removeItem(PENDING_JOB_KEY);
    } catch {
      /* nothing more to do */
    }
  }

  // A dropped stream is not the end of the job, so it keeps the job for a
  // reload; only a server that no longer knows the job (a 404) forgets it.
  async function forgetIfGone(jobId: string): Promise<void> {
    const ctrl = new AbortController();
    try {
      const res = await fetch(`/api/provision/status/${jobId}`, { signal: ctrl.signal });
      if (res.status === 404) forgetJob();
    } catch {
      /* unreachable: keep the job */
    } finally {
      ctrl.abort();
    }
  }

  function finish(next: ProvisionResult): void {
    result = next;
    phase = "result";
    source?.close();
    source = null;
  }

  // The job reported its end (done, error or the closing status frame).
  function finishJob(next: ProvisionResult): void {
    forgetJob();
    finish(next);
  }

  function onEvent(data: ProvisionEvent): void {
    if (result) return;
    if (data.stage === "done") {
      finishJob({
        ok: true,
        project: data.summary?.project ?? "",
        runtimeVersion: data.summary?.runtime_version ?? "",
        error: "",
        hostPreflight: data.summary?.host_preflight ?? [],
        rebootPending: data.summary?.reboot_pending ?? false,
        preserved: data.summary?.preserved ?? [],
        runtimeConfigKept: data.summary?.runtime_config_kept === true,
      });
    } else if (data.stage === "error") {
      finishJob({
        ok: false,
        project: "",
        runtimeVersion: "",
        error: data.detail ?? "Install failed",
        hostPreflight: data.host_preflight ?? [],
        rebootPending: data.reboot_pending ?? false,
        preserved: [],
        runtimeConfigKept: false,
      });
    } else if (data.stage === "status") {
      // The stream's closing frame; reached without done/error only if a job
      // ended without reporting either.
      finishJob({
        ok: data.status === "done",
        project: "",
        runtimeVersion: "",
        error: data.error ?? `Install ${data.status ?? "ended"}`,
        hostPreflight: [],
        rebootPending: false,
        preserved: [],
        runtimeConfigKept: false,
      });
    } else {
      progress = [...progress, { stage: data.stage, detail: data.detail ?? "" }];
    }
  }

  function attach(job: PendingJob): void {
    lastRequest = { url: job.url, body: job.body };
    progress = [];
    result = null;
    confirmingUnmet = false;
    phase = "running";
    source = new EventSource(`/api/provision/status/${job.jobId}`);
    source.onmessage = (ev: MessageEvent<string>) => onEvent(JSON.parse(ev.data) as ProvisionEvent);
    source.onerror = () => {
      // A closed stream after done/error is the normal end, not a loss.
      if (result) return;
      void forgetIfGone(job.jobId);
      finish({
        ok: false,
        project: "",
        runtimeVersion: "",
        error:
          "Lost the connection to the workbench while installing. The install may still be running on the target.",
        hostPreflight: [],
        rebootPending: false,
        preserved: [],
        runtimeConfigKept: false,
      });
    };
  }

  function takeSudoPassword(): Record<string, unknown> {
    const secret = localSudoPassword ? { sudo_password: localSudoPassword } : {};
    localSudoPassword = "";
    return secret;
  }

  async function startJob(
    url: string,
    body: Record<string, unknown>,
    secret: Record<string, unknown> = {},
  ): Promise<void> {
    starting = true;
    startError = "";
    try {
      const started = await fetchJson<{ job_id: string }>(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...body, ...secret }),
      });
      const job = { jobId: started.job_id, url, body };
      rememberJob(job);
      attach(job);
    } catch (err) {
      startError = err instanceof Error ? err.message : "Failed to start provisioning";
    } finally {
      starting = false;
    }
  }

  function startLocalInstall(): Promise<void> {
    return startJob(LOCAL_INSTALL_URL, {}, takeSudoPassword());
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
    const secret = lastRequest.url === LOCAL_INSTALL_URL ? takeSudoPassword() : {};
    return startJob(lastRequest.url, { ...lastRequest.body, allow_unmet_host: true }, secret);
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
  const pending = readPendingJob();
  if (pending) attach(pending);
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
          Reloading this page reattaches to the install; it keeps running either way.
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
          {#if result.runtimeConfigKept}
            <p>The existing runtime config was kept; the runtime runs it, not the quickstart's.</p>
          {/if}
          {#if result.preserved.length}
            <p>install.sh left these existing configs in place:</p>
            <pre class="onboarding-preflight">{result.preserved.join("\n")}</pre>
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
            {#if result.project}
              {@const project = result.project}
              <button
                type="button"
                class="btn-primary"
                onclick={() => onNavigate(`/projects/${encodeURIComponent(project)}/operate`)}
              >
                Open {project} in Operate
              </button>
            {/if}
            <button
              type="button"
              class={result.project ? "btn-secondary" : "btn-primary"}
              onclick={skip}>Go to projects</button
            >
          {:else}
            {#if canInstallAnyway(result)}
              {#if confirmingUnmet}
                <p class="onboarding-warning">
                  The providers above will start with no devices until the host is fixed and the
                  service restarted.
                </p>
                {#if lastRequest?.url === LOCAL_INSTALL_URL && loopbackClient}
                  <label for="unmet-sudo-password">Sudo password (empty if sudo needs none)</label>
                  <input
                    id="unmet-sudo-password"
                    type="password"
                    autocomplete="off"
                    bind:value={localSudoPassword}
                  />
                {/if}
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
      {#if loopbackClient}
        <div class="form-group">
          <label for="local-sudo-password">
            Sudo password for this device (empty if sudo needs none)
          </label>
          <input
            id="local-sudo-password"
            type="password"
            autocomplete="off"
            disabled={starting}
            bind:value={localSudoPassword}
          />
        </div>
      {:else}
        <p class="onboarding-note">
          A sudo password can be entered only in a browser on this machine (localhost).
        </p>
      {/if}

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

      <p class="onboarding-note">
        On a device that already has anolis installed, install.sh keeps the existing runtime and
        provider configs: they are not replaced with the quickstart's.
      </p>

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
