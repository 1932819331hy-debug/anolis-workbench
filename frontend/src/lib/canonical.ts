/**
 * Canonical-project helpers, mirroring `anolis_workbench/core/canonical.py`.
 *
 * The one idea to keep in mind: inside a runtime config a provider `command`
 * is NOT a host path — it is a deploy TOKEN that install.sh rewrites to
 * `{prefix}/bin/anolis-provider-<kind>`. The real host binary path lives only
 * in the project's `host_paths` and is used for dev-launch. Authoring the
 * wrong shape here means install.sh silently ships dev-relative paths, so
 * these builders exist to make the token the only thing the UI can produce.
 */
import type { ProjectDocument, RuntimeConfigDoc, UnknownRecord } from "./contracts";

export const MANUAL_VARIANT = "manual";
export const AUTOMATION_VARIANT = "automation";

/** install.sh accepts any single path segment as the build preset. */
export const DEFAULT_BUILD_PRESET = "dev-release";

/** Mirrors install.sh's provider-command rewrite pattern. */
const PROVIDER_COMMAND_RE =
  /^\.\.\/anolis-provider-([a-z0-9-]+)\/build\/[^/]+\/anolis-provider-([a-z0-9-]+)$/;

export function providerCommandToken(kind: string, preset = DEFAULT_BUILD_PRESET): string {
  return `../anolis-provider-${kind}/build/${preset}/anolis-provider-${kind}`;
}

export function projectPathToken(machineId: string, relpath: string): string {
  return `../anolis-projects/projects/${machineId}/${relpath}`;
}

/**
 * install.sh takes the provider config's stem up to the FIRST dot as the
 * installed name, so the kind has to come first.
 */
export function providerConfigFilename(kind: string, providerId: string): string {
  return `provider-${kind}.${providerId}.yaml`;
}

export function providerConfigRelpath(kind: string, providerId: string): string {
  return `config/${providerConfigFilename(kind, providerId)}`;
}

export function variantRelpath(variant: string): string {
  return `config/anolis-runtime.${variant}.yaml`;
}

/**
 * The kind install.sh will resolve from a command token, or null.
 *
 * install.sh rewrites using the SECOND capture group and re-derives the kind
 * from the rendered path, so a token whose two kind captures disagree resolves
 * to something other than what the filename implies — treat that as unusable.
 */
export function commandKind(command: unknown): string | null {
  if (typeof command !== "string") return null;
  const match = PROVIDER_COMMAND_RE.exec(command);
  if (!match || match[1] !== match[2]) return null;
  return match[2];
}

/** Slugify a project name into a machine_id (mirrors machine_id_from_name). */
export function machineIdFromName(name: string): string {
  const cleaned = name
    .trim()
    .toLowerCase()
    .replace(/_/g, "-")
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64)
    .replace(/^-+|-+$/g, "");
  return cleaned || "machine";
}

/**
 * Which variant dev-launch runs: `launch.variant`, else `manual` (or, in a
 * project without a `manual` variant, its first one). Only the
 * Commission control writes `launch.variant`; Compose's variant selector picks
 * what is being edited and never changes it (#339). An unset value is read as
 * `manual`, not written, so opening an old project changes no saved file.
 */
export function activeVariant(doc: ProjectDocument | null): string {
  const requested = typeof doc?.launch?.variant === "string" ? doc.launch.variant : "";
  if (requested && doc?.variants?.[requested]) return requested;
  if (doc?.variants?.[MANUAL_VARIANT]) return MANUAL_VARIANT;
  const names = Object.keys(doc?.variants ?? {});
  return names[0] ?? MANUAL_VARIANT;
}

/**
 * "Editing automation; Launch runs manual" when the variant open in Compose is
 * not the one dev-launch runs, else null. An empty or unknown `editing` means
 * nothing was picked, so Compose is showing the launch variant itself.
 * `launch` defaults to `doc`'s own; pass the saved project's when it differs.
 */
