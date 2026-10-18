<script lang="ts">
  import type { UnknownRecord } from "../contracts";
  import SchemaField from "./SchemaField.svelte";
  import {
    asObject,
    authoredProperties,
    isArraySchema,
    isI2cField,
    itemsSchema,
    properties,
    pruneForbidden,
    resolveConditionals,
    type SchemaNode,
  } from "./schema";

  /**
   * SchemaForm.svelte — schema-driven form over one provider's native config
   * document. Renders the envelope's root object schema; every write prunes
   * keys the current discriminator state forbids before flagging dirty.
   */
  let {
    schema,
    value,
    onChanged,
    derivedFields = {},
  }: {
    schema: SchemaNode;
    value: UnknownRecord;
    onChanged: () => void;
    /** Dotted paths the caller owns, each with the note shown beside its read-only value. */
    derivedFields?: Record<string, string>;
  } = $props();

  const conditionals = $derived(resolveConditionals(schema, value));

  function handleChanged(): void {
    pruneForbidden(schema, value);
    onChanged();
  }

  // Generic "import from devices": offered to any i2c-typed array field when
  // this config also carries a devices array with an address column.
  function importAddresses(): unknown[] | null {
    for (const [key, prop] of properties(schema)) {
      if (!isArraySchema(prop)) continue;
      const items = itemsSchema(prop);
      const addressSchema = items ? asObject(asObject(items.properties)?.address) : null;
      if (!addressSchema || !isI2cField(addressSchema)) continue;
      const list = value[key];
      if (!Array.isArray(list)) return [];
      return list
        .map((entry) => asObject(entry)?.address)
        .filter((address) => address !== undefined && address !== "");
    }
    return null;
  }

  const hasDeviceAddresses = $derived(importAddresses() !== null);
</script>

<div class="schema-form">
  {#each authoredProperties(schema) as [key, node] (key)}
    {#if !conditionals.forbidden.has(key)}
      <SchemaField
        schema={node}
        {key}
        required={conditionals.required.has(key)}
        parent={value}
        onChanged={handleChanged}
        i2cImport={hasDeviceAddresses ? importAddresses : null}
        {derivedFields}
      />
    {/if}
  {/each}
</div>
