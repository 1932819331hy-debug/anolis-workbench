/**
 * Evaluate which navigation confirmation prompts to show before a route transition.
 * Ported from the legacy shell guards module.
 *
 * @param opts Navigation context
 */
type NavigationPrompt = { id: string; message: string };

type EvaluateNavigationPromptsOptions = {
  dirty: boolean;
  currentProject: string | null;
  currentWorkspace: string | null;
  nextProject: string | null;
  nextWorkspace: string;
  runtimeRunning: boolean;
  runningProject: string;
};

export function evaluateNavigationPrompts({
  dirty,
  currentProject,
  currentWorkspace,
  nextProject,
  nextWorkspace,
  runtimeRunning,
  runningProject,
}: EvaluateNavigationPromptsOptions): NavigationPrompt[] {
  const prompts: NavigationPrompt[] = [];

  const hasCurrentProject = typeof currentProject === "string" && currentProject !== "";
  const hasNextProject = typeof nextProject === "string" && nextProject !== "";

  const switchingProject = hasCurrentProject && hasNextProject && nextProject !== currentProject;
  const switchingWorkspace =
    hasCurrentProject &&
    hasNextProject &&
    nextProject === currentProject &&
    typeof currentWorkspace === "string" &&
    currentWorkspace !== "" &&
    nextWorkspace !== currentWorkspace;
  const leavingProjectContext = hasCurrentProject && !hasNextProject;

  if (dirty && (switchingProject || switchingWorkspace || leavingProjectContext)) {
    prompts.push({
      id: "unsaved_changes",
      message: "You have unsaved Compose edits. Continue and discard unsaved changes?",
    });
  }

  if (
    hasNextProject &&
    runtimeRunning &&
    typeof runningProject === "string" &&
    runningProject !== "" &&
    runningProject !== nextProject &&
    nextProject !== currentProject
  ) {
    prompts.push({
      id: "switch_while_running",
      message:
        `Project "${runningProject}" is currently running. ` +
        `Switching to "${nextProject}" will not stop it. Continue?`,
    });
  }

  return prompts;
}

/**
 * Returns a banner message when the active project differs from the running project,
 * or an empty string when no banner is needed.
 *
 * @param opts Banner context
 */
type DescribeCrossProjectRunningBannerOptions = {
  activeProject: string | null;
  runtimeRunning: boolean;
  runningProject: string | null;
};

export function describeCrossProjectRunningBanner({
  activeProject,
  runtimeRunning,
  runningProject,
}: DescribeCrossProjectRunningBannerOptions): string {
  if (
    typeof activeProject === "string" &&
    activeProject !== "" &&
    runtimeRunning &&
    typeof runningProject === "string" &&
    runningProject !== "" &&
    runningProject !== activeProject
  ) {
    return (
      `Runtime for "${runningProject}" remains active. ` +
      `Launch for "${activeProject}" is blocked until you stop the running runtime.`
    );
  }
  return "";
}

/**
 * True when the page was loaded from the workbench's own machine. The server
 * speaks plain HTTP, so a password typed on another host would cross the
 * network; the server refuses one from a non-loopback client as well.
 */
export function isLoopbackHost(hostname: string): boolean {
  return hostname === "127.0.0.1" || hostname === "localhost" || hostname === "[::1]";
}
