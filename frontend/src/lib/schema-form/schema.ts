/**
 * schema.ts — pure JSON-Schema interpreter for provider --config-schema envelopes (#270).
 *
 * Understands the subset of Draft 2020-12 the anolis provider SDK emits
 * (see anolis-provider-sdk docs/config-schema.md): object/array/scalar types,
 * required, const, default, oneOf-of-consts enums, allOf/if(const)/then
 * conditionals with `properties: {k: false}` forbids, dependentRequired,
 * pattern/min-max bounds, and the x-anolis-{unique,placeholder,type}
 * vocabulary. Backend validation is authoritative; this module only powers
 * form rendering and inline hints.
 */

import type { UnknownRecord } from "../contracts";

export type SchemaNode = UnknownRecord;

export interface EnumOption {
  value: string;
  title: string;
}

export type FieldControl =
  | { control: "readonly"; value: unknown }
  | { control: "select"; options: EnumOption[] }
  | { control: "i2c" }
  | { control: "number"; integer: boolean; min?: number; max?: number }
  | { control: "checkbox" }
  | { control: "text"; pattern?: string; minLength?: number };

export interface Conditionals {
  required: Set<string>;
  forbidden: Set<string>;
  /** Why a conditionally required key is required, in the schema's own titles. */
  reasons: Map<string, string>;
}

/** Narrow an unknown to a plain object (the shape guard used across the form). */
export function asObject(value: unknown): UnknownRecord | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as UnknownRecord)
    : null;
}

const asRecord = asObject;

export function isObjectSchema(schema: SchemaNode): boolean {
  return (
    schema.type === "object" || (schema.type === undefined && asRecord(schema.properties) !== null)
  );
}

export function isArraySchema(schema: SchemaNode): boolean {
  return schema.type === "array";
}

export function properties(schema: SchemaNode): Array<[string, SchemaNode]> {
  const props = asRecord(schema.properties);
  if (!props) return [];
  return Object.entries(props).flatMap(([key, node]) => {
    const record = asRecord(node);
    return record ? [[key, record] as [string, SchemaNode]] : [];
  });
}

/**
 * Properties the user authors. A const property has exactly one legal value,
 * which defaultsFor already seeds, so it is never rendered as a control.
 */
export function authoredProperties(schema: SchemaNode): Array<[string, SchemaNode]> {
  return properties(schema).filter(([, node]) => node.const === undefined);
}

export function itemsSchema(schema: SchemaNode): SchemaNode | null {
  return asRecord(schema.items);
}

/** Human label for a property: schema title, else the key itself. */
export function labelFor(key: string, schema: SchemaNode): string {
  return typeof schema.title === "string" && schema.title !== "" ? schema.title : key;
}

export function descriptionFor(schema: SchemaNode): string | null {
  return typeof schema.description === "string" && schema.description !== ""
    ? schema.description
    : null;
}

export function placeholderFor(schema: SchemaNode): string | null {
  const raw = schema["x-anolis-placeholder"];
  return typeof raw === "string" && raw !== "" ? raw : null;
}

export function isI2cField(schema: SchemaNode): boolean {
  return schema["x-anolis-type"] === "i2c_address";
}

/** oneOf where every branch is {const, title?} — the SDK's titled enum idiom. */
export function enumOptions(schema: SchemaNode): EnumOption[] | null {
  const oneOf = schema.oneOf;
  if (!Array.isArray(oneOf) || oneOf.length === 0) return null;
  const options: EnumOption[] = [];
  for (const branch of oneOf) {
    const record = asRecord(branch);
    if (!record || typeof record.const !== "string") return null;
    options.push({
      value: record.const,
      title: typeof record.title === "string" && record.title !== "" ? record.title : record.const,
    });
  }
  return options;
}

function propertyLabel(schema: SchemaNode, key: string): string {
  return labelFor(key, asRecord(asRecord(schema.properties)?.[key]) ?? {});
}

/** "Discovery mode is 'Manual address list'": a property's title and its value's enum title. */
function describeValue(schema: SchemaNode, key: string, value: unknown): string {
  const prop = asRecord(asRecord(schema.properties)?.[key]) ?? {};
  const option = enumOptions(prop)?.find((o) => o.value === value);
  const shown = option ? option.title : typeof value === "string" ? value : JSON.stringify(value);
  return `${propertyLabel(schema, key)} is '${shown}'`;
}

