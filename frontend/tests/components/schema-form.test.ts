import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/svelte";
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

  it("explains a conditionally required empty list beside it, and only while empty", async () => {
    const config = breadConfig();
    delete (config.discovery as UnknownRecord).addresses;
    render(SchemaForm, { props: { schema: breadSchema, value: config, onChanged: vi.fn() } });

    const hint = "Required because Discovery mode is 'Manual address list'";
    const group = screen.getByText("Manual addresses").closest(".schema-array") as HTMLElement;
    expect(within(group).getByText(hint)).toBeInTheDocument();

    await fireEvent.click(within(group).getByRole("button", { name: "+ Add" }));
    const input = group.querySelector(".schema-array-scalar-row input") as HTMLInputElement;
    await fireEvent.change(input, { target: { value: "0x0A" } });

    expect((config.discovery as UnknownRecord).addresses).toEqual(["0x0A"]);
    await waitFor(() => {
      expect(screen.queryByText(hint)).not.toBeInTheDocument();
    });
    expect(within(group).queryByText(/Required/)).not.toBeInTheDocument();
  });

  it("offers bread 0.9.0's encoders flag on a DCMT row, absent reading as its default (bread#179)", async () => {
    // An existing config has no `encoders` key, which bread reads as true.
    const config = breadConfig();
    config.devices = [{ id: "dcmt0", type: "dcmt", address: "0x14" }];
    const onChanged = vi.fn();
    render(SchemaForm, { props: { schema: breadSchema, value: config, onChanged } });

    const encoders = screen.getByRole("checkbox", { name: /Encoders fitted/ });
    expect(encoders).toBeChecked();
    expect("encoders" in (config.devices as UnknownRecord[])[0]).toBe(false);

    await fireEvent.click(encoders);

    expect(onChanged).toHaveBeenCalled();
    expect((config.devices as UnknownRecord[])[0].encoders).toBe(false);
    expect(encoders).not.toBeChecked();
  });

  it("offers bread 0.10.0's outputs_on_startup without writing it until chosen (bread#195)", async () => {
    // An existing config has no `outputs_on_startup` key, which bread reads as safe.
    const config = breadConfig();
    const onChanged = vi.fn();
    render(SchemaForm, { props: { schema: breadSchema, value: config, onChanged } });

    expect(screen.getByText("Outputs on startup")).toBeInTheDocument();
    const select = screen
      .getAllByRole("combobox")
      .find((el) => el.querySelectorAll("option[value=leave]").length === 1) as HTMLSelectElement;
    expect(select).toBeDefined();
    expect(within(select).getByRole("option", { name: "Drive outputs safe" })).toBeInTheDocument();
    expect(
      within(select).getByRole("option", { name: "Leave outputs as they are" }),
    ).toBeInTheDocument();
    expect("outputs_on_startup" in (config.provider as UnknownRecord)).toBe(false);
    expect(onChanged).not.toHaveBeenCalled();

    await fireEvent.change(select, { target: { value: "leave" } });

    expect(onChanged).toHaveBeenCalled();
    expect((config.provider as UnknownRecord).outputs_on_startup).toBe("leave");
  });

  it("shows an explicit encoders: false unchecked", () => {
    const config = breadConfig();
    config.devices = [{ id: "dcmt0", type: "dcmt", address: "0x14", encoders: false }];
    render(SchemaForm, { props: { schema: breadSchema, value: config, onChanged: vi.fn() } });

    expect(screen.getByRole("checkbox", { name: /Encoders fitted/ })).not.toBeChecked();
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
    // Required, but not reported as an error before the field is touched (#578).
    expect(tick).toHaveAttribute("aria-required", "true");
  });

  it("explains a conditionally required scalar beside it, and only while empty", async () => {
    const config = reactive({
      devices: [],
      simulation: { mode: "non_interacting" },
    });
    render(SchemaForm, { props: { schema: simSchema, value: config, onChanged: vi.fn() } });

    const hint = "Required because Simulation mode is 'Local physics (fixed tick)'";
    const tick = screen.getByPlaceholderText("10");
    const group = tick.closest(".form-group") as HTMLElement;
    expect(within(group).getByText(hint)).toBeInTheDocument();
    // A statically required field needs no explanation.
    const mode = screen.getByText("Simulation mode").closest(".form-group") as HTMLElement;
    expect(within(mode).queryByText(/Required because/)).not.toBeInTheDocument();

    await fireEvent.change(tick, { target: { value: "10" } });
    await waitFor(() => {
      expect(screen.queryByText(hint)).not.toBeInTheDocument();
    });
  });
});

