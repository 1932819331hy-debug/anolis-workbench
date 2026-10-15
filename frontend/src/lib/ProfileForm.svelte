<script lang="ts">
  import type { ComponentPin, ProjectDocument, ProviderSchemasResponse } from "./contracts";
  import { commandKind } from "./canonical";

  /**
   * ProfileForm.svelte — the machine's identity and its component pins.
   *
   * Pins are AUTHORED data (#255). Nothing in the deploy or export path
   * resolves them from the network any more: before this, a deploy pinned
   * whatever happened to be the latest release at that minute, which could
   * bump the runtime under a running rig and made deploying offline
   * impossible. The cost is that they have to be filled in here.
   */
  let {
    doc,
    providerSchemas = null,
    onChanged,
  }: {
    doc: ProjectDocument;
    providerSchemas?: ProviderSchemasResponse | null;
    onChanged: () => void;
  } = $props();

  const profile = $derived(doc.profile);

  /** Every kind any variant actually runs — these are the pins install.sh needs. */
  const requiredKinds = $derived(
    Array.from(
      new Set(
        Object.values(doc.variants ?? {}).flatMap((config) =>
          (config.providers ?? [])
            .map((entry) => commandKind(entry.command))
            .filter((kind): kind is string => kind !== null),
        ),
      ),
    ).sort(),
  );

  const missingPins = $derived(
    requiredKinds.filter((kind) => !doc.profile?.components?.providers?.[kind]?.version),
  );

  /**
   * Pins for kinds no variant runs any more.
   *
   * install.sh fetches a binary for EVERY entry in components.providers and
   * fails the whole bundle when an asset is missing, so a stale pin is not
   * cosmetic. It is kept rather than auto-removed because the pin carries
   * `repo` — a fork or an air-gapped mirror — but it needs a way out.
   */
  const stalePins = $derived(
    Object.keys(doc.profile?.components?.providers ?? {})
      .filter((kind) => !requiredKinds.includes(kind))
      .sort(),
  );
  const runtimePinned = $derived(Boolean(doc.profile?.components?.runtime?.version));

  function inputTarget(event: Event): HTMLInputElement {
    return event.currentTarget as HTMLInputElement;
  }

  function setDisplayName(value: string): void {
    doc.profile.display_name = value;
    onChanged();
  }

  function components(): { runtime?: ComponentPin; providers?: Record<string, ComponentPin> } {
    doc.profile.components ??= {};
    return doc.profile.components;
  }

  function setRuntimePin(version: string): void {
    const c = components();
    c.runtime = { repo: c.runtime?.repo ?? "anolishq/anolis", version: version.trim() };
    onChanged();
  }

  function removeProviderPin(kind: string): void {
    const pins = doc.profile?.components?.providers;
    if (pins) delete pins[kind];
    onChanged();
  }

  /**
   * The provider version the vendored envelope was generated from, when it
   * says. A config edited here is checked against THAT contract, so a pin
   * naming a different version is checked against the wrong one (#283).
   */
  function skewFor(kind: string): string | null {
    const envelopeVersion = providerSchemas?.providers?.[kind]?.provider_version;
    const pinned = doc.profile?.components?.providers?.[kind]?.version?.trim();
    if (!envelopeVersion || !pinned || envelopeVersion === pinned) return null;
    return envelopeVersion;
  }

  function setProviderPin(kind: string, version: string): void {
    const c = components();
    c.providers ??= {};
    c.providers[kind] = {
      repo: c.providers[kind]?.repo ?? `anolishq/anolis-provider-${kind}`,
      version: version.trim(),
    };
    onChanged();
  }
</script>

{#snippet skewNote(kind: string)}
  {@const envelopeVersion = skewFor(kind)}
  {#if envelopeVersion}
    <div class="bus-note note-warning" data-testid={`schema-skew-${kind}`}>
      Config forms and validation for <code>{kind}</code> use the schema published by
      <strong>{kind} {envelopeVersion}</strong>, but this machine pins
      <strong>{profile?.components?.providers?.[kind]?.version}</strong>. If the config contract
      changed between them, this machine's provider may reject a config accepted here.
    </div>
  {/if}
{/snippet}

<section class="form-section">
  <h3>Machine</h3>

  <div class="form-group">
    <label for="profile-display-name">Display name</label>
    <input
      id="profile-display-name"
      type="text"
      spellcheck="false"
      value={profile?.display_name ?? ""}
      oninput={(e: Event) => setDisplayName(inputTarget(e).value)}
    />
  </div>

  <div class="form-group">
    <label for="profile-machine-id">Machine ID</label>
    <input
      id="profile-machine-id"
      type="text"
      readonly
      style="font-family:monospace"
      value={profile?.machine_id ?? ""}
    />
    <span class="field-note"
      >The deploy identity. install.sh keys the installed directory and every config path on it, so
      renaming the project deliberately leaves it alone; duplicating a project re-keys it.</span
    >
  </div>

  <h3>Component pins</h3>
  {#if missingPins.length > 0 || !runtimePinned}
    <div class="bus-note note-warning" data-testid="missing-pins-warning">
      This machine cannot be deployed until every component is pinned{#if missingPins.length > 0}&nbsp;({missingPins.join(
          ", ",
        )}){/if}. Pins are recorded here rather than resolved at deploy time, so a deploy can never
      silently move a rig to a newer release.
    </div>
  {/if}

  <div class="form-group">
    <label for="pin-runtime">Runtime version</label>
    <input
      id="pin-runtime"
      type="text"
      spellcheck="false"
      style="font-family:monospace"
      placeholder="MAJOR.MINOR.PATCH"
      value={profile?.components?.runtime?.version ?? ""}
      oninput={(e: Event) => setRuntimePin(inputTarget(e).value)}
    />
    <span class="field-note">{profile?.components?.runtime?.repo ?? "anolishq/anolis"}</span>
  </div>

  {#each stalePins as kind (kind)}
    <div class="form-group" data-testid={`stale-pin-${kind}`}>
      <label for={`pin-${kind}`}>{kind} version</label>
      <input
        id={`pin-${kind}`}
        type="text"
        readonly
        style="font-family:monospace"
        value={profile?.components?.providers?.[kind]?.version ?? ""}
      />
      <button type="button" class="btn-remove-provider" onclick={() => removeProviderPin(kind)}
        >✕ Remove pin</button
      >
      <span class="field-note"
        >No runtime variant runs <code>{kind}</code> any more. install.sh still downloads a binary for
        every pin and fails the bundle if that release is missing.</span
      >
      {@render skewNote(kind)}
    </div>
  {/each}

  {#each requiredKinds as kind (kind)}
    <div class="form-group">
      <label for={`pin-${kind}`}>{kind} version</label>
      <input
        id={`pin-${kind}`}
        type="text"
        spellcheck="false"
        style="font-family:monospace"
        placeholder="MAJOR.MINOR.PATCH"
        value={profile?.components?.providers?.[kind]?.version ?? ""}
        oninput={(e: Event) => setProviderPin(kind, inputTarget(e).value)}
      />
      <span class="field-note"
        >{profile?.components?.providers?.[kind]?.repo ?? `anolishq/anolis-provider-${kind}`}</span
      >
      {@render skewNote(kind)}
    </div>
  {/each}
</section>
