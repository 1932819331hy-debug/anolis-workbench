<script lang="ts">
  import { onMount } from "svelte";
  import ConfirmModal from "./lib/ConfirmModal.svelte";
  import Home from "./routes/Home.svelte";
  import Fleet from "./routes/Fleet.svelte";
  import Onboarding, { readPendingJob } from "./routes/Onboarding.svelte";
  import Compose from "./routes/Compose.svelte";
  import Commission from "./routes/Commission.svelte";
  import Operate from "./routes/Operate.svelte";
  import { fetchJson } from "./lib/api";
  import { savedLaunch, type SavedLaunch } from "./lib/canonical";
  import type {
    ProjectSummary,
    ProviderSchemasResponse,
    RuntimeStatus,
    ProjectDoc,
    TemplateSummary,
    WorkbenchConfig,
  } from "./lib/contracts";
  import { describeCrossProjectRunningBanner, evaluateNavigationPrompts } from "./lib/guards";

  type WorkspaceName = "compose" | "commission" | "operate";
  type Route = { path: string; project: string | null; workspace: WorkspaceName | null };
  type NavigateOptions = {
    replaceHistory?: boolean;
    historyAlreadySet?: boolean;
    bypassGuards?: boolean;
  };
  const WORKSPACES: WorkspaceName[] = ["compose", "commission", "operate"];

  // ── State ────────────────────────────────────────────────────────────────
  let providerSchemas = $state<ProviderSchemasResponse | null>(null);
  let templates = $state<TemplateSummary[]>([]);
  let projects = $state<ProjectSummary[]>([]);
  // Whether the first /api/projects and /api/status replies have settled: until
  // then the shell says it is loading rather than "No projects" or "Stopped" (#568).
  let projectsLoaded = $state<boolean>(false);
  let statusSettled = $state<boolean>(false);
  let runtimeStatus = $state<RuntimeStatus | null>(null);
  let showOnboarding = $state<boolean>(false);
  let projectName = $state<string | null>(null);
  let system = $state<ProjectDoc | null>(null);
  // The variant open in Compose. Only Compose's selector sets it; it is never
  // written to the project, and dev-launch runs launch.variant instead (#339).
  let editingVariant = $state<string>("");
  // The project as last read from or written to disk, for what Launch runs.
  let saved = $state<SavedLaunch | null>(null);
  let workspace = $state<WorkspaceName | null>(null);
  let dirty = $state<boolean>(false);
  let currentPath = $state<string>("/");
  let commissionRunningForCurrent = $state<boolean>(false);
  let confirmModalOpen = $state<boolean>(false);
  let confirmModalMessage = $state<string>("");
  let pendingConfirmResolve: ((value: boolean) => void) | null = null;
  let lastNavigationId = 0;

  // ── Derived ──────────────────────────────────────────────────────────────
  const running = $derived(Boolean(runtimeStatus?.running));
  const runningProject = $derived(
    typeof runtimeStatus?.active_project === "string" ? runtimeStatus.active_project : null,
  );
  const crossProjectBanner = $derived(
    describeCrossProjectRunningBanner({
      activeProject: projectName,
      runtimeRunning: running,
      runningProject,
    }),
  );

  // ── Navigation ────────────────────────────────────────────────────────────
  function parseRoute(path: string): Route | null {
    if (path === "/") return { path: "/", project: null, workspace: null };
    if (path === "/fleet") return { path: "/fleet", project: null, workspace: null };
    const match = path.match(/^\/projects\/([^/]+)(?:\/(compose|commission|operate))?\/?$/);
    if (!match) return null;
    const project = decodeURIComponent(match[1]);
    const ws = (match[2] || "compose") as WorkspaceName;
    return { path: `/projects/${encodeURIComponent(project)}/${ws}`, project, workspace: ws };
  }

  function updateWindowComposerConfig({ telemetryUrl }: { telemetryUrl?: string }): void {
    const next = { ...(window.__ANOLIS_COMPOSER__ ?? {}) };
    if (typeof telemetryUrl === "string" && telemetryUrl.trim()) {
      next.telemetryUrl = telemetryUrl.trim().replace(/\/$/, "");
    }
    window.__ANOLIS_COMPOSER__ = next;
  }

  function promptNavigationConfirm(message: string): Promise<boolean> {
    if (pendingConfirmResolve) pendingConfirmResolve(false);
    confirmModalMessage = message;
    confirmModalOpen = true;
    return new Promise<boolean>((resolve) => {
      pendingConfirmResolve = resolve;
    });
  }

  function settleNavigationConfirm(value: boolean): void {
    const resolve = pendingConfirmResolve;
    pendingConfirmResolve = null;
    confirmModalOpen = false;
    confirmModalMessage = "";
    if (resolve) resolve(value);
  }

  async function confirmNavigation(route: Route): Promise<boolean> {
    const prompts = evaluateNavigationPrompts({
      dirty,
      currentProject: projectName,
      currentWorkspace: workspace,
      nextProject: route.project,
      nextWorkspace: route.workspace || "compose",
      runtimeRunning: running,
      runningProject: runningProject ?? "",
    });
    for (const p of prompts) {
      const confirmed = await promptNavigationConfirm(p.message);
      if (!confirmed) return false;
    }
    return true;
  }

  async function navigateTo(
    path: string,
    {
      replaceHistory = false,
      historyAlreadySet = false,
      bypassGuards = false,
    }: NavigateOptions = {},
  ): Promise<boolean> {
    const route = parseRoute(path);
    if (!route) {
      if (!historyAlreadySet) history.replaceState({}, "", "/");
      return navigateTo("/", { replaceHistory: true, historyAlreadySet: true, bypassGuards });
    }

    if (!bypassGuards && !(await confirmNavigation(route))) return false;

    const navId = ++lastNavigationId;
    if (!historyAlreadySet) {
      if (replaceHistory) history.replaceState({}, "", route.path);
      else history.pushState({}, "", route.path);
    }

    const projectChanged = route.project !== projectName;
    if (projectChanged) {
      dirty = false;
      editingVariant = "";
    }

    if (projectChanged && route.project) {
      const loaded = await loadProject(route.project);
      if (!loaded) {
        if (navId !== lastNavigationId) return false;
        history.replaceState({}, "", "/");
        await navigateTo("/", {
          replaceHistory: true,
          historyAlreadySet: true,
          bypassGuards: true,
        });
        return false;
      }
    }

    if (!route.project) {
      projectName = null;
      system = null;
      workspace = null;
      // "/" or "/fleet": the shell renders Fleet only when currentPath says so (wb#555).
      currentPath = route.path;
      return true;
    }

    if (projectChanged) {
      projectName = route.project;
      commissionRunningForCurrent = false;
    }

    workspace = route.workspace || "compose";
    currentPath = route.path;
    return true;
  }

  async function loadProject(name: string): Promise<boolean> {
    try {
      const doc = await fetchJson<ProjectDoc>(`/api/projects/${encodeURIComponent(name)}`);
      saved = savedLaunch(doc);
      system = doc;
      projectName = name;
      return true;
    } catch {
      return false;
    }
  }

  async function refreshStatus(): Promise<void> {
    try {
      const status = await fetchJson<RuntimeStatus>("/api/status");
      runtimeStatus = status;
      const nowRunningForCurrent =
        Boolean(status?.running) && status?.active_project === projectName;
      if (workspace === "commission" && nowRunningForCurrent !== commissionRunningForCurrent) {
        commissionRunningForCurrent = nowRunningForCurrent;
      }
    } catch {
      // non-fatal; keep prior status
    } finally {
      statusSettled = true;
    }
  }

  async function refreshWorkbenchConfig(): Promise<void> {
    try {
      const config = await fetchJson<WorkbenchConfig>("/api/config");
      updateWindowComposerConfig({
        telemetryUrl: config.telemetry_url,
      });
    } catch {
      // non-fatal; keep prior globals
    }
  }

  // ── Lifecycle ─────────────────────────────────────────────────────────────
  onMount(() => {
    let statusInterval: ReturnType<typeof setInterval> | null = null;

    const handlePopState = () => {
      void navigateTo(window.location.pathname, {
        replaceHistory: true,
        historyAlreadySet: true,
      }).then((ok) => {
        if (!ok) history.pushState({}, "", currentPath);
      });
    };

    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = "";
    };

    const init = async (): Promise<void> => {
      await Promise.all([
        fetchJson<ProviderSchemasResponse>("/api/provider-schemas")
          .then((c) => {
            providerSchemas = c;
          })
          .catch(() => {}),
        fetchJson<TemplateSummary[]>("/api/templates")
          .then((t) => {
            templates = t;
          })
          .catch(() => {}),
        fetchJson<ProjectSummary[]>("/api/projects")
          .then((p) => {
            projects = p;
          })
          .catch(() => {})
          .finally(() => {
            projectsLoaded = true;
          }),
        refreshWorkbenchConfig(),
        refreshStatus(),
      ]);

      // Onboarding reopens on a provision job it started, which by then has
      // created its project. Otherwise first run lasts until a deploy has
      // succeeded, whatever projects a failed attempt left behind (#424).
      if (readPendingJob()) {
        showOnboarding = true;
      } else {
        try {
          const ob = await fetchJson<{ first_run: boolean }>("/api/onboarding");
          if (ob.first_run) {
            showOnboarding = true;
          }
        } catch {
          // Non-fatal — just show normal home
        }
      }

      await navigateTo(window.location.pathname, { replaceHistory: true, bypassGuards: true });
      statusInterval = setInterval(() => void refreshStatus(), 2000);
    };

    void init();

    window.addEventListener("popstate", handlePopState);
    window.addEventListener("beforeunload", handleBeforeUnload);

    return () => {
      if (pendingConfirmResolve) pendingConfirmResolve(false);
      if (statusInterval !== null) clearInterval(statusInterval);
      window.removeEventListener("popstate", handlePopState);
      window.removeEventListener("beforeunload", handleBeforeUnload);
    };
  });

  // ── UI event handlers ─────────────────────────────────────────────────────
  function onProjectSelect(event: Event): void {
    const select = event.currentTarget as HTMLSelectElement;
    const selected = String(select.value ?? "");
    const ws = workspace || "compose";
    const path = selected ? `/projects/${encodeURIComponent(selected)}/${ws}` : "/";
    // A refused switch ("Stay Here") keeps the current project, so put the
    // selector back on it rather than leave it naming the other (#562).
    void navigateTo(path).then((ok) => {
      if (!ok) select.value = projectName ?? "";
    });
  }

  function onTabClick(ws: WorkspaceName): void {
    if (!projectName) return;
    void navigateTo(`/projects/${encodeURIComponent(projectName)}/${ws}`);
  }

  async function onProjectsRefreshed(): Promise<void> {
    projects = await fetchJson<ProjectSummary[]>("/api/projects").catch(() => projects);
  }
