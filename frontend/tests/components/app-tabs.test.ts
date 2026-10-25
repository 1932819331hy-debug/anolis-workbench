import { render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import App from '../../src/App.svelte';
import { createProjectDocument, jsonResponse, pathFromInput } from './helpers';

function stubApi() {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === '/api/projects') return Promise.resolve(jsonResponse(200, [{ name: 'a', meta: {} }]));
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

const TABS = ['Compose', 'Commission', 'Operate'];

describe('App: the workspace tabs (#569)', () => {
  it('say a project is needed while none is open', async () => {
    stubApi();
    history.replaceState({}, '', '/');
    render(App);

    for (const name of TABS) {
      const tab = await screen.findByRole('button', { name });
      expect(tab).toBeDisabled();
      expect(tab).toHaveAttribute('title', `Open a project to use ${name}.`);
    }
  });

  it('carry no such hint once a project is open', async () => {
    stubApi();
    history.replaceState({}, '', '/projects/a/compose');
    render(App);

    await screen.findByRole('heading', { name: 'Compose' });
    for (const name of TABS) {
      const tab = screen.getByRole('button', { name });
      expect(tab).toBeEnabled();
      expect(tab).not.toHaveAttribute('title');
    }
  });
});
