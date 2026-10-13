import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import App from '../../src/App.svelte';
import { createProjectDocument, deferred, jsonResponse, pathFromInput } from './helpers';

// Tests wait on the check's name in the results list, which renders the same
// with or without the preflight summary line.
const PASSING_PREFLIGHT = {
  ok: true,
  checks: [{ name: 'System-level validation', ok: true, error: null, hint: null }],
  summary: { passed: 1, skipped: 0, failed: 0 },
};

/**
 * Two projects, both openable. `preflightFor` answers each project's
 * preflight; everything else the shell polls gets a benign default.
 */
function stubApi(preflightFor: (project: string) => Promise<Response>) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const path = pathFromInput(input);
    const preflight = path.match(/^\/api\/projects\/([^/]+)\/preflight$/);
    if (preflight) return preflightFor(preflight[1]);
    if (path === '/api/projects')
      return Promise.resolve(jsonResponse(200, [{ name: 'a', meta: {} }, { name: 'b', meta: {} }]));
    const project = path.match(/^\/api\/projects\/([^/]+)$/);
    if (project) return Promise.resolve(jsonResponse(200, createProjectDocument(project[1])));
    if (path === '/api/status')
      return Promise.resolve(jsonResponse(200, { running: false, active_project: null }));
    if (path === '/api/templates') return Promise.resolve(jsonResponse(200, []));
    if (path === '/api/provider-schemas') return Promise.resolve(jsonResponse(200, { providers: {} }));
    return Promise.resolve(jsonResponse(200, {}));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

async function openCommissionFor(project: string): Promise<void> {
  history.replaceState({}, '', `/projects/${project}/commission`);
  render(App);
  await screen.findByRole('button', { name: 'Preflight Check' });
}

async function switchProject(project: string): Promise<void> {
  await fireEvent.change(document.querySelector('#project-selector') as HTMLSelectElement, {
    target: { value: project },
  });
  await waitFor(() => expect(window.location.pathname).toBe(`/projects/${project}/commission`));
}

describe('App: Commission across a project switch', () => {
  it("does not carry one project's preflight result into another", async () => {
    stubApi(() => Promise.resolve(jsonResponse(200, PASSING_PREFLIGHT)));
    await openCommissionFor('a');

    await fireEvent.click(screen.getByRole('button', { name: 'Preflight Check' }));
    await screen.findByText('System-level validation');
    expect(screen.getByRole('button', { name: /Launch/ })).toBeEnabled();

    await switchProject('b');

    await waitFor(() => {
      expect(screen.queryByText('System-level validation')).not.toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /Launch/ })).toBeDisabled();
  });

  it("does not let a preflight still running for one project land in another", async () => {
    const pendingForA = deferred<Response>();
    stubApi((project) =>
      project === 'a' ? pendingForA.promise : Promise.resolve(jsonResponse(200, PASSING_PREFLIGHT)),
    );
    await openCommissionFor('a');

    await fireEvent.click(screen.getByRole('button', { name: 'Preflight Check' }));
    await switchProject('b');

    pendingForA.resolve(jsonResponse(200, PASSING_PREFLIGHT));
    // Let a's response settle before asserting it went nowhere.
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(screen.queryByText('System-level validation')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Launch/ })).toBeDisabled();
  });
});
