import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";

import type { UnknownRecord } from "../../src/lib/contracts";
import SchemaForm from "../../src/lib/schema-form/SchemaForm.svelte";
import { reactive } from "./helpers.svelte";

const HERE = dirname(fileURLToPath(import.meta.url));

function loadSchema(kind: string): UnknownRecord {
  const path = resolve(
    HERE,
    `../../../anolis_workbench/schemas/providers/${kind}.config-schema.json`,
  );
  const envelope = JSON.parse(readFileSync(path, "utf-8")) as UnknownRecord;
  return envelope.schema as UnknownRecord;
}

const breadSchema = loadSchema("bread");
const simSchema = loadSchema("sim");

function breadConfig(): UnknownRecord {
  return reactive({
    provider: { name: "bread0" },
    hardware: { bus_path: "mock://bus", query_delay_us: 10000, timeout_ms: 100, retry_count: 2 },
    discovery: { mode: "manual", addresses: ["0x0A"] },
    devices: [{ id: "rlht0", type: "rlht", address: "0x0A" }],
  });
}

describe("SchemaForm.svelte (bread envelope)", () => {
  it("renders schema titles, enum option titles and current values", () => {
    render(SchemaForm, {
      props: { schema: breadSchema, value: breadConfig(), onChanged: vi.fn() },
    });

    expect(screen.getByText("I2C bus path")).toBeInTheDocument();
    expect(screen.getByText("Query delay (µs)")).toBeInTheDocument();
    // Titled oneOf consts become option labels
    expect(screen.getByRole("option", { name: "Scan the bus" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "RLHT Heater" })).toBeInTheDocument();
    expect(screen.getByDisplayValue("mock://bus")).toBeInTheDocument();
  });

  it("adds no note of its own to a bus path; the schema's placeholder carries the hint", () => {
    for (const busPath of ["mock://bus", "/dev/i2c-1"]) {
      const config = breadConfig();
      (config.hardware as UnknownRecord).bus_path = busPath;
      const { unmount } = render(SchemaForm, {
        props: { schema: breadSchema, value: config, onChanged: vi.fn() },
      });
      expect(screen.getByDisplayValue(busPath)).toBeInTheDocument();
      expect(screen.queryByText(/Mock bus mode|Live hardware path/)).toBeNull();
      unmount();
    }
  });

  it("switching discovery mode to scan hides AND prunes addresses", async () => {
    const config = breadConfig();
    const onChanged = vi.fn();
    render(SchemaForm, { props: { schema: breadSchema, value: config, onChanged } });

    const modeSelect = screen
      .getAllByRole("combobox")
      .find((el) => el.querySelectorAll("option[value=scan]").length === 1);
    expect(modeSelect).toBeDefined();
    await fireEvent.change(modeSelect as HTMLElement, { target: { value: "scan" } });

    expect(onChanged).toHaveBeenCalled();
    const discovery = config.discovery as UnknownRecord;
    expect(discovery.mode).toBe("scan");
    expect(discovery.addresses).toBeUndefined();
    await waitFor(() => {
      expect(screen.queryByText("Manual addresses")).not.toBeInTheDocument();
    });
  });

  it("device add/remove works and generates type-prefixed ids", async () => {
    const config = breadConfig();
    const onChanged = vi.fn();
    render(SchemaForm, { props: { schema: breadSchema, value: config, onChanged } });

    const addButtons = screen.getAllByRole("button", { name: "+ Add" });
    // Last array in schema order is devices
    await fireEvent.click(addButtons[addButtons.length - 1]);

    const devices = config.devices as UnknownRecord[];
    expect(devices).toHaveLength(2);
    expect(devices[1].type).toBe("rlht"); // first enum option seeded
    expect(devices[1].id).toBe("rlht1"); // rlht0 exists
    expect(devices[1].address).toBeUndefined(); // never invented

    const removeButtons = await screen.findAllByRole("button", { name: "✕" });
    await fireEvent.click(removeButtons[removeButtons.length - 1]);
    expect(config.devices as UnknownRecord[]).toHaveLength(1);
  });

  it("import from devices fills discovery.addresses from the device column", async () => {
    const config = breadConfig();
    (config.devices as UnknownRecord[]).push({ id: "dcmt0", type: "dcmt", address: "0x14" });
    (config.discovery as UnknownRecord).addresses = [];
    const onChanged = vi.fn();
    render(SchemaForm, { props: { schema: breadSchema, value: config, onChanged } });

    await fireEvent.click(screen.getByRole("button", { name: "Import from devices" }));

    expect((config.discovery as UnknownRecord).addresses).toEqual(["0x0A", "0x14"]);
    expect(onChanged).toHaveBeenCalled();
  });

  it("flags duplicate addresses across representations (uniqueViolations)", () => {
    const config = breadConfig();
    (config.devices as UnknownRecord[]).push({ id: "dcmt0", type: "dcmt", address: 10 });
    render(SchemaForm, { props: { schema: breadSchema, value: config, onChanged: vi.fn() } });

    expect(screen.getAllByText(/Duplicate address/).length).toBeGreaterThan(0);
  });
});

