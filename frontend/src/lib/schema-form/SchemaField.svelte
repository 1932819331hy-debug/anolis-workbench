<script lang="ts">
  import type { UnknownRecord } from "../contracts";
  import SchemaArray from "./SchemaArray.svelte";
  import SchemaFieldSelf from "./SchemaField.svelte";
  import {
    asObject,
    descriptionFor,
    fieldControl,
    isArraySchema,
    isObjectSchema,
    labelFor,
    parseI2c,
    placeholderFor,
    properties,
    resolveConditionals,
    validateField,
    type SchemaNode,
  } from "./schema";

  /**
   * SchemaField.svelte — one property of a provider config, rendered from its
   * schema node: scalar controls, nested object sections (recursive), and
   * arrays (delegated to SchemaArray). Mutates `parent[key]` in place.
   */
  let {
    schema,
    key,
    required,
    parent,
    onChanged,
    i2cImport = null,
  }: {
    schema: SchemaNode;
    key: string;
    required: boolean;
    parent: UnknownRecord;
    onChanged: () => void;
    i2cImport: (() => unknown[] | null) | null;
  } = $props();

  const label = $derived(labelFor(key, schema));
  const description = $derived(descriptionFor(schema));
  const isObject = $derived(isObjectSchema(schema));
  const isArray = $derived(isArraySchema(schema));
  const control = $derived(fieldControl(schema));
  const value = $derived(parent[key]);
  const error = $derived(!isObject && !isArray ? validateField(schema, value, required) : null);

  // Nested object sections whose container doesn't exist yet render into a
  // local draft; the draft is committed to the document on the first child
  // write. Rendering must never mutate the document — materializing an empty
  // required-members object (e.g. sim's optional provider{name}) would turn a
  // valid config invalid without the user touching anything.
  const draft: UnknownRecord = $state({});
  const childRecord = $derived(isObject ? (asObject(parent[key]) ?? draft) : null);
  const childConditionals = $derived(childRecord ? resolveConditionals(schema, childRecord) : null);

  function childChanged(): void {
    if (isObject && asObject(parent[key]) === null && Object.keys(draft).length > 0) {
      parent[key] = draft;
    }
    onChanged();
  }

  function inputTarget(event: Event): HTMLInputElement {
    return event.currentTarget as HTMLInputElement;
  }
  function selectTarget(event: Event): HTMLSelectElement {
    return event.currentTarget as HTMLSelectElement;
  }

  function commit(next: unknown | undefined): void {
    if (next === undefined) {
      delete parent[key];
    } else {
      parent[key] = next;
    }
    onChanged();
  }

  function commitNumber(raw: string): void {
    if (raw.trim() === "") {
      commit(undefined);
      return;
    }
    const n = Number(raw);
    if (!Number.isNaN(n)) commit(n);
  }

  function commitI2c(raw: string): void {
    const text = raw.trim();
    if (text === "") {
      commit(undefined);
      return;
    }
    // Hex form stays the authored string; a bare decimal is stored as an
    // integer so it satisfies the schema's integer branch.
    if (/^0[xX][0-9a-fA-F]+$/.test(text)) {
      commit(text);
      return;
    }
    const parsed = parseI2c(text);
    commit(parsed !== null ? parsed : text);
  }
</script>

{#if isArray}
  <SchemaArray {schema} {key} {required} {parent} {onChanged} {i2cImport} />
{:else if isObject}
  <fieldset class="schema-object">
    <legend>{label}</legend>
    {#if description}<span class="field-note">{description}</span>{/if}
    {#if childRecord && childConditionals}
      {#each properties(schema) as [childKey, childSchema] (childKey)}
        {#if !childConditionals.forbidden.has(childKey)}
          <SchemaFieldSelf
            schema={childSchema}
            key={childKey}
            required={childConditionals.required.has(childKey)}
            parent={childRecord}
            onChanged={childChanged}
            {i2cImport}
          />
        {/if}
      {/each}
    {/if}
  </fieldset>
{:else if control.control === "readonly"}
  <div class="form-group">
    <label>{label}</label>
    <input type="text" value={String(control.value)} disabled />
    {#if description}<span class="field-note">{description}</span>{/if}
  </div>
{:else if control.control === "select"}
  <div class="form-group">
    <label
      >{label}{#if required}<span class="required-mark" title="Required">*</span>{/if}</label
    >
    <select
      value={typeof value === "string" ? value : ""}
      onchange={(e: Event) => commit(selectTarget(e).value || undefined)}
    >
      {#if typeof value !== "string" || value === ""}
        <option value="">— select —</option>
      {/if}
      {#each control.options as option (option.value)}
        <option value={option.value}>{option.title}</option>
      {/each}
    </select>
    {#if description}<span class="field-note">{description}</span>{/if}
    {#if error}<span class="field-error">{error}</span>{/if}
  </div>
{:else if control.control === "checkbox"}
  <div class="form-group form-group-inline">
    <label>
      <input
        type="checkbox"
        checked={value === true}
        onchange={(e: Event) => commit(inputTarget(e).checked)}
      />
      {label}
    </label>
    {#if description}<span class="field-note">{description}</span>{/if}
  </div>
{:else if control.control === "number"}
  <div class="form-group">
    <label
      >{label}{#if required}<span class="required-mark" title="Required">*</span>{/if}</label
    >
    <input
      type="number"
      min={control.min}
      max={control.max}
      step={control.integer ? 1 : "any"}
      placeholder={placeholderFor(schema) ?? undefined}
      value={typeof value === "number" ? value : ""}
      onchange={(e: Event) => commitNumber(inputTarget(e).value)}
    />
    {#if description}<span class="field-note">{description}</span>{/if}
    {#if error}<span class="field-error">{error}</span>{/if}
  </div>
{:else if control.control === "i2c"}
  <div class="form-group">
    <label
      >{label}{#if required}<span class="required-mark" title="Required">*</span>{/if}</label
    >
    <input
      type="text"
      spellcheck="false"
      style="font-family:monospace"
      class="device-addr-input"
      placeholder={placeholderFor(schema) ?? "0x0A"}
      value={value === undefined || value === null ? "" : String(value)}
      onchange={(e: Event) => commitI2c(inputTarget(e).value)}
    />
    {#if description}<span class="field-note">{description}</span>{/if}
    {#if error}<span class="field-error">{error}</span>{/if}
  </div>
{:else}
  <div class="form-group">
    <label
      >{label}{#if required}<span class="required-mark" title="Required">*</span>{/if}</label
    >
    <input
      type="text"
      spellcheck="false"
      placeholder={placeholderFor(schema) ?? undefined}
      value={typeof value === "string" ? value : ""}
      oninput={(e: Event) => commit(inputTarget(e).value || undefined)}
    />
    {#if description}<span class="field-note">{description}</span>{/if}
    {#if error}<span class="field-error">{error}</span>{/if}
  </div>
{/if}
