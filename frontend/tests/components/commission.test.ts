import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import Commission from '../../src/routes/Commission.svelte';
import {
  createRuntimeStatus,
  createProjectDocument,
  deferred,
  jsonResponse,
  pathFromInput,
  setPageUrl,
} from './helpers';

describe('Commission.svelte', () => {
  it('shows preflight result summary and details', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === '/api/projects/demo/preflight') {
        return jsonResponse(200, {
          ok: false,
          checks: [
            {
              name: 'Runtime executable',
              ok: false,
              error: 'Runtime binary not found',
              hint: 'Set runtime path in Compose',
            },
          ],
          summary: { passed: 0, skipped: 0, failed: 1 },
        });
      }
      return jsonResponse(200, {});
    });
    vi.stubGlobal('fetch', fetchMock);

    render(Commission, {
      props: {
        projectName: 'demo',
        system: createProjectDocument('demo'),
        runtimeStatus: createRuntimeStatus(),
        commissionRunningForCurrent: false,
      },
    });

    await fireEvent.click(screen.getByRole('button', { name: 'Preflight Check' }));

    expect(await screen.findByText('0 passed · 0 skipped · 1 failed')).toBeInTheDocument();
    expect(screen.getByText('Runtime executable')).toBeInTheDocument();
    expect(screen.getByText('Runtime binary not found')).toBeInTheDocument();
    expect(screen.getByText('Set runtime path in Compose')).toBeInTheDocument();
  });

  it('counts a skipped preflight check as skipped, never as passed', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === '/api/projects/demo/preflight') {
        return jsonResponse(200, {
          ok: true,
          // A binary that exists but predates --check-config: the shape
          // launcher._check_config_binary returns as "Not yet available".
          checks: [
            { name: 'Provider sim0 binary exists', ok: true, error: null, hint: null },
            { name: 'Provider sim0 --check-config', ok: null, note: 'Not yet available' },
          ],
          summary: { passed: 1, skipped: 1, failed: 0 },
        });
      }
      return jsonResponse(200, {});
    });
    vi.stubGlobal('fetch', fetchMock);

    render(Commission, {
      props: {
        projectName: 'demo',
        system: createProjectDocument('demo'),
        runtimeStatus: createRuntimeStatus(),
        commissionRunningForCurrent: false,
      },
    });

    await fireEvent.click(screen.getByRole('button', { name: 'Preflight Check' }));

    expect(await screen.findByText('1 passed · 1 skipped · 0 failed')).toBeInTheDocument();
    expect(screen.queryByText(/All checks passed/)).not.toBeInTheDocument();
    expect(screen.getByText('Not yet available')).toBeInTheDocument();
    // Nothing failed, so Launch stays available.
    expect(screen.getByRole('button', { name: /Launch/ })).toBeEnabled();
  });

  it('lists every validation error when several share a check name', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === '/api/projects/demo/preflight') {
        return jsonResponse(200, {
          ok: false,
          // launcher.preflight emits one same-named check per validation error.
          checks: [
            { name: 'System-level validation', ok: false, error: 'first error', hint: null },
            { name: 'System-level validation', ok: false, error: 'second error', hint: null },
          ],
          summary: { passed: 0, skipped: 0, failed: 2 },
        });
      }
      return jsonResponse(200, {});
    });
    vi.stubGlobal('fetch', fetchMock);

    render(Commission, {
      props: {
        projectName: 'demo',
        system: createProjectDocument('demo'),
        runtimeStatus: createRuntimeStatus(),
        commissionRunningForCurrent: false,
      },
    });

    await fireEvent.click(screen.getByRole('button', { name: 'Preflight Check' }));

    expect(await screen.findByText('0 passed · 0 skipped · 2 failed')).toBeInTheDocument();
    expect(screen.getByText('first error')).toBeInTheDocument();
    expect(screen.getByText('second error')).toBeInTheDocument();
    expect(screen.getAllByText('System-level validation')).toHaveLength(2);
  });

  it('builds the bundle for the architecture the user chose', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { job_id: 'job-1' }));
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal(
      'EventSource',
      class {
        onmessage: ((ev: MessageEvent) => void) | null = null;
        close(): void {}
      },
    );

    render(Commission, {
      props: {
        projectName: 'demo',
        system: createProjectDocument('demo'),
        runtimeStatus: createRuntimeStatus(),
        commissionRunningForCurrent: false,
      },
    });

    const arch = screen.getByLabelText('Target architecture') as HTMLSelectElement;
    expect(arch.value).toBe('arm64');
    expect(screen.getByRole('option', { name: 'Raspberry Pi (arm64)' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'x86_64' })).toBeInTheDocument();

    await fireEvent.change(arch, { target: { value: 'x86_64' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Export Bundle' }));

    await waitFor(() => {
      const call = (fetchMock.mock.calls as unknown as Array<[RequestInfo | URL, RequestInit]>).find(
        ([input]) => pathFromInput(input) === '/api/provision/bundle',
      );
      expect(call).toBeDefined();
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ project: 'demo', arch: 'x86_64' });
    });
  });

  it('keeps Launch disabled until a preflight has run without failures', async () => {
    let preflightOk = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === '/api/projects/demo/preflight') {
        return preflightOk
          ? jsonResponse(200, {
              ok: true,
              // The binary exists but predates --check-config.
              checks: [
                { name: 'Runtime binary exists', ok: true, error: null, hint: null },
                { name: 'Runtime --check-config', ok: null, note: 'Not yet available' },
              ],
              summary: { passed: 1, skipped: 1, failed: 0 },
            })
          : jsonResponse(200, {
              ok: false,
              checks: [{ name: 'System-level validation', ok: false, error: 'bad', hint: null }],
              summary: { passed: 0, skipped: 0, failed: 1 },
            });
      }
      return jsonResponse(200, {});
    });
    vi.stubGlobal('fetch', fetchMock);

    render(Commission, {
      props: {
        projectName: 'demo',
        system: createProjectDocument('demo'),
        runtimeStatus: createRuntimeStatus(),
        commissionRunningForCurrent: false,
      },
    });

    // Never checked: no path to a running runtime yet.
    expect(screen.getByRole('button', { name: /Launch/ })).toBeDisabled();
    expect(screen.getByText('Run a preflight check to enable Launch.')).toBeInTheDocument();

    await fireEvent.click(screen.getByRole('button', { name: 'Preflight Check' }));
    await screen.findByText('System-level validation');
    expect(screen.getByRole('button', { name: /Launch/ })).toBeDisabled();

    // A preflight whose only non-pass is a skip ("Not yet available",
    // "Config not yet rendered") must not grey Launch out. A missing binary
    // is not a skip: its exists check fails.
    preflightOk = true;
    await fireEvent.click(screen.getByRole('button', { name: 'Preflight Check' }));
    await screen.findByText('Runtime binary exists');
    expect(screen.getByRole('button', { name: /Launch/ })).toBeEnabled();
  });

  it('needs a fresh preflight after a launch', async () => {
    // A runtime that stops on its own drops Commission back to the idle bar;
    // the preflight that preceded the last launch must not enable the next.
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === '/api/status') return jsonResponse(200, { running: false });
      if (path === '/api/projects/demo/preflight')
        return jsonResponse(200, {
          ok: true,
          checks: [{ name: 'Runtime binary exists', ok: true, error: null, hint: null }],
          summary: { passed: 1, skipped: 0, failed: 0 },
        });
      return jsonResponse(200, { ok: true });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(Commission, {
      props: {
        projectName: 'demo',
        system: createProjectDocument('demo'),
        runtimeStatus: createRuntimeStatus(),
        commissionRunningForCurrent: false,
      },
    });

    await fireEvent.click(screen.getByRole('button', { name: 'Preflight Check' }));
    await screen.findByText('Runtime binary exists');
    await fireEvent.click(screen.getByRole('button', { name: /Launch/ }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([input]) => pathFromInput(input) === '/api/projects/demo/launch')).toBe(
        true,
      );
    });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Launch →' })).toBeDisabled();
    });
    expect(screen.queryByText('Runtime binary exists')).not.toBeInTheDocument();
  });

  it('shows launch in-flight state while launch request is pending', async () => {
    const launchResponse = deferred<Response>();
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === '/api/status') return Promise.resolve(jsonResponse(200, { running: false }));
      if (path === '/api/projects/demo/preflight')
        return Promise.resolve(
          jsonResponse(200, {
            ok: true,
            checks: [{ name: 'Runtime binary exists', ok: true, error: null, hint: null }],
            summary: { passed: 1, skipped: 0, failed: 0 },
          }),
        );
      if (path === '/api/projects/demo/launch') return launchResponse.promise;
      if (path === '/v0/runtime/status')
        return Promise.resolve(
          jsonResponse(200, {
            status: { code: 'OK' },
            mode: 'ACTIVE',
            uptime_seconds: 1,
            polling_interval_ms: 1000,
            device_count: 0,
            providers: [],
          }),
        );
      if (path === '/v0/providers/health') return Promise.resolve(jsonResponse(200, { providers: [] }));
      return Promise.resolve(jsonResponse(200, {}));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(Commission, {
      props: {
        projectName: 'demo',
        system: createProjectDocument('demo'),
        runtimeStatus: createRuntimeStatus(),
        commissionRunningForCurrent: false,
      },
    });

    await fireEvent.click(screen.getByRole('button', { name: 'Preflight Check' }));
    await screen.findByText('Runtime binary exists');
    await fireEvent.click(screen.getByRole('button', { name: /Launch/ }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Launching/ })).toBeDisabled();
    });

    launchResponse.resolve(jsonResponse(200, { ok: true }));

    // In-flight state ends; Launch itself then waits for a fresh preflight.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Launch →' })).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: /Launching/ })).not.toBeInTheDocument();
  });

  it('shows stop in-flight state while stop request is pending', async () => {
    const stopResponse = deferred<Response>();
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === '/api/status')
        return Promise.resolve(jsonResponse(200, { running: true, active_project: 'demo' }));
      if (path === '/v0/runtime/status')
        return Promise.resolve(
          jsonResponse(200, {
            status: { code: 'OK' },
            mode: 'ACTIVE',
            uptime_seconds: 4,
            polling_interval_ms: 1000,
            device_count: 0,
            providers: [],
          }),
        );
      if (path === '/v0/providers/health') return Promise.resolve(jsonResponse(200, { providers: [] }));
      if (path === '/api/projects/demo/stop') return stopResponse.promise;
      return Promise.resolve(jsonResponse(200, {}));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(Commission, {
      props: {
        projectName: 'demo',
        system: createProjectDocument('demo'),
        runtimeStatus: createRuntimeStatus({ running: true, active_project: 'demo' }),
        commissionRunningForCurrent: true,
      },
    });

    await fireEvent.click(screen.getByRole('button', { name: 'Stop' }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Stopping/ })).toBeDisabled();
    });

    stopResponse.resolve(jsonResponse(200, { ok: true }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Stop' })).toBeEnabled();
    });
  });
});

