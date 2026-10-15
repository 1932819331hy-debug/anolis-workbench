<script lang="ts">
  import {
    activeVariant,
    commandKind,
    providerCommandToken,
    providerConfigRelpath,
    projectPathToken,
  } from "./canonical";
  import type {
    ProjectDocument,
    ProviderSchemasResponse,
    RuntimeProviderEntry,
    UnknownRecord,
  } from "./contracts";
  import SchemaForm from "./schema-form/SchemaForm.svelte";
  import { asObject, defaultsFor, properties, type SchemaNode } from "./schema-form/schema";

  /**
   * ProviderList.svelte — provider list with add/remove/kind-switch.
   *
   * Provider config is rendered schema-driven from the vendored
   * --config-schema envelopes (#270); this component has no per-kind knowledge.
   *
   * A provider exists in THREE places that must agree (#255): the machine
   * profile declares it and names its config file, each runtime variant lists
   * it under `providers[]`, and the document carries its config. Add/remove/
   * rename here keep all three in step — a save that breaks the agreement is
   * rejected by the backend, and would be rejected by install.sh after that.
   */
  let {
    doc,
    variant,
    providerSchemas,
    onChanged,
  }: {
    doc: ProjectDocument;
    variant?: string;
    providerSchemas: ProviderSchemasResponse | null;
    onChanged: () => void;
  } = $props();

  const variantName = $derived(variant ?? activeVariant(doc));
  const machineId = $derived(doc.profile?.machine_id ?? "");

  // The set of composable kinds IS the set of vendored envelopes.
  const kinds = $derived(Object.keys(providerSchemas?.providers ?? {}).sort());

  // Display labels come from the provider-owned schema envelopes: the schema
  // title (minus the " configuration" suffix) or the provider binary name.
  const kindLabels = $derived(
    Object.fromEntries(
      Object.entries(providerSchemas?.providers ?? {}).map(([kind, env]) => {
        const title = typeof env.schema?.title === "string" ? (env.schema.title as string) : "";
        const label = title.replace(/ configuration$/i, "") || (env.provider as string) || kind;
        return [kind, label];
      }),
    ) as Record<string, string>,
  );

  const entries = $derived(
    (doc.variants?.[variantName]?.providers ?? []) as RuntimeProviderEntry[],
  );

  /** Kinds the profile pins. install.sh refuses a command that resolves to an unpinned kind. */
  const pinned = $derived(new Set(Object.keys(doc.profile?.components?.providers ?? {})));

  function inputTarget(event: Event): HTMLInputElement {
    return event.currentTarget as HTMLInputElement;
  }

  function kindOf(id: string): string {
    return doc.providers?.[id]?.kind ?? "";
  }

  function genId(kind: string): string {
    const existing = Object.keys(doc.providers ?? {})
      .filter((id) => id.startsWith(kind))
      .map((id) => parseInt(id.slice(kind.length), 10))
      .filter((n) => !isNaN(n));
    const next = existing.length ? Math.max(...existing) + 1 : 0;
    return `${kind}${next}`;
  }

  function envelopeSchema(kind: string): SchemaNode | null {
    return asObject(providerSchemas?.providers?.[kind]?.schema);
  }

  /**
   * Seed a fresh native config for a kind: the schema's own defaults/consts,
   * and the provider instance name set to the id. A field the schema gives no
   * default stays unset until the user picks a value.
   */
  function seedConfig(kind: string, id: string): UnknownRecord {
    const schema = envelopeSchema(kind);
    if (!schema) return {};
    const config = defaultsFor(schema);
    const schemaProps = Object.fromEntries(properties(schema));

    if (schemaProps.provider) {
      const provider = asObject(config.provider) ?? {};
      provider.name = id;
      config.provider = provider;
    }
    return config;
  }

  /** A runtime entry whose command and args are canonical deploy tokens. */
  function runtimeEntry(id: string, kind: string): RuntimeProviderEntry {
    return {
      id,
      command: providerCommandToken(kind),
      args: ["--config", projectPathToken(machineId, providerConfigRelpath(kind, id))],
      timeout_ms: 5000,
      hello_timeout_ms: 3000,
      ready_timeout_ms: 10000,
      restart_policy: { enabled: false },
    };
  }

  /** Re-point an existing entry's command/args after an id or kind change. */
  function retarget(entry: RuntimeProviderEntry, id: string, kind: string): void {
    entry.id = id;
    entry.command = providerCommandToken(kind);
    entry.args = ["--config", projectPathToken(machineId, providerConfigRelpath(kind, id))];
  }

  function compatEntries(): UnknownRecord {
    doc.profile.compatibility ??= {};
    return ((doc.profile.compatibility as UnknownRecord).providers ??= {}) as UnknownRecord;
  }

  /** `carry` preserves an authored compatibility entry across a rename. */
  function declare(id: string, kind: string, carry?: unknown): void {
    doc.profile.providers ??= {};
    doc.profile.providers[id] = { config: providerConfigRelpath(kind, id) };
    const compat = compatEntries();
    compat[id] = carry ?? compat[id] ?? { strategy: "local-build", version: "unspecified" };
  }

  function undeclare(id: string): void {
    delete doc.profile?.providers?.[id];
    const compat = asObject((doc.profile?.compatibility as UnknownRecord)?.providers);
    if (compat) delete compat[id];
  }

  // Add-Provider asks for a kind and offers no default.
  let newKind = $state("");

  function addProvider(): void {
    const kind = newKind;
    if (!kinds.includes(kind)) return;
    newKind = "";
    const id = genId(kind);

    declare(id, kind);
    doc.providers ??= {};
    doc.providers[id] = { kind, config: seedConfig(kind, id) };
    // Added to the variant being edited only; other variants deliberately run
    // their own provider sets.
    const config = (doc.variants[variantName] ??= {});
    config.providers = [...(config.providers ?? []), runtimeEntry(id, kind)];

    doc.host_paths ??= {};
    doc.host_paths.providers ??= {};
    doc.host_paths.providers[id] = { executable: "" };
    onChanged();
  }

  function removeProvider(id: string): void {
    undeclare(id);
    delete doc.providers?.[id];
    // Removed from EVERY variant: a variant may not run a provider the profile
    // no longer declares.
    for (const config of Object.values(doc.variants ?? {})) {
      if (Array.isArray(config.providers)) {
        config.providers = config.providers.filter((p) => p.id !== id);
      }
    }
    delete doc.host_paths?.providers?.[id];
    onChanged();
  }

  function renameProvider(oldId: string, newId: string): void {
    const kind = kindOf(oldId);
    const instance = doc.providers?.[oldId];
    if (instance) {
      doc.providers[newId] = instance;
      delete doc.providers[oldId];
      // The provider-native config names the instance too.
      const provider = asObject(instance.config?.provider);
      if (provider && provider.name === oldId) provider.name = newId;
    }
    // The compatibility entry is AUTHORED data with no other editor in the UI —
    // re-keying an id must carry it, not reset it to local-build/unspecified.
    const carried = compatEntries()[oldId];
    undeclare(oldId);
    declare(newId, kind, carried);

    for (const config of Object.values(doc.variants ?? {})) {
      for (const entry of config.providers ?? []) {
        if (entry.id === oldId) retarget(entry, newId, kind);
      }
    }

    const hosts = doc.host_paths?.providers;
    if (hosts && hosts[oldId] !== undefined) {
      hosts[newId] = hosts[oldId];
      delete hosts[oldId];
    }
    onChanged();
  }

  function changeKind(id: string, newKind: string): void {
    doc.providers ??= {};
    doc.providers[id] = { kind: newKind, config: seedConfig(newKind, id) };
    // The old kind's compatibility entry describes a provider this instance no
    // longer is. Component PINS are left alone: they are authored data (a fork
    // or an air-gapped mirror lives in `repo`), and install.sh only warns about
    // one nothing runs — whereas dropping it silently re-points a later
    // re-pin at the upstream repo.
    compatEntries()[id] = { strategy: "local-build", version: "unspecified" };
    declare(id, newKind, compatEntries()[id]);
    for (const config of Object.values(doc.variants ?? {})) {
      for (const entry of config.providers ?? []) {
        if (entry.id === id) retarget(entry, id, newKind);
      }
    }
    doc.host_paths ??= {};
    doc.host_paths.providers ??= {};
    doc.host_paths.providers[id] = { executable: "" };
    onChanged();
  }

  function setHostExecutable(id: string, value: string): void {
    doc.host_paths ??= {};
    doc.host_paths.providers ??= {};
    doc.host_paths.providers[id] = { ...(doc.host_paths.providers[id] ?? {}), executable: value };
    onChanged();
  }

  function formatBackoff(v: unknown): string {
    return Array.isArray(v) ? v.join(", ") : "";
  }
  function parseBackoff(s: string): number[] {
    return String(s)
      .split(",")
      .map((p) => p.trim())
      .filter(Boolean)
      .map(Number)
      .filter((n) => !isNaN(n));
  }
