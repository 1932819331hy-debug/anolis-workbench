import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";

import { AUTOMATION_VARIANT, MANUAL_VARIANT } from "../../src/lib/canonical";
import ProfileForm from "../../src/lib/ProfileForm.svelte";
import RuntimeForm from "../../src/lib/RuntimeForm.svelte";
import Compose from "../../src/routes/Compose.svelte";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import ProviderList from "../../src/lib/ProviderList.svelte";
import type { ProviderSchemasResponse } from "../../src/lib/contracts";
import { createProjectDocument, createRuntimeStatus, jsonResponse, withProvider } from "./helpers";

const HERE = dirname(fileURLToPath(import.meta.url));

function schemasResponseForPins(): ProviderSchemasResponse {
  const load = (kind: string) =>
    JSON.parse(
      readFileSync(
        resolve(HERE, `../../../anolis_workbench/schemas/providers/${kind}.config-schema.json`),
        "utf-8",
      ),
    );
  return { schema_version: 1, providers: { sim: load("sim"), bread: load("bread") } };
}
import { reactive } from "./helpers.svelte";

describe("RuntimeForm.svelte (canonical runtime config)", () => {
  it("edits the runtime config's own fields, not a shadow document", async () => {
    const doc = reactive(createProjectDocument("demo"));
    const onChanged = vi.fn();
    render(RuntimeForm, { props: { doc, onChanged } });

    await fireEvent.change(screen.getByDisplayValue("8080"), { target: { value: "9090" } });
    await fireEvent.input(screen.getByDisplayValue("127.0.0.1"), { target: { value: "0.0.0.0" } });

    const manual = doc.variants[MANUAL_VARIANT];
    expect(manual.http?.port).toBe(9090);
    expect(manual.http?.bind).toBe("0.0.0.0");
    expect(onChanged).toHaveBeenCalled();
  });

  it("writes the runtime binary to host_paths, never into the deployed config", async () => {
    const doc = reactive(createProjectDocument("demo"));
    render(RuntimeForm, { props: { doc, onChanged: vi.fn() } });

    const inputs = screen.getAllByRole("textbox");
    const exePath = inputs[inputs.length - 1] as HTMLInputElement;
    await fireEvent.input(exePath, { target: { value: "/home/me/build/anolis-runtime" } });

    expect(doc.host_paths?.runtime_executable).toBe("/home/me/build/anolis-runtime");
    // The runtime config carries no host paths — install.sh installs the pinned
    // binary under its own prefix.
    expect(JSON.stringify(doc.variants[MANUAL_VARIANT])).not.toContain("/home/me");
  });

  it("warns when the manual variant would boot into automation", () => {
    const doc = createProjectDocument("demo");
    doc.variants[MANUAL_VARIANT].automation = { enabled: true };

    render(RuntimeForm, { props: { doc: reactive(doc), onChanged: vi.fn() } });

    // install.sh refuses this outright; catching it here beats failing at sudo.
    expect(screen.getByTestId("inertness-warning")).toBeInTheDocument();
  });

  it("offers no InfluxDB token field: the runtime reads INFLUXDB_TOKEN from its environment", () => {
    const doc = createProjectDocument("demo");
    doc.variants[MANUAL_VARIANT].telemetry = { enabled: true, influxdb: {} };

    render(RuntimeForm, { props: { doc: reactive(doc), onChanged: vi.fn() } });

    expect(screen.getByText("InfluxDB bucket")).toBeInTheDocument();
    expect(screen.queryAllByText(/token/i)).toHaveLength(0);
  });
});

