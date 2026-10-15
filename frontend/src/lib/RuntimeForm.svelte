<script lang="ts">
  import { activeVariant, inertnessViolation, MANUAL_VARIANT } from "./canonical";
  import type { ProjectDocument, RuntimeConfigDoc, RuntimeTelemetry } from "./contracts";

  /**
   * RuntimeForm.svelte — the runtime-config form.
   *
   * Since #255 this edits the runtime config DIRECTLY (one variant of
   * `config/anolis-runtime.<variant>.yaml`) rather than a shadow document that
   * was translated at deploy time. Field names are the runtime's own.
   */
  let {
    doc,
    variant,
    onChanged,
  }: {
    doc: ProjectDocument;
    variant?: string;
    onChanged: () => void;
  } = $props();

  const name = $derived(variant ?? activeVariant(doc));
  const rt = $derived((doc.variants?.[name] ?? {}) as RuntimeConfigDoc);
  const hostPaths = $derived(doc.host_paths ?? {});

  /**
   * install.sh refuses to install a `manual` variant that boots into
   * automation, so surface that here rather than at sudo time on the target.
   */
  const inertnessError = $derived(
    name === MANUAL_VARIANT ? inertnessViolation(doc.variants?.[name]) : null,
  );

  function inputTarget(event: Event): HTMLInputElement {
    return event.currentTarget as HTMLInputElement;
  }

  function selectTarget(event: Event): HTMLSelectElement {
    return event.currentTarget as HTMLSelectElement;
  }

  function textAreaTarget(event: Event): HTMLTextAreaElement {
    return event.currentTarget as HTMLTextAreaElement;
  }

  /** Set `doc.variants[name].<section>.<key>`, creating the section if needed. */
  function setIn(section: string, key: string, value: unknown): void {
    const config = (doc.variants[name] ??= {});
    const target = ((config as Record<string, unknown>)[section] ??= {}) as Record<string, unknown>;
    target[key] = value;
    onChanged();
  }

  function setInflux(key: string, value: unknown): void {
    const config = (doc.variants[name] ??= {});
    const telemetry = (config.telemetry ??= {} as RuntimeTelemetry);
    telemetry.influxdb ??= {};
    telemetry.influxdb[key] = value;
    onChanged();
  }

  function setNumber(section: string, key: string, raw: string): void {
    const n = Number(raw);
    if (!isNaN(n)) setIn(section, key, n);
  }

  function setCorsOrigins(raw: string): void {
    const origins = raw
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    if (origins.length > 0) {
      setIn("http", "cors_allowed_origins", origins);
      return;
    }
    // The runtime schema requires a non-empty list when http is enabled, so an
    // empty box has to mean "no CORS section" — writing [] makes the project
    // unsaveable with an error that names neither the rule nor the fix.
    const config = (doc.variants[name] ??= {});
    if (config.http) delete config.http.cors_allowed_origins;
    onChanged();
  }

  function setRuntimeExecutable(value: string): void {
    const paths = (doc.host_paths ??= {});
    paths.runtime_executable = value;
    onChanged();
  }
</script>