describe('Commission.svelte — Deploy to device (#275)', () => {
  class DrivenEventSource {
    static instances: DrivenEventSource[] = [];
    url: string;
    closed = false;
    onmessage: ((event: MessageEvent<string>) => void) | null = null;
    onerror: ((event: Event) => void) | null = null;
    constructor(url: string) {
      this.url = url;
      DrivenEventSource.instances.push(this);
    }
    emit(data: unknown): void {
      this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(data) }));
    }
    close(): void {
      this.closed = true;
    }
  }

  function setup() {
    DrivenEventSource.instances = [];
    vi.stubGlobal('EventSource', DrivenEventSource as unknown as typeof EventSource);
    const posts: { path: string; body: Record<string, unknown> }[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = pathFromInput(input);
        if (path.startsWith('/api/provision/')) {
          posts.push({ path, body: JSON.parse(String(init?.body ?? '{}')) });
          return jsonResponse(202, { job_id: 'job-1' });
        }
        return jsonResponse(200, {});
      }),
    );
    const doc = createProjectDocument('demo');
    doc.profile.runtime_profiles.automation = 'config/anolis-runtime.automation.yaml';
    doc.variants.automation = {
      ...doc.variants.manual,
      automation: { enabled: true, behavior_tree: 'behaviors/main.xml' },
    };
    render(Commission, {
      props: {
        projectName: 'demo',
        system: doc,
        runtimeStatus: createRuntimeStatus(),
        commissionRunningForCurrent: false,
      },
    });
    return { posts };
  }

  it('deploys the selected project to a remote device with the chosen variant', async () => {
    const { posts } = setup();

    const variant = screen.getByLabelText('Runtime variant') as HTMLSelectElement;
    expect(variant.value).toBe('manual');
    expect(screen.getByRole('button', { name: 'Deploy' })).toBeDisabled();

    await fireEvent.input(screen.getByLabelText('Target (user@host)'), {
      target: { value: 'pi@rig' },
    });
    await fireEvent.change(variant, { target: { value: 'automation' } });
    await fireEvent.click(screen.getByLabelText('Also install telemetry-export'));
    await fireEvent.click(screen.getByLabelText('Also install observability (InfluxDB + Grafana)'));
    await fireEvent.click(screen.getByRole('button', { name: 'Deploy' }));

    await waitFor(() => expect(DrivenEventSource.instances.length).toBe(1));
    expect(posts).toEqual([
      {
        path: '/api/provision/remote',
        body: {
          project: 'demo',
          variant: 'automation',
          target: 'pi@rig',
          with_telemetry_export: true,
          with_observability: true,
        },
      },
    ]);
    const source = DrivenEventSource.instances[0];
    expect(source.url).toBe('/api/provision/status/job-1');

    source.emit({
      stage: 'existing-config',
      detail: '/opt/anolis/config/runtime.yaml exists on the target and is kept',
    });
    expect(await screen.findByText(/runtime\.yaml exists on the target and is kept/)).toBeInTheDocument();

    source.emit({
      stage: 'done',
      summary: {
        runtime_version: '0.1.43',
        host_preflight: [],
        reboot_pending: false,
        preserved: ["⚠ config (runtime): --variant 'automation' NOT applied; /opt/anolis/config/runtime.yaml is preserved."],
        runtime_config_kept: true,
      },
    });
    expect(await screen.findByText('Deployed')).toBeInTheDocument();
    expect(screen.getByText(/existing runtime config was kept; the runtime runs it/)).toBeInTheDocument();
    expect(screen.getByText(/--variant 'automation' NOT applied/)).toBeInTheDocument();
    expect(source.closed).toBe(true);
  });

  it('deploys to this machine through the local install job', async () => {
    const { posts } = setup();

    await fireEvent.click(screen.getByLabelText('This machine'));
    expect(screen.queryByLabelText('Target (user@host)')).not.toBeInTheDocument();
    await fireEvent.click(screen.getByRole('button', { name: 'Deploy' }));

    await waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0]).toEqual({
      path: '/api/provision/install',
      body: { project: 'demo', variant: 'manual' },
    });

    DrivenEventSource.instances[0].emit({
      stage: 'error',
      detail: 'install.sh failed (exit 1):\n✗ verify: checksum mismatch for anolis-runtime',
      host_preflight: [],
      reboot_pending: false,
    });
    expect(await screen.findByText('Deploy failed')).toBeInTheDocument();
    expect(screen.getByText(/checksum mismatch/)).toBeInTheDocument();
  });

  it('warns after a deploy whose install.sh could not be checked against SHA256SUMS', async () => {
    setup();
    await fireEvent.click(screen.getByLabelText('This machine'));
    await fireEvent.click(screen.getByRole('button', { name: 'Deploy' }));
    await waitFor(() => expect(DrivenEventSource.instances.length).toBe(1));

    const warning = 'WARNING: install.sh not verified: anolis v0.1.49 predates install.sh in SHA256SUMS';
    DrivenEventSource.instances[0].emit({
      stage: 'done',
      summary: { runtime_version: '0.1.49', install_sh_warning: warning },
    });
    expect(await screen.findByText('Deployed')).toBeInTheDocument();
    expect(screen.getByText(warning)).toHaveClass('deploy-warning');
  });

  it('does not call a freshly written runtime config kept after a variant switch', async () => {
    setup();
    await fireEvent.click(screen.getByLabelText('This machine'));
    await fireEvent.click(screen.getByRole('button', { name: 'Deploy' }));
    await waitFor(() => expect(DrivenEventSource.instances.length).toBe(1));

    // install.sh after "remove runtime.yaml and re-run": runtime.yaml was
    // written fresh, the provider configs were kept.
    DrivenEventSource.instances[0].emit({
      stage: 'done',
      summary: {
        runtime_version: '0.1.43',
        preserved: ['→ config (providers): 2 preserved, 0 written'],
        runtime_config_kept: false,
      },
    });
    expect(await screen.findByText('Deployed')).toBeInTheDocument();
    expect(screen.queryByText(/runtime config was kept/)).not.toBeInTheDocument();
    expect(screen.getByText(/left these existing configs in place/)).toBeInTheDocument();
    expect(screen.getByText(/2 preserved, 0 written/)).toBeInTheDocument();
  });

  it('says how to reach Grafana after an observability install', async () => {
    setup();
    await fireEvent.input(screen.getByLabelText('Target (user@host)'), {
      target: { value: 'pi@rig' },
    });
    await fireEvent.click(screen.getByRole('button', { name: 'Deploy' }));
    await waitFor(() => expect(DrivenEventSource.instances.length).toBe(1));

    DrivenEventSource.instances[0].emit({
      stage: 'done',
      summary: {
        runtime_version: '0.1.45',
        grafana_access: [
          'Grafana listens on 127.0.0.1:3000 on the target. Open a tunnel, then browse http://localhost:3000:',
          '  ssh -L 3000:127.0.0.1:3000 pi@rig',
          'admin password: GRAFANA_ADMIN_PASSWORD in /etc/anolis/observability.env on the target (root only)',
        ],
      },
    });
    expect(await screen.findByText('Deployed')).toBeInTheDocument();
    const access = screen.getByText(/ssh -L 3000:127\.0\.0\.1:3000 pi@rig/);
    expect(access.textContent).toContain('/etc/anolis/observability.env on the target (root only)');
  });

  it('shows nothing about Grafana when the deploy reports no access', async () => {
    setup();
    await fireEvent.input(screen.getByLabelText('Target (user@host)'), {
      target: { value: 'pi@rig' },
    });
    await fireEvent.click(screen.getByRole('button', { name: 'Deploy' }));
    await waitFor(() => expect(DrivenEventSource.instances.length).toBe(1));
    DrivenEventSource.instances[0].emit({
      stage: 'done',
      summary: { runtime_version: '0.1.45', grafana_access: [] },
    });
    expect(await screen.findByText('Deployed')).toBeInTheDocument();
    expect(document.getElementById('deploy-result')?.textContent).not.toContain('Grafana');
  });

  it('warns when install.sh reports an observability problem', async () => {
    setup();
    await fireEvent.input(screen.getByLabelText('Target (user@host)'), {
      target: { value: 'pi@rig' },
    });
    await fireEvent.click(screen.getByLabelText('Also install observability (InfluxDB + Grafana)'));
    await fireEvent.click(screen.getByRole('button', { name: 'Deploy' }));
    await waitFor(() => expect(DrivenEventSource.instances.length).toBe(1));

    // tests/fixtures/install-sh/offline.txt, through deploy.observability_lines
    DrivenEventSource.instances[0].emit({
      stage: 'done',
      summary: {
        runtime_version: '0.1.45',
        observability: [
          '⚠ observability: vendor apt repos unreachable (offline?) — skipped',
          '    The runtime install is unaffected; re-run with --with-observability once online.',
          '  Grafana bind unchanged: the observability step did not finish (see the warnings above)',
          '  InfluxDB:  http://127.0.0.1:8086 (org anolis, bucket anolis, retention 30d)',
        ],
        grafana_access: [],
      },
    });
    expect(await screen.findByText('Deployed')).toBeInTheDocument();
    expect(screen.getByText(/Observability needs attention/)).toBeInTheDocument();
    const warning = screen.getByText(/vendor apt repos unreachable/);
    expect(warning).toHaveClass('deploy-warning');
    expect(screen.getByText(/Grafana bind unchanged/)).not.toHaveClass('deploy-warning');
  });

  it("shows a refusal's recovery lines, the checksum result and the log (#649)", async () => {
    setup();
    await fireEvent.input(screen.getByLabelText('Target (user@host)'), {
      target: { value: 'pi@rig' },
    });
    await fireEvent.click(screen.getByLabelText('Also install observability (InfluxDB + Grafana)'));
    await fireEvent.click(screen.getByRole('button', { name: 'Deploy' }));
    await waitFor(() => expect(DrivenEventSource.instances.length).toBe(1));

    // tests/fixtures/install-sh/credentials-gone.txt, through deploy's filters
    DrivenEventSource.instances[0].emit({
      stage: 'done',
      summary: {
        runtime_version: '0.1.55',
        verify: ['✓ verify: all checksums pass'],
        log_path: '/home/op/.anolis/logs/install-rig-20261027T020000Z.log',
        observability: [
          '→ observability: influxdb already set up — bucket/tokens left alone',
          '✗ observability: influxdb is already set up, but its credentials are gone (no tokens in /etc/anolis/observability.env) — Grafana not provisioned',
          '  An uninstall by an earlier install.sh deleted them and kept the instance. To recover,',
          '    sudo -H influx auth create --org anolis --write-bucket "${id}" --description \'anolis-runtime telemetry write\'',
        ],
        grafana_access: [],
      },
    });
    expect(await screen.findByText('Deployed')).toBeInTheDocument();
    expect(screen.getByText(/Observability needs attention/)).toBeInTheDocument();
    expect(screen.getByText(/credentials are gone/)).toHaveClass('deploy-warning');
    expect(screen.getByText(/--write-bucket/)).toBeInTheDocument();
    expect(screen.getByText('✓ verify: all checksums pass')).toBeInTheDocument();
    expect(document.getElementById('deploy-result')?.textContent).toContain(
      '/home/op/.anolis/logs/install-rig-20261027T020000Z.log',
    );
  });

  it('shows install.sh observability lines without a warning when all went well', async () => {
    setup();
    await fireEvent.input(screen.getByLabelText('Target (user@host)'), {
      target: { value: 'pi@rig' },
    });
    await fireEvent.click(screen.getByRole('button', { name: 'Deploy' }));
    await waitFor(() => expect(DrivenEventSource.instances.length).toBe(1));
    DrivenEventSource.instances[0].emit({
      stage: 'done',
      summary: {
        runtime_version: '0.1.45',
        observability: ['✓ observability: influxdb (:8086) and grafana (:3000) up'],
      },
    });
    expect(await screen.findByText('Deployed')).toBeInTheDocument();
    expect(screen.getByText(/grafana \(:3000\) up/)).toBeInTheDocument();
    expect(screen.queryByText(/Observability needs attention/)).not.toBeInTheDocument();
  });

  it('warns when install.sh reports a provider that is not running', async () => {
    setup();
    await fireEvent.input(screen.getByLabelText('Target (user@host)'), {
      target: { value: 'pi@rig' },
    });
    await fireEvent.click(screen.getByRole('button', { name: 'Deploy' }));
    await waitFor(() => expect(DrivenEventSource.instances.length).toBe(1));

    // tests/fixtures/install-sh/health-one-down.txt, through deploy.health_lines
    DrivenEventSource.instances[0].emit({
      stage: 'done',
      summary: {
        runtime_version: '0.1.45',
        health: [
          '✓ health: runtime responding (v0.1.45)',
          '✓ health: run registry available',
          '⚠ health: providers: 1 of 2 running',
          '⚠ health: provider io1: UNAVAILABLE',
          '    sudo journalctl -u anolis-runtime --no-pager -n 50',
        ],
      },
    });
    expect(await screen.findByText('Deployed')).toBeInTheDocument();
    expect(screen.getByText(/Health needs attention/)).toBeInTheDocument();
    expect(screen.getByText('⚠ health: provider io1: UNAVAILABLE')).toHaveClass('deploy-warning');
    expect(screen.getByText('⚠ health: providers: 1 of 2 running')).toHaveClass('deploy-warning');
    expect(screen.getByText('✓ health: run registry available')).not.toHaveClass('deploy-warning');
  });

  it('shows install.sh health lines without a warning when every provider runs', async () => {
    setup();
    await fireEvent.input(screen.getByLabelText('Target (user@host)'), {
      target: { value: 'pi@rig' },
    });
    await fireEvent.click(screen.getByRole('button', { name: 'Deploy' }));
    await waitFor(() => expect(DrivenEventSource.instances.length).toBe(1));
    DrivenEventSource.instances[0].emit({
      stage: 'done',
      summary: {
        runtime_version: '0.1.45',
        health: ['✓ health: runtime responding (v0.1.45)', '✓ health: providers: 2 of 2 running'],
      },
    });
    expect(await screen.findByText('Deployed')).toBeInTheDocument();
    expect(screen.getByText("install.sh's health check:")).toBeInTheDocument();
    expect(screen.getByText('✓ health: providers: 2 of 2 running')).toBeInTheDocument();
    expect(screen.queryByText(/Health needs attention/)).not.toBeInTheDocument();
  });

  it('says where Grafana listens without promising a tunnel on every runtime', () => {
    setup();
    const note = screen.getByText(/Installed on the target beside the runtime/);
    expect(note.textContent).toContain('127.0.0.1 only');
    expect(note.textContent).toContain('network address');
  });

  it('marks a variant that does not boot inert, and says unsaved edits are not deployed', () => {
    setup();
    const deployVariant = screen.getByRole('combobox', { name: 'Runtime variant' });
    expect(within(deployVariant).getByRole('option', { name: 'manual' })).toBeInTheDocument();
    expect(
      within(deployVariant).getByRole('option', { name: 'automation (fresh install refuses this)' }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Deploys the saved project; unsaved edits are not included/)).toBeInTheDocument();
  });

  it('sends a sudo password only with a local deploy, then clears it (#230)', async () => {
    const { posts } = setup();

    expect(screen.queryByLabelText(/Sudo password/)).not.toBeInTheDocument();
    await fireEvent.click(screen.getByLabelText('This machine'));
    const field = screen.getByLabelText(/Sudo password/) as HTMLInputElement;
    expect(field.type).toBe('password');
    await fireEvent.input(field, { target: { value: 'pw-7f3c' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Deploy' }));

    await waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0]).toEqual({
      path: '/api/provision/install',
      body: { project: 'demo', variant: 'manual', sudo_password: 'pw-7f3c' },
    });
    expect(field.value).toBe('');
  });

  it('never sends a password typed for this machine to a remote deploy (#230)', async () => {
    const { posts } = setup();

    await fireEvent.click(screen.getByLabelText('This machine'));
    await fireEvent.input(screen.getByLabelText(/Sudo password/), { target: { value: 'pw-7f3c' } });
    await fireEvent.click(screen.getByLabelText('A remote device over SSH'));
    await fireEvent.input(screen.getByLabelText('Target (user@host)'), {
      target: { value: 'pi@rig' },
    });
    await fireEvent.click(screen.getByRole('button', { name: 'Deploy' }));

    await waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0]).toEqual({
      path: '/api/provision/remote',
      body: { project: 'demo', variant: 'manual', target: 'pi@rig' },
    });
  });

  describe('the sudo password field is offered only on this machine (#230)', () => {
    afterEach(() => setPageUrl('http://localhost:3000/'));

    it.each(['127.0.0.1', 'localhost', '[::1]'])('offers it on %s', async (host) => {
      setPageUrl(`http://${host}:8000/`);
      setup();
      await fireEvent.click(screen.getByLabelText('This machine'));
      expect(screen.getByLabelText(/Sudo password/)).toBeInTheDocument();
    });

    it('does not offer it to a browser on another host', async () => {
      setPageUrl('http://192.168.1.20:8000/');
      setup();
      await fireEvent.click(screen.getByLabelText('This machine'));
      expect(screen.queryByLabelText(/Sudo password/)).not.toBeInTheDocument();
      expect(screen.getByText(/sudo password can be entered only/)).toBeInTheDocument();
    });
  });

  it('says before deploying that an existing runtime config is kept', () => {
    setup();
    expect(
      screen.getByText(/already has a runtime config, install\.sh keeps that config/),
    ).toBeInTheDocument();
  });
});

