import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  coerceParameterValue,
  deriveOperateAvailability,
  extractAutomationStatus,
  extractAutomationTree,
  extractCapabilities,
  extractDeviceStateValues,
  extractDevices,
  extractEstopStatus,
  extractMode,
  extractParameters,
  extractProvidersHealth,
  extractRuntimeStatus,
  normalizeArgSpec,
  normalizeFunctionArgs,
  normalizeFunctionSpecs,
  normalizeParameterType,
  normalizeProviderHealthQuality,
  normalizeSoftwareSafeState,
  renderBtOutline,
  RUNTIME_MODES,
  SOFTWARE_SAFE_STATES,
  summarizeEstopResponse,
  versionAtLeast,
} from "../../src/lib/operate-contracts";

type FakeTreeNode = {
  tagName: string;
  children: FakeTreeNode[];
  getAttribute: (name: string) => string | null;
};

function createNode(
  tagName: string,
  attrs: Record<string, string> = {},
  children: FakeTreeNode[] = [],
): FakeTreeNode {
  return {
    tagName,
    children,
    getAttribute(name: string) {
      return attrs[name] ?? null;
    },
  };
}

describe("operate-contracts extractors", () => {
  it("extractDevices and extractProvidersHealth return arrays or fallback to []", () => {
    expect(extractDevices({ devices: [{ id: "dev0" }] })).toEqual([{ id: "dev0" }]);
    expect(extractDevices({})).toEqual([]);
    expect(extractProvidersHealth({ providers: [{ provider_id: "sim0" }] })).toEqual([
      { provider_id: "sim0" },
    ]);
    expect(extractProvidersHealth(undefined)).toEqual([]);
  });

  it("extractCapabilities normalizes function metadata and arg specs", () => {
    const caps = extractCapabilities({
      capabilities: {
        signals: [{ signal_id: "temp" }],
        functions: [
          { function_id: 3, function_name: "set_mode", args: { mode: { type: "string" } } },
          { function_id: 1, name: "", args: [{ name: "enabled", type: "bool" }] },
        ],
      },
    });

    expect(Array.isArray(caps.signals)).toBe(true);
    expect(caps.functions).toHaveLength(2);
    expect(caps.functions[0].function_id).toBe(1);
    expect(caps.functions[0].display_name).toBe("Function 1");
    expect(caps.functions[1].display_name).toBe("set_mode");
    expect(caps.functions[1].args[0]).toEqual({
      name: "mode",
      type: "string",
      required: true,
      min: undefined,
      max: undefined,
      allowed_values: undefined,
    });
  });

  it("normalizeFunctionArgs supports array and object arg forms", () => {
    expect(
      normalizeFunctionArgs([
        { name: "setpoint", type: "double" },
        { name: "enabled", type: "bool" },
      ]),
    ).toEqual([
      {
        name: "setpoint",
        type: "double",
        required: true,
        min: undefined,
        max: undefined,
        allowed_values: undefined,
      },
      {
        name: "enabled",
        type: "bool",
        required: true,
        min: undefined,
        max: undefined,
        allowed_values: undefined,
      },
    ]);

    expect(
      normalizeFunctionArgs({
        z: { type: "double" },
        a: { type: "string" },
      }),
    ).toEqual([
      {
        name: "a",
        type: "string",
        required: true,
        min: undefined,
        max: undefined,
        allowed_values: undefined,
      },
      {
        name: "z",
        type: "double",
        required: true,
        min: undefined,
        max: undefined,
        allowed_values: undefined,
      },
    ]);
  });

  it("normalizeArgSpec returns empty sentinel when arg name cannot be resolved", () => {
    expect(normalizeArgSpec({}, "")).toEqual({
      name: "",
      type: "string",
      required: true,
    });
  });

  it("extractDeviceStateValues maps timestamp fields deterministically", () => {
    const values = extractDeviceStateValues({
      values: [
        { signal_id: "temp", timestamp_ms: 2000 },
        { signal_id: "ph", timestamp_epoch_ms: 1234 },
      ],
    });
    expect(values[0].timestamp_ms).toBe(2000);
    expect(values[1].timestamp_ms).toBe(1234);
  });

  it("extractMode, extractRuntimeStatus, extractAutomationStatus, and extractAutomationTree apply defaults", () => {
    expect(extractMode({ mode: "AUTO" })).toBe("AUTO");
    expect(extractMode({})).toBeNull();

    expect(extractRuntimeStatus({})).toEqual({
      status: {},
      version: "",
      mode: "UNKNOWN",
      uptime_seconds: 0,
      polling_interval_ms: 0,
      device_count: 0,
      providers: [],
    });

    expect(
      extractAutomationStatus({
        execution_status: "running",
        execution_reason: "waiting",
        automation_version: {
          engine_kind: "behaviortree",
          id: "main",
          digest: "abcdef0123456789",
          digest_scope: "top_level_file",
        },
        last_evaluation_at_epoch_ms: 1234,
        run_id: "01J0RUN",
        last_error: null,
      }),
    ).toEqual({
      execution_status: "running",
      execution_reason: "waiting",
      automation_version: {
        engine_kind: "behaviortree",
        id: "main",
        digest: "abcdef0123456789",
        digest_scope: "top_level_file",
      },
      last_evaluation_at_epoch_ms: 1234,
      run_id: "01J0RUN",
      last_error: null,
    });

    // Unknown / missing fields normalize to neutral defaults; a null
    // automation_version (manual-only run) is preserved, not fabricated.
    expect(
      extractAutomationStatus({
        execution_status: "not-a-real-status",
        automation_version: null,
      }),
    ).toEqual({
      execution_status: "unknown",
      execution_reason: null,
      automation_version: null,
      last_evaluation_at_epoch_ms: null,
      run_id: null,
      last_error: null,
    });

    expect(extractAutomationTree({ tree: "<BehaviorTree/>" })).toBe("<BehaviorTree/>");
    expect(extractAutomationTree({})).toBe("");
  });

  it("normalizeParameterType and extractParameters keep only valid named parameters", () => {
    expect(normalizeParameterType("double")).toBe("double");
    expect(normalizeParameterType("bytes")).toBeNull();

    expect(
      extractParameters({
        parameters: [
          { name: " zeta ", type: "double" },
          { name: "alpha", type: "bytes" },
          { name: "", type: "string" },
          { type: "string" },
        ],
      }),
    ).toEqual([
      { name: "alpha", type: "bytes" },
      { name: "zeta", type: "double" },
    ]);
  });

  it("normalizes provider quality and deriveOperateAvailability decisions", () => {
    expect(normalizeProviderHealthQuality("READY")).toBe("OK");
    expect(normalizeProviderHealthQuality("FAULT")).toBe("FAULT");
    expect(normalizeProviderHealthQuality("STALE")).toBe("UNAVAILABLE");
    expect(normalizeProviderHealthQuality("weird")).toBe("UNKNOWN");

    expect(deriveOperateAvailability({ running: false, active_project: null }, "alpha")).toEqual({
      available: false,
      reason: "stopped",
      message: "Runtime is stopped. Start runtime from Commission to operate this project.",
      runningProject: "",
    });

    expect(deriveOperateAvailability({ running: true, active_project: "beta" }, "alpha")).toEqual({
      available: false,
      reason: "different_project",
      message: 'Runtime is running for project "beta". Stop it before operating "alpha".',
      runningProject: "beta",
    });

    expect(deriveOperateAvailability({ running: true, active_project: "alpha" }, "alpha")).toEqual({
      available: true,
      reason: "available",
      message: "",
      runningProject: "alpha",
    });
  });
});