describe("SchemaForm.svelte (sim envelope)", () => {
  it("preserves and displays undescribed device extras read-only", () => {
    const config = reactive({
      devices: [{ id: "tempctl0", type: "tempctl", initial_temp: 25.0 }],
      simulation: { mode: "non_interacting", tick_rate_hz: 10 },
    });
    render(SchemaForm, { props: { schema: simSchema, value: config, onChanged: vi.fn() } });

    expect(screen.getByText("initial_temp")).toBeInTheDocument();
    expect(screen.getByText("25")).toBeInTheDocument(); // read-only JSON, not an input
    expect(screen.queryByDisplayValue("25")).not.toBeInTheDocument();
  });

  it("switching simulation mode to inert prunes tick_rate_hz", async () => {
    const config = reactive({
      devices: [],
      simulation: { mode: "non_interacting", tick_rate_hz: 10 },
    }) as UnknownRecord;
    const onChanged = vi.fn();
    render(SchemaForm, { props: { schema: simSchema, value: config, onChanged } });

    const modeSelect = screen
      .getAllByRole("combobox")
      .find((el) => el.querySelectorAll("option[value=inert]").length === 1);
    await fireEvent.change(modeSelect as HTMLElement, { target: { value: "inert" } });

    expect((config.simulation as UnknownRecord).tick_rate_hz).toBeUndefined();
    await waitFor(() => {
      expect(screen.queryByText("Tick rate (Hz)")).not.toBeInTheDocument();
    });
  });

  it("rendering never materializes optional object sections (provider stays absent)", async () => {
    // A legacy sim config without provider is VALID (name is required only
    // inside the section); rendering the form must not inject provider: {}.
    const config = reactive({
      devices: [],
      simulation: { mode: "inert" },
    }) as UnknownRecord;
    const onChanged = vi.fn();
    render(SchemaForm, { props: { schema: simSchema, value: config, onChanged } });

    await waitFor(() => {
      expect(screen.getByText("Provider name")).toBeInTheDocument();
    });
    expect(config.provider).toBeUndefined();
    expect(onChanged).not.toHaveBeenCalled();

    // First write into the section commits the draft to the document.
    const nameInput = screen.getByText("Provider name").parentElement?.querySelector("input");
    await fireEvent.input(nameInput as HTMLInputElement, { target: { value: "sim-a" } });
    expect(config.provider).toEqual({ name: "sim-a" });
    expect(onChanged).toHaveBeenCalled();
  });

  it("tick_rate_hz uses the placeholder, never a fabricated value", () => {
    const config = reactive({
      devices: [],
      simulation: { mode: "non_interacting" },
    });
    render(SchemaForm, { props: { schema: simSchema, value: config, onChanged: vi.fn() } });

    const tick = screen.getByPlaceholderText("10");
    expect(tick).toHaveValue(null);
    expect(screen.getAllByText("Required").length).toBeGreaterThan(0);
  });
});