/**
 * Effective required/forbidden property sets for an object schema given its
 * current value: static `required`, `dependentRequired`, plus every matching
 * allOf/if(const+required)/then branch (`then.required` adds requirements,
 * `then.properties: {k: false}` forbids keys). Each conditionally required
 * key carries a reason naming the condition that made it required.
 */
export function resolveConditionals(schema: SchemaNode, value: unknown): Conditionals {
  const required = new Set<string>();
  const forbidden = new Set<string>();
  const reasons = new Map<string, string>();
  const record = asRecord(value) ?? {};

  if (Array.isArray(schema.required)) {
    for (const key of schema.required) if (typeof key === "string") required.add(key);
  }
  const requireFor = (key: string, reason: string): void => {
    required.add(key);
    reasons.set(key, reason);
  };

  const dependent = asRecord(schema.dependentRequired);
  if (dependent) {
    for (const [trigger, needs] of Object.entries(dependent)) {
      if (record[trigger] === undefined || !Array.isArray(needs)) continue;
      const reason = `Required because ${propertyLabel(schema, trigger)} is set`;
      for (const key of needs) if (typeof key === "string") requireFor(key, reason);
    }
  }

  const allOf = Array.isArray(schema.allOf) ? schema.allOf : [];
  for (const entry of allOf) {
    const branch = asRecord(entry);
    const condition = branch ? asRecord(branch.if) : null;
    const consequence = branch ? asRecord(branch.then) : null;
    if (!condition || !consequence) continue;
    if (!ifMatches(condition, record)) continue;

    if (Array.isArray(consequence.required)) {
      const when = Object.keys(asRecord(condition.properties) ?? {})
        .map((key) => describeValue(schema, key, record[key]))
        .join(" and ");
      const reason = `Required because ${when}`;
      for (const key of consequence.required) {
        if (typeof key === "string") requireFor(key, reason);
      }
    }
    const thenProps = asRecord(consequence.properties);
    if (thenProps) {
      for (const [key, sub] of Object.entries(thenProps)) {
        if (sub === false) forbidden.add(key);
      }
    }
  }

  for (const key of forbidden) required.delete(key);
  return { required, forbidden, reasons };
}

function ifMatches(condition: UnknownRecord, record: UnknownRecord): boolean {
  const condProps = asRecord(condition.properties);
  if (!condProps) return false;
  for (const [key, sub] of Object.entries(condProps)) {
    const subRecord = asRecord(sub);
    if (!subRecord || subRecord.const === undefined) return false;
    if (record[key] !== subRecord.const) return false;
  }
  if (Array.isArray(condition.required)) {
    for (const key of condition.required) {
      if (typeof key === "string" && record[key] === undefined) return false;
    }
  }
  return true;
}

/**
 * Seed value for an object schema: declared defaults and consts, required
 * sub-objects (recursively), and empty arrays for object-item arrays.
 * Never invents values — a field without a default stays absent
 * (x-anolis-placeholder is a hint, not a value).
 */
export function defaultsFor(schema: SchemaNode): UnknownRecord {
  const out: UnknownRecord = {};
  const requiredKeys = new Set<string>(
    Array.isArray(schema.required)
      ? schema.required.filter((k): k is string => typeof k === "string")
      : [],
  );
  for (const [key, prop] of properties(schema)) {
    if (prop.default !== undefined) {
      out[key] = structuredClone(prop.default);
    } else if (prop.const !== undefined) {
      out[key] = structuredClone(prop.const);
    } else if (isObjectSchema(prop)) {
      const sub = defaultsFor(prop);
      if (requiredKeys.has(key) || Object.keys(sub).length > 0) out[key] = sub;
    } else if (isArraySchema(prop)) {
      const items = itemsSchema(prop);
      if (items && isObjectSchema(items)) out[key] = [];
    }
  }
  return out;
}

