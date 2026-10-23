import type { ProjectDocument, ProviderInstance, RuntimeStatus } from '../../src/lib/contracts';
import {
  MANUAL_VARIANT,
  providerCommandToken,
  providerConfigRelpath,
  projectPathToken,
  variantRelpath,
} from '../../src/lib/canonical';

/**
 * A canonical project document (#255) — the shape the API serves and accepts.
 *
 * Provider `command` values are deploy TOKENS, not host paths; the host binary
 * lives in `host_paths`. Getting that backwards is the failure this whole
 * change exists to prevent, so the fixtures build tokens through the same
 * helpers the composer uses.
 */
export function createProjectDocument(name = 'demo'): ProjectDocument {
  return {
    format: 'machine-profile',
    authored: true,
    meta: { name, created: '2026-01-01T00:00:00Z', template: 'sim-quickstart' },
    profile: {
      schema_version: 1,
      machine_id: name,
      display_name: name,
      runtime_profiles: { [MANUAL_VARIANT]: variantRelpath(MANUAL_VARIANT) },
      providers: {},
      compatibility: {
        runtime: { config_contract: '01-runtime-config', http_contract: '02-runtime-http' },
        providers: {},
      },
      components: {
        runtime: { repo: 'anolishq/anolis', version: '0.1.39' },
        providers: {},
      },
    },
    variants: {
      [MANUAL_VARIANT]: {
        runtime: { name, shutdown_timeout_ms: 5000, startup_timeout_ms: 30000 },
        http: {
          enabled: true,
          bind: '127.0.0.1',
          port: 8080,
          cors_allowed_origins: [],
          cors_allow_credentials: false,
        },
        providers: [],
        polling: { interval_ms: 1000 },
        telemetry: { enabled: false },
        logging: { level: 'info' },
        automation: { enabled: false },
      },
    },
    providers: {},
    host_paths: { runtime_executable: '', providers: {} },
    launch: { variant: MANUAL_VARIANT },
    warnings: [],
  };
}

/** Add a provider to a document the way the composer does: profile + variant + config. */
export function withProvider(
  doc: ProjectDocument,
  id: string,
  kind: string,
  config: ProviderInstance['config'] = {},
  options: { executable?: string; variant?: string } = {},
): ProjectDocument {
  const variant = options.variant ?? MANUAL_VARIANT;
  const machineId = doc.profile.machine_id;

  doc.profile.providers[id] = { config: providerConfigRelpath(kind, id) };
  (doc.profile.compatibility as Record<string, Record<string, unknown>>).providers[id] = {
    strategy: 'pinned-ref',
    version: '0.2.7',
  };
  doc.profile.components!.providers![kind] = {
    repo: `anolishq/anolis-provider-${kind}`,
    version: '0.2.7',
  };
  doc.providers[id] = { kind, config };
  doc.variants[variant].providers = [
    ...(doc.variants[variant].providers ?? []),
    {
      id,
      command: providerCommandToken(kind),
      args: ['--config', projectPathToken(machineId, providerConfigRelpath(kind, id))],
      timeout_ms: 5000,
      hello_timeout_ms: 3000,
      ready_timeout_ms: 10000,
      restart_policy: { enabled: false },
    },
  ];
  doc.host_paths!.providers![id] = { executable: options.executable ?? '' };
  return doc;
}

/** An imported project: same shape, carried verbatim, read-only. */
export function createImportedDocument(name = 'rig-a'): ProjectDocument {
  const doc = createProjectDocument(name);
  doc.authored = false;
  doc.meta = { name, imported_from: '/srv/machines/rig-a' };
  return doc;
}

export function createRuntimeStatus(overrides: Partial<RuntimeStatus> = {}): RuntimeStatus {
  return {
    running: false,
    active_project: null,
    ...overrides,
  };
}

export function jsonResponse(status: number, payload: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(payload),
    json: async () => payload,
    headers: new Headers(),
    blob: async () => new Blob([JSON.stringify(payload)]),
  } as unknown as Response;
}

export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

export function pathFromInput(input: RequestInfo | URL): string {
  const raw =
    typeof input === 'string'
      ? input
      : input instanceof URL
        ? input.toString()
        : input.url;
  return new URL(raw, 'http://localhost').pathname;
}

/** Serve the page under test from `url`, as a browser on that host would see it. */
export function setPageUrl(url: string): void {
  (window as unknown as { happyDOM: { setURL(next: string): void } }).happyDOM.setURL(url);
}