describe("SchemaForm — form details (#578)", () => {
  function hardwareWithoutBus(): UnknownRecord {
    const config = breadConfig();
    delete (config.hardware as UnknownRecord).bus_path;
    return config;
  }
  const busInput = () => screen.getByPlaceholderText("/dev/i2c-1 or mock://name");

  it("shows no Required line or invalid state on an untouched empty required field", () => {
    render(SchemaForm, {
      props: { schema: breadSchema, value: hardwareWithoutBus(), onChanged: vi.fn() },
    });

    expect(busInput()).toHaveAttribute("aria-required", "true");
    expect(busInput()).not.toHaveAttribute("aria-invalid");
    expect(screen.queryByText("Required")).not.toBeInTheDocument();
  });

  it("shows the Required line and invalid state once the empty field is left", async () => {
    render(SchemaForm, {
      props: { schema: breadSchema, value: hardwareWithoutBus(), onChanged: vi.fn() },
    });

    await fireEvent.focus(busInput());
    await fireEvent.blur(busInput());

    expect(await screen.findByText("Required")).toBeInTheDocument();
    expect(busInput()).toHaveAttribute("aria-invalid", "true");
  });

  async function addDevice(config: UnknownRecord): Promise<UnknownRecord> {
    // The last "+ Add" is the devices array, as in the add/remove test above.
    const addButtons = screen.getAllByRole("button", { name: "+ Add" });
    await fireEvent.click(addButtons[addButtons.length - 1]);
    const devices = config.devices as UnknownRecord[];
    return devices[devices.length - 1];
  }
  const lastTypeSelect = () => {
    const selects = screen
      .getAllByRole("combobox")
      .filter((el) => el.querySelectorAll("option[value=dcmt]").length === 1);
    return selects[selects.length - 1];
  };

  it("lets a generated device id follow the type", async () => {
    const config = breadConfig();
    render(SchemaForm, { props: { schema: breadSchema, value: config, onChanged: vi.fn() } });

    const added = await addDevice(config);
    expect(added.id).toBe("rlht1");
    await fireEvent.change(lastTypeSelect(), { target: { value: "dcmt" } });

    const devices = config.devices as UnknownRecord[];
    expect(devices[devices.length - 1].id).toBe("dcmt0");
  });

  it("keeps a typed device id across a type change", async () => {
    const config = breadConfig();
    render(SchemaForm, { props: { schema: breadSchema, value: config, onChanged: vi.fn() } });

    await addDevice(config);
    const idInputs = screen.getAllByDisplayValue("rlht1");
    await fireEvent.input(idInputs[idInputs.length - 1], { target: { value: "heater-a" } });
    await fireEvent.change(lastTypeSelect(), { target: { value: "dcmt" } });

    const devices = config.devices as UnknownRecord[];
    expect(devices[devices.length - 1].id).toBe("heater-a");
  });

  it("gives a new RLHT row no encoders, as bread 0.9.2's schema forbids it there (#640)", async () => {
    const config = breadConfig();
    render(SchemaForm, { props: { schema: breadSchema, value: config, onChanged: vi.fn() } });

    const added = await addDevice(config);
    expect(added.type).toBe("rlht");
    expect("encoders" in added).toBe(false);
    expect(screen.queryByRole("checkbox", { name: /Encoders fitted/ })).not.toBeInTheDocument();

    // As a DCMT it offers the flag, absent reading as bread's default.
    await fireEvent.change(lastTypeSelect(), { target: { value: "dcmt" } });
    expect(screen.getByRole("checkbox", { name: /Encoders fitted/ })).toBeChecked();
  });

  it("gives the address box a placeholder that does not read as a value", async () => {
    const config = breadConfig();
    render(SchemaForm, { props: { schema: breadSchema, value: config, onChanged: vi.fn() } });

    await addDevice(config);
    expect(screen.getAllByPlaceholderText("e.g. 0x0A").length).toBeGreaterThan(0);
    expect(screen.queryByPlaceholderText("0x0A")).not.toBeInTheDocument();
  });
});

describe("SchemaForm — the manual address list (#563)", () => {
  const modeSelect = () =>
    screen
      .getAllByRole("combobox")
      .find((el) => el.querySelectorAll("option[value=scan]").length === 1) as HTMLElement;

  it("import from devices merges, keeping typed entries and not doubling one already there", async () => {
    const config = breadConfig();
    (config.devices as UnknownRecord[]).push({ id: "dcmt0", type: "dcmt", address: "0x14" });
    (config.discovery as UnknownRecord).addresses = ["0x0A", "0x30"];
    render(SchemaForm, { props: { schema: breadSchema, value: config, onChanged: vi.fn() } });

    await fireEvent.click(screen.getByRole("button", { name: "Import from devices" }));

    expect((config.discovery as UnknownRecord).addresses).toEqual(["0x0A", "0x30", "0x14"]);
  });

  it("import from devices with no device addresses leaves typed entries alone", async () => {
    const config = breadConfig();
    config.devices = [];
    (config.discovery as UnknownRecord).addresses = ["0x30"];
    render(SchemaForm, { props: { schema: breadSchema, value: config, onChanged: vi.fn() } });

    await fireEvent.click(screen.getByRole("button", { name: "Import from devices" }));

    expect((config.discovery as UnknownRecord).addresses).toEqual(["0x30"]);
  });

  it("switching to scan and back to manual brings the typed list back", async () => {
    const config = breadConfig();
    (config.discovery as UnknownRecord).addresses = ["0x0A", "0x30"];
    render(SchemaForm, { props: { schema: breadSchema, value: config, onChanged: vi.fn() } });

    await fireEvent.change(modeSelect(), { target: { value: "scan" } });
    expect((config.discovery as UnknownRecord).addresses).toBeUndefined();

    await fireEvent.change(modeSelect(), { target: { value: "manual" } });
    expect((config.discovery as UnknownRecord).addresses).toEqual(["0x0A", "0x30"]);
  });
});
