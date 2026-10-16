import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";

import App from "../../src/App.svelte";
import { createProjectDocument, jsonResponse, pathFromInput } from "./helpers";

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

describe("App: landing in Operate after a local install (#238)", () => {
  it("opens the installed project in Operate and lists it in the project selector", async () => {
    let installed = false;
    const sources: { onmessage: ((ev: MessageEvent<string>) => void) | null }[] = [];
    vi.stubGlobal(
      "EventSource",
      class {
        onmessage: ((ev: MessageEvent<string>) => void) | null = null;
        onerror: ((ev: Event) => void) | null = null;
        constructor() {
          sources.push(this);
        }
        close(): void {}
      },
    );
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const path = pathFromInput(input);
        if (path === "/api/projects")
          return Promise.resolve(
            jsonResponse(200, installed ? [{ name: "sim-quickstart", meta: {} }] : []),
          );
        if (path === "/api/projects/sim-quickstart")
          return Promise.resolve(jsonResponse(200, createProjectDocument("sim-quickstart")));
        if (path === "/api/onboarding")
          return Promise.resolve(
            jsonResponse(200, {
              first_run: true,
              has_projects: false,
              has_runtime: false,
              runtime_path: "",
            }),
          );
        if (path === "/api/provision/install")
          return Promise.resolve(jsonResponse(202, { job_id: "job-1" }));
        if (path === "/api/status")
          return Promise.resolve(jsonResponse(200, { running: false, active_project: null }));
        if (path === "/api/templates") return Promise.resolve(jsonResponse(200, []));
        if (path === "/api/provider-schemas")
          return Promise.resolve(jsonResponse(200, { providers: {} }));
        return Promise.resolve(jsonResponse(200, {}));
      }),
    );
    sessionStorage.clear();
    history.replaceState({}, "", "/");
    render(App);

    await fireEvent.click(await screen.findByRole("button", { name: /Set up this device/ }));
    await waitFor(() => expect(sources.length).toBe(1));
    installed = true;
    sources[0].onmessage?.(
      new MessageEvent("message", {
        data: JSON.stringify({ stage: "done", summary: { project: "sim-quickstart" } }),
      }),
    );
    await fireEvent.click(
      await screen.findByRole("button", { name: "Open sim-quickstart in Operate" }),
    );

    await waitFor(() => expect(window.location.pathname).toBe("/projects/sim-quickstart/operate"));
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "Project" })).toHaveValue("sim-quickstart"),
    );
  });
});
