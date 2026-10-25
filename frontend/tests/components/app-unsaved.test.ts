import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import App from '../../src/App.svelte';
import { createProjectDocument, jsonResponse, pathFromInput } from './helpers';

function stubApi() {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const path = pathFromInput(input);
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

const selector = () => document.querySelector('#project-selector') as HTMLSelectElement;

/** Open a's Compose and make an edit, so the draft is unsaved. */
async function openDirtyCompose(): Promise<void> {
  stubApi();
  history.replaceState({}, '', '/projects/a/compose');
  render(App);
  await fireEvent.change(await screen.findByDisplayValue('8080'), { target: { value: '9090' } });
  expect(document.querySelector('#unsaved-indicator')).not.toBeNull();
}

describe('App: the unsaved-changes prompt (#562)', () => {
  it('does not prompt on a tab switch, which keeps the draft', async () => {
    await openDirtyCompose();

    await fireEvent.click(screen.getByRole('button', { name: 'Commission' }));
    await waitFor(() => expect(window.location.pathname).toBe('/projects/a/commission'));
    expect(screen.queryByText(/Continue and discard unsaved changes/)).not.toBeInTheDocument();

    await fireEvent.click(screen.getByRole('button', { name: 'Compose' }));
    expect(await screen.findByDisplayValue('9090')).toBeInTheDocument();
    expect(screen.queryByText(/Continue and discard unsaved changes/)).not.toBeInTheDocument();
  });

  it('on a project switch, Stay Here keeps the project and its draft, and the header agrees', async () => {
    await openDirtyCompose();

    await fireEvent.change(selector(), { target: { value: 'b' } });
    expect(await screen.findByText(/Continue and discard unsaved changes/)).toBeInTheDocument();
    await fireEvent.click(screen.getByRole('button', { name: 'Stay Here' }));

    await waitFor(() => expect(selector().value).toBe('a'));
    expect(window.location.pathname).toBe('/projects/a/compose');
    expect(screen.getByDisplayValue('9090')).toBeInTheDocument();
  });

  it('on a project switch, Continue opens the other project without the draft', async () => {
    await openDirtyCompose();

    await fireEvent.change(selector(), { target: { value: 'b' } });
    await fireEvent.click(await screen.findByRole('button', { name: 'Continue' }));

    await waitFor(() => expect(window.location.pathname).toBe('/projects/b/compose'));
    expect(await screen.findByDisplayValue('8080')).toBeInTheDocument();
    expect(document.querySelector('#unsaved-indicator')).toBeNull();
  });
});
