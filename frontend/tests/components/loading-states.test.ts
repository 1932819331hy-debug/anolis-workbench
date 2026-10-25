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
});