/** Pick the concrete input control for a scalar property schema. */
export function fieldControl(schema: SchemaNode): FieldControl {
  if (schema.const !== undefined) return { control: "readonly", value: schema.const };
  const options = enumOptions(schema);
  if (options) return { control: "select", options };
  if (isI2cField(schema)) return { control: "i2c" };
  if (schema.type === "boolean") return { control: "checkbox" };
  if (schema.type === "integer" || schema.type === "number") {
    return {
      control: "number",
      integer: schema.type === "integer",
      min: typeof schema.minimum === "number" ? schema.minimum : undefined,
      max: typeof schema.maximum === "number" ? schema.maximum : undefined,
    };
  }
  return {
    control: "text",
    pattern: typeof schema.pattern === "string" ? schema.pattern : undefined,
    minLength: typeof schema.minLength === "number" ? schema.minLength : undefined,
  };
}

/** Parse an I2C address in int-or-hex form ("0x0A", "10", 10). */
export function parseI2c(value: unknown): number | null {
  if (typeof value === "number" && Number.isInteger(value)) return value;
  if (typeof value !== "string") return null;
  const text = value.trim();
  if (text === "") return null;
  const parsed = /^0[xX][0-9a-fA-F]+$/.test(text) ? parseInt(text, 16) : Number(text);
  return Number.isInteger(parsed) ? parsed : null;
}

const I2C_MIN = 0x08;
const I2C_MAX = 0x77;

/**
 * Inline validation hint for one scalar field. Deliberately shallow —
 * required/enum/pattern/bounds only; the backend envelope pass is
 * authoritative and reports everything else on save.
 */
export function validateField(
  schema: SchemaNode,
  value: unknown,
  required: boolean,
): string | null {
  const empty = value === undefined || value === null || value === "";
  if (empty) return required ? "Required" : null;

  const control = fieldControl(schema);
  switch (control.control) {
    case "select":
      return control.options.some((o) => o.value === value)
        ? null
        : "Not one of the allowed values";
    case "i2c": {
      const parsed = parseI2c(value);
      if (parsed === null) return "Enter an address like 0x0A (or decimal)";
      if (parsed < I2C_MIN || parsed > I2C_MAX)
        return `Address must be between 0x${I2C_MIN.toString(16).padStart(2, "0").toUpperCase()} and 0x${I2C_MAX.toString(16).toUpperCase()}`;
      return null;
    }
    case "number": {
      const n = typeof value === "number" ? value : Number(value);
      if (Number.isNaN(n)) return "Must be a number";
      if (control.integer && !Number.isInteger(n)) return "Must be an integer";
      if (control.min !== undefined && n < control.min) return `Must be >= ${control.min}`;
      if (control.max !== undefined && n > control.max) return `Must be <= ${control.max}`;
      return null;
    }
    case "text": {
      if (typeof value !== "string") return "Must be text";
      if (control.minLength !== undefined && value.length < control.minLength) return "Required";
      if (control.pattern !== undefined && !new RegExp(control.pattern).test(value)) {
        return "Does not match the required format";
      }
      return null;
    }
    default:
      return null;
  }
}

export interface UniqueViolation {
  path: string;
  message: string;
}

function normalizeUniqueValue(value: unknown, fieldSchema: SchemaNode | null): unknown {
  if (fieldSchema && isI2cField(fieldSchema)) {
    const parsed = parseI2c(value);
    if (parsed !== null) return parsed;
  }
  return value;
}

/**
 * Each duplicate in `normalized`, as first entered in `entered` (the same
 * list before normalization), so a message reads "0x13" rather than 19.
 */
function duplicatesAsEntered(entered: unknown[], normalized: unknown[]): unknown[] {
  return duplicates(normalized).map((dup) => entered[normalized.indexOf(dup)]);
}

/**
 * `existing` plus each entry of `incoming` whose value it does not hold yet,
 * compared the way the uniqueness check compares them (10 vs "0x0A"). The
 * entries already in `existing` keep the notation they were entered in.
 */
export function mergeUnique(
  existing: unknown[],
  incoming: unknown[],
  fieldSchema: SchemaNode | null,
): unknown[] {
  const merged = [...existing];
  const seen = new Set(existing.map((v) => normalizeUniqueValue(v, fieldSchema)));
  for (const value of incoming) {
    const normalized = normalizeUniqueValue(value, fieldSchema);
    if (seen.has(normalized)) continue;
    seen.add(normalized);
    merged.push(value);
  }
  return merged;
}