describe("coerceParameterValue", () => {
  it("coerces valid values for each supported type", () => {
    expect(
      coerceParameterValue({
        type: "double",
        rawValue: "2.5",
        min: "2",
        max: "3",
        allowedValues: undefined,
      }),
    ).toBe(2.5);
    expect(
      coerceParameterValue({
        type: "int64",
        rawValue: "42",
        min: "0",
        max: "100",
        allowedValues: undefined,
      }),
    ).toBe(42);
    expect(
      coerceParameterValue({
        type: "bool",
        rawValue: "TRUE",
        min: undefined,
        max: undefined,
        allowedValues: undefined,
      }),
    ).toBe(true);
    expect(
      coerceParameterValue({
        type: "bool",
        rawValue: "false",
        min: undefined,
        max: undefined,
        allowedValues: undefined,
      }),
    ).toBe(false);
    expect(
      coerceParameterValue({
        type: "string",
        rawValue: "AUTO",
        min: undefined,
        max: undefined,
        allowedValues: ["MANUAL", "AUTO"],
      }),
    ).toBe("AUTO");
  });

  it("rejects unsupported types and invalid values", () => {
    expect(() =>
      coerceParameterValue({
        type: "bytes",
        rawValue: "AAAA",
        min: undefined,
        max: undefined,
        allowedValues: undefined,
      }),
    ).toThrow(/unsupported parameter type/i);
    expect(() =>
      coerceParameterValue({
        type: "double",
        rawValue: "nanx",
        min: undefined,
        max: undefined,
        allowedValues: undefined,
      }),
    ).toThrow(/invalid number/i);
    expect(() => coerceParameterValue({ type: "double", rawValue: "1.0", min: "2.0" })).toThrow(
      /below minimum/i,
    );
    expect(() => coerceParameterValue({ type: "double", rawValue: "9.0", max: "5.0" })).toThrow(
      /above maximum/i,
    );
    expect(() =>
      coerceParameterValue({
        type: "int64",
        rawValue: "not-int",
        min: undefined,
        max: undefined,
        allowedValues: undefined,
      }),
    ).toThrow(/invalid integer/i);
    expect(() =>
      coerceParameterValue({
        type: "int64",
        rawValue: "9223372036854775808",
        min: undefined,
        max: undefined,
        allowedValues: undefined,
      }),
    ).toThrow(/out-of-range int64/i);
    expect(() =>
      coerceParameterValue({
        type: "int64",
        rawValue: "9007199254740992",
        min: undefined,
        max: undefined,
        allowedValues: undefined,
      }),
    ).toThrow(/browser-safe range/i);
    expect(() =>
      coerceParameterValue({
        type: "string",
        rawValue: "UNKNOWN",
        min: undefined,
        max: undefined,
        allowedValues: ["MANUAL", "AUTO"],
      }),
    ).toThrow(/must be one of/i);
  });
});

