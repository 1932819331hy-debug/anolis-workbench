import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../../src/App.svelte";
import Onboarding from "../../src/routes/Onboarding.svelte";
import { jsonResponse, pathFromInput, setPageUrl } from "./helpers";

// install.sh v0.1.43's host-preflight block, as the deploy job carries it
// (ANSI stripped), from a run with the service user out of the bus's group.
const UNMET_BLOCK = [
  "✓ host preflight: anolis-runtime runs as anolis",
  "✗ host preflight: bread0: host requirements unmet",
  "    i2c.bus_access (unmet): anolis cannot open /dev/i2c-1 read-write (group 'i2c', mode 0660)",
  "      fix: add anolis to group 'i2c', the group that owns the node on this host, and start a new login or restart the service",
  "  Fix the host (the project's host prep, then any reboot it asks for) and re-run,",
  "  or pass --allow-unmet-host to install now and fix the host after.",
  "✗ host preflight: requirements unmet",
];

/** An EventSource the test drives: every instance is recorded, and emit()
 *  delivers a provision-job event the way the SSE stream frames it. */
class DrivenEventSource {
  static instances: DrivenEventSource[] = [];
  url: string;
  closed = false;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    DrivenEventSource.instances.push(this);
  }

  emit(data: unknown): void {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(data) }));
  }

  fail(): void {
    this.onerror?.(new Event("error"));
  }

  close(): void {
    this.closed = true;
  }
}

/** `probeStatus` answers a plain GET of a job's status URL, which the view
 *  makes after a dropped stream to learn whether the server still has it. */
function setup(jobIds: string[] = ["job-1"], probeStatus = 200) {
  DrivenEventSource.instances = [];
  vi.stubGlobal("EventSource", DrivenEventSource as unknown as typeof EventSource);
  const posts: { url: string; body: Record<string, unknown> }[] = [];
  const ids = [...jobIds];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/onboarding") {
      return jsonResponse(200, {
        first_run: true,
        has_projects: false,
        has_runtime: false,
        runtime_path: "",
      });
    }
    if (url.startsWith("/api/provision/status/")) return jsonResponse(probeStatus, {});
    posts.push({ url, body: JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown> });
    return jsonResponse(202, { job_id: ids.shift() ?? "job-x" });
  });
  vi.stubGlobal("fetch", fetchMock);
  const onNavigate = vi.fn();
  render(Onboarding, { props: { onNavigate } });
  return { posts, onNavigate };
}

async function startLocal(): Promise<DrivenEventSource> {
  await fireEvent.click(screen.getByRole("button", { name: /Set up this device/ }));
  await waitFor(() => expect(DrivenEventSource.instances.length).toBeGreaterThan(0));
  return DrivenEventSource.instances.at(-1)!;
}

beforeEach(() => {
  sessionStorage.clear();
});

