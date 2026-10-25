import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import App from '../../src/App.svelte';
import { createProjectDocument, jsonResponse, pathFromInput } from './helpers';

/** Two projects; saving `a` fails validation, everything else is benign. */
function stubApi() {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathFromInput(input);
      if (path === '/api/projects/a' && init?.method === 'PUT')
        return Promise.resolve(
          jsonResponse(422, {
            error: 'Validation failed',
            errors: [{ path: '$.providers.bread0', message: "'bus_path' is a required property" }],
          }),
        );
      if (path === '/api/projects')
        return Promise.resolve(jsonResponse(200, [{ name: 'a', meta: {} }, { name: 'b', meta: {} }]));
      const project = path.match(/^\/api\/projects\/([^/]+)$/);
      if (project) return Promise.resolve(jsonResponse(200, createProjectDocument(project[1])));
      if (path === '/api/status')
        return Promise.resolve(jsonResponse(200, { running: false, active_project: null }));
      if (path === '/api/templates') return Promise.resolve(jsonResponse(200, []));
      if (path === '/api/provider-schemas') return Promise.resolve(jsonResponse(200, { providers: {} }));
      return Promise.resolve(jsonResponse(200, {}));
    }),
  );
}

describe('App: Compose across a project switch (#561)', () => {
  it("does not show one project's failed save under another", async () => {
    stubApi();
    history.replaceState({}, '', '/projects/a/compose');
    render(App);

    await fireEvent.click(await screen.findByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Validation failed')).toBeInTheDocument();
    expect(screen.getByText(/'bus_path' is a required property/)).toBeInTheDocument();

    await fireEvent.change(document.querySelector('#project-selector') as HTMLSelectElement, {
      target: { value: 'b' },
    });
    await waitFor(() => expect(window.location.pathname).toBe('/projects/b/compose'));

    await waitFor(() => expect(screen.queryByText('Validation failed')).not.toBeInTheDocument());
    expect(screen.queryByText(/'bus_path' is a required property/)).not.toBeInTheDocument();
  });
});