describe('Commission.svelte — the target check (#567)', () => {
  type Reply = { status: number; body: unknown };

  function setup(reply: Reply) {
    const posts: { path: string; body: Record<string, unknown> }[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = pathFromInput(input);
        if (path === '/api/provision/check-target') {
          posts.push({ path, body: JSON.parse(String(init?.body ?? '{}')) });
          return jsonResponse(reply.status, reply.body);
        }
        return jsonResponse(200, {});
      }),
    );
    render(Commission, {
      props: {
        projectName: 'demo',
        system: createProjectDocument('demo'),
        runtimeStatus: createRuntimeStatus(),
        commissionRunningForCurrent: false,
      },
    });
    return { posts };
  }

  const refused = {
    runtime: {
      name: 'runtime',
      pinned: '0.1.43',
      installed: '0.1.51',
      verdict: 'refuse',
      message:
        'The target runs runtime 0.1.51, newer than this project\'s pin 0.1.43. install.sh 0.1.43 predates the downgrade guard (runtime 0.1.45), so it would install the older runtime without asking. Pin runtime 0.1.51 or newer to deploy.',
    },
    providers: [
      { name: 'bread', pinned: '0.5.0', installed: '0.8.1', verdict: 'older', message: '' },
      { name: 'ezo', pinned: '0.4.0', installed: '0.4.0', verdict: 'ok', message: '' },
    ],
    manifest: 'ok',
  };

  async function check(target = 'pi@rig') {
    await fireEvent.input(screen.getByLabelText('Target (user@host)'), { target: { value: target } });
    await fireEvent.click(screen.getByRole('button', { name: 'Check target' }));
  }

  it('shows the pins against the target and the refusal deploy would give', async () => {
    const { posts } = setup({ status: 200, body: refused });
    expect(screen.getByRole('button', { name: 'Check target' })).toBeDisabled();

    await check();

    const table = await screen.findByRole('table', { name: 'Pinned by this project, and installed on pi@rig' });
    const rows = within(table)
      .getAllByRole('row')
      .slice(1)
      .map((row) => within(row).getAllByRole('cell').map((cell) => cell.textContent));
    expect(rows).toEqual([
      ['runtime', '0.1.43', '0.1.51', 'older than installed: deploy refuses it'],
      ['bread', '0.5.0', '0.8.1', 'older than installed'],
      ['ezo', '0.4.0', '0.4.0', ''],
    ]);
    expect(within(table).getAllByRole('row')[1]).toHaveClass('target-version-warning');
    expect(within(table).getAllByRole('row')[3]).not.toHaveClass('target-version-warning');
    expect(screen.getByText(/predates the downgrade guard/)).toHaveClass('field-error');
    expect(posts).toEqual([{ path: '/api/provision/check-target', body: { project: 'demo', target: 'pi@rig' } }]);
  });

  it('shows a guarded downgrade as a warning', async () => {
    setup({
      status: 200,
      body: {
        ...refused,
        runtime: {
          ...refused.runtime,
          pinned: '0.1.46',
          verdict: 'downgrade',
          message: 'The target runs runtime 0.1.51, newer than this project\'s pin 0.1.46; install.sh 0.1.46 refuses to downgrade it.',
        },
      },
    });
    await check();
    expect(await screen.findByText('older than installed: install.sh refuses it')).toBeInTheDocument();
    expect(screen.getByText(/install\.sh 0\.1\.46 refuses to downgrade it/)).toHaveClass('deploy-warning');
  });

  it('says when the provider versions come from a stale manifest', async () => {
    setup({ status: 200, body: { ...refused, manifest: 'stale' } });
    await check();
    expect(await screen.findByText(/manifest\.json was written for another runtime version/)).toBeInTheDocument();
  });

  it('shows a fresh target without a manifest note', async () => {
    setup({
      status: 200,
      body: {
        runtime: { name: 'runtime', pinned: '0.1.51', installed: null, verdict: 'fresh', message: '' },
        providers: [{ name: 'bread', pinned: '0.8.1', installed: null, verdict: 'fresh', message: '' }],
        manifest: 'missing',
      },
    });
    await check();
    const table = await screen.findByRole('table');
    expect(within(table).getAllByText('not installed')).toHaveLength(2);
    expect(screen.queryByText(/manifest\.json/)).toBeNull();
  });

  it('lists the optional components the project pins, after the providers (#620)', async () => {
    setup({
      status: 200,
      body: {
        ...refused,
        optional: [
          { name: 'telemetry_export', pinned: '0.1.1', installed: null, verdict: 'unknown', message: '' },
        ],
      },
    });
    await check();

    const table = await screen.findByRole('table');
    const last = within(table).getAllByRole('row').at(-1) as HTMLElement;
    expect(within(last).getAllByRole('cell').map((cell) => cell.textContent)).toEqual([
      'telemetry_export (optional)',
      '0.1.1',
      '—',
      'not recorded on the target',
    ]);
  });

  it('reports a target it could not read', async () => {
    setup({ status: 422, body: { error: '/opt/anolis/bin/anolis-runtime on the target reports no version' } });
    await check();
    expect(
      await screen.findByText('Could not check the target: /opt/anolis/bin/anolis-runtime on the target reports no version'),
    ).toHaveClass('field-error');
    expect(screen.queryByRole('table')).toBeNull();
  });

  it('checks this machine without a target', async () => {
    const { posts } = setup({ status: 200, body: refused });
    await fireEvent.click(screen.getByLabelText('This machine'));
    await fireEvent.click(screen.getByRole('button', { name: 'Check target' }));
    expect(await screen.findByRole('table', { name: 'Pinned by this project, and installed on this machine' })).toBeInTheDocument();
    expect(posts[0].body).toEqual({ project: 'demo' });
  });
});
