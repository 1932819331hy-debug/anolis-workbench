import { fireEvent, render, screen, waitFor, within } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";

import Operate from "../../src/routes/Operate.svelte";
import { createProjectDocument, jsonResponse, pathFromInput } from "./helpers";

// wb#572: after downtime bread's boards come up WATCHDOG_TRIP. Operate lists
// each tripped board and walks the operator through clearing it, one
// confirmed step at a time: release the software stop, MANUAL (skipped
// without an automation layer), clear_watchdog_trip, and on a board with
// brakes, release them. Nothing is called until the operator clicks.

const RUNNING = { running: true, active_project: "demo" };

const bool = (name: string) => ({ name, type: "bool", required: true });
const CAPS: Record<string, unknown> = {
  dcmt0: {
    capabilities: {
      signals: [],
      functions: [
        {
          function_id: 2,
          name: "set_brake",
          category: "ACTUATE",
          args: [bool("motor1_brake"), bool("motor2_brake")],
        },
        { function_id: 6, name: "clear_watchdog_trip", category: "CONFIG", args: [] },
      ],
    },
  },
  rlht0: {
    capabilities: {
      signals: [],
      functions: [{ function_id: 7, name: "clear_watchdog_trip", category: "CONFIG", args: [] }],
    },
  },
};
const CLEAR_ID: Record<string, number> = { dcmt0: 6, rlht0: 7 };

interface Post {
  path: string;
  body: Record<string, unknown> | null;
}

/** A runtime with the latch, the mode and each board's trip, recording every POST. */
function fakeRuntime(
  opts: {
    latched?: boolean;
    mode?: string;
    automation?: boolean;
    tripped?: Record<string, boolean>;
    onCall?: (body: Record<string, unknown>) => Response | null;
  } = {},
) {
  const rt = {
    latched: opts.latched ?? true,
    mode: opts.mode ?? "AUTO",
    automation: opts.automation ?? true,
    tripped: { dcmt0: true, rlht0: false, ...opts.tripped } as Record<string, boolean>,
    posts: [] as Post[],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathFromInput(input);
      const body =
        typeof init?.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : null;
      if (init?.method === "POST") rt.posts.push({ path, body });
      if (path === "/api/status") return jsonResponse(200, RUNNING);
      if (path === "/v0/runtime/status")
        return jsonResponse(200, {
          status: { code: "OK" },
          version: "0.1.55",
          mode: rt.mode,
          providers: [],
          device_count: 2,
          estop: {
            latched: rt.latched,
            software_safe_state: "hooks",
            latched_at_epoch_ms: rt.latched ? 1_700_000_000_000 : null,
            uncovered_actuating_functions: 0,
          },
        });
      if (path === "/v0/mode") {
        if (!rt.automation)
          return jsonResponse(503, {
            status: { code: "UNAVAILABLE", message: "Automation layer not enabled" },
          });
        if (init?.method === "POST") rt.mode = String(body?.mode);
        return jsonResponse(200, { status: { code: "OK" }, mode: rt.mode });
      }
      if (path === "/v0/estop/clear" && init?.method === "POST") {
        rt.latched = false;
        return jsonResponse(200, { status: { code: "OK" }, latched: false });
      }
      if (path === "/v0/providers/health")
        return jsonResponse(200, {
          providers: [
            {
              provider_id: "bread0",
              state: "AVAILABLE",
              devices: ["dcmt0", "rlht0"].map((id) => ({
                device_id: id,
                health: "OK",
                reported: { state: "OK", metrics: { watchdog_tripped: String(rt.tripped[id]) } },
              })),
            },
          ],
        });
      if (path === "/v0/devices")
        return jsonResponse(200, {
          devices: [
            { provider_id: "bread0", device_id: "dcmt0", display_name: "Drive", type: "dcmt" },
            { provider_id: "bread0", device_id: "rlht0", display_name: "Heater", type: "rlht" },
          ],
        });
      const caps = path.match(/^\/v0\/devices\/bread0\/(\w+)\/capabilities$/);
      if (caps) return jsonResponse(200, CAPS[caps[1]]);
      if (path === "/v0/call" && init?.method === "POST" && body) {
        const answer = opts.onCall?.(body);
        if (answer) return answer;
        const device = String(body.device_id);
        if (body.function_id === CLEAR_ID[device]) rt.tripped[device] = false;
        return jsonResponse(200, { status: { code: "OK" } });
      }
      return jsonResponse(200, {});
    }),
  );
  render(Operate, {
    props: { projectName: "demo", system: createProjectDocument("demo"), runtimeStatus: RUNNING },
  });
  return rt;
}

