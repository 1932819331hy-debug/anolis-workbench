import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";

import App from "../../src/App.svelte";
import Commission from "../../src/routes/Commission.svelte";
import Operate from "../../src/routes/Operate.svelte";
import { activeVariant, AUTOMATION_VARIANT, variantRelpath } from "../../src/lib/canonical";
import type { ProjectDocument } from "../../src/lib/contracts";
import { createProjectDocument, createRuntimeStatus, jsonResponse, pathFromInput } from "./helpers";
import { reactive } from "./helpers.svelte";

const BANNER = "Editing automation; Launch runs manual";

/** demo with both variants. `launch` undefined means an old project that never wrote it. */
function twoVariantDoc(launch?: string): ProjectDocument {
  const doc = createProjectDocument("demo");
  doc.variants[AUTOMATION_VARIANT] = {
    ...structuredClone(doc.variants.manual),
    automation: { enabled: true },
  };
  doc.profile.runtime_profiles[AUTOMATION_VARIANT] = variantRelpath(AUTOMATION_VARIANT);
  if (launch === undefined) delete doc.launch;
  else doc.launch = { variant: launch };
  return doc;
}

function launchSelect(): HTMLSelectElement {
  return screen.getByRole("combobox", { name: "Dev-launch variant" }) as HTMLSelectElement;
}

function putBodies(fetchMock: ReturnType<typeof vi.fn>): ProjectDocument[] {
  return fetchMock.mock.calls
    .filter(([, init]) => (init as RequestInit | undefined)?.method === "PUT")
    .map(([, init]) => JSON.parse(String((init as RequestInit).body)) as ProjectDocument);
}

const PASSING_PREFLIGHT = {
  ok: true,
  checks: [{ name: "Runtime binary exists", ok: true, error: null, hint: null }],
  summary: { passed: 1, skipped: 0, failed: 0 },
};

/** Serve `saved` as the project on disk; record every request. */
function serveSaved(saved: ProjectDocument) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = pathFromInput(input);
    if (path === "/api/projects/demo" && (init?.method ?? "GET") === "GET")
      return jsonResponse(200, structuredClone(saved));
    return jsonResponse(200, {});
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** The control is enabled once the saved project has loaded. */
async function loadedLaunchSelect(): Promise<HTMLSelectElement> {
  await waitFor(() => expect(launchSelect()).toBeEnabled());
  return launchSelect();
}

/** What the shell knows about the project on disk: its variants and launch variant. */
function savedOf(doc: ProjectDocument) {
  const launch = activeVariant(doc);
  return {
    variants: Object.keys(doc.variants ?? {}),
    launch,
    port: doc.variants?.[launch]?.http?.port ?? null,
  };
}

function renderCommission(
  system: ProjectDocument,
  editingVariant = "",
  savedDoc: ProjectDocument = system,
) {
  return render(Commission, {
    props: {
      projectName: "demo",
      system,
      runtimeStatus: createRuntimeStatus(),
      commissionRunningForCurrent: false,
      editingVariant,
      saved: savedOf(savedDoc),
    },
  });
}