</script>

<header id="shell-topbar">
  <div class="brand-wrap">
    <button id="btn-home" class="ghost-btn" type="button" onclick={() => void navigateTo("/")}>
      Anolis Workbench
    </button>
  </div>

  <div class="project-wrap">
    <label for="project-selector" class="topbar-label">Project</label>
    <select id="project-selector" value={projectName ?? ""} onchange={onProjectSelect}>
      <option value="">No project selected</option>
      {#each projects as project (project.name)}
        <option value={project.name}>{project.name}</option>
      {/each}
    </select>
    {#if dirty}
      <span id="unsaved-indicator" title="Unsaved changes">●</span>
    {/if}
  </div>

  <nav id="workspace-tabs" aria-label="Workspace tabs">
    {#each WORKSPACES as ws (ws)}
      <button
        type="button"
        class="tab-btn"
        class:active={workspace === ws && Boolean(projectName)}
        disabled={!projectName}
        title={projectName
          ? undefined
          : `Open a project to use ${ws.charAt(0).toUpperCase() + ws.slice(1)}.`}
        onclick={() => onTabClick(ws)}>{ws.charAt(0).toUpperCase() + ws.slice(1)}</button
      >
    {/each}
  </nav>

  <div
    id="runtime-indicator"
    class="runtime-indicator"
    class:running={running && Boolean(runningProject)}
    class:stopped={statusSettled && !(running && Boolean(runningProject))}
  >
    {running && runningProject
      ? `Running: ${runningProject}`
      : statusSettled
        ? "Stopped"
        : "Checking runtime…"}
  </div>
</header>

{#if crossProjectBanner}
  <div id="global-banner" class="global-banner">{crossProjectBanner}</div>
{/if}

<main id="shell-main" class:has-banner={Boolean(crossProjectBanner)}>
  {#if showOnboarding && !projectName}
    <Onboarding
      onNavigate={(path) => {
        showOnboarding = false;
        // An install seeds its project after the list was fetched (#238).
        void onProjectsRefreshed();
        void navigateTo(path, { bypassGuards: true });
      }}
    />
  {:else if !projectName && currentPath === "/fleet"}
    <Fleet onNavigate={(path) => void navigateTo(path, { bypassGuards: true })} />
  {:else if !projectName}
    <Home
      {projects}
      {projectsLoaded}
      {templates}
      {runtimeStatus}
      onNavigate={(path) => void navigateTo(path, { bypassGuards: true })}
      {onProjectsRefreshed}
    />
  {:else if workspace === "compose"}
    <!-- Remount per project: a failed save's errors belong to the project
         they were reported for (wb#561). -->
    {#key projectName}
      <Compose
        {projectName}
        {system}
        {providerSchemas}
        {runtimeStatus}
        bind:editingVariant
        onDirty={() => {
          dirty = true;
        }}
        onSaved={() => {
          dirty = false;
          saved = savedLaunch(system);
        }}
      />
    {/key}
  {:else if workspace === "commission"}
    <!-- Remount per project: a preflight result, or one still in flight,
         belongs to the project it ran for and must not gate another's Launch. -->
    {#key projectName}
      <Commission
        {projectName}
        {system}
        {runtimeStatus}
        {commissionRunningForCurrent}
        {editingVariant}
        bind:saved
      />
    {/key}
  {:else if workspace === "operate"}
    <Operate
      {projectName}
      {system}
      {runtimeStatus}
      {editingVariant}
      {saved}
      onNavigate={(path) => void navigateTo(path)}
    />
  {/if}
</main>

<ConfirmModal
  open={confirmModalOpen}
  title="Confirm Navigation"
  message={confirmModalMessage}
  confirmLabel="Continue"
  cancelLabel="Stay Here"
  onConfirm={() => settleNavigationConfirm(true)}
  onCancel={() => settleNavigationConfirm(false)}
/>