const trips = () => screen.getByTestId("watchdog-trips");
const panel = () => screen.getByTestId("trip-clear");
const step = (n: number) => within(panel()).getByTestId(`trip-step-${n}`);
// A step's button is enabled once what it depends on has loaded; press it then.
async function press(n: number, name: string) {
  const button = await within(step(n)).findByRole("button", { name });
  await waitFor(() => expect(button).toBeEnabled());
  await fireEvent.click(button);
}
const actionPosts = (rt: { posts: Post[] }) =>
  rt.posts.filter(
    (p) => p.path === "/v0/call" || p.path === "/v0/mode" || p.path === "/v0/estop/clear",
  );

afterEach(() => vi.unstubAllGlobals());

describe("Operate.svelte — a guided clear for watchdog-tripped boards (wb#572)", () => {
  it("lists only the tripped boards, badges them, and calls nothing on its own", async () => {
    const rt = fakeRuntime({ tripped: { dcmt0: true, rlht0: false } });

    expect(await screen.findByTestId("watchdog-trips")).toBeInTheDocument();
    expect(within(trips()).getByText("bread0/dcmt0")).toBeInTheDocument();
    expect(within(trips()).queryByText("bread0/rlht0")).not.toBeInTheDocument();
    expect(await screen.findAllByTestId("device-watchdog-tripped")).toHaveLength(1);

    await fireEvent.click(
      within(trips()).getByRole("button", { name: "Clear the watchdog trip on bread0/dcmt0…" }),
    );
    await waitFor(() => expect(panel()).toBeInTheDocument());
    expect(actionPosts(rt)).toEqual([]);
  });

  it("walks a DCMT through release, MANUAL, the clear and the brakes, each step confirmed and in order", async () => {
    const rt = fakeRuntime();
    await fireEvent.click(
      await screen.findByRole("button", { name: "Clear the watchdog trip on bread0/dcmt0…" }),
    );
    await within(panel()).findByTestId("trip-step-4");

    // With the latch read, nothing later can run before the step it depends on.
    await waitFor(() =>
      expect(
        within(step(1)).getByRole("button", { name: "Release the software stop…" }),
      ).toBeEnabled(),
    );
    expect(within(step(2)).getByRole("button", { name: "Set MANUAL" })).toBeDisabled();
    expect(within(step(3)).getByRole("button", { name: "Clear the trip" })).toBeDisabled();

    // 1. The release takes two clicks, as the stop card's clear does.
    await press(1, "Release the software stop…");
    expect(actionPosts(rt)).toEqual([]);
    await press(1, "Confirm release — re-enables actuation");
    await waitFor(() => expect(step(1)).toHaveTextContent("Done"));

    // 2. MANUAL.
    await press(2, "Set MANUAL");
    await waitFor(() => expect(step(2)).toHaveTextContent("Done"));

    // 3. The clear.
    await press(3, "Clear the trip");
    await waitFor(() => expect(step(3)).toHaveTextContent("Done"));

    // 4. The brakes: an actuating call, so it takes two clicks too.
    await press(4, "Release both brakes…");
    expect(actionPosts(rt)).toHaveLength(3);
    await press(4, "Confirm — releases both brakes");
    await waitFor(() => expect(step(4)).toHaveTextContent("Done"));

    expect(actionPosts(rt)).toEqual([
      { path: "/v0/estop/clear", body: null },
      { path: "/v0/mode", body: { mode: "MANUAL" } },
      {
        path: "/v0/call",
        body: { provider_id: "bread0", device_id: "dcmt0", function_id: 6, args: {} },
      },
      {
        path: "/v0/call",
        body: {
          provider_id: "bread0",
          device_id: "dcmt0",
          function_id: 2,
          args: {
            motor1_brake: { type: "bool", bool: false },
            motor2_brake: { type: "bool", bool: false },
          },
        },
      },
    ]);
    // The board is no longer listed once the runtime stops reporting it tripped.
    await waitFor(() =>
      expect(within(trips()).queryByText("bread0/dcmt0")).not.toBeInTheDocument(),
    );
    expect(panel()).toHaveTextContent("command it again");
  });

  it("can leave the brakes on", async () => {
    const rt = fakeRuntime({ latched: false, mode: "MANUAL" });
    await fireEvent.click(
      await screen.findByRole("button", { name: "Clear the watchdog trip on bread0/dcmt0…" }),
    );
    await press(3, "Clear the trip");
    await waitFor(() => expect(step(3)).toHaveTextContent("Done"));
    await press(4, "Leave the brakes on");

    expect(step(4)).toHaveTextContent("Skipped");
    expect(actionPosts(rt).filter((p) => p.body?.function_id === 2)).toEqual([]);
  });

  it("skips MANUAL when the runtime has no automation layer, and never posts a mode", async () => {
    const rt = fakeRuntime({ automation: false });
    await fireEvent.click(
      await screen.findByRole("button", { name: "Clear the watchdog trip on bread0/dcmt0…" }),
    );
    await waitFor(() => expect(step(2)).toHaveTextContent("Skipped"));
    expect(step(2)).toHaveTextContent("Automation layer not enabled");
    // MANUAL needs nothing here, so only the latch holds the clear back.
    await waitFor(() =>
      expect(
        within(step(1)).getByRole("button", { name: "Release the software stop…" }),
      ).toBeEnabled(),
    );
    expect(await within(step(3)).findByRole("button", { name: "Clear the trip" })).toBeDisabled();

    await press(1, "Release the software stop…");
    await press(1, "Confirm release — re-enables actuation");
    await press(3, "Clear the trip");
    await waitFor(() => expect(step(3)).toHaveTextContent("Done"));
    expect(rt.posts.some((p) => p.path === "/v0/mode")).toBe(false);
  });

  it("offers no brake step on a board without set_brake (RLHT)", async () => {
    const rt = fakeRuntime({
      latched: false,
      mode: "MANUAL",
      tripped: { dcmt0: false, rlht0: true },
    });
    await fireEvent.click(
      await screen.findByRole("button", { name: "Clear the watchdog trip on bread0/rlht0…" }),
    );
    await press(3, "Clear the trip");
    await waitFor(() => expect(step(3)).toHaveTextContent("Done"));

    expect(within(panel()).queryByTestId("trip-step-4")).not.toBeInTheDocument();
    expect(actionPosts(rt)).toEqual([
      {
        path: "/v0/call",
        body: { provider_id: "bread0", device_id: "rlht0", function_id: 7, args: {} },
      },
    ]);
  });

  it("shows a refused clear and leaves the step to retry", async () => {
    fakeRuntime({
      latched: false,
      mode: "MANUAL",
      onCall: () =>
        jsonResponse(409, {
          status: { code: "FAILED_PRECONDITION", message: "Actuation latched by e-stop" },
        }),
    });
    await fireEvent.click(
      await screen.findByRole("button", { name: "Clear the watchdog trip on bread0/dcmt0…" }),
    );
    await press(3, "Clear the trip");

    expect(
      await within(step(3)).findByText("Error: Actuation latched by e-stop"),
    ).toBeInTheDocument();
    expect(step(3)).not.toHaveTextContent("Done");
    expect(within(step(3)).getByRole("button", { name: "Clear the trip" })).toBeEnabled();
    expect(within(panel()).queryByTestId("trip-step-4")).toBeInTheDocument();
    expect(within(step(4)).getByRole("button", { name: "Release both brakes…" })).toBeDisabled();
  });
});