describe("Onboarding.svelte — install progress and result (#228)", () => {
  it("stays in onboarding and shows stages live instead of navigating away", async () => {
    const { onNavigate } = setup();
    const source = await startLocal();

    expect(source.url).toBe("/api/provision/status/job-1");
    expect(onNavigate).not.toHaveBeenCalled();
    expect(screen.getByText(/Reloading this page reattaches/)).toBeInTheDocument();

    source.emit({ stage: "fetch", detail: "Fetching install.sh v0.1.43" });
    expect(await screen.findByText("Fetching install.sh v0.1.43")).toBeInTheDocument();
  });

  it("reads success from done.summary, which carries no detail", async () => {
    setup();
    const source = await startLocal();

    source.emit({
      stage: "done",
      summary: { runtime_version: "0.1.43", host_preflight: [], reboot_pending: false },
    });

    expect(await screen.findByText("anolis-runtime v0.1.43 is installed.")).toBeInTheDocument();
    expect(screen.queryByText(/host preflight needs attention/)).not.toBeInTheDocument();
    expect(screen.queryByText(/reboot is pending/)).not.toBeInTheDocument();
    expect(source.closed).toBe(true);
  });

  it("opens the installed project in Operate after a local install (#238)", async () => {
    const { onNavigate } = setup();
    const source = await startLocal();

    source.emit({
      stage: "done",
      summary: { runtime_version: "0.1.45", host_preflight: [], project: "sim-quickstart" },
    });

    await fireEvent.click(
      await screen.findByRole("button", { name: "Open sim-quickstart in Operate" }),
    );
    expect(onNavigate).toHaveBeenCalledWith("/projects/sim-quickstart/operate");
  });

  it("offers no Operate shortcut when the done summary names no project", async () => {
    setup();
    const source = await startLocal();

    source.emit({ stage: "done", summary: { runtime_version: "0.1.45" } });

    expect(await screen.findByText("anolis-runtime v0.1.45 is installed.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /in Operate/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Go to projects" })).toBeInTheDocument();
  });

  it("shows a successful install that let unmet requirements through, and a pending reboot", async () => {
    setup();
    const source = await startLocal();

    source.emit({
      stage: "done",
      summary: {
        runtime_version: "0.1.43",
        host_preflight: [
          "⚠ host preflight: continuing (--allow-unmet-host); those providers start up not ready",
        ],
        reboot_pending: true,
      },
    });

    expect(await screen.findByText(/host preflight needs attention/)).toBeInTheDocument();
    expect(screen.getByText(/continuing \(--allow-unmet-host\)/)).toBeInTheDocument();
    expect(screen.getByText(/A reboot is pending on the target/)).toBeInTheDocument();
  });

  it("offers Install anyway for unmet requirements, behind a confirm, and resubmits with allow_unmet_host", async () => {
    const { posts } = setup(["job-1", "job-2"]);
    await fireEvent.click(screen.getByRole("button", { name: /Set up a remote device/ }));
    await fireEvent.input(screen.getByLabelText("Target (user@host)"), {
      target: { value: "pi@rig" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Start Provisioning" }));
    await waitFor(() => expect(DrivenEventSource.instances.length).toBe(1));

    DrivenEventSource.instances[0].emit({
      stage: "error",
      detail: `install.sh failed (exit 1):\n${UNMET_BLOCK.join("\n")}`,
      host_preflight: UNMET_BLOCK,
      reboot_pending: false,
    });

    expect(await screen.findByText("Install failed")).toBeInTheDocument();
    expect(screen.getByText(/i2c\.bus_access \(unmet\)/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Confirm: install anyway/ }),
    ).not.toBeInTheDocument();

    await fireEvent.click(screen.getByRole("button", { name: "Install anyway…" }));
    expect(screen.getByText(/start with no devices until the host is fixed/)).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "Confirm: install anyway" }));

    await waitFor(() => expect(posts.length).toBe(2));
    expect(posts[0]).toEqual({ url: "/api/provision/remote", body: { target: "pi@rig" } });
    expect(posts[1]).toEqual({
      url: "/api/provision/remote",
      body: { target: "pi@rig", allow_unmet_host: true },
    });
    expect(DrivenEventSource.instances[1].url).toBe("/api/provision/status/job-2");
  });

  it("does not offer Install anyway when the failure is not unmet requirements", async () => {
    setup();
    const source = await startLocal();

    source.emit({
      stage: "error",
      detail: "install.sh failed (exit 1):\n✗ verify: checksum mismatch for anolis-runtime",
      host_preflight: [],
      reboot_pending: true,
    });

    expect(await screen.findByText(/checksum mismatch/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Install anyway/ })).not.toBeInTheDocument();
    expect(screen.getByText(/A reboot is pending on the target/)).toBeInTheDocument();
  });

  it("does not offer Install anyway twice once it was already allowed", async () => {
    setup(["job-1", "job-2"]);
    const first = await startLocal();
    first.emit({
      stage: "error",
      detail: UNMET_BLOCK.join("\n"),
      host_preflight: UNMET_BLOCK,
      reboot_pending: false,
    });
    await fireEvent.click(await screen.findByRole("button", { name: "Install anyway…" }));
    await fireEvent.click(screen.getByRole("button", { name: "Confirm: install anyway" }));
    await waitFor(() => expect(DrivenEventSource.instances.length).toBe(2));

    DrivenEventSource.instances[1].emit({
      stage: "error",
      detail: UNMET_BLOCK.join("\n"),
      host_preflight: UNMET_BLOCK,
      reboot_pending: false,
    });
    expect(await screen.findByText("Install failed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Install anyway/ })).not.toBeInTheDocument();
  });

  it("says the connection was lost rather than showing a blank or false result", async () => {
    setup();
    const source = await startLocal();

    source.fail();

    expect(
      await screen.findByText(/Lost the connection to the workbench while installing/),
    ).toBeInTheDocument();
    expect(screen.getByText("Install failed")).toBeInTheDocument();
  });

  it("treats the stream closing after done as the normal end", async () => {
    setup();
    const source = await startLocal();

    source.emit({ stage: "done", summary: { runtime_version: "0.1.43" } });
    source.emit({ stage: "status", status: "done", error: null });
    source.fail();

    expect(await screen.findByText("anolis-runtime v0.1.43 is installed.")).toBeInTheDocument();
    expect(screen.queryByText(/Lost the connection/)).not.toBeInTheDocument();
  });

  it("shows why a local install could not start instead of swallowing it", async () => {
    DrivenEventSource.instances = [];
    vi.stubGlobal("EventSource", DrivenEventSource as unknown as typeof EventSource);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url === "/api/onboarding"
          ? jsonResponse(200, {
              first_run: true,
              has_projects: false,
              has_runtime: false,
              runtime_path: "",
            })
          : jsonResponse(409, { error: "install already running" }),
      ),
    );
    render(Onboarding, { props: { onNavigate: vi.fn() } });

    await fireEvent.click(screen.getByRole("button", { name: /Set up this device/ }));

    expect(await screen.findByText(/install already running/)).toBeInTheDocument();
    expect(DrivenEventSource.instances.length).toBe(0);
  });
});