describe("ProfileForm.svelte", () => {
  it("warns until every kind a variant runs is pinned", async () => {
    const doc = createProjectDocument("demo");
    withProvider(doc, "bread0", "bread", {});
    delete doc.profile.components!.providers!.bread;
    const reactiveDoc = reactive(doc);

    render(ProfileForm, { props: { doc: reactiveDoc, onChanged: vi.fn() } });

    expect(screen.getByTestId("missing-pins-warning")).toHaveTextContent("bread");

    await fireEvent.input(screen.getByLabelText("bread version"), { target: { value: "0.3.8" } });

    expect(reactiveDoc.profile.components?.providers?.bread).toEqual({
      repo: "anolishq/anolis-provider-bread",
      version: "0.3.8",
    });
    await waitFor(() => {
      expect(screen.queryByTestId("missing-pins-warning")).not.toBeInTheDocument();
    });
  });

  it("names no release in the pin placeholders, so they cannot go stale", () => {
    const doc = createProjectDocument("demo");
    withProvider(doc, "bread0", "bread", {});
    render(ProfileForm, { props: { doc: reactive(doc), onChanged: vi.fn() } });

    for (const label of ["Runtime version", "bread version"]) {
      expect(screen.getByLabelText(label).getAttribute("placeholder")).not.toMatch(/\d/);
    }
  });

  it("does not let the machine_id be edited", () => {
    const doc = reactive(createProjectDocument("demo"));
    render(ProfileForm, { props: { doc, onChanged: vi.fn() } });

    // It is the deploy identity: install.sh keys the installed directory and
    // every config path token on it.
    expect(screen.getByLabelText("Machine ID")).toHaveAttribute("readonly");
  });
});

