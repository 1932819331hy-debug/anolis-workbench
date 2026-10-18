<script lang="ts">
  import type { UnknownRecord } from "../contracts";
  import SchemaField from "./SchemaField.svelte";
  import {
    asObject,
    authoredProperties,
    defaultsFor,
    descriptionFor,
    enumOptions,
    isI2cField,
    isObjectSchema,
    itemsSchema,
    labelFor,
    parseI2c,
    properties,
    resolveConditionals,
    uniqueViolations,
    validateField,
    type SchemaNode,
  } from "./schema";

  /**
   * SchemaArray.svelte — an array property. Object items render as rows of
   * SchemaFields (device lists); scalar items render as a simple value list
   * (e.g. discovery.addresses). Item keys the schema does not describe are
   * preserved and shown read-only.
   */
  let {
    schema,
    key,
    required,
    requiredReason = null,
    parent,
    onChanged,
    i2cImport = null,
  }: {
    schema: SchemaNode;
    key: string;
    required: boolean;
    /** Why `required` holds when a schema conditional made it so. */
    requiredReason?: string | null;
    parent: UnknownRecord;
    onChanged: () => void;
    i2cImport: (() => unknown[] | null) | null;
  } = $props();

  const label = $derived(labelFor(key, schema));
  const description = $derived(descriptionFor(schema));
  const items = $derived(itemsSchema(schema));
  const objectMode = $derived(items !== null && isObjectSchema(items));
  const value = $derived(Array.isArray(parent[key]) ? (parent[key] as unknown[]) : null);
  const violations = $derived(uniqueViolations(schema, value ?? []));
  const showImport = $derived(i2cImport !== null && items !== null && isI2cField(items));

  function inputTarget(event: Event): HTMLInputElement {
    return event.currentTarget as HTMLInputElement;
  }

  function ensureArray(): unknown[] {
    if (!Array.isArray(parent[key])) parent[key] = [];
    return parent[key] as unknown[];
  }

  function extraKeys(item: UnknownRecord): string[] {
    if (!items) return [];
    const known = new Set(properties(items).map(([k]) => k));
    return Object.keys(item).filter((k) => !known.has(k));
  }

  function nextItemId(list: unknown[], prefix: string): string {
    const nums = list
      .map((entry) => asObject(entry)?.id)
      .filter((id): id is string => typeof id === "string" && id.startsWith(prefix))
      .map((id) => parseInt(id.slice(prefix.length), 10))
      .filter((n) => !Number.isNaN(n));
    return `${prefix}${nums.length ? Math.max(...nums) + 1 : 0}`;
  }

  function addObjectItem(): void {
    if (!items) return;
    const list = ensureArray();
    const item = defaultsFor(items);
    const { required: itemRequired } = resolveConditionals(items, item);
    for (const [propKey, propSchema] of properties(items)) {
      if (item[propKey] !== undefined || !itemRequired.has(propKey)) continue;
      const options = enumOptions(propSchema);
      if (options && options.length > 0) item[propKey] = options[0].value;
    }
    if (properties(items).some(([k]) => k === "id") && item.id === undefined) {
      const prefix = typeof item.type === "string" && item.type !== "" ? item.type : "item";
      item.id = nextItemId(list, prefix);
    }
    list.push(item);
    onChanged();
  }

  function removeItem(index: number): void {
    const list = ensureArray();
    list.splice(index, 1);
    onChanged();
  }

  function addScalarItem(): void {
    ensureArray().push("");
    onChanged();
  }

  function commitScalarItem(index: number, raw: string): void {
    const list = ensureArray();
    const text = raw.trim();
    if (items && isI2cField(items) && !/^0[xX][0-9a-fA-F]+$/.test(text)) {
      const parsed = parseI2c(text);
      list[index] = parsed !== null ? parsed : text;
    } else {
      list[index] = text;
    }
    onChanged();
  }

  function importFromDevices(): void {
    if (!i2cImport) return;
    const imported = i2cImport();
    if (imported === null) return;
    parent[key] = [...imported];
    onChanged();
  }

  function scalarItemError(entry: unknown): string | null {
    return items ? validateField(items, entry, true) : null;
  }
</script>

<div class="schema-array form-group">
  <div class="schema-array-header">
    <span class="field-group-label">{label}</span>
    {#if showImport}
      <button type="button" class="btn-small" onclick={importFromDevices}
        >Import from devices</button
      >
    {/if}
    <button type="button" class="btn-small" onclick={objectMode ? addObjectItem : addScalarItem}>
      + Add
    </button>
  </div>
  {#if description}<span class="field-note">{description}</span>{/if}
  {#if required && (!value || value.length === 0)}
    <span class="field-error">Required — add at least one entry.</span>
    {#if requiredReason}<span class="field-note">{requiredReason}</span>{/if}
  {/if}

  {#if objectMode && items}
    {#each value ?? [] as entry, index (index)}
      {@const record = asObject(entry)}
      <div class="device-row schema-array-row">
        {#if record}
          {@const rowConditionals = resolveConditionals(items, record)}
          {#each authoredProperties(items) as [propKey, propSchema] (propKey)}
            {#if !rowConditionals.forbidden.has(propKey)}
              <SchemaField
                schema={propSchema}
                key={propKey}
                required={rowConditionals.required.has(propKey)}
                requiredReason={rowConditionals.reasons.get(propKey) ?? null}
                parent={record}
                {onChanged}
                i2cImport={null}
              />
            {/if}
          {/each}
          {#each extraKeys(record) as extra (extra)}
            <div class="form-group schema-array-extra">
              <label>{extra}</label>
              <code title="Not described by this provider schema revision; preserved as-is."
                >{JSON.stringify(record[extra])}</code
              >
            </div>
          {/each}
        {:else}
          <code>{JSON.stringify(entry)}</code>
        {/if}
        <button type="button" class="btn-remove-device" onclick={() => removeItem(index)}>✕</button>
      </div>
    {/each}
  {:else}
    {#each value ?? [] as entry, index (index)}
      <div class="schema-array-row schema-array-scalar-row">
        <input
          type="text"
          spellcheck="false"
          style="font-family:monospace"
          value={entry === undefined || entry === null ? "" : String(entry)}
          onchange={(e: Event) => commitScalarItem(index, inputTarget(e).value)}
        />
        {#if scalarItemError(entry)}
          <span class="field-error">{scalarItemError(entry)}</span>
        {/if}
        <button type="button" class="btn-remove-device" onclick={() => removeItem(index)}>✕</button>
      </div>
    {/each}
  {/if}

  {#each violations as violation, index (index)}
    <span class="field-error">{violation.message}</span>
  {/each}
</div>
