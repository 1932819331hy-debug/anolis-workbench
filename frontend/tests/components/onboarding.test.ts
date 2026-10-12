import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";

import Onboarding from "../../src/routes/Onboarding.svelte";
import { jsonResponse } from "./helpers";

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

function setup(jobIds: string[] = ["job-1"]) {
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

describe("Onboarding.svelte — install progress and result (#228)", () => {
  it("stays in onboarding and shows stages live instead of navigating away", async () => {
    const { onNavigate } = setup();
    const source = await startLocal();

    expect(source.url).toBe("/api/provision/status/job-1");
    expect(onNavigate).not.toHaveBeenCalled();
    expect(screen.getByText(/Reloading loses this view/)).toBeInTheDocument();

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