function duplicates(values: unknown[]): unknown[] {
  const seen = new Set<unknown>();
  const dups: unknown[] = [];
  for (const value of values) {
    if (seen.has(value)) {
      if (!dups.includes(value)) dups.push(value);
    } else {
      seen.add(value);
    }
  }
  return dups;
}

/**
 * x-anolis-unique violations for a schema+value pair (mirror of the backend
 * walker): an array schema annotated directly enforces distinct scalar items;
 * an items property annotated enforces distinct values of that property
 * across items. i2c-typed fields compare value-normalized (10 vs "0x0A").
 */
export function uniqueViolations(
  schema: SchemaNode,
  value: unknown,
  path = "$",
): UniqueViolation[] {
  const violations: UniqueViolation[] = [];

  const record = asRecord(value);
  if (record) {
    for (const [key, prop] of properties(schema)) {
      if (record[key] !== undefined) {
        violations.push(...uniqueViolations(prop, record[key], `${path}.${key}`));
      }
    }
    return violations;
  }

  if (Array.isArray(value)) {
    const items = itemsSchema(schema);
    if (schema["x-anolis-unique"] === true) {
      const normalized = value.map((v) => normalizeUniqueValue(v, items));
      for (const dup of duplicatesAsEntered(value, normalized)) {
        violations.push({
          path,
          message: `Duplicate value ${JSON.stringify(dup)} — values must be unique`,
        });
      }
    }
    if (items) {
      for (const [key, prop] of properties(items)) {
        if (prop["x-anolis-unique"] !== true) continue;
        const present = value.map((item) => asRecord(item)?.[key]).filter((v) => v !== undefined);
        const normalized = present.map((v) => normalizeUniqueValue(v, prop));
        for (const dup of duplicatesAsEntered(present, normalized)) {
          violations.push({
            path,
            message: `Duplicate ${key} ${JSON.stringify(dup)} — ${key} must be unique`,
          });
        }
      }
      value.forEach((item, index) => {
        violations.push(...uniqueViolations(items, item, `${path}[${index}]`));
      });
    }
  }

  return violations;
}

/**
 * Delete keys the current discriminator state forbids (recursively), so a
 * mode switch cannot leave stale keys the provider would reject. Returns
 * whether anything changed.
 *
 * With a `stash`, a pruned value is set aside and put back once its key is
 * allowed again and still unset, so switching a mode away and back does not
 * lose what was entered (#563). Nothing set aside is ever written while its
 * key is forbidden.
 */
export function pruneForbidden(
  schema: SchemaNode,
  value: unknown,
  stash?: WeakMap<object, UnknownRecord>,
): boolean {
  const record = asRecord(value);
  if (!record) return false;

  let changed = false;
  const { forbidden } = resolveConditionals(schema, record);
  // One stash object per record, so a switch that forbids several keys keeps
  // them all. Only keys set aside by an earlier call are restored below.
  let aside = stash?.get(record);
  if (stash && !aside) {
    aside = {};
    stash.set(record, aside);
  }
  const setAsideEarlier = aside ? Object.keys(aside) : [];
  for (const key of forbidden) {
    if (record[key] !== undefined) {
      if (aside) aside[key] = record[key];
      delete record[key];
      changed = true;
    }
  }
  if (aside) {
    for (const key of setAsideEarlier) {
      if (forbidden.has(key)) continue;
      if (record[key] === undefined) {
        record[key] = aside[key];
        changed = true;
      }
      delete aside[key];
    }
  }
  for (const [key, prop] of properties(schema)) {
    const child = record[key];
    if (child === undefined) continue;
    if (isObjectSchema(prop)) {
      if (pruneForbidden(prop, child, stash)) changed = true;
    } else if (isArraySchema(prop) && Array.isArray(child)) {
      const items = itemsSchema(prop);
      if (items && isObjectSchema(items)) {
        for (const item of child) {
          if (pruneForbidden(items, item, stash)) changed = true;
        }
      }
    }
  }
  return changed;
}