describe("Commission: the dev-launch variant control (#339)", () => {
  it("shows launch.variant", async () => {
    serveSaved(twoVariantDoc(AUTOMATION_VARIANT));
    renderCommission(twoVariantDoc(AUTOMATION_VARIANT));
    expect((await loadedLaunchSelect()).value).toBe(AUTOMATION_VARIANT);
  });

  it("shows manual for a project that never wrote launch.variant", async () => {
    const doc = twoVariantDoc();
    // Key order would make "the first variant" automation.
    doc.variants = { automation: doc.variants.automation, manual: doc.variants.manual };
    serveSaved(doc);
    renderCommission(structuredClone(doc), "", doc);
    expect((await loadedLaunchSelect()).value).toBe("manual");
  });

  it("does not offer a variant that exists only in unsaved edits", async () => {
    // automation was added in Compose and not saved yet.
    const fetchMock = serveSaved(createProjectDocument("demo"));
    const system = reactive(twoVariantDoc("manual"));
    renderCommission(system, "", createProjectDocument("demo"));

    const select = await loadedLaunchSelect();
    const option = select.querySelector(`option[value="${AUTOMATION_VARIANT}"]`);
    expect(option === null || (option as HTMLOptionElement).disabled).toBe(true);
    if (option) expect(option.textContent).toMatch(/save/i);

    await fireEvent.change(select, { target: { value: AUTOMATION_VARIANT } });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(putBodies(fetchMock)).toHaveLength(0);
    expect(system.launch?.variant).toBe("manual");
    expect(launchSelect().value).toBe("manual");
  });

  it.each([
    ["a variant only in unsaved edits", AUTOMATION_VARIANT],
    ["a variant that exists nowhere", "gone"],
  ])("shows manual when launch.variant names %s, and does not rewrite it", async (_what, named) => {
    const saved = createProjectDocument("demo");
    saved.launch = { variant: named };
    const fetchMock = serveSaved(saved);
    const system = reactive(twoVariantDoc(named));
    renderCommission(system, "", saved);

    expect((await loadedLaunchSelect()).value).toBe("manual");
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(putBodies(fetchMock)).toHaveLength(0);
    expect(system.launch?.variant).toBe(named);
  });

  it("writes launch.variant into the saved project, and nothing else from memory", async () => {
    const saved = twoVariantDoc("manual");
    const fetchMock = serveSaved(saved);

    // An unsaved Compose edit is in memory; choosing the launch variant must
    // not save it behind the user's back.
    const system = reactive(twoVariantDoc("manual"));
    system.variants.manual.logging = { level: "debug" };
    renderCommission(system);

    await fireEvent.change(await loadedLaunchSelect(), { target: { value: AUTOMATION_VARIANT } });

    await waitFor(() => expect(putBodies(fetchMock)).toHaveLength(1));
    const [body] = putBodies(fetchMock);
    expect(body.launch?.variant).toBe(AUTOMATION_VARIANT);
    expect(body.variants.manual.logging).toEqual({ level: "info" });
    await waitFor(() => expect(system.launch?.variant).toBe(AUTOMATION_VARIANT));
    expect(launchSelect().value).toBe(AUTOMATION_VARIANT);
  });
});

describe("the edited/launched mismatch banner (#339)", () => {
  it.each([
    ["Commission", (doc: ProjectDocument, editing: string) => renderCommission(doc, editing)],
    [
      "Operate",
      (doc: ProjectDocument, editing: string) =>
        render(Operate, {
          props: {
            projectName: "demo",
            system: doc,
            runtimeStatus: createRuntimeStatus(),
            editingVariant: editing,
          },
        }),
    ],
  ])("%s shows it only on a mismatch", async (_name, renderView) => {
    const cases: Array<[string | undefined, string, boolean]> = [
      ["manual", AUTOMATION_VARIANT, true],
      [undefined, AUTOMATION_VARIANT, true],
      ["manual", "manual", false],
      [undefined, "", false],
      [AUTOMATION_VARIANT, AUTOMATION_VARIANT, false],
    ];
    for (const [launch, editing, shown] of cases) {
      serveSaved(twoVariantDoc(launch));
      const { unmount } = renderView(twoVariantDoc(launch), editing);
      if (_name === "Commission") await loadedLaunchSelect();
      const banner = screen.queryByTestId("launch-variant-mismatch");
      if (shown) expect(banner, `${launch} / ${editing}`).toHaveTextContent(BANNER);
      else expect(banner, `${launch} / ${editing}`).toBeNull();
      unmount();
    }
  });

  it("does not block Launch or ask for confirmation", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === "/api/projects/demo") return jsonResponse(200, twoVariantDoc("manual"));
      if (path === "/api/status") return jsonResponse(200, { running: false });
      if (path === "/api/projects/demo/preflight") return jsonResponse(200, PASSING_PREFLIGHT);
      return jsonResponse(200, { ok: true });
    });
    vi.stubGlobal("fetch", fetchMock);
    const confirmSpy = vi.fn(() => false);
    vi.stubGlobal("confirm", confirmSpy);

    renderCommission(twoVariantDoc("manual"), AUTOMATION_VARIANT);
    expect(screen.getByTestId("launch-variant-mismatch")).toBeInTheDocument();

    await fireEvent.click(screen.getByRole("button", { name: "Preflight Check" }));
    await screen.findByText("Runtime binary exists");
    expect(screen.getByRole("button", { name: /Launch/ })).toBeEnabled();
    await fireEvent.click(screen.getByRole("button", { name: /Launch/ }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([input]) => pathFromInput(input) === "/api/projects/demo/launch",
        ),
      ).toBe(true);
    });
    expect(confirmSpy).not.toHaveBeenCalled();
  });
});

