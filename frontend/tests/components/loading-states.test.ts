import { render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import App from '../../src/App.svelte';
import Operate from '../../src/routes/Operate.svelte';
import { createProjectDocument, deferred, jsonResponse, pathFromInput } from './helpers';

describe('loading states are not empty states (#568)', () => {
  it("the header and Home say they are loading until status and projects answer", async () => {
    const projects = deferred<Response>();
    const status = deferred<Response>();
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        const path = pathFromInput(input);
        if (path === '/api/projects') return projects.promise;
        if (path === '/api/status') return status.promise;
        if (path === '/api/templates') return Promise.resolve(jsonResponse(200, []));
        if (path === '/api/provider-schemas') return Promise.resolve(jsonResponse(200, { providers: {} }));
        return Promise.resolve(jsonResponse(200, {}));
      }),
    );
    history.replaceState({}, '', '/');
    render(App);

    expect(await screen.findByText('Loading projects…')).toBeInTheDocument();
    expect(screen.queryByText('No projects yet.')).not.toBeInTheDocument();
    expect(document.querySelector('#runtime-indicator')).toHaveTextContent('Checking runtime…');
    expect(document.querySelector('#runtime-indicator')).not.toHaveClass('stopped');

    projects.resolve(jsonResponse(200, []));
    status.resolve(jsonResponse(200, { running: false, active_project: null }));

    expect(await screen.findByText('No projects yet.')).toBeInTheDocument();
    expect(document.querySelector('#runtime-indicator')).toHaveTextContent('Stopped');
  });

  it('Operate says devices and functions are loading until they answer', async () => {
    const RUNNING = { running: true, active_project: 'demo' };
    const devices = deferred<Response>();
    const caps = deferred<Response>();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const path = pathFromInput(input);
        if (path === '/api/status') return jsonResponse(200, RUNNING);
        if (path === '/v0/devices') return devices.promise;
        if (path === '/v0/devices/sim0/dev0/capabilities') return caps.promise;
        return jsonResponse(200, {});
      }),
    );
    render(Operate, {
      props: { projectName: 'demo', system: createProjectDocument('demo'), runtimeStatus: RUNNING },
    });

    expect(await screen.findByText('Loading devices…')).toBeInTheDocument();
    expect(screen.queryByText('No devices found.')).not.toBeInTheDocument();

    devices.resolve(jsonResponse(200, { devices: [{ provider_id: 'sim0', device_id: 'dev0' }] }));
    expect(await screen.findByText('Loading functions…')).toBeInTheDocument();
    expect(screen.queryByText('No callable functions declared.')).not.toBeInTheDocument();

    caps.resolve(jsonResponse(200, { capabilities: { signals: [], functions: [] } }));
    expect(await screen.findByText('No callable functions declared.')).toBeInTheDocument();
  });

  // #623: the two #598 left out.
  const RUNNING = { running: true, active_project: 'demo' };

  function renderHeld(held: Record<string, Promise<Response>>) {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const path = pathFromInput(input);
        if (path === '/api/status') return jsonResponse(200, RUNNING);
        if (path in held) return held[path];
        return jsonResponse(200, {});
      }),
    );
    render(Operate, {
      props: { projectName: 'demo', system: createProjectDocument('demo'), runtimeStatus: RUNNING },
    });
  }

  it('Operate says runtime status is loading until it answers, and unavailable once it fails', async () => {
    const status = deferred<Response>();
    renderHeld({ '/v0/runtime/status': status.promise });

    expect(await screen.findByText('Loading runtime status…')).toBeInTheDocument();
    expect(screen.queryByText('Runtime status unavailable.')).not.toBeInTheDocument();
    expect(screen.queryByText('UNAVAILABLE')).not.toBeInTheDocument();

    status.resolve(jsonResponse(500, { error: 'runtime unreachable' }));
    expect(await screen.findByText('Runtime status unavailable.')).toBeInTheDocument();
    expect(screen.queryByText('Loading runtime status…')).not.toBeInTheDocument();
  });

  it("Operate says a device's signals are loading until its first read answers", async () => {
    const state = deferred<Response>();
    renderHeld({
      '/v0/devices': Promise.resolve(jsonResponse(200, { devices: [{ provider_id: 'sim0', device_id: 'dev0' }] })),
      '/v0/state/sim0/dev0': state.promise,
    });

    expect(await screen.findByText('Loading signals…')).toBeInTheDocument();
    expect(screen.queryByText('No signals available.')).not.toBeInTheDocument();

    state.resolve(jsonResponse(200, { values: [] }));
    expect(await screen.findByText('No signals available.')).toBeInTheDocument();
    expect(screen.queryByText('Loading signals…')).not.toBeInTheDocument();
  });
});