describe("renderBtOutline", () => {
  it("returns a no-tree message when BehaviorTree node is absent", () => {
    const fakeDocument = {
      querySelector() {
        return null;
      },
    } as unknown as Document;

    expect(renderBtOutline(fakeDocument)).toBe("No BehaviorTree found.");
  });

  it("renders a readable outline for nested tree nodes", () => {
    const tree = createNode("BehaviorTree", { name: "main" }, [
      createNode("Sequence"),
      createNode("Fallback", {}, [createNode("Action", { name: "SetMode" })]),
    ]);
    const fakeDocument = {
      querySelector(selector: string) {
        return selector === "BehaviorTree" ? tree : null;
      },
    } as unknown as Document;

    const outline = renderBtOutline(fakeDocument);
    expect(outline).toContain('BehaviorTree "main"');
    expect(outline).toContain("|- Sequence");
    expect(outline).toContain("\\- Fallback");
    expect(outline).toContain('"SetMode"');
  });
});

describe("RUNTIME_MODES contract", () => {
  it("matches the RuntimeMode enum in the vendored runtime-http OpenAPI contract", () => {
    // Guards against the Operate mode selector drifting from the runtime enum
    // (e.g. the old ACTIVE/SAFE options that /v0/mode rejects). No YAML parser
    // is vendored, so read the single-line enum directly from the contract.
    const contractPath = fileURLToPath(
      new URL("../../../contracts/runtime-http.openapi.v0.yaml", import.meta.url),
    );
    const contract = readFileSync(contractPath, "utf8");
    const match = contract.match(/RuntimeMode:\s*\n\s*type:\s*string\s*\n\s*enum:\s*\[([^\]]+)\]/);
    expect(match, "RuntimeMode enum not found in contract").not.toBeNull();

    const contractModes = (match as RegExpMatchArray)[1].split(",").map((m) => m.trim());
    expect([...RUNTIME_MODES]).toEqual(contractModes);
  });
});