</script>

<section class="form-section providers-section">
  <h3>Providers</h3>

  {#if providerSchemas === null}
    <p class="muted">
      Provider schemas are unavailable — provider configuration cannot be edited right now.
    </p>
  {/if}

  <div class="provider-list">
    {#each entries as prov (prov.id)}
      {@const id = prov.id}
      {@const kind = kindOf(id)}
      {@const isKnownKind = kinds.includes(kind)}
      {@const cfgValue = asObject(doc.providers?.[id]?.config)}
      {@const schema = envelopeSchema(kind)}
      {@const hostExe = doc.host_paths?.providers?.[id]?.executable ?? ""}
      {@const resolvedKind = commandKind(prov.command)}

      <div class="provider-row">
        <!-- Header: id, kind, remove -->
        <div class="provider-row-header">
          <input
            type="text"
            class="provider-id-input"
            spellcheck="false"
            title="Provider ID"
            value={id}
            onblur={(e: Event) => {
              const newId = inputTarget(e).value.trim();
              if (!newId || newId === id) {
                inputTarget(e).value = id;
                return;
              }
              if (doc.providers?.[newId] !== undefined) {
                inputTarget(e).value = id;
                alert(`Provider ID "${newId}" is already in use.`);
                return;
              }
              renameProvider(id, newId);
            }}
          />

          <select
            class="provider-kind-select"
            value={kind}
            disabled={kinds.length === 0}
            onchange={(e: Event) => changeKind(id, inputTarget(e).value)}
          >
            {#if !isKnownKind}
              <option value={kind}>Unknown ({kind || "none"})</option>
            {/if}
            {#each kinds as k (k)}
              <option value={k}>{kindLabels[k] ?? k}</option>
            {/each}
          </select>

          <button type="button" class="btn-remove-provider" onclick={() => removeProvider(id)}
            >✕ Remove</button
          >
        </div>

        {#if !isKnownKind && providerSchemas !== null}
          <div class="bus-note note-warning">
            No config schema is vendored for provider kind "{kind}". Pick a known kind or remove
            this provider before saving.
          </div>
        {/if}

        {#if resolvedKind !== null && pinned.size > 0 && !pinned.has(resolvedKind)}
          <div class="bus-note note-warning" data-testid="unpinned-kind-warning">
            Kind "{resolvedKind}" is not in this machine's pinned components, so install.sh has no
            binary to fetch for it. Resolve component pins before deploying.
          </div>
        {/if}

        <!-- Timeouts -->
        <div class="provider-timing">
          <span class="field-group-label">Timeouts</span>
          {#each [["timeout_ms", "timeout"], ["hello_timeout_ms", "hello"], ["ready_timeout_ms", "ready"]] as [key, display] (key)}
            <label class="inline-label">
              <input
                type="number"
                min="100"
                max="120000"
                value={prov[key] ?? ""}
                onchange={(e: Event) => {
                  const n = Number(inputTarget(e).value);
                  if (!isNaN(n)) {
                    prov[key] = n;
                    onChanged();
                  }
                }}
              />
              ms ({display})
            </label>
          {/each}
        </div>

        <!-- Restart policy -->
        <div class="provider-restart">
          <label>
            <input
              type="checkbox"
              checked={prov.restart_policy?.enabled ?? false}
              onchange={(e: Event) => {
                prov.restart_policy = prov.restart_policy ?? {};
                prov.restart_policy.enabled = inputTarget(e).checked;
                if (inputTarget(e).checked) {
                  prov.restart_policy.max_attempts = prov.restart_policy.max_attempts ?? 3;
                  prov.restart_policy.backoff_ms = Array.isArray(prov.restart_policy.backoff_ms)
                    ? prov.restart_policy.backoff_ms
                    : [200, 500, 1000];
                  prov.restart_policy.timeout_ms = prov.restart_policy.timeout_ms ?? 30000;
                }
                onChanged();
              }}
            />
            Enable restart policy
          </label>

          {#if prov.restart_policy?.enabled}
            <div class="restart-fields">
              <label class="inline-label">
                <input
                  type="number"
                  min="0"
                  value={prov.restart_policy.max_attempts ?? 3}
                  onchange={(e: Event) => {
                    const n = Number(inputTarget(e).value);
                    if (!isNaN(n)) {
                      const rp = prov.restart_policy ?? (prov.restart_policy = {});
                      rp.max_attempts = n;
                      onChanged();
                    }
                  }}
                />
                max attempts
              </label>
              <label class="inline-label">
                <input
                  type="text"
                  spellcheck="false"
                  style="font-family:monospace"
                  placeholder="200, 500, 1000"
                  value={formatBackoff(prov.restart_policy.backoff_ms)}
                  onchange={(e: Event) => {
                    const rp = prov.restart_policy ?? (prov.restart_policy = {});
                    rp.backoff_ms = parseBackoff(inputTarget(e).value);
                    onChanged();
                  }}
                />
                backoff ms
              </label>
              <label class="inline-label">
                <input
                  type="number"
                  min="0"
                  value={prov.restart_policy.timeout_ms ?? 30000}
                  onchange={(e: Event) => {
                    const n = Number(inputTarget(e).value);
                    if (!isNaN(n)) {
                      const rp = prov.restart_policy ?? (prov.restart_policy = {});
                      rp.timeout_ms = n;
                      onChanged();
                    }
                  }}
                />
                timeout (ms)
              </label>
              <label class="inline-label">
                <input
                  type="number"
                  min="0"
                  value={prov.restart_policy.success_reset_ms ?? ""}
                  onchange={(e: Event) => {
                    const rp = prov.restart_policy ?? (prov.restart_policy = {});
                    const v = inputTarget(e).value.trim();
                    if (v === "") {
                      delete rp.success_reset_ms;
                    } else {
                      const n = Number(v);
                      if (!isNaN(n)) rp.success_reset_ms = n;
                    }
                    onChanged();
                  }}
                />
                success reset (ms)
              </label>
            </div>
          {/if}
        </div>

        <!-- Host executable path (dev-launch only; never deployed) -->
        <div class="form-group">
          <label>Executable path</label>
          <input
            type="text"
            spellcheck="false"
            style="font-family:monospace"
            value={hostExe}
            oninput={(e: Event) => setHostExecutable(id, inputTarget(e).value)}
          />
          <span class="field-note"
            >Host path, used for dev-launch only. Deploys use <code>{prov.command ?? ""}</code>,
            which install.sh rewrites to the installed binary.</span
          >
        </div>

        <!-- Schema-driven provider configuration -->
        {#if schema && cfgValue}
          <details class="provider-configure" open>
            <summary>Configure</summary>
            <div class="provider-typed-form">
              <SchemaForm {schema} value={cfgValue} {onChanged} />
            </div>
          </details>
        {/if}
      </div>
    {/each}
  </div>

  <div class="provider-add">
    <select
      class="provider-add-kind"
      aria-label="Kind for the new provider"
      value={newKind}
      onchange={(e: Event) => (newKind = inputTarget(e).value)}
      disabled={kinds.length === 0}
    >
      <option value="" disabled>Choose a kind…</option>
      {#each kinds as k (k)}
        <option value={k}>{kindLabels[k] ?? k}</option>
      {/each}
    </select>
    <button
      type="button"
      class="btn-add-provider"
      onclick={addProvider}
      disabled={!kinds.includes(newKind)}>+ Add Provider</button
    >
  </div>
</section>
