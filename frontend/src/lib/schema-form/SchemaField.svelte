<script lang="ts">
  import { getContext } from "svelte";
  import type { UnknownRecord } from "../contracts";
  import SchemaArray from "./SchemaArray.svelte";
  import SchemaFieldSelf from "./SchemaField.svelte";
  import {
    asObject,
    authoredProperties,
    descriptionFor,
    fieldControl,
    isArraySchema,
    isObjectSchema,
    labelFor,
    parseI2c,
    placeholderFor,
    resolveConditionals,
    SAVE_ATTEMPT_CONTEXT,
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
    requiredReason = null,
    parent,
    onChanged,
    i2cImport = null,
    derivedFields = {},
  }: {
    schema: SchemaNode;
    key: string;
    required: boolean;
    /** Why `required` holds when a schema conditional made it so. */
    requiredReason?: string | null;
    parent: UnknownRecord;
    onChanged: () => void;
    i2cImport: (() => unknown[] | null) | null;
    /** Dotted paths, relative to `parent`, rendered read-only with their note. */
    derivedFields?: Record<string, string>;
  } = $props();

  const label = $derived(labelFor(key, schema));
  const description = $derived(descriptionFor(schema));
  const isObject = $derived(isObjectSchema(schema));
  const isArray = $derived(isArraySchema(schema));
  const control = $derived(fieldControl(schema));
  const value = $derived(parent[key]);
  const derivedNote = $derived(derivedFields[key] ?? null);
  const childDerived = $derived(
    Object.fromEntries(
      Object.entries(derivedFields)
        .filter(([path]) => path.startsWith(`${key}.`))
        .map(([path, note]) => [path.slice(key.length + 1), note]),
    ),
  );
  const childProperties = $derived(isObject ? authoredProperties(schema) : []);
  const error = $derived(!isObject && !isArray ? validateField(schema, value, required) : null);
  const reason = $derived(error === "Required" ? requiredReason : null);

  // An empty required field is not reported until the user has left it or a
  // save has failed: a line that appears on load and vanishes on the first
  // commit moves the fields below it under the cursor (#578). The label's *
  // and aria-required say it is required meanwhile.
  const saveAttempt = getContext<{ failed: boolean } | undefined>(SAVE_ATTEMPT_CONTEXT);
  let touched = $state(false);
  const shownError = $derived(
    error === "Required" && !touched && !saveAttempt?.failed ? null : error,
  );

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
  <SchemaArray {schema} {key} {required} {requiredReason} {parent} {onChanged} {i2cImport} />
{:else if isObject && childProperties.length > 0}
  <fieldset class="schema-object">
    <legend>{label}</legend>
    {#if description}<span class="field-note">{description}</span>{/if}
    {#if childRecord && childConditionals}
      {#each childProperties as [childKey, childSchema] (childKey)}
        {#if !childConditionals.forbidden.has(childKey)}
          <SchemaFieldSelf
            schema={childSchema}
            key={childKey}
            required={childConditionals.required.has(childKey)}
            requiredReason={childConditionals.reasons.get(childKey) ?? null}
            parent={childRecord}
            onChanged={childChanged}
            {i2cImport}
            derivedFields={childDerived}
          />
        {/if}
      {/each}
    {/if}
  </fieldset>
{:else if isObject}
  <!-- Nothing to author: every declared property is const. -->
{:else if derivedNote !== null}
  <div class="form-group">
    <label>{label}</label>
    <input
      type="text"
      value={value === undefined || value === null ? "" : String(value)}
      readonly
    />
    <span class="field-note">{derivedNote}</span>
  </div>
{:else if control.control === "select"}
  <div class="form-group">
    <label
      >{label}{#if required}<span class="required-mark" title="Required">*</span>{/if}</label
    >
    <select
      aria-required={required}
      aria-invalid={shownError ? "true" : undefined}
      class:field-invalid={shownError !== null}
      onblur={() => (touched = true)}
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
    {#if shownError}<span class="field-error">{shownError}</span>{/if}
    {#if reason}<span class="field-note">{reason}</span>{/if}
  </div>
{:else if control.control === "checkbox"}
  <div class="form-group form-group-inline">
    <label>
      <input
        type="checkbox"
        checked={value === true || (value === undefined && schema.default === true)}
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
      aria-required={required}
      aria-invalid={shownError ? "true" : undefined}
      class:field-invalid={shownError !== null}
      onblur={() => (touched = true)}
      type="number"
      min={control.min}
      max={control.max}
      step={control.integer ? 1 : "any"}
      placeholder={placeholderFor(schema) ?? undefined}
      value={typeof value === "number" ? value : ""}
      onchange={(e: Event) => commitNumber(inputTarget(e).value)}
    />
    {#if description}<span class="field-note">{description}</span>{/if}
    {#if shownError}<span class="field-error">{shownError}</span>{/if}
    {#if reason}<span class="field-note">{reason}</span>{/if}
  </div>
{:else if control.control === "i2c"}
  <div class="form-group">
    <label
      >{label}{#if required}<span class="required-mark" title="Required">*</span>{/if}</label
    >
    <input
      aria-required={required}
      aria-invalid={shownError ? "true" : undefined}
      class:field-invalid={shownError !== null}
      onblur={() => (touched = true)}
      type="text"
      spellcheck="false"
      style="font-family:monospace"
      class="device-addr-input"
      placeholder={placeholderFor(schema) ?? "e.g. 0x0A"}
      value={value === undefined || value === null ? "" : String(value)}
      onchange={(e: Event) => commitI2c(inputTarget(e).value)}
    />
    {#if description}<span class="field-note">{description}</span>{/if}
    {#if shownError}<span class="field-error">{shownError}</span>{/if}
    {#if reason}<span class="field-note">{reason}</span>{/if}
  </div>
{:else}
  <div class="form-group">
    <label
      >{label}{#if required}<span class="required-mark" title="Required">*</span>{/if}</label
    >
    <input
      aria-required={required}
      aria-invalid={shownError ? "true" : undefined}
      class:field-invalid={shownError !== null}
      onblur={() => (touched = true)}
      type="text"
      spellcheck="false"
      placeholder={placeholderFor(schema) ?? undefined}
      value={typeof value === "string" ? value : ""}
      oninput={(e: Event) => commit(inputTarget(e).value || undefined)}
    />
    {#if description}<span class="field-note">{description}</span>{/if}
    {#if shownError}<span class="field-error">{shownError}</span>{/if}
    {#if reason}<span class="field-note">{reason}</span>{/if}
  </div>
{/if}