describe("App: the Compose selector and the launch variant (#339)", () => {
  function stubApi(saved: ProjectDocument) {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathFromInput(input);
      if (path === "/api/projects")
        return Promise.resolve(jsonResponse(200, [{ name: "demo", meta: {} }]));
      if (path === "/api/projects/demo" && (init?.method ?? "GET") === "GET")
        return Promise.resolve(jsonResponse(200, structuredClone(saved)));
      if (path === "/api/status")
        return Promise.resolve(jsonResponse(200, { running: false, active_project: null }));
      if (path === "/api/templates") return Promise.resolve(jsonResponse(200, []));
      if (path === "/api/provider-schemas")
        return Promise.resolve(jsonResponse(200, { providers: {} }));
      return Promise.resolve(jsonResponse(200, {}));
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it.each([["manual"], [undefined]])(
    "with launch.variant %s: choosing automation in Compose moves only the banner",
    async (launch) => {
      const fetchMock = stubApi(twoVariantDoc(launch));
      history.replaceState({}, "", "/projects/demo/compose");
      render(App);

      const select = (await screen.findByLabelText("Runtime variant")) as HTMLSelectElement;
      expect(select.value).toBe("manual");
      await fireEvent.change(select, { target: { value: AUTOMATION_VARIANT } });

      await fireEvent.click(screen.getByRole("button", { name: "Commission" }));
      expect(await screen.findByTestId("launch-variant-mismatch")).toHaveTextContent(BANNER);
      expect(launchSelect().value).toBe("manual");

      await fireEvent.click(screen.getByRole("button", { name: "Operate" }));
      expect(await screen.findByTestId("launch-variant-mismatch")).toHaveTextContent(BANNER);

      // Selecting a variant to look at it wrote nothing.
      expect(putBodies(fetchMock)).toHaveLength(0);

      // Pointing dev-launch at the edited variant clears the banner.
      await fireEvent.click(screen.getByRole("button", { name: "Commission" }));
      await fireEvent.change(await screen.findByRole("combobox", { name: "Dev-launch variant" }), {
        target: { value: AUTOMATION_VARIANT },
      });
      await waitFor(() => {
        expect(screen.queryByTestId("launch-variant-mismatch")).not.toBeInTheDocument();
      });
      expect(putBodies(fetchMock).map((b) => b.launch?.variant)).toEqual([AUTOMATION_VARIANT]);
      await fireEvent.click(screen.getByRole("button", { name: "Operate" }));
      await screen.findByRole("heading", { name: "Operate" });
      expect(screen.queryByTestId("launch-variant-mismatch")).not.toBeInTheDocument();
    },
  );

  /** Like stubApi, but the project on disk is what the last PUT stored. */
  function stubDisk(initial: ProjectDocument, running = false) {
    let disk = structuredClone(initial);
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathFromInput(input);
      if (path === "/api/projects/demo" && init?.method === "PUT") {
        disk = JSON.parse(String(init.body)) as ProjectDocument;
        return Promise.resolve(jsonResponse(200, {}));
      }
      if (path === "/api/projects/demo")
        return Promise.resolve(jsonResponse(200, structuredClone(disk)));
      if (path === "/api/projects")
        return Promise.resolve(jsonResponse(200, [{ name: "demo", meta: {} }]));
      if (path === "/api/status")
        return Promise.resolve(
          jsonResponse(200, { running, active_project: running ? "demo" : null }),
        );
      if (path === "/api/templates") return Promise.resolve(jsonResponse(200, []));
      if (path === "/api/provider-schemas")
        return Promise.resolve(jsonResponse(200, { providers: {} }));
      if (path === "/v0/runtime/status")
        return Promise.resolve(jsonResponse(500, { error: "runtime unreachable" }));
      return Promise.resolve(jsonResponse(200, {}));
    });
    vi.stubGlobal("fetch", fetchMock);
    return { fetchMock, disk: () => disk };
  }

  async function tab(name: string): Promise<void> {
    await fireEvent.click(screen.getByRole("button", { name }));
  }

  it("removing the launch variant in Compose unsets it, so re-adding it does not relaunch it", async () => {
    const api = stubDisk(twoVariantDoc("manual"));
    history.replaceState({}, "", "/projects/demo/commission");
    render(App);

    // Commission points dev-launch at automation.
    await waitFor(() => expect(launchSelect()).toBeEnabled());
    await fireEvent.change(launchSelect(), { target: { value: AUTOMATION_VARIANT } });
    await waitFor(() => expect(api.disk().launch?.variant).toBe(AUTOMATION_VARIANT));

    // Compose removes automation and saves: Launch runs manual.
    await tab("Compose");
    await fireEvent.click(await screen.findByRole("button", { name: /Remove automation variant/ }));
    await fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.disk().variants.automation).toBeUndefined());
    expect(api.disk().launch?.variant).toBeUndefined();

    // Compose re-adds automation and saves: nobody chose it in Commission.
    await fireEvent.click(screen.getByRole("button", { name: "+ Automation variant" }));
    await fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.disk().variants.automation).toBeDefined());
    expect(api.disk().launch?.variant).toBeUndefined();

    await tab("Commission");
    await waitFor(() => expect(launchSelect()).toBeEnabled());
    expect(launchSelect().value).toBe("manual");
  });

  it("Operate's banner agrees with Commission's when the saved launch variant differs from memory", async () => {
    // automation is saved as the launch variant, then removed in Compose
    // without saving: Launch still runs automation.
    stubDisk(twoVariantDoc(AUTOMATION_VARIANT));
    history.replaceState({}, "", "/projects/demo/compose");
    render(App);

    await fireEvent.click(await screen.findByRole("button", { name: /Remove automation variant/ }));
    const expected = "Editing manual; Launch runs automation";

    // The removal stays unsaved across tab switches, which keep the draft.
    await tab("Commission");
    expect(await screen.findByTestId("launch-variant-mismatch")).toHaveTextContent(expected);
    await tab("Operate");
    await screen.findByRole("heading", { name: "Operate" });
    expect(await screen.findByTestId("launch-variant-mismatch")).toHaveTextContent(expected);
  });

  it("Operate's safe-state note names the file of the variant Launch runs", async () => {
    // automation is saved as the launch variant and is running; removing it
    // in Compose without saving does not change what runs.
    stubDisk(twoVariantDoc(AUTOMATION_VARIANT), true);
    history.replaceState({}, "", "/projects/demo/compose");
    render(App);

    await fireEvent.click(await screen.findByRole("button", { name: /Remove automation variant/ }));
    await tab("Operate");

    expect(await screen.findByTestId("safe-state-fix")).toHaveTextContent(
      "config/anolis-runtime.automation.yaml",
    );
  });
});

describe("Commission: the running port (#339)", () => {
  it("is the saved launch variant's port, not the in-memory one", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(200, {})),
    );
    // Saved: automation launches on 9090. In memory automation was removed,
    // so the in-memory reading would fall back to manual's 8080.
    const savedDoc = twoVariantDoc(AUTOMATION_VARIANT);
    savedDoc.variants.automation.http = { ...savedDoc.variants.manual.http!, port: 9090 };
    const system = createProjectDocument("demo");
    system.launch = { variant: AUTOMATION_VARIANT };
    render(Commission, {
      props: {
        projectName: "demo",
        system,
        runtimeStatus: createRuntimeStatus({ running: true, active_project: "demo" }),
        commissionRunningForCurrent: true,
        saved: savedOf(savedDoc),
      },
    });
    expect(screen.getByText(/Running — demo on port/)).toHaveTextContent("port 9090");
  });
});