export function launchVariantMismatch(
  doc: ProjectDocument | null,
  editing: string,
  launch: string = activeVariant(doc),
): string | null {
  if (!editing || !doc?.variants?.[editing]) return null;
  return editing === launch ? null : `Editing ${editing}; Launch runs ${launch}`;
}

/**
 * What dev-launch resolves against: the SAVED project's variants and the
 * variant Launch will run from it. The shell keeps one copy so Commission and
 * Operate agree, refreshed on load, on every Compose save and after the
 * Commission control writes launch.variant (#339).
 */
export interface SavedLaunch {
  variants: string[];
  launch: string;
  /** The HTTP port the launch variant serves on, or null. */
  port: number | null;
}

export function savedLaunch(doc: ProjectDocument | null): SavedLaunch {
  const launch = activeVariant(doc);
  return {
    variants: Object.keys(doc?.variants ?? {}),
    launch,
    port: doc?.variants?.[launch]?.http?.port ?? null,
  };
}

export function activeRuntimeConfig(doc: ProjectDocument | null): RuntimeConfigDoc | null {
  if (!doc) return null;
  return doc.variants?.[activeVariant(doc)] ?? null;
}

/**
 * Why this runtime config is not inert, or null.
 *
 * Mirrors install.sh's install-time gate, which refuses a non-inert `manual`
 * variant. That gate is an awk pass over the raw YAML, so it is blunter than a
 * structural reading: `automation:` present but empty dies, and the scan is not
 * depth-anchored (a hook or flag nested at any depth counts).
 *
 * ADVISORY ONLY — this runs on the parsed document, so it cannot see an
 * `enabled:` that appears as a wrapped line of a multi-line string. The
 * authoritative gate runs server-side over the serialized text.
 */
export function inertnessViolation(doc: RuntimeConfigDoc | null | undefined): string | null {
  if (!doc || !("automation" in doc)) return null;
  const automation = doc.automation;
  if (automation === null || automation === undefined) {
    return "automation is present but empty (install.sh requires a plain block mapping)";
  }
  if (typeof automation !== "object" || Array.isArray(automation)) {
    return "automation must be a mapping";
  }
  if (Object.keys(automation).length === 0) {
    return "automation is an empty mapping (install.sh requires a plain block mapping)";
  }
  return nestedAutomationViolation(automation, "automation");
}

/**
 * install.sh un-quotes and lowercases the field, then compares it against a
 * false-ish set — so `'false'` and `no` are inert to it while `0` is not.
 * Mirrored from the awk in tools/install.sh.
 */
const FALSEY_ENABLED = ["", "false", "no", "off", "n"];

function isFalseyEnabled(value: unknown): boolean {
  if (value === false) return true;
  if (typeof value !== "string") return false;
  return FALSEY_ENABLED.includes(value.replace(/["']/g, "").toLowerCase());
}

function nestedAutomationViolation(node: unknown, path: string): string | null {
  if (Array.isArray(node)) {
    for (let i = 0; i < node.length; i++) {
      const nested = nestedAutomationViolation(node[i], `${path}[${i}]`);
      if (nested) return nested;
    }
    return null;
  }
  if (typeof node !== "object" || node === null) return null;

  const record = node as UnknownRecord;
  if ("mode_transition_hooks" in record) return `${path}.mode_transition_hooks is present`;
  if ("enabled" in record && !isFalseyEnabled(record.enabled)) {
    return `${path}.enabled is ${JSON.stringify(record.enabled)}, which install.sh reads as enabled`;
  }
  for (const [key, value] of Object.entries(record)) {
    if (key === "enabled" || key === "mode_transition_hooks") continue;
    const nested = nestedAutomationViolation(value, `${path}.${key}`);
    if (nested) return nested;
  }
  return null;
}

/** Kinds the profile pins — install.sh fetches provider binaries by these keys. */
export function pinnedKinds(doc: ProjectDocument | null): string[] {
  const providers = doc?.profile?.components?.providers;
  return providers ? Object.keys(providers).sort() : [];
}