describe("estop contract", () => {
  const contractPath = fileURLToPath(
    new URL("../../../contracts/runtime-http.openapi.v0.yaml", import.meta.url),
  );
  const contract = readFileSync(contractPath, "utf8");

  it("exposes the POST /v0/estop and /v0/estop/clear paths", () => {
    expect(contract).toMatch(/^ {2}\/v0\/estop:$/m);
    expect(contract).toMatch(/^ {2}\/v0\/estop\/clear:$/m);
  });

  it("matches the SoftwareSafeState enum in the vendored contract", () => {
    // enum sits below a multi-line description, so skip intervening lines.
    const match = contract.match(/SoftwareSafeState:\s*\n(?:.*\n)*?\s*enum:\s*\[([^\]]+)\]/);
    expect(match, "SoftwareSafeState enum not found in contract").not.toBeNull();
    const contractStates = (match as RegExpMatchArray)[1].split(",").map((s) => s.trim());
    expect([...SOFTWARE_SAFE_STATES]).toEqual(contractStates);
  });
});

describe("normalizeSoftwareSafeState", () => {
  it("passes through the declared rungs (case/space-insensitive)", () => {
    expect(normalizeSoftwareSafeState("zero")).toBe("zero");
    expect(normalizeSoftwareSafeState(" HOOKS ")).toBe("hooks");
    expect(normalizeSoftwareSafeState("setpoints")).toBe("setpoints");
  });
  it("degrades any unrecognized value to the weakest claim 'none'", () => {
    expect(normalizeSoftwareSafeState("FULL_STOP")).toBe("none");
    expect(normalizeSoftwareSafeState(undefined)).toBe("none");
    expect(normalizeSoftwareSafeState(null)).toBe("none");
  });
});

describe("extractEstopStatus", () => {
  it("normalizes a full estop block", () => {
    const out = extractEstopStatus({
      estop: {
        latched: true,
        software_safe_state: "setpoints",
        latched_at_epoch_ms: 1234,
        uncovered_actuating_functions: 2,
      },
    });
    expect(out).toEqual({
      latched: true,
      software_safe_state: "setpoints",
      latched_at_epoch_ms: 1234,
      uncovered_actuating_functions: 2,
    });
  });

  it("returns null when the block is absent or not an object", () => {
    expect(extractEstopStatus({})).toBeNull();
    expect(extractEstopStatus({ estop: "yes" })).toBeNull();
    expect(extractEstopStatus(null)).toBeNull();
  });

  it("applies safe defaults and weakest-claim normalization", () => {
    expect(
      extractEstopStatus({
        estop: { latched: "nope", software_safe_state: "FULL_STOP", latched_at_epoch_ms: "x" },
      }),
    ).toEqual({
      latched: false,
      software_safe_state: "none",
      latched_at_epoch_ms: null,
      uncovered_actuating_functions: 0,
    });
  });

  it("keeps an explicit null latch time as null (not coerced to 0)", () => {
    const out = extractEstopStatus({
      estop: {
        latched: false,
        software_safe_state: "hooks",
        latched_at_epoch_ms: null,
        uncovered_actuating_functions: 0,
      },
    });
    expect(out?.latched_at_epoch_ms).toBeNull();
  });
});

