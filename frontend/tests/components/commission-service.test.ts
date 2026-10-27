import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import Commission from "../../src/routes/Commission.svelte";
import { createProjectDocument, createRuntimeStatus, jsonResponse, pathFromInput } from "./helpers";

// wb#581: stop and restart an installed anolis-runtime service, on a remote
// device over SSH or on this machine, each behind a confirm naming the
// machine. The workbench's own Stop cannot stop a service install.sh made.

type Post = { path: string; body: Record<string, unknown> };

function setup(
  opts: {
    runtimeStatus?: ReturnType<typeof createRuntimeStatus>;
    running?: boolean;
    onService?: (body: Record<string, unknown>) => Response;
  } = {},
) {
  const posts: Post[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathFromInput(input);
      if (init?.method === "POST")
        posts.push({ path, body: JSON.parse(String(init.body ?? "{}")) });
      if (path === "/api/provision/service") {
        const body = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
        if (opts.onService) return opts.onService(body);
        const machine = typeof body.target === "string" ? body.target : "this machine";
        const state = body.action === "stop" ? "inactive" : "active";
        return jsonResponse(200, { ok: true, action: body.action, machine, state });
      }
      return jsonResponse(200, {});
    }),
  );
  render(Commission, {
    props: {
      projectName: "demo",
      system: createProjectDocument("demo"),
      runtimeStatus: opts.runtimeStatus ?? createRuntimeStatus(),
      commissionRunningForCurrent: opts.running ?? false,
    },
  });
  return { posts, service: () => posts.filter((p) => p.path === "/api/provision/service") };
}

afterEach(() => vi.unstubAllGlobals());

describe("Commission.svelte — the installed runtime service (wb#581)", () => {
  it("stops the runtime on a remote device after a confirm naming it", async () => {
    const { service } = setup();
    await fireEvent.input(screen.getByLabelText("Target (user@host)"), {
      target: { value: "pi@rig" },
    });

    await fireEvent.click(screen.getByRole("button", { name: "Stop the installed runtime…" }));
    expect(service()).toEqual([]);
    await fireEvent.click(
      screen.getByRole("button", { name: "Confirm: stop anolis-runtime on pi@rig" }),
    );

    expect(await screen.findByTestId("service-result")).toHaveTextContent(
      "anolis-runtime on pi@rig: inactive",
    );
    expect(service()).toEqual([
      { path: "/api/provision/service", body: { action: "stop", target: "pi@rig" } },
    ]);
  });

  it("restarts the runtime on this machine with the sudo password, then clears it", async () => {
    const { service } = setup();
    await fireEvent.click(screen.getByLabelText("This machine"));
    const password = screen.getByLabelText(
      "Sudo password (empty if sudo needs none)",
    ) as HTMLInputElement;
    await fireEvent.input(password, { target: { value: "pw-7f3c" } });

    await fireEvent.click(screen.getByRole("button", { name: "Restart the installed runtime…" }));
    await fireEvent.click(
      screen.getByRole("button", { name: "Confirm: restart anolis-runtime on this machine" }),
    );

    expect(await screen.findByTestId("service-result")).toHaveTextContent(
      "anolis-runtime on this machine: active",
    );
    expect(service()).toEqual([
      { path: "/api/provision/service", body: { action: "restart", sudo_password: "pw-7f3c" } },
    ]);
    expect(password.value).toBe("");
  });

  it("can be cancelled, and posts nothing", async () => {
    const { service } = setup();
    await fireEvent.input(screen.getByLabelText("Target (user@host)"), {
      target: { value: "pi@rig" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Restart the installed runtime…" }));
    await fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(
      screen.getByRole("button", { name: "Restart the installed runtime…" }),
    ).toBeInTheDocument();
    expect(service()).toEqual([]);
  });

  it("needs a remote target before it offers anything", async () => {
    setup();
    expect(screen.getByRole("button", { name: "Stop the installed runtime…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Restart the installed runtime…" })).toBeDisabled();
  });

  it("shows the workbench's error when the service did not stop", async () => {
    setup({
      onService: () =>
        jsonResponse(502, {
          error: "anolis-runtime on pi@rig is still active after systemctl stop",
          state: "active",
        }),
    });
    await fireEvent.input(screen.getByLabelText("Target (user@host)"), {
      target: { value: "pi@rig" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Stop the installed runtime…" }));
    await fireEvent.click(
      screen.getByRole("button", { name: "Confirm: stop anolis-runtime on pi@rig" }),
    );

    const result = await screen.findByTestId("service-result");
    await waitFor(() => expect(result).toHaveTextContent("still active after systemctl stop"));
    expect(result).toHaveClass("field-error");
  });

  it("points at these controls when the running runtime is an installed service", async () => {
    setup({
      running: true,
      runtimeStatus: createRuntimeStatus({ running: true, active_project: "demo", external: true }),
    });
    expect(await screen.findByTestId("external-runtime-note")).toHaveTextContent(
      "installed as a service on this machine",
    );
    expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Restart" })).not.toBeInTheDocument();
  });
});
