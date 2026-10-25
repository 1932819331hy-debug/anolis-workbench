import { describe, expect, it } from "vitest";

import { describeCrossProjectRunningBanner, evaluateNavigationPrompts } from "../../src/lib/guards";

describe("evaluateNavigationPrompts", () => {
  it("returns no prompts when no risky transition conditions are met", () => {
    const prompts = evaluateNavigationPrompts({
      dirty: false,
      currentProject: "alpha",
      currentWorkspace: "compose",
      nextProject: "alpha",
      nextWorkspace: "compose",
      runtimeRunning: false,
      runningProject: "",
    });
    expect(prompts).toEqual([]);
  });

  // A tab switch keeps the Compose draft, so a discard prompt there is false (#562).
  it("does not prompt for unsaved changes when switching workspace in same project", () => {
    const prompts = evaluateNavigationPrompts({
      dirty: true,
      currentProject: "alpha",
      currentWorkspace: "compose",
      nextProject: "alpha",
      nextWorkspace: "commission",
      runtimeRunning: false,
      runningProject: "",
    });
    expect(prompts).toEqual([]);
  });

  it("requires unsaved-change confirmation when switching project", () => {
    const prompts = evaluateNavigationPrompts({
      dirty: true,
      currentProject: "alpha",
      currentWorkspace: "compose",
      nextProject: "beta",
      nextWorkspace: "compose",
      runtimeRunning: false,
      runningProject: "",
    });
    expect(prompts.map((p) => p.id)).toEqual(["unsaved_changes"]);
  });

  it("requires unsaved-change confirmation when leaving project context", () => {
    const prompts = evaluateNavigationPrompts({
      dirty: true,
      currentProject: "alpha",
      currentWorkspace: "compose",
      nextProject: null,
      nextWorkspace: "compose",
      runtimeRunning: false,
      runningProject: "",
    });
    expect(prompts).toHaveLength(1);
    expect(prompts[0].id).toBe("unsaved_changes");
  });

  it("warns when switching to another project while runtime is running", () => {
    const prompts = evaluateNavigationPrompts({
      dirty: false,
      currentProject: "alpha",
      currentWorkspace: "compose",
      nextProject: "beta",
      nextWorkspace: "compose",
      runtimeRunning: true,
      runningProject: "alpha",
    });
    expect(prompts).toHaveLength(1);
    expect(prompts[0].id).toBe("switch_while_running");
    expect(prompts[0].message).toContain("will not stop it");
  });

  it("does not warn when runtime is running for same destination project", () => {
    const prompts = evaluateNavigationPrompts({
      dirty: false,
      currentProject: "alpha",
      currentWorkspace: "compose",
      nextProject: "alpha",
      nextWorkspace: "commission",
      runtimeRunning: true,
      runningProject: "alpha",
    });
    expect(prompts).toEqual([]);
  });
});

describe("describeCrossProjectRunningBanner", () => {
  it("returns a banner when active project differs from running project", () => {
    const message = describeCrossProjectRunningBanner({
      activeProject: "beta",
      runtimeRunning: true,
      runningProject: "alpha",
    });
    expect(message).toContain('Runtime for "alpha" remains active.');
    expect(message).toContain('Launch for "beta" is blocked');
  });

  it("returns empty string when no cross-project conflict exists", () => {
    expect(
      describeCrossProjectRunningBanner({
        activeProject: "alpha",
        runtimeRunning: true,
        runningProject: "alpha",
      }),
    ).toBe("");
    expect(
      describeCrossProjectRunningBanner({
        activeProject: "alpha",
        runtimeRunning: false,
        runningProject: "alpha",
      }),
    ).toBe("");
  });
});
