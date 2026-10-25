import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import App from '../../src/App.svelte';
import { jsonResponse, pathFromInput } from './helpers';

/** No projects; /api/fleet answers with one target. */
function stubApi() {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const path = pathFromInput(input);
    if (path === '/api/projects') return Promise.resolve(jsonResponse(200, []));
    if (path === '/api/status')
      return Promise.resolve(jsonResponse(200, { running: false, active_project: null }));
    if (path === '/api/templates') return Promise.resolve(jsonResponse(200, []));
    if (path === '/api/fleet')
      return Promise.resolve(
        jsonResponse(200, {
          targets: [{ name: 'rig-a', host: 'pi@rig-a', project: 'demo', template: 'sim-quickstart' }],
        }),
      );
    return Promise.resolve(jsonResponse(200, {}));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const fleetRequests = (fetchMock: ReturnType<typeof stubApi>) =>
  fetchMock.mock.calls.filter(([input]) => pathFromInput(input) === '/api/fleet').length;

describe('App: the Fleet Dashboard route (#555)', () => {
  it('renders the dashboard on a direct load of /fleet', async () => {
    const fetchMock = stubApi();
    history.replaceState({}, '', '/fleet');
    render(App);

    expect(await screen.findByRole('heading', { name: 'Fleet Dashboard' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/fleet');
    await waitFor(() => expect(fleetRequests(fetchMock)).toBe(1));
  });

  it("renders the dashboard from Home's Fleet Dashboard button", async () => {
    const fetchMock = stubApi();
    history.replaceState({}, '', '/');
    render(App);

    await fireEvent.click(await screen.findByRole('button', { name: 'Fleet Dashboard' }));

    expect(await screen.findByRole('heading', { name: 'Fleet Dashboard' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/fleet');
    await waitFor(() => expect(fleetRequests(fetchMock)).toBe(1));
  });
});
