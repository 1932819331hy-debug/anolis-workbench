<script lang="ts">
  import ConfirmModal from "../lib/ConfirmModal.svelte";
  import { ApiResponseError, fetchJson } from "../lib/api";
  import type {
    ApiErrorResponse,
    ProjectSummary,
    RuntimeStatus,
    TemplateSummary,
  } from "../lib/contracts";

  let {
    projects,
    templates,
    runtimeStatus = null,
    onNavigate,
    onProjectsRefreshed,
  }: {
    projects: ProjectSummary[];
    templates: TemplateSummary[];
    runtimeStatus?: RuntimeStatus | null;
    onNavigate: (path: string) => void;
    onProjectsRefreshed: () => Promise<void> | void;
  } = $props();

  let createName = $state<string>("");
  let createTemplate = $state<string>("");
  let createError = $state<string>("");
  let creating = $state<boolean>(false);

  // ── Import (machine-profile) state ────────────────────────────────────────
  let importPath = $state<string>("");
  let importName = $state<string>("");
  let importError = $state<string>("");
  let importErrors = $state<Array<{ path?: string; message?: string }>>([]);
  let importing = $state<boolean>(false);

  // ── Update state ──────────────────────────────────────────────────────────
  interface UpdateCheckResult {
    current_version: string;
    latest_version: string | null;
    update_available: boolean;
    error: string | null;
  }
  let updateInfo = $state<UpdateCheckResult | null>(null);
  let updateChecking = $state<boolean>(false);
  let updateRunning = $state<boolean>(false);
  let updateFeedback = $state<string>("");
  let updateIsError = $state<boolean>(false);

  // ── Rollback state ────────────────────────────────────────────────────────
  let rollbackRunning = $state<boolean>(false);
  let rollbackFeedback = $state<string>("");
  let rollbackIsError = $state<boolean>(false);
  let rollbackConfirmOpen = $state<boolean>(false);
  // install.sh --rollback reports nothing about what <prefix>/.prev holds, so
  // the target version cannot be named here (#338). /api/status does not say
  // whether a running runtime is the installed service or a dev launch, so
  // the message names the project without claiming where it came from.
  const rollbackMessage = $derived(
    "Rollback restores the runtime and provider binaries from the previous install " +
      "and restarts the installed runtime service. Anything that service is driving " +
      "stops while it restarts. The version it rolls back to is not reported." +
      (runtimeStatus?.running && runtimeStatus.active_project
        ? ` A runtime is running project "${runtimeStatus.active_project}" on this machine.`
        : ""),
  );

  $effect(() => {
    if (templates.length > 0 && !createTemplate) {
      createTemplate = templates[0].id;
    }
  });

  function validProjectName(name: string): boolean {
    return /^[a-zA-Z0-9_-]{1,64}$/.test(name);
  }

  async function handleCreate() {
    createError = "";
    if (!validProjectName(createName)) {
      createError = "Project name must be 1-64 chars: letters, digits, hyphens, underscores.";
      return;
    }
    if (!createTemplate) {
      createError = "Template is required.";
      return;
    }
    creating = true;
    try {
      await fetchJson("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: createName, template: createTemplate }),
      });
      const name = createName;
      createName = "";
      await onProjectsRefreshed();
      onNavigate(`/projects/${encodeURIComponent(name)}/compose`);
    } catch (err) {
      createError = err instanceof Error ? err.message : "Failed to create project";
    } finally {
      creating = false;
    }
  }

  async function handleImport() {
    importError = "";
    importErrors = [];
    const path = importPath.trim();
    if (!path) {
      importError = "Path to the machine-profile project directory is required.";
      return;
    }
    const name = importName.trim();
    if (name && !validProjectName(name)) {
      importError = "Project name must be 1-64 chars: letters, digits, hyphens, underscores.";
      return;
    }
    importing = true;
    try {
      const result = await fetchJson<{ name: string; warnings?: string[] }>(
        "/api/projects/import",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(name ? { path, name } : { path }),
        },
      );
      importPath = "";
      importName = "";
      await onProjectsRefreshed();
      // Any import warnings are persisted in the sidecar and shown by the
      // profile view we navigate to.
      onNavigate(`/projects/${encodeURIComponent(result.name)}/compose`);
    } catch (err) {
      if (err instanceof ApiResponseError && err.payload && typeof err.payload === "object") {
        const payload = err.payload as ApiErrorResponse;
        importErrors = Array.isArray(payload.errors) ? payload.errors : [];
      }
      importError = err instanceof Error ? err.message : "Failed to import project";
    } finally {
      importing = false;
    }
  }

  // ── Update check ──────────────────────────────────────────────────────────
  async function checkForUpdate() {
    updateChecking = true;
    updateFeedback = "";
    updateIsError = false;
    try {
      updateInfo = await fetchJson<UpdateCheckResult>("/api/update-check");
      if (updateInfo.error) {
        updateFeedback = `Check failed: ${updateInfo.error}`;
        updateIsError = true;
      } else if (updateInfo.update_available) {
        updateFeedback = `Update available: v${updateInfo.latest_version}`;
      } else {
        updateFeedback = `Up to date (v${updateInfo.current_version})`;
      }
    } catch (err) {
      updateFeedback = `Check failed: ${err instanceof Error ? err.message : String(err)}`;
      updateIsError = true;
    } finally {
      updateChecking = false;
    }
  }

  async function doUpdate() {
    updateRunning = true;
    updateFeedback = "";
    updateIsError = false;
    try {
      const res = await fetchJson<{ success: boolean; version?: string; error?: string }>(
        "/api/update",
        { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
      );
      if (res.success) {
        updateFeedback = `Updated to v${res.version ?? "latest"}. Restart workbench to apply.`;
        updateInfo = null;
      } else {
        updateFeedback = `Update failed: ${res.error ?? "unknown error"}`;
        updateIsError = true;
      }
    } catch (err) {
      updateFeedback = `Update failed: ${err instanceof Error ? err.message : String(err)}`;
      updateIsError = true;
    } finally {
      updateRunning = false;
    }
  }

  // ── Rollback ──────────────────────────────────────────────────────────────
  async function doRollback() {
    rollbackRunning = true;
    rollbackFeedback = "";
    rollbackIsError = false;
    try {
      const res = await fetchJson<{
        success: boolean;
        output: string;
        error: string | null;
      }>("/api/rollback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (res.success) {
        rollbackFeedback = "Rolled back to previous binaries (service restarted)";
      } else {
        rollbackFeedback = res.error ?? "Nothing to rollback";
        rollbackIsError = true;
      }
    } catch (err) {
      rollbackFeedback = `Rollback failed: ${err instanceof Error ? err.message : String(err)}`;
      rollbackIsError = true;
    } finally {
      rollbackRunning = false;
    }
  }
</script>

<section id="workspace-home" class="workspace visible">
  <div class="workspace-header">
    <h1>Commissioning Workbench</h1>
    <p>Choose a project to start or create a new one from a template.</p>
  </div>

  <div class="home-grid">
    <div class="home-card">
      <h2>Existing Projects</h2>
      {#if projects.length === 0}
        <ul class="home-project-list">
          <li class="placeholder">No projects yet.</li>
        </ul>
      {:else}
        <ul class="home-project-list">
          {#each projects as project (project.name)}
            <li class="home-project-item">
              <span
                >{project.name}{#if project.authored === false}
                  <span class="badge" title="Imported machine profile — carried verbatim"
                    >imported</span
                  >{/if}</span
              >
              <button
                type="button"
                class="btn-secondary btn-sm"
                onclick={() => onNavigate(`/projects/${encodeURIComponent(project.name)}/compose`)}
                >Open</button
              >
            </li>
          {/each}
        </ul>
      {/if}
    </div>

    <div class="home-card">
      <h2>Create Project</h2>
      <div class="form-group">
        <label for="create-project-name">Project name</label>
        <input
          id="create-project-name"
          type="text"
          maxlength="64"
          placeholder="my-system"
          autocomplete="off"
          spellcheck="false"
          bind:value={createName}
        />
      </div>
      <div class="form-group">
        <label for="create-project-template">Template</label>
        <select
          id="create-project-template"
          bind:value={createTemplate}
          disabled={templates.length === 0}
        >
          {#if templates.length === 0}
            <option value="">No templates available</option>
          {:else}
            {#each templates as t (t.id)}
              <option value={t.id}>{t.meta?.name ?? t.id}</option>
            {/each}
          {/if}
        </select>
      </div>
      <button
        id="btn-create-project"
        type="button"
        class="btn-primary"
        disabled={creating}
        onclick={handleCreate}>{creating ? "Creating…" : "Create Project"}</button
      >
      {#if createError}
        <p class="field-error">{createError}</p>
      {/if}
    </div>

    <div class="home-card">
      <h2>Import Machine Profile</h2>
      <p class="field-note">
        Imports a canonical machine-profile project directory verbatim (read-only; deploy-focused).
      </p>
      <div class="form-group">
        <label for="import-project-path">Profile directory</label>
        <input
          id="import-project-path"
          type="text"
          spellcheck="false"
          style="font-family:monospace"
          placeholder="/path/to/anolis-projects/projects/bioreactor-v1"
          bind:value={importPath}
        />
      </div>
      <div class="form-group">
        <label for="import-project-name">Project name (optional)</label>
        <input
          id="import-project-name"
          type="text"
          maxlength="64"
          placeholder="defaults to the directory name"
          autocomplete="off"
          spellcheck="false"
          bind:value={importName}
        />
      </div>
      <button
        id="btn-import-project"
        type="button"
        class="btn-primary"
        disabled={importing}
        onclick={handleImport}>{importing ? "Importing…" : "Import Project"}</button
      >
      {#if importError}
        <p class="field-error">{importError}</p>
        {#if importErrors.length > 0}
          <ul class="field-error">
            {#each importErrors as e, i (i)}
              <li>{e.message ?? "Validation error"}</li>
            {/each}
          </ul>
        {/if}
      {/if}
    </div>

    <!-- System Updates -->
    <div class="home-card">
      <h2>System</h2>
      <div class="system-update-row">
        <button
          type="button"
          class="btn-secondary btn-sm"
          disabled={updateChecking}
          onclick={checkForUpdate}
        >
          {updateChecking ? "Checking…" : "Check for Updates"}
        </button>
        {#if updateInfo?.update_available}
          <button
            type="button"
            class="btn-primary btn-sm"
            disabled={updateRunning}
            onclick={doUpdate}
          >
            {updateRunning ? "Updating…" : `Update to v${updateInfo.latest_version}`}
          </button>
        {/if}
        <button
          type="button"
          class="btn-secondary btn-sm"
          disabled={rollbackRunning}
          onclick={() => (rollbackConfirmOpen = true)}
        >
          {rollbackRunning ? "Rolling back…" : "Rollback"}
        </button>
      </div>
      {#if updateFeedback}
        <p
          class="system-feedback"
          style="color: {updateIsError ? 'var(--feedback-error)' : 'var(--feedback-ok)'}"
        >
          {updateFeedback}
        </p>
      {/if}
      {#if rollbackFeedback}
        <p
          class="system-feedback"
          style="color: {rollbackIsError ? 'var(--feedback-error)' : 'var(--feedback-ok)'}"
        >
          {rollbackFeedback}
        </p>
      {/if}
      <div class="system-update-row" style="margin-top: 0.5rem">
        <button type="button" class="btn-secondary btn-sm" onclick={() => onNavigate("/fleet")}>
          Fleet Dashboard
        </button>
      </div>
    </div>
  </div>
</section>

<ConfirmModal
  open={rollbackConfirmOpen}
  title="Roll back installed binaries?"
  message={rollbackMessage}
  confirmLabel="Roll back and restart"
  onConfirm={() => {
    rollbackConfirmOpen = false;
    void doRollback();
  }}
  onCancel={() => (rollbackConfirmOpen = false)}
/>