describe("Onboarding.svelte — a reload reattaches to the running job (#348)", () => {
  it("reattaches to the same job's stream after a reload and rebuilds it from the replay", async () => {
    setup();
    const first = await startLocal();
    first.emit({ stage: "fetch", detail: "Fetching install.sh v0.1.43" });

    cleanup();
    render(Onboarding, { props: { onNavigate: vi.fn() } });

    const second = DrivenEventSource.instances.at(-1)!;
    expect(DrivenEventSource.instances.length).toBe(2);
    expect(second.url).toBe("/api/provision/status/job-1");
    expect(screen.getByText("Installing…")).toBeInTheDocument();

    second.emit({ stage: "fetch", detail: "Fetching install.sh v0.1.43" });
    second.emit({ stage: "done", summary: { runtime_version: "0.1.43" } });
    expect(await screen.findByText("anolis-runtime v0.1.43 is installed.")).toBeInTheDocument();
  });

  it("keeps Install anyway working after a reload, with the original request", async () => {
    const { posts } = setup(["job-1", "job-2"]);
    await startLocal();

    cleanup();
    render(Onboarding, { props: { onNavigate: vi.fn() } });
    DrivenEventSource.instances.at(-1)!.emit({
      stage: "error",
      detail: UNMET_BLOCK.join("\n"),
      host_preflight: UNMET_BLOCK,
      reboot_pending: false,
    });
    await fireEvent.click(await screen.findByRole("button", { name: "Install anyway…" }));
    await fireEvent.click(screen.getByRole("button", { name: "Confirm: install anyway" }));

    await waitFor(() => expect(posts.length).toBe(2));
    expect(posts[1]).toEqual({ url: "/api/provision/install", body: { allow_unmet_host: true } });
  });

  it("forgets the job once it has ended, so the next load starts at the choice", async () => {
    setup();
    const source = await startLocal();
    source.emit({ stage: "done", summary: { runtime_version: "0.1.43" } });
    await screen.findByText("anolis-runtime v0.1.43 is installed.");

    cleanup();
    render(Onboarding, { props: { onNavigate: vi.fn() } });

    expect(DrivenEventSource.instances.length).toBe(1);
    expect(screen.getByRole("button", { name: /Set up this device/ })).toBeInTheDocument();
  });

  it("reopens onboarding in the shell on a reload, although the install has created a project", async () => {
    setup();
    await startLocal();
    cleanup();

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = pathFromInput(input);
        if (path === "/api/projects")
          return jsonResponse(200, [{ name: "sim-quickstart", meta: {} }]);
        if (path === "/api/status")
          return jsonResponse(200, { running: false, active_project: null });
        if (path === "/api/templates") return jsonResponse(200, []);
        if (path === "/api/provider-schemas") return jsonResponse(200, { providers: {} });
        return jsonResponse(200, {});
      }),
    );
    history.replaceState({}, "", "/");
    render(App);

    expect(await screen.findByText("Installing…")).toBeInTheDocument();
    expect(DrivenEventSource.instances.at(-1)!.url).toBe("/api/provision/status/job-1");
  });
});

