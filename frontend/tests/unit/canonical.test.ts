import { describe, expect, it } from "vitest";

import {
  activeVariant,
  commandKind,
  inertnessViolation,
  machineIdFromName,
  pinnedKinds,
  launchVariantMismatch,
  providerCommandToken,
  providerConfigFilename,
  projectPathToken,
  variantRelpath,
} from "../../src/lib/canonical";
import type { ProjectDocument } from "../../src/lib/contracts";

/**
 * These helpers mirror rules that live in install.sh. Getting one of them
 * subtly wrong produces a project the workbench happily saves and the target
 * then refuses at `sudo` time — or worse, installs pointing at nothing.
 */
describe("deploy tokens", () => {
  it("builds a provider command install.sh will rewrite", () => {
    expect(providerCommandToken("bread")).toBe(
      "../anolis-provider-bread/build/dev-release/anolis-provider-bread",
    );
    expect(commandKind(providerCommandToken("bread"))).toBe("bread");
  });

  it("puts the kind before the first dot in a provider config filename", () => {
    // install.sh takes the stem up to the FIRST dot as the installed name.
    expect(providerConfigFilename("ezo", "ezo0")).toBe("provider-ezo.ezo0.yaml");
    expect(providerConfigFilename("ezo", "ezo0").split(".")[0]).toBe("provider-ezo");
  });

  it("keys project path tokens on the machine id", () => {
    expect(projectPathToken("rig-a", "config/provider-sim.sim0.yaml")).toBe(
      "../anolis-projects/projects/rig-a/config/provider-sim.sim0.yaml",
    );
    expect(variantRelpath("manual")).toBe("config/anolis-runtime.manual.yaml");
  });

  it("rejects a command whose two kind captures disagree", () => {
    // install.sh rewrites using the SECOND capture and re-derives the kind from
    // the result, so such a token resolves to something the filename doesn't say.
    expect(
      commandKind("../anolis-provider-bread/build/dev-release/anolis-provider-ezo"),
    ).toBeNull();
  });

  it("rejects host paths and anything that is not a token", () => {
    expect(commandKind("/opt/anolis/bin/anolis-provider-sim")).toBeNull();
    expect(commandKind("C:\\build\\Release\\anolis-provider-sim.exe")).toBeNull();
    expect(commandKind(undefined)).toBeNull();
    expect(commandKind(42)).toBeNull();
  });
});

describe("machineIdFromName", () => {
  it("produces an id valid for BOTH the profile schema and install.sh path tokens", () => {
    // Project names allow uppercase and underscores; path tokens do not.
    expect(machineIdFromName("My_Rig 01")).toBe("my-rig-01");
    expect(machineIdFromName("--weird--name--")).toBe("weird-name");
    expect(machineIdFromName("!!!")).toBe("machine");
    expect(machineIdFromName("a".repeat(200))).toHaveLength(64);
  });

  it("always starts with an alphanumeric, as the profile schema requires", () => {
    for (const name of ["_leading", "-leading", "9lives", "ünïcode"]) {
      expect(machineIdFromName(name)).toMatch(/^[a-z0-9][a-z0-9-]*$/);
    }
  });
});

