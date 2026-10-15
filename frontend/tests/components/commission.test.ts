import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import Commission from '../../src/routes/Commission.svelte';
import {
  createRuntimeStatus,
  createProjectDocument,
  deferred,
  jsonResponse,
  pathFromInput,
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

  it('marks a variant that does not boot inert, and says unsaved edits are not deployed', () => {
    setup();
    expect(screen.getByRole('option', { name: 'manual' })).toBeInTheDocument();
    expect(
      screen.getByRole('option', { name: 'automation (fresh install refuses this)' }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Deploys the saved project; unsaved edits are not included/)).toBeInTheDocument();
  });

  it('says before deploying that an existing runtime config is kept', () => {
    setup();
    expect(
      screen.getByText(/already has a runtime config, install\.sh keeps that config/),
    ).toBeInTheDocument();
  });
});