describe("summarizeEstopResponse", () => {
  it("reports success when all safe-state actions succeed", () => {
    const out = summarizeEstopResponse({
      latched: true,
      software_safe_state: "setpoints",
      actions: [
        { device_handle: "bread0/pump0", function: "set_flow", success: true },
        { device_handle: "bread0/pump1", function: "set_flow", success: true },
      ],
    });
    expect(out.ok).toBe(true);
    expect(out.text).toMatch(/engaged \(setpoints\)/);
    expect(out.text).toMatch(/2 safe-state action\(s\) succeeded/);
  });

  it("reports failure and names the failed action", () => {
    const out = summarizeEstopResponse({
      latched: true,
      software_safe_state: "setpoints",
      actions: [
        { device_handle: "bread0/pump1", function: "set_flow", success: false, error: "timeout" },
      ],
    });
    expect(out.ok).toBe(false);
    expect(out.text).toContain("bread0/pump1/set_flow: timeout");
  });

  it("is honest about the 'none' rung (latch only, no outputs driven)", () => {
    const withWarning = summarizeEstopResponse({
      latched: true,
      software_safe_state: "none",
      actions: [],
      warning: "no software safe-state declared",
    });
    expect(withWarning.ok).toBe(true);
    expect(withWarning.text).toContain("no software safe-state declared");

    const noWarning = summarizeEstopResponse({
      latched: true,
      software_safe_state: "none",
      actions: [],
    });
    expect(noWarning.ok).toBe(true);
    expect(noWarning.text).toMatch(/outputs were NOT driven/);
  });

  it("flags a non-engaged latch and a failed FAULT transition", () => {
    const notLatched = summarizeEstopResponse({
      latched: false,
      software_safe_state: "hooks",
      actions: [{ device_handle: "d/x", function: "f", success: true }],
    });
    expect(notLatched.ok).toBe(false);
    expect(notLatched.text).toMatch(/latch did not engage/i);

    const faultFailed = summarizeEstopResponse({
      latched: true,
      software_safe_state: "hooks",
      actions: [{ device_handle: "d/x", function: "f", success: true }],
      fault_engaged: false,
    });
    expect(faultFailed.ok).toBe(false);
    expect(faultFailed.text).toMatch(/FAULT transition did not engage/);
  });

  it("never claims 'engaged'/'latched' when the latch did not engage", () => {
    const out = summarizeEstopResponse({
      latched: false,
      software_safe_state: "none",
      actions: [],
    });
    expect(out.ok).toBe(false);
    expect(out.text).not.toMatch(/Software stop engaged/);
    expect(out.text).not.toMatch(/Actuation is latched/);
    expect(out.text).toMatch(/latch did not engage/i);
  });

  it("directs the operator to the hardware e-stop on any failure", () => {
    for (const payload of [
      { latched: false, software_safe_state: "hooks", actions: [] },
      {
        latched: true,
        software_safe_state: "setpoints",
        actions: [{ device_handle: "d/x", function: "f", success: false, error: "e" }],
      },
      {
        latched: true,
        software_safe_state: "hooks",
        actions: [{ device_handle: "d/x", function: "f", success: true }],
        fault_engaged: false,
      },
    ]) {
      const out = summarizeEstopResponse(payload);
      expect(out.ok).toBe(false);
      expect(out.text).toMatch(/hardware e-stop/i);
    }
  });
});

describe("normalizeFunctionSpecs", () => {
  it("sorts by function_id and then display name for ties", () => {
    const result = normalizeFunctionSpecs([
      { function_id: 2, function_name: "B" },
      { function_id: 1, function_name: "Z" },
      { function_id: 1, function_name: "A" },
    ]);
    expect(result.map((entry) => `${entry.function_id}:${entry.display_name}`)).toEqual([
      "1:A",
      "1:Z",
      "2:B",
    ]);
  });
});

describe("versionAtLeast", () => {
  it.each([
    ["0.1.45", true],
    ["0.1.46", true],
    ["0.2.0", true],
    ["1.0.0", true],
    ["v0.1.45", true],
    ["0.1.45+build.7", true],
    ["0.1.44", false],
    ["0.1.9", false],
    ["0.0.99", false],
    ["0.1.45-rc.1", false],
    ["", false],
    ["dev-build", false],
    ["0.1", false],
  ])("%s against 0.1.45 is %s", (version, expected) => {
    expect(versionAtLeast(version, "0.1.45")).toBe(expected);
  });

  it("treats a missing version as old", () => {
    expect(versionAtLeast(undefined, "0.1.45")).toBe(false);
    expect(versionAtLeast(null, "0.1.45")).toBe(false);
  });
});