describe("inertnessViolation", () => {
  it("accepts an inert config", () => {
    expect(inertnessViolation({})).toBeNull();
    expect(inertnessViolation({ automation: { enabled: false } })).toBeNull();
  });

  it("flags every shape install.sh treats as enabled", () => {
    // install.sh un-quotes and lowercases the field, then compares it against a
    // false-ish set — verified by running its awk directly.
    expect(inertnessViolation({ automation: { enabled: true } })).toMatch(/reads as enabled/);
    expect(inertnessViolation({ automation: { enabled: 0 as never } })).toMatch(/reads as enabled/);
    expect(inertnessViolation({ automation: { mode_transition_hooks: [] } })).toMatch(
      /mode_transition_hooks/,
    );
    expect(inertnessViolation({ automation: "yes" as never })).toMatch(/mapping/);
  });

  it("matches install.sh's un-anchored scan of the whole automation block", () => {
    // Its awk is not depth-aware: a flag or hook nested anywhere inside the
    // block trips it, so a structural top-level-only reading would let the
    // workbench author a manual variant install.sh then refuses.
    expect(
      inertnessViolation({ automation: { enabled: false, policy: { enabled: true } } }),
    ).toMatch(/policy\.enabled/);
    expect(
      inertnessViolation({ automation: { enabled: false, policy: { mode_transition_hooks: [] } } }),
    ).toMatch(/policy\.mode_transition_hooks/);
  });

  it("treats an empty automation block as a violation", () => {
    // safe_dump writes a bare `automation:` key as null, so this is reachable
    // just by round-tripping such a config through the workbench.
    expect(inertnessViolation({ automation: null as never })).toMatch(/empty/);
    expect(inertnessViolation({ automation: {} })).toMatch(/empty/);
    expect(inertnessViolation({})).toBeNull();
  });
});

describe("activeVariant / pinnedKinds", () => {
  const doc = {
    format: "machine-profile",
    authored: true,
    profile: {
      machine_id: "rig",
      runtime_profiles: {},
      providers: {},
      components: { providers: { bread: { version: "0.3.8" }, ezo: { version: "0.3.4" } } },
    },
    variants: { manual: {}, automation: {} },
    providers: {},
  } as unknown as ProjectDocument;

  it("prefers the launch variant, then manual", () => {
    expect(activeVariant(doc)).toBe("manual");
    expect(activeVariant({ ...doc, launch: { variant: "automation" } })).toBe("automation");
    // A launch variant that no longer exists must not strand the UI.
    expect(activeVariant({ ...doc, launch: { variant: "gone" } })).toBe("manual");
    expect(activeVariant(null)).toBe("manual");
  });

  it("lists the kinds install.sh will fetch binaries for", () => {
    expect(pinnedKinds(doc)).toEqual(["bread", "ezo"]);
    expect(pinnedKinds(null)).toEqual([]);
  });
});

describe("quoted false-ish spellings", () => {
  it("accepts everything install.sh accepts", () => {
    // Blocking these would refuse a config install.sh is perfectly happy with.
    for (const enabled of [false, "false", "False", "no", "off", ""]) {
      expect(
        inertnessViolation({ automation: { enabled: enabled as never } }),
        `should be inert: ${JSON.stringify(enabled)}`,
      ).toBeNull();
    }
  });
});

describe("the dev-launch variant (#339)", () => {
  // Key order puts automation first, so "the first variant" and "manual"
  // differ here.
  const doc = {
    format: "machine-profile",
    authored: true,
    profile: { machine_id: "rig", runtime_profiles: {}, providers: {} },
    variants: { automation: {}, manual: {} },
    providers: {},
  } as unknown as ProjectDocument;

  it("is manual when launch.variant is unset, whatever the key order", () => {
    expect(Object.keys(doc.variants)[0]).toBe("automation");
    expect(activeVariant(doc)).toBe("manual");
    expect(activeVariant({ ...doc, launch: {} })).toBe("manual");
  });

  it("names the mismatch between the edited and the launched variant", () => {
    expect(launchVariantMismatch(doc, "automation")).toBe("Editing automation; Launch runs manual");
    expect(launchVariantMismatch({ ...doc, launch: { variant: "automation" } }, "manual")).toBe(
      "Editing manual; Launch runs automation",
    );
  });

  it("is silent when they agree, when nothing was picked, or for an unknown variant", () => {
    expect(launchVariantMismatch(doc, "manual")).toBeNull();
    expect(
      launchVariantMismatch({ ...doc, launch: { variant: "automation" } }, "automation"),
    ).toBeNull();
    expect(launchVariantMismatch(doc, "")).toBeNull();
    expect(launchVariantMismatch(doc, "gone")).toBeNull();
    expect(launchVariantMismatch(null, "automation")).toBeNull();
  });
});