describe("Onboarding.svelte — storage and dropped streams (#348)", () => {
  /** A sessionStorage whose `refuse` method throws, as a full or denied
   *  store does. The shared afterEach unstubs it. */
  function refusingStorage(refuse: "setItem" | "removeItem"): void {
    const items = new Map<string, string>();
    const storage = {
      getItem: (key: string) => items.get(key) ?? null,
      setItem: (key: string, value: string) => void items.set(key, value),
      removeItem: (key: string) => void items.delete(key),
      clear: () => items.clear(),
    };
    storage[refuse] = () => {
      throw new DOMException("refused", "QuotaExceededError");
    };
    vi.stubGlobal("sessionStorage", storage);
  }

  it("attaches to a started install even when sessionStorage refuses the write", async () => {
    refusingStorage("setItem");
    const { posts } = setup();

    await startLocal();

    expect(posts.length).toBe(1);
    expect(DrivenEventSource.instances.length).toBe(1);
    expect(screen.getByText("Installing…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Set up this device/ })).not.toBeInTheDocument();
  });

  it("shows the result even when sessionStorage refuses the removal", async () => {
    refusingStorage("removeItem");
    setup();
    const source = await startLocal();

    source.emit({ stage: "done", summary: { runtime_version: "0.1.43" } });

    expect(await screen.findByText("anolis-runtime v0.1.43 is installed.")).toBeInTheDocument();
  });

  it("keeps the job after a dropped stream, so a reload reattaches to it", async () => {
    setup();
    const first = await startLocal();

    first.fail();
    await screen.findByText(/Lost the connection to the workbench while installing/);
    await new Promise((resolve) => setTimeout(resolve, 20));
    cleanup();
    render(Onboarding, { props: { onNavigate: vi.fn() } });

    expect(DrivenEventSource.instances.length).toBe(2);
    expect(DrivenEventSource.instances[1].url).toBe("/api/provision/status/job-1");
  });

  it("forgets the job after a dropped stream when the server no longer has it", async () => {
    setup(["job-1"], 404);
    const first = await startLocal();

    first.fail();
    await waitFor(() =>
      expect(sessionStorage.getItem("anolis-workbench.provision-job")).toBeNull(),
    );
    cleanup();
    render(Onboarding, { props: { onNavigate: vi.fn() } });

    expect(DrivenEventSource.instances.length).toBe(1);
  });
});

describe("Onboarding.svelte — an existing install's config is kept (#354)", () => {
  it("says before installing that an existing install's configs are kept", () => {
    setup();
    expect(
      screen.getByText(/already has anolis installed, install\.sh keeps the existing runtime/),
    ).toBeInTheDocument();
  });

  it("names what was kept instead of an unqualified success", async () => {
    setup();
    const source = await startLocal();

    source.emit({
      stage: "existing-config",
      detail:
        "/opt/anolis/config/runtime.yaml exists on the target and is kept: install.sh does not replace it with this project's runtime config.",
    });
    expect(
      await screen.findByText(/runtime\.yaml exists on the target and is kept/),
    ).toBeInTheDocument();

    source.emit({
      stage: "done",
      summary: {
        runtime_version: "0.1.43",
        host_preflight: [],
        reboot_pending: false,
        preserved: ["→ config (runtime): /opt/anolis/config/runtime.yaml exists (preserved)"],
        runtime_config_kept: true,
      },
    });

    expect(
      await screen.findByText(/existing runtime config was kept; the runtime runs it/),
    ).toBeInTheDocument();
    expect(screen.getByText(/runtime\.yaml exists \(preserved\)/)).toBeInTheDocument();
  });

  it("does not call a freshly written runtime config kept after a variant switch", async () => {
    setup();
    const source = await startLocal();

    // install.sh after "remove runtime.yaml and re-run": runtime.yaml was
    // written fresh, the provider configs were kept.
    source.emit({
      stage: "done",
      summary: {
        runtime_version: "0.1.43",
        preserved: ["→ config (providers): 2 preserved, 0 written"],
        runtime_config_kept: false,
      },
    });

    expect(await screen.findByText("anolis-runtime v0.1.43 is installed.")).toBeInTheDocument();
    expect(screen.queryByText(/runtime config was kept/)).not.toBeInTheDocument();
    expect(screen.getByText(/left these existing configs in place/)).toBeInTheDocument();
    expect(screen.getByText(/2 preserved, 0 written/)).toBeInTheDocument();
  });

  it("says nothing was kept when nothing was", async () => {
    setup();
    const source = await startLocal();
    source.emit({ stage: "done", summary: { runtime_version: "0.1.43", preserved: [] } });
    expect(await screen.findByText("anolis-runtime v0.1.43 is installed.")).toBeInTheDocument();
    expect(screen.queryByText(/runtime config was kept/)).not.toBeInTheDocument();
    expect(screen.queryByText(/left these existing configs in place/)).not.toBeInTheDocument();
  });
});

describe("Onboarding.svelte — no install options (#229)", () => {
  it("offers no install-option toggles and no template picker", () => {
    setup();
    expect(screen.queryAllByRole("checkbox")).toEqual([]);
    expect(screen.queryAllByRole("combobox")).toEqual([]);
  });
});

describe("Onboarding.svelte — sudo password for a local install (#230)", () => {
  it("sends it in the local install request only, then clears it and keeps it out of storage", async () => {
    const { posts } = setup();
    const field = screen.getByLabelText(/Sudo password for this device/) as HTMLInputElement;
    expect(field.type).toBe("password");
    await fireEvent.input(field, { target: { value: "pw-7f3c" } });

    await startLocal();

    expect(posts).toEqual([{ url: "/api/provision/install", body: { sudo_password: "pw-7f3c" } }]);
    expect(sessionStorage.getItem("anolis-workbench.provision-job")).not.toContain("pw-7f3c");
    expect(field.value).toBe("");
  });

  it("sends no password field when none was typed", async () => {
    const { posts } = setup();
    await startLocal();
    expect(posts).toEqual([{ url: "/api/provision/install", body: {} }]);
  });

  it("asks again for Install anyway on a local job, since the first was not kept", async () => {
    const { posts } = setup(["job-1", "job-2"]);
    await fireEvent.input(screen.getByLabelText(/Sudo password for this device/), {
      target: { value: "pw-7f3c" },
    });
    const source = await startLocal();
    source.emit({
      stage: "error",
      detail: UNMET_BLOCK.join("\n"),
      host_preflight: UNMET_BLOCK,
      reboot_pending: false,
    });
    await fireEvent.click(await screen.findByRole("button", { name: "Install anyway…" }));
    const again = screen.getByLabelText(/Sudo password/) as HTMLInputElement;
    expect(again.type).toBe("password");
    expect(again.value).toBe("");
    await fireEvent.input(again, { target: { value: "pw-7f3c" } });
    await fireEvent.click(screen.getByRole("button", { name: "Confirm: install anyway" }));

    await waitFor(() => expect(posts.length).toBe(2));
    expect(posts[1]).toEqual({
      url: "/api/provision/install",
      body: { allow_unmet_host: true, sudo_password: "pw-7f3c" },
    });
    expect(sessionStorage.getItem("anolis-workbench.provision-job")).not.toContain("pw-7f3c");
  });
});

describe("Onboarding.svelte — the sudo password field is offered only on this machine (#230)", () => {
  afterEach(() => setPageUrl("http://localhost:3000/"));

  it.each(["127.0.0.1", "localhost", "[::1]"])("offers it on %s", (host) => {
    setPageUrl(`http://${host}:8000/`);
    setup();
    expect(screen.getByLabelText(/Sudo password for this device/)).toBeInTheDocument();
  });

  it("does not offer it to a browser on another host", () => {
    setPageUrl("http://192.168.1.20:8000/");
    setup();
    expect(screen.queryByLabelText(/Sudo password/)).not.toBeInTheDocument();
    expect(screen.getByText(/sudo password can be entered only/)).toBeInTheDocument();
  });

  it("does not ask for it again on Install anyway from another host", async () => {
    setPageUrl("http://192.168.1.20:8000/");
    setup();
    const source = await startLocal();
    source.emit({
      stage: "error",
      detail: UNMET_BLOCK.join("\n"),
      host_preflight: UNMET_BLOCK,
      reboot_pending: false,
    });
    await fireEvent.click(await screen.findByRole("button", { name: "Install anyway…" }));
    expect(screen.getByRole("button", { name: "Confirm: install anyway" })).toBeInTheDocument();
    expect(screen.queryByLabelText(/Sudo password/)).not.toBeInTheDocument();
  });
});
