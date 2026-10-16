/**
 * schema.ts interpreter tests — run against the LITERAL vendored provider
 * envelopes so any envelope re-sync that changes shape in a way the
 * interpreter mishandles fails here, not in the browser.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import type { UnknownRecord } from "../../src/lib/contracts";
import {
  asObject,
  defaultsFor,
  enumOptions,
  fieldControl,
  isArraySchema,
  isObjectSchema,
  labelFor,
  parseI2c,
  placeholderFor,
  properties,
  pruneForbidden,
  resolveConditionals,
  uniqueViolations,
  validateField,
  type SchemaNode,
} from "../../src/lib/schema-form/schema";

function loadEnvelopeSchema(kind: string): SchemaNode {
  const url = new URL(
    `../../../anolis_workbench/schemas/providers/${kind}.config-schema.json`,
    import.meta.url,
  );
  const envelope = JSON.parse(readFileSync(url, "utf-8")) as UnknownRecord;
  const schema = asObject(envelope.schema);
  if (!schema) throw new Error(`envelope for ${kind} has no schema object`);
  return schema;
}

const bread = loadEnvelopeSchema("bread");
const ezo = loadEnvelopeSchema("ezo");
const sim = loadEnvelopeSchema("sim");

function prop(schema: SchemaNode, ...path: string[]): SchemaNode {
  let node = schema;
  for (const key of path) {
    const next = properties(node).find(([k]) => k === key);
    if (!next) throw new Error(`no property ${path.join(".")}`);
    node = next[1];
  }
  return node;
}

function deviceItems(schema: SchemaNode): SchemaNode {
  const items = asObject(prop(schema, "devices").items);
  if (!items) throw new Error("devices has no items schema");
  return items;
}

describe("schema shape guards", () => {
  it("recognizes the envelope roots as object schemas", () => {
    for (const schema of [bread, ezo, sim]) {
      expect(isObjectSchema(schema)).toBe(true);
      expect(isArraySchema(schema)).toBe(false);
    }
  });

  it("recognizes device lists as arrays of objects", () => {
    const devices = prop(bread, "devices");
    expect(isArraySchema(devices)).toBe(true);
  });
});

describe("resolveConditionals", () => {
  it("bread discovery: manual requires addresses", () => {
    const discovery = prop(bread, "discovery");
    const { required, forbidden } = resolveConditionals(discovery, { mode: "manual" });
    expect(required.has("addresses")).toBe(true);
    expect(forbidden.size).toBe(0);
  });

  it("bread discovery: scan forbids addresses", () => {
    const discovery = prop(bread, "discovery");
    const { required, forbidden } = resolveConditionals(discovery, { mode: "scan" });
    expect(forbidden.has("addresses")).toBe(true);
    expect(required.has("addresses")).toBe(false);
  });

  it("bread discovery: no mode chosen — only the static requireds", () => {
    const discovery = prop(bread, "discovery");
    const { required, forbidden } = resolveConditionals(discovery, {});
    expect(required.has("mode")).toBe(true);
    expect(required.has("addresses")).toBe(false);
    expect(forbidden.size).toBe(0);
  });

  it("sim simulation: inert forbids the ticking fields", () => {
    const simulation = prop(sim, "simulation");
    const { forbidden } = resolveConditionals(simulation, { mode: "inert" });
    expect(forbidden.has("tick_rate_hz")).toBe(true);
    expect(forbidden.has("physics_config")).toBe(true);
    expect(forbidden.has("ambient_temp_c")).toBe(true);
  });

  it("sim simulation: non_interacting requires tick_rate_hz, forbids physics_config", () => {
    const simulation = prop(sim, "simulation");
    const { required, forbidden } = resolveConditionals(simulation, { mode: "non_interacting" });
    expect(required.has("tick_rate_hz")).toBe(true);
    expect(forbidden.has("physics_config")).toBe(true);
  });

  it("sim simulation: sim mode requires tick_rate_hz and physics_config", () => {
    const simulation = prop(sim, "simulation");
    const { required } = resolveConditionals(simulation, { mode: "sim" });
    expect(required.has("tick_rate_hz")).toBe(true);
    expect(required.has("physics_config")).toBe(true);
  });

  it("sim simulation: dependentRequired ambient_signal_path -> ambient_temp_c", () => {
    const simulation = prop(sim, "simulation");
    const { required } = resolveConditionals(simulation, {
      mode: "sim",
      ambient_signal_path: "x/y",
    });
    expect(required.has("ambient_temp_c")).toBe(true);
  });
});

describe("defaultsFor", () => {
  it("bread: fills hardware defaults, provider name default, no invented bus_path", () => {
    const seeded = defaultsFor(bread);
    expect(seeded.provider).toEqual({ name: "anolis-provider-bread" });
    expect(seeded.hardware).toEqual({ query_delay_us: 10000, timeout_ms: 100, retry_count: 2 });
    expect(seeded.discovery).toEqual({ mode: "manual" });
    expect(seeded.devices).toEqual([]);
  });

  it("ezo: const discovery.mode is filled", () => {
    const seeded = defaultsFor(ezo);
    expect(seeded.discovery).toEqual({ mode: "manual" });
  });

  it("sim: startup_policy default only — no fabricated tick_rate_hz", () => {
    const seeded = defaultsFor(sim);
    expect(seeded.startup_policy).toBe("strict");
    expect(seeded.simulation).toEqual({});
    expect(JSON.stringify(seeded)).not.toContain("tick_rate_hz");
  });
});

describe("fieldControl", () => {
  it("bread device type is a titled select", () => {
    expect(fieldControl(prop(deviceItems(bread), "type"))).toEqual({
      control: "select",
      options: [
        { value: "rlht", title: "RLHT Heater" },
        { value: "dcmt", title: "DCMT Motor" },
      ],
    });
  });

  it("device address is an i2c control", () => {
    expect(fieldControl(prop(deviceItems(ezo), "address"))).toEqual({ control: "i2c" });
  });

  it("ezo discovery.mode const renders readonly", () => {
    expect(fieldControl(prop(ezo, "discovery", "mode"))).toEqual({
      control: "readonly",
      value: "manual",
    });
  });

  it("integer bounds map to a number control", () => {
    const watchdog = prop(deviceItems(bread), "command_watchdog_ms");
    expect(fieldControl(watchdog)).toEqual({ control: "number", integer: true, min: 0, max: 65535 });
  });

  it("bus_path is text with placeholder", () => {
    const busPath = prop(bread, "hardware", "bus_path");
    expect(fieldControl(busPath)).toEqual({ control: "text", pattern: undefined, minLength: 1 });
    expect(placeholderFor(busPath)).toBe("/dev/i2c-1 or mock://name");
  });

  it("labels come from titles", () => {
    expect(labelFor("bus_path", prop(bread, "hardware", "bus_path"))).toBe("I2C bus path");
    expect(labelFor("mystery", {})).toBe("mystery");
  });

  it("enumOptions rejects non-const oneOf", () => {
    expect(enumOptions({ oneOf: [{ title: "no const" }] })).toBeNull();
    expect(enumOptions({})).toBeNull();
  });
});

describe("parseI2c / validateField", () => {
  it("parses hex strings, decimal strings and ints", () => {
    expect(parseI2c("0x0A")).toBe(10);
    expect(parseI2c("0X63")).toBe(99);
    expect(parseI2c("20")).toBe(20);
    expect(parseI2c(16)).toBe(16);
    expect(parseI2c("junk")).toBeNull();
    expect(parseI2c("")).toBeNull();
    expect(parseI2c(1.5)).toBeNull();
  });

  it("i2c range enforcement", () => {
    const address = prop(deviceItems(ezo), "address");
    expect(validateField(address, "0x63", true)).toBeNull();
    expect(validateField(address, 99, true)).toBeNull();
    expect(validateField(address, "0x07", true)).toMatch(/between/);
    expect(validateField(address, "0xFF", true)).toMatch(/between/);
    expect(validateField(address, "junk", true)).toMatch(/0x0A/);
    expect(validateField(address, undefined, true)).toBe("Required");
    expect(validateField(address, undefined, false)).toBeNull();
  });

  it("number bounds and integer check", () => {
    const timeout = prop(bread, "hardware", "timeout_ms");
    expect(validateField(timeout, 100, true)).toBeNull();
    expect(validateField(timeout, 0, true)).toMatch(/>= 1/);
    expect(validateField(timeout, 1.5, true)).toBe("Must be an integer");
    expect(validateField(timeout, "fast", true)).toBe("Must be a number");
  });

  it("text pattern and minLength", () => {
    const name = prop(bread, "provider", "name");
    expect(validateField(name, "bread0", false)).toBeNull();
    expect(validateField(name, "has spaces!", false)).toMatch(/format/);
    const busPath = prop(bread, "hardware", "bus_path");
    expect(validateField(busPath, 5, true)).toBe("Must be text");
  });

  it("select membership", () => {
    const mode = prop(bread, "discovery", "mode");
    expect(validateField(mode, "manual", true)).toBeNull();
    expect(validateField(mode, "warp", true)).toMatch(/allowed/);
  });
});

describe("uniqueViolations", () => {
  it("flags normalized duplicate addresses across devices (10 vs '0x0A')", () => {
    const config = {
      devices: [
        { id: "a", type: "rlht", address: "0x0A" },
        { id: "b", type: "dcmt", address: 10 },
      ],
    };
    const violations = uniqueViolations(bread, config);
    expect(violations.some((v) => v.path === "$.devices" && v.message.includes("address"))).toBe(true);
  });

  it("flags duplicate scalar addresses in discovery.addresses", () => {
    const config = { discovery: { mode: "manual", addresses: ["0x14", 20] } };
    const violations = uniqueViolations(bread, config);
    expect(violations.some((v) => v.path === "$.discovery.addresses")).toBe(true);
  });

  it("flags duplicate device ids", () => {
    const config = { devices: [{ id: "dup" }, { id: "dup" }] };
    expect(uniqueViolations(sim, config).length).toBeGreaterThan(0);
  });

  it("clean config has no violations", () => {
    const config = {
      discovery: { mode: "manual", addresses: ["0x0A", "0x14"] },
      devices: [
        { id: "a", type: "rlht", address: "0x0A" },
        { id: "b", type: "dcmt", address: "0x14" },
      ],
    };
    expect(uniqueViolations(bread, config)).toEqual([]);
  });
});

describe("pruneForbidden", () => {
  it("removes ticking fields when sim mode flips to inert", () => {
    const config = {
      devices: [],
      simulation: { mode: "inert", tick_rate_hz: 10, physics_config: "x.yaml" },
    };
    const changed = pruneForbidden(sim, config);
    expect(changed).toBe(true);
    expect(config.simulation).toEqual({ mode: "inert" });
  });

  it("removes addresses when bread discovery flips to scan", () => {
    const config = { discovery: { mode: "scan", addresses: ["0x0A"] } };
    expect(pruneForbidden(bread, config)).toBe(true);
    expect(config.discovery).toEqual({ mode: "scan" });
  });

  it("prunes inside array items and reports no change when clean", () => {
    const config = { discovery: { mode: "manual", addresses: ["0x0A"] }, devices: [{ id: "a" }] };
    expect(pruneForbidden(bread, config)).toBe(false);
  });

  it("ignores non-object values", () => {
    expect(pruneForbidden(bread, null)).toBe(false);
    expect(pruneForbidden(bread, "text")).toBe(false);
  });
});