describe("Compose.svelte (variants)", () => {
  it("adds automation as a SEPARATE variant, leaving manual inert", async () => {
    const doc = reactive(createProjectDocument("demo"));
    render(Compose, {
      props: {
        projectName: "demo",
        system: doc,
        providerSchemas: null,
        runtimeStatus: createRuntimeStatus(),
        onDirty: vi.fn(),
        onSaved: vi.fn(),
      },
    });

    // The behaviour tree is authored outside the workbench, so the control asks
    // for its path rather than inventing one that isn't there.
    await fireEvent.click(screen.getByRole("button", { name: /Automation variant/ }));
    await fireEvent.input(screen.getByPlaceholderText("behaviors/main.xml"), {
      target: { value: "behaviors/main.xml" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Add" }));

    // Before #255 the composer set automation on its ONLY config, and install.sh
    // rejected every such deploy for having a non-inert manual variant.
    expect(doc.variants[AUTOMATION_VARIANT].automation).toEqual({
      enabled: true,
      behavior_tree: "../anolis-projects/projects/demo/behaviors/main.xml",
    });
    expect(doc.variants[MANUAL_VARIANT].automation).toEqual({ enabled: false });
    expect(doc.profile.runtime_profiles[AUTOMATION_VARIANT]).toBe(
      "config/anolis-runtime.automation.yaml",
    );
    expect(doc.profile.behaviors).toEqual(["behaviors/main.xml"]);
  });

  it("sends the whole document on save, so unknown keys survive", async () => {
    const doc = createProjectDocument("demo");
    // A key the workbench has no model for must round-trip untouched.
    doc.variants[MANUAL_VARIANT].experimental_knob = { deep: true };
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse(200, { ok: true }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(Compose, {
      props: {
        projectName: "demo",
        system: reactive(doc),
        providerSchemas: null,
        runtimeStatus: createRuntimeStatus(),
        onDirty: vi.fn(),
        onSaved: vi.fn(),
      },
    });

    await fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(body.variants.manual.experimental_knob).toEqual({ deep: true });
    expect(body.profile.machine_id).toBe("demo");
  });
});

describe("Compose.svelte (automation variant is reversible)", () => {
  it("removes the variant and its behaviour reference again", async () => {
    const doc = reactive(createProjectDocument("demo"));
    render(Compose, {
      props: {
        projectName: "demo",
        system: doc,
        providerSchemas: null,
        runtimeStatus: createRuntimeStatus(),
        onDirty: vi.fn(),
        onSaved: vi.fn(),
      },
    });

    await fireEvent.click(screen.getByRole("button", { name: /Automation variant/ }));
    await fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(doc.variants[AUTOMATION_VARIANT]).toBeDefined();

    await fireEvent.click(screen.getByRole("button", { name: /Remove automation variant/ }));

    // A dangling behaviours reference blocks EVERY deploy of the project, so
    // undoing has to take the profile entry with it.
    expect(doc.variants[AUTOMATION_VARIANT]).toBeUndefined();
    expect(doc.profile.runtime_profiles[AUTOMATION_VARIANT]).toBeUndefined();
    expect(doc.profile.behaviors).toBeUndefined();
  });
});

describe("regressions found reviewing the fixes", () => {
  it("removing the automation variant clears behaviours at ANY path depth", async () => {
    // Reconstructing the relpath by counting segments only worked for a
    // two-deep path; anything else left the behaviour declared with no variant
    // using it, which blocks the save AND every deploy, unclearable from the UI.
    for (const rel of ["behaviors/main.xml", "behaviors/sub/dir/tree.xml", "tree.xml"]) {
      const doc = reactive(createProjectDocument("demo"));
      const { unmount } = render(Compose, {
        props: {
          projectName: "demo",
          system: doc,
          providerSchemas: null,
          runtimeStatus: createRuntimeStatus(),
          onDirty: vi.fn(),
          onSaved: vi.fn(),
        },
      });

      await fireEvent.click(screen.getByRole("button", { name: /Automation variant/ }));
      await fireEvent.input(screen.getByPlaceholderText("behaviors/main.xml"), {
        target: { value: rel },
      });
      await fireEvent.click(screen.getByRole("button", { name: "Add" }));
      expect(doc.profile.behaviors).toEqual([rel]);

      await fireEvent.click(screen.getByRole("button", { name: /Remove automation variant/ }));
      expect(doc.profile.behaviors, `left dangling for ${rel}`).toBeUndefined();
      unmount();
    }
  });

  it("clearing the CORS box omits the key instead of writing an invalid []", async () => {
    // The runtime schema requires a non-empty list when http is enabled, so []
    // makes the project unsaveable with an error naming neither rule nor fix.
    const doc = reactive(createProjectDocument("demo"));
    doc.variants[MANUAL_VARIANT].http!.cors_allowed_origins = ["http://localhost:3000"];
    // Scope to THIS render: other suites in this file mount Compose, which
    // renders a RuntimeForm of its own.
    const { container } = render(RuntimeForm, { props: { doc, onChanged: vi.fn() } });

    const textarea = container.querySelector("textarea") as HTMLTextAreaElement;
    expect(textarea.value).toContain("http://localhost:3000");
    await fireEvent.input(textarea, { target: { value: "  \n  " } });

    // Assert on the serialized form: a Svelte $state proxy still answers
    // `toHaveProperty` for a deleted key, but the value that gets PUT does not.
    expect(Object.keys(doc.variants[MANUAL_VARIANT].http ?? {})).not.toContain(
      "cors_allowed_origins",
    );
  });
});

describe("component pins are authored data", () => {
  it("changing a provider's kind does not drop the old kind's pin", async () => {
    // A pin carries `repo` — a fork or an air-gapped mirror. Dropping it means
    // a later re-pin silently substitutes the upstream repo.
    const doc = createProjectDocument("demo");
    withProvider(doc, "sim0", "sim", { provider: { name: "sim0" } });
    doc.profile.components!.providers!.sim = {
      repo: "myorg/anolis-provider-sim",
      version: "0.9.1",
    };
    const reactiveDoc = reactive(doc);

    render(ProviderList, {
      props: { doc: reactiveDoc, providerSchemas: schemasResponseForPins(), onChanged: vi.fn() },
    });

    const kindSelect = document.querySelector(".provider-kind-select") as HTMLSelectElement;
    await fireEvent.change(kindSelect, { target: { value: "bread" } });

    expect(reactiveDoc.profile.components?.providers?.sim).toEqual({
      repo: "myorg/anolis-provider-sim",
      version: "0.9.1",
    });
  });
});

describe("project warnings", () => {
  it("are shown for an authored project, not just an imported one", () => {
    // The migrator records what it could NOT carry — a dropped provider, an
    // automation variant left behind. Those are authored projects, so rendering
    // warnings only for imports meant real data loss had no surface at all.
    const doc = createProjectDocument("demo");
    doc.warnings = ["Provider 'ezo0' has no kind; it was dropped from the migrated project."];

    render(Compose, {
      props: {
        projectName: "demo",
        system: reactive(doc),
        providerSchemas: null,
        runtimeStatus: createRuntimeStatus(),
        onDirty: vi.fn(),
        onSaved: vi.fn(),
      },
    });

    expect(screen.getByTestId("project-warnings")).toHaveTextContent("ezo0");
  });
});

describe("stale component pins", () => {
  it("are surfaced with a way to remove them", async () => {
    // install.sh downloads a binary for EVERY pin and fails the bundle when the
    // release is missing, so a pin no variant runs is a hard deploy failure —
    // and it used to have no editor at all.
    const doc = createProjectDocument("demo");
    withProvider(doc, "sim0", "sim", { provider: { name: "sim0" } });
    doc.profile.components!.providers!.ezo = {
      repo: "anolishq/anolis-provider-ezo",
      version: "0.3.4",
    };
    const reactiveDoc = reactive(doc);

    render(ProfileForm, { props: { doc: reactiveDoc, onChanged: vi.fn() } });

    expect(screen.getByTestId("stale-pin-ezo")).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: /Remove pin/ }));

    expect(reactiveDoc.profile.components?.providers?.ezo).toBeUndefined();
    // The pin a variant DOES run is untouched.
    expect(reactiveDoc.profile.components?.providers?.sim).toBeDefined();
  });
});

describe("provider schema version skew (#283)", () => {
  /**
   * Read the version off the envelope rather than hardcoding it: a routine
   * schema-sync PR bumps `provider_version`, and a test pinned to the old
   * literal would fail for a reason that has nothing to do with the feature.
   */
  function packagedVersion(kind: string): string {
    const version = schemasResponseForPins().providers[kind]?.provider_version;
    if (!version) throw new Error(`vendored ${kind} envelope has no provider_version`);
    return version;
  }

  /** A version guaranteed to differ from whatever is currently vendored. */
  function olderThanPackaged(kind: string): string {
    return `${packagedVersion(kind)}-older`;
  }

  function skewedDoc(pin = olderThanPackaged("bread")) {
    const doc = createProjectDocument("demo");
    withProvider(doc, "bread0", "bread", {});
    // A machine commissioned before the packaged envelope was synced — the
    // live case: deployed rigs run an older bread than the workbench ships.
    doc.profile.components!.providers!.bread = {
      repo: "anolishq/anolis-provider-bread",
      version: pin,
    };
    return reactive(doc);
  }

  it("names both versions when the pin disagrees with the packaged envelope", () => {
    render(ProfileForm, {
      props: { doc: skewedDoc(), providerSchemas: schemasResponseForPins(), onChanged: vi.fn() },
    });

    const warning = screen.getByTestId("schema-skew-bread");
    expect(warning).toHaveTextContent(packagedVersion("bread"));
    expect(warning).toHaveTextContent(olderThanPackaged("bread"));
  });

  it("clears as soon as the pin is corrected, with no save", async () => {
    // Derived, not stored — the property that keeps it from going stale the
    // way sidecar-carried warnings do (#290).
    render(ProfileForm, {
      props: { doc: skewedDoc(), providerSchemas: schemasResponseForPins(), onChanged: vi.fn() },
    });
    expect(screen.getByTestId("schema-skew-bread")).toBeInTheDocument();

    await fireEvent.input(screen.getByLabelText("bread version"), {
      target: { value: packagedVersion("bread") },
    });

    await waitFor(() => {
      expect(screen.queryByTestId("schema-skew-bread")).not.toBeInTheDocument();
    });
  });

  it("stays quiet when the schemas could not be loaded", () => {
    // Nothing to compare against is not the same as agreement, but a warning
    // the user cannot act on is worse than none.
    render(ProfileForm, { props: { doc: skewedDoc(), providerSchemas: null, onChanged: vi.fn() } });

    expect(screen.queryByTestId("schema-skew-bread")).not.toBeInTheDocument();
  });

  it("warns on a pin no variant runs, where the backend also warns", () => {
    // The backend keys skew on declared providers; this loop keys on variant
    // commands. Without this the user gets a project-level warning naming a
    // kind whose only field on screen is a readonly stale-pin input.
    const doc = createProjectDocument("demo");
    withProvider(doc, "sim0", "sim", {});
    doc.profile.components!.providers!.sim = {
      repo: "anolishq/anolis-provider-sim",
      version: packagedVersion("sim"),
    };
    doc.profile.components!.providers!.bread = {
      repo: "anolishq/anolis-provider-bread",
      version: olderThanPackaged("bread"),
    };

    render(ProfileForm, {
      props: { doc: reactive(doc), providerSchemas: schemasResponseForPins(), onChanged: vi.fn() },
    });

    expect(screen.getByTestId("stale-pin-bread")).toBeInTheDocument();
    expect(screen.getByTestId("schema-skew-bread")).toHaveTextContent(packagedVersion("bread"));
  });

  it("stays quiet when the pin matches", () => {
    const doc = createProjectDocument("demo");
    withProvider(doc, "sim0", "sim", {});
    doc.profile.components!.providers!.sim = {
      repo: "anolishq/anolis-provider-sim",
      version: packagedVersion("sim"),
    };

    render(ProfileForm, {
      props: { doc: reactive(doc), providerSchemas: schemasResponseForPins(), onChanged: vi.fn() },
    });

    expect(screen.queryByTestId("schema-skew-sim")).not.toBeInTheDocument();
  });
});

describe("Compose.svelte — removing a variant removes the providers only it ran (#554)", () => {
  it("leaves no trace of a provider added while the automation variant was selected", async () => {
    const doc = reactive(withProvider(createProjectDocument("demo"), "sim0", "sim"));
    render(Compose, {
      props: {
        projectName: "demo",
        system: doc,
        providerSchemas: schemasResponseForPins(),
        runtimeStatus: createRuntimeStatus(),
        onDirty: vi.fn(),
        onSaved: vi.fn(),
      },
    });

    await fireEvent.click(screen.getByRole("button", { name: /Automation variant/ }));
    await fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await fireEvent.change(screen.getByRole("combobox", { name: "Kind for the new provider" }), {
      target: { value: "bread" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "+ Add Provider" }));
    expect(doc.variants[AUTOMATION_VARIANT].providers?.map((p) => p.id)).toEqual(["sim0", "bread0"]);

    await fireEvent.click(screen.getByRole("button", { name: /Remove automation variant/ }));

    const saved = JSON.stringify(doc);
    expect(saved).not.toContain("bread0");
    // sim0 is still run by the manual variant, so it stays.
    expect(Object.keys(doc.providers)).toEqual(["sim0"]);
    expect(Object.keys(doc.profile.providers)).toEqual(["sim0"]);
    expect(Object.keys(doc.host_paths?.providers ?? {})).toEqual(["sim0"]);
  });
});

describe("Compose.svelte — a failed save shows untouched required fields (#578)", () => {
  it("reports an empty required provider field after a failed save, not before", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(422, { error: "Validation failed", errors: [] })),
    );
    const doc = reactive(
      withProvider(createProjectDocument("demo"), "bread0", "bread", {
        provider: { name: "bread0" },
        hardware: {},
        discovery: { mode: "scan" },
        devices: [],
      }),
    );
    render(Compose, {
      props: {
        projectName: "demo",
        system: doc,
        providerSchemas: schemasResponseForPins(),
        runtimeStatus: createRuntimeStatus(),
        onDirty: vi.fn(),
        onSaved: vi.fn(),
      },
    });

    const bus = await screen.findByPlaceholderText("/dev/i2c-1 or mock://name");
    expect(bus).not.toHaveAttribute("aria-invalid");

    await fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(bus).toHaveAttribute("aria-invalid", "true"));
  });
});
