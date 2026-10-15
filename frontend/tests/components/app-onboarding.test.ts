import { render, screen } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";

import App from "../../src/App.svelte";
import { jsonResponse, pathFromInput } from "./helpers";

/** A shell whose project list holds what a failed onboarding install left. */
function stubApi(firstRun: boolean) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === "/api/projects")
        return Promise.resolve(jsonResponse(200, [{ name: "sim-quickstart", meta: {} }]));
      if (path === "/api/onboarding")
        return Promise.resolve(
          jsonResponse(200, {
            first_run: firstRun,
            has_projects: true,
            has_runtime: !firstRun,
            runtime_path: "/opt/anolis/bin/anolis-runtime",
          }),
        );
      if (path === "/api/status")
        return Promise.resolve(jsonResponse(200, { running: false, active_project: null }));
      if (path === "/api/templates") return Promise.resolve(jsonResponse(200, []));
      if (path === "/api/provider-schemas")
        return Promise.resolve(jsonResponse(200, { providers: {} }));
      return Promise.resolve(jsonResponse(200, {}));
    }),
  );
}

describe("App: first-run onboarding (#424)", () => {
  it("shows onboarding while no deploy has succeeded, even with a project", async () => {
    stubApi(true);
    history.replaceState({}, "", "/");
    render(App);
    expect(await screen.findByText("Welcome to Anolis Workbench")).toBeInTheDocument();
  });

  it("does not show onboarding once a deploy has succeeded", async () => {
    stubApi(false);
    history.replaceState({}, "", "/");
    render(App);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByText("Welcome to Anolis Workbench")).not.toBeInTheDocument();
  });
});