<section class="form-section">
  <h3>Runtime <span class="variant-badge">{name}</span></h3>

  {#if inertnessError}
    <div class="bus-note note-warning" data-testid="inertness-warning">
      The <code>{MANUAL_VARIANT}</code> variant must boot inert ({inertnessError}). install.sh
      refuses to install it otherwise — author automation into a separate variant.
    </div>
  {/if}

  <div class="form-group">
    <label>Runtime name</label>
    <input
      type="text"
      spellcheck="false"
      value={rt.runtime?.name ?? ""}
      oninput={(e: Event) => setIn("runtime", "name", inputTarget(e).value)}
    />
  </div>

  <div class="form-group">
    <label>HTTP port</label>
    <input
      type="number"
      min="1"
      max="65535"
      value={rt.http?.port ?? ""}
      onchange={(e: Event) => setNumber("http", "port", inputTarget(e).value)}
    />
  </div>

  <div class="form-group">
    <label>HTTP bind address</label>
    <input
      type="text"
      spellcheck="false"
      style="font-family:monospace"
      value={rt.http?.bind ?? ""}
      oninput={(e: Event) => setIn("http", "bind", inputTarget(e).value)}
    />
    <span class="field-note"
      >Keep <code>127.0.0.1</code> unless the rig must be reachable over the LAN — install.sh turns on
      authentication when it rewrites this for LAN exposure.</span
    >
  </div>

  <div class="form-group">
    <label>CORS origins (one per line)</label>
    <textarea
      rows="3"
      placeholder="https://workbench.example"
      value={(rt.http?.cors_allowed_origins ?? []).join("\n")}
      oninput={(e: Event) => setCorsOrigins(textAreaTarget(e).value)}></textarea>
  </div>

  <div class="form-group form-group-inline">
    <label>
      <input
        type="checkbox"
        checked={rt.http?.cors_allow_credentials ?? false}
        onchange={(e: Event) => setIn("http", "cors_allow_credentials", inputTarget(e).checked)}
      />
      CORS allow credentials
    </label>
  </div>

  <div class="form-group">
    <label>Shutdown timeout (ms)</label>
    <input
      type="number"
      min="500"
      max="30000"
      value={rt.runtime?.shutdown_timeout_ms ?? ""}
      onchange={(e: Event) => setNumber("runtime", "shutdown_timeout_ms", inputTarget(e).value)}
    />
  </div>

  <div class="form-group">
    <label>Startup timeout (ms)</label>
    <input
      type="number"
      min="5000"
      max="300000"
      value={rt.runtime?.startup_timeout_ms ?? ""}
      onchange={(e: Event) => setNumber("runtime", "startup_timeout_ms", inputTarget(e).value)}
    />
  </div>

  <div class="form-group">
    <label>Polling interval (ms)</label>
    <input
      type="number"
      min="100"
      max="10000"
      value={rt.polling?.interval_ms ?? ""}
      onchange={(e: Event) => setNumber("polling", "interval_ms", inputTarget(e).value)}
    />
  </div>

  <div class="form-group">
    <label>Log level</label>
    <select
      value={rt.logging?.level ?? "info"}
      onchange={(e: Event) => setIn("logging", "level", selectTarget(e).value)}
    >
      <option value="debug">debug</option>
      <option value="info">info</option>
      <option value="warn">warn</option>
      <option value="error">error</option>
    </select>
  </div>

  <div class="form-group form-group-inline">
    <label>
      <input
        type="checkbox"
        checked={rt.telemetry?.enabled ?? false}
        onchange={(e: Event) => setIn("telemetry", "enabled", inputTarget(e).checked)}
      />
      Telemetry enabled
    </label>
  </div>

  {#if rt.telemetry?.enabled}
    <div class="form-group">
      <label>InfluxDB URL</label>
      <input
        type="text"
        spellcheck="false"
        style="font-family:monospace"
        placeholder="https://influxdb.example:8086"
        value={rt.telemetry?.influxdb?.url ?? ""}
        oninput={(e: Event) => setInflux("url", inputTarget(e).value.trim())}
      />
    </div>
    <div class="form-group">
      <label>InfluxDB org</label>
      <input
        type="text"
        spellcheck="false"
        value={rt.telemetry?.influxdb?.org ?? ""}
        oninput={(e: Event) => setInflux("org", inputTarget(e).value.trim())}
      />
    </div>
    <div class="form-group">
      <label>InfluxDB bucket</label>
      <input
        type="text"
        spellcheck="false"
        value={rt.telemetry?.influxdb?.bucket ?? ""}
        oninput={(e: Event) => setInflux("bucket", inputTarget(e).value.trim())}
      />
    </div>
    <div class="form-group">
      <label>Influx batch size</label>
      <input
        type="number"
        min="1"
        max="100000"
        value={rt.telemetry?.influxdb?.batch_size ?? ""}
        onchange={(e: Event) => {
          const n = Number(inputTarget(e).value);
          if (!isNaN(n)) setInflux("batch_size", n);
        }}
      />
    </div>
    <div class="form-group">
      <label>Influx flush interval (ms)</label>
      <input
        type="number"
        min="1"
        max="600000"
        value={rt.telemetry?.influxdb?.flush_interval_ms ?? ""}
        onchange={(e: Event) => {
          const n = Number(inputTarget(e).value);
          if (!isNaN(n)) setInflux("flush_interval_ms", n);
        }}
      />
    </div>
  {/if}

  <div class="form-group">
    <label>Runtime executable path</label>
    <input
      type="text"
      spellcheck="false"
      style="font-family:monospace"
      value={hostPaths.runtime_executable ?? ""}
      oninput={(e: Event) => setRuntimeExecutable(inputTarget(e).value)}
    />
    <span class="field-note"
      >Host path, used for dev-launch only. It is never deployed — install.sh installs the pinned
      runtime under its own prefix.</span
    >
  </div>
</section>

<style>
  .variant-badge {
    font-size: 0.75rem;
    font-weight: 500;
    padding: 0.1rem 0.4rem;
    border-radius: 3px;
    background: rgba(127, 127, 127, 0.18);
    vertical-align: middle;
  }
</style>
