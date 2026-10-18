import { readdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";

import { AUTOMATION_VARIANT, MANUAL_VARIANT, variantRelpath } from "../../src/lib/canonical";
import type {
  ProjectDocument,
  ProviderSchemaEnvelope,
  ProviderSchemasResponse,
  UnknownRecord,
} from "../../src/lib/contracts";
import ProviderList from "../../src/lib/ProviderList.svelte";
import { asObject, defaultsFor } from "../../src/lib/schema-form/schema";
import { createProjectDocument, withProvider } from "./helpers";
import { reactive } from "./helpers.svelte";

const HERE = dirname(fileURLToPath(import.meta.url));

function loadEnvelope(kind: string): ProviderSchemaEnvelope {
  const path = resolve(
    HERE,
    `../../../anolis_workbench/schemas/providers/${kind}.config-schema.json`,
  );
  return JSON.parse(readFileSync(path, "utf-8")) as ProviderSchemaEnvelope;
}

// Every vendored envelope, globbed rather than named, so a new provider is
// covered without editing this file.
const ENVELOPE_DIR = resolve(HERE, "../../../anolis_workbench/schemas/providers");
const VENDORED_KINDS = readdirSync(ENVELOPE_DIR)
  .filter((f) => f.endsWith(".config-schema.json"))
  .map((f) => f.replace(/\.config-schema\.json$/, ""))
  .sort();

function allSchemasResponse(): ProviderSchemasResponse {
  return {
    schema_version: 1,
    providers: Object.fromEntries(VENDORED_KINDS.map((kind) => [kind, loadEnvelope(kind)])),
  };
}

function schemasResponse(): ProviderSchemasResponse {
  return {
    schema_version: 1,
    providers: {
      bread: loadEnvelope("bread"),
      ezo: loadEnvelope("ezo"),
      sim: loadEnvelope("sim"),
    },
  };
}

function docWithSim(): ProjectDocument {
  const doc = createProjectDocument("demo");
  withProvider(
    doc,
    "sim0",
    "sim",
    {
      provider: { name: "sim0" },
      startup_policy: "degraded",
      devices: [],
      simulation: { mode: "non_interacting", tick_rate_hz: 10 },
    },
    { executable: "/bin/sim" },
  );
  return reactive(doc);
}

describe("ProviderList.svelte", () => {
  it("degrades gracefully with providerSchemas null", () => {
    render(ProviderList, {
      props: { doc: docWithSim(), providerSchemas: null, onChanged: vi.fn() },
    });

    expect(screen.getByText(/Provider schemas are unavailable/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ Add Provider" })).toBeDisabled();
    // No schema — no schema-driven form, but the row itself renders
    expect(screen.getByDisplayValue("sim0")).toBeInTheDocument();
    expect(screen.queryByText("Configure")).not.toBeInTheDocument();
  });

  it("renders the schema-driven form with kind labels from envelopes", () => {
    render(ProviderList, {
      props: { doc: docWithSim(), providerSchemas: schemasResponse(), onChanged: vi.fn() },
    });

    const kindSelect = document.querySelector(".provider-kind-select") as HTMLSelectElement;
    expect(
      within(kindSelect).getByRole("option", { name: "anolis-provider-sim" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Configure")).toBeInTheDocument();
    expect(screen.getByText("Startup policy")).toBeInTheDocument();
    expect(screen.getByText("Simulation mode")).toBeInTheDocument();
  });

  it("kind change seeds a native config and re-points the deploy token", async () => {
    const doc = docWithSim();
    const onChanged = vi.fn();
    render(ProviderList, {
      props: { doc, providerSchemas: schemasResponse(), onChanged },
    });

    const kindSelect = document.querySelector(".provider-kind-select") as HTMLSelectElement;
    await fireEvent.change(kindSelect, { target: { value: "bread" } });

    const entry = doc.providers.sim0;
    expect(entry.kind).toBe("bread");
    expect(entry.config.provider).toEqual({ name: "sim0" });
    expect(entry.config.hardware).toEqual({
      query_delay_us: 10000,
      timeout_ms: 100,
      retry_count: 2,
    });
    // The schema declares discovery.mode's default, so it is prefilled.
    expect(entry.config.discovery).toEqual({ mode: "manual" });
    expect(entry.config.devices).toEqual([]);

    // The command token, the config path token and the profile must all move
    // with the kind, or install.sh fetches the wrong binary — or none.
    const runtimeEntry = doc.variants[MANUAL_VARIANT].providers![0];
    expect(runtimeEntry.command).toBe(
      "../anolis-provider-bread/build/dev-release/anolis-provider-bread",
    );
    expect(runtimeEntry.args).toEqual([
      "--config",
      "../anolis-projects/projects/demo/config/provider-bread.sim0.yaml",
    ]);
    expect(doc.profile.providers.sim0.config).toBe("config/provider-bread.sim0.yaml");

    // The host path is dev-launch state and is reset, not carried over.
    expect(doc.host_paths?.providers?.sim0).toEqual({ executable: "" });
    expect(onChanged).toHaveBeenCalled();
    await waitFor(() => {
      expect(screen.getByText("I2C bus path")).toBeInTheDocument();
    });
  });

  it("add provider declares it in the profile, the variant and the config set", async () => {
    const doc = reactive(createProjectDocument("demo"));
    const onChanged = vi.fn();
    render(ProviderList, {
      props: { doc, providerSchemas: schemasResponse(), onChanged },
    });

    await fireEvent.change(screen.getByRole("combobox", { name: "Kind for the new provider" }), {
      target: { value: "sim" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "+ Add Provider" }));

    const entry = doc.providers.sim0;
    expect(entry.kind).toBe("sim");
    expect(entry.config.provider).toEqual({ name: "sim0" });
    expect(entry.config.startup_policy).toBe("strict");
    expect((entry.config.simulation as UnknownRecord).mode).toBeUndefined();

    expect(doc.profile.providers.sim0.config).toBe("config/provider-sim.sim0.yaml");
    expect(doc.variants[MANUAL_VARIANT].providers).toHaveLength(1);
    expect(doc.variants[MANUAL_VARIANT].providers![0].command).toBe(
      "../anolis-provider-sim/build/dev-release/anolis-provider-sim",
    );
    expect(onChanged).toHaveBeenCalled();
  });

  it("add provider offers no default kind and cannot submit until one is chosen", async () => {
    const doc = reactive(createProjectDocument("demo"));
    render(ProviderList, {
      props: { doc, providerSchemas: allSchemasResponse(), onChanged: vi.fn() },
    });

    const kindSelect = screen.getByRole("combobox", {
      name: "Kind for the new provider",
    }) as HTMLSelectElement;
    const add = screen.getByRole("button", { name: "+ Add Provider" });
    expect(kindSelect.value).toBe("");
    expect(add).toBeDisabled();

    await fireEvent.click(add);
    expect(Object.keys(doc.providers ?? {})).toEqual([]);
  });

  it.each(VENDORED_KINDS)(
    "add provider seeds a %s config from its schema's own defaults",
    async (kind) => {
      const doc = reactive(createProjectDocument("demo"));
      const schemas = allSchemasResponse();
      render(ProviderList, { props: { doc, providerSchemas: schemas, onChanged: vi.fn() } });

      const kindSelect = screen.getByRole("combobox", {
        name: "Kind for the new provider",
      }) as HTMLSelectElement;
      await fireEvent.change(kindSelect, { target: { value: kind } });
      await fireEvent.click(screen.getByRole("button", { name: "+ Add Provider" }));

      const id = `${kind}0`;
      expect(doc.providers[id].kind).toBe(kind);
      const schema = asObject(schemas.providers[kind].schema)!;
      const expected = defaultsFor(schema);
      // The instance name is the one value seeding adds beyond the schema's own.
      if (asObject(asObject(schema.properties)?.provider)) {
        expected.provider = { ...(asObject(expected.provider) ?? {}), name: id };
      }
      expect(doc.providers[id].config).toEqual(expected);

      // The next provider needs its own explicit choice.
      expect(kindSelect.value).toBe("");
      expect(screen.getByRole("button", { name: "+ Add Provider" })).toBeDisabled();
    },
  );

  /** Titles of every const-valued property in a schema, at any depth. */
  function constTitles(node: UnknownRecord): string[] {
    const out: string[] = [];
    for (const child of Object.values(asObject(node.properties) ?? {})) {
      const record = asObject(child);
      if (!record) continue;
      if (record.const !== undefined && typeof record.title === "string") out.push(record.title);
      out.push(...constTitles(record));
      const items = asObject(record.items);
      if (items) out.push(...constTitles(items));
    }
    return out;
  }

  async function addKind(kind: string): Promise<ProjectDocument> {
    const doc = reactive(createProjectDocument("demo"));
    render(ProviderList, {
      props: { doc, providerSchemas: allSchemasResponse(), onChanged: vi.fn() },
    });
    await fireEvent.change(screen.getByRole("combobox", { name: "Kind for the new provider" }), {
      target: { value: kind },
    });
    await fireEvent.click(screen.getByRole("button", { name: "+ Add Provider" }));
    return doc;
  }

  const NAMED_KINDS = VENDORED_KINDS.filter((kind) => {
    const schema = asObject(loadEnvelope(kind).schema);
    const provider = asObject(asObject(schema?.properties)?.provider);
    return asObject(asObject(provider?.properties)?.name) !== null;
  });

  it.each(NAMED_KINDS)("%s: provider.name follows the provider id, read-only", async (kind) => {
    const doc = await addKind(kind);
    const id = `${kind}0`;

    const group = (await screen.findByText("Provider name")).parentElement as HTMLElement;
    const input = group.querySelector("input") as HTMLInputElement;
    expect(input.value).toBe(id);
    expect(input.readOnly || input.disabled).toBe(true);
    expect(within(group).getByText("Follows the provider ID.")).toBeInTheDocument();

    const idInput = document.querySelector(".provider-id-input") as HTMLInputElement;
    await fireEvent.blur(idInput, { target: { value: "renamed" } });
    expect(asObject(doc.providers.renamed.config.provider)?.name).toBe("renamed");
    await waitFor(() => {
      const after = screen.getByText("Provider name").parentElement as HTMLElement;
      expect((after.querySelector("input") as HTMLInputElement).value).toBe("renamed");
    });
  });

  const CONST_KINDS = VENDORED_KINDS.filter(
    (kind) => constTitles(asObject(loadEnvelope(kind).schema)!).length > 0,
  );

  it("some vendored envelope declares a const property", () => {
    expect(CONST_KINDS.length).toBeGreaterThan(0);
  });

  it.each(CONST_KINDS)(
    "%s: const properties render no control but stay in the config",
    async (kind) => {
      const doc = await addKind(kind);
      const schema = asObject(loadEnvelope(kind).schema)!;

      await screen.findByText("Configure");
      for (const title of constTitles(schema)) {
        expect(screen.queryByText(title)).not.toBeInTheDocument();
      }
      // The const values are still seeded into the authored config.
      const expected = defaultsFor(schema);
      delete expected.provider; // name is the id, covered above
      expect(doc.providers[`${kind}0`].config).toEqual(expect.objectContaining(expected));
    },
  );

  it("remove drops the provider from EVERY variant, not just the edited one", async () => {
    const doc = createProjectDocument("demo");
    doc.profile.runtime_profiles[AUTOMATION_VARIANT] = variantRelpath(AUTOMATION_VARIANT);
    doc.variants[AUTOMATION_VARIANT] = {
      ...structuredClone(doc.variants[MANUAL_VARIANT]),
      automation: { enabled: true },
    };
    withProvider(doc, "sim0", "sim", { provider: { name: "sim0" } });
    withProvider(
      doc,
      "sim0b",
      "sim",
      { provider: { name: "sim0b" } },
      { variant: AUTOMATION_VARIANT },
    );
    // The automation variant runs sim0 too.
    doc.variants[AUTOMATION_VARIANT].providers = [
      ...(doc.variants[AUTOMATION_VARIANT].providers ?? []),
      { id: "sim0", command: doc.variants[MANUAL_VARIANT].providers![0].command },
    ];
    const reactiveDoc = reactive(doc);

    render(ProviderList, {
      props: { doc: reactiveDoc, providerSchemas: schemasResponse(), onChanged: vi.fn() },
    });

    await fireEvent.click(screen.getAllByRole("button", { name: "✕ Remove" })[0]);

    // A variant may not run a provider the profile no longer declares — leaving
    // it behind in another variant makes install.sh reject the whole machine.
    expect(reactiveDoc.profile.providers.sim0).toBeUndefined();
    expect(reactiveDoc.providers.sim0).toBeUndefined();
    for (const config of Object.values(reactiveDoc.variants)) {
      expect((config.providers ?? []).some((p) => p.id === "sim0")).toBe(false);
    }
  });

  it("warns when a provider runs a kind the profile does not pin", () => {
    const doc = createProjectDocument("demo");
    withProvider(doc, "sim0", "sim", { provider: { name: "sim0" } });
    delete doc.profile.components!.providers!.sim;
    doc.profile.components!.providers!.bread = {
      repo: "anolishq/anolis-provider-bread",
      version: "0.3.8",
    };

    render(ProviderList, {
      props: { doc: reactive(doc), providerSchemas: schemasResponse(), onChanged: vi.fn() },
    });

    expect(screen.getByTestId("unpinned-kind-warning")).toBeInTheDocument();
  });

  it("tolerates a provider whose kind has no vendored schema", () => {
    const doc = createProjectDocument("demo");
    withProvider(doc, "ghost0", "ghost", {});

    render(ProviderList, {
      props: { doc: reactive(doc), providerSchemas: schemasResponse(), onChanged: vi.fn() },
    });

    expect(screen.getByDisplayValue("ghost0")).toBeInTheDocument();
    expect(screen.getByText(/No config schema is vendored/)).toBeInTheDocument();
  });
});
