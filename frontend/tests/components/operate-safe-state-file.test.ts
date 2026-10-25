import { render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import Operate from '../../src/routes/Operate.svelte';
import { createImportedDocument, jsonResponse, pathFromInput } from './helpers';

const RUNNING = { running: true, active_project: 'rig-a' };

describe("Operate.svelte — the safe-state file is the project's own (#564)", () => {
  it("names the file an imported project's runtime_profiles maps the variant to", async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        if (pathFromInput(input) === '/api/status') return jsonResponse(200, RUNNING);
        return jsonResponse(200, {});
      }),
    );
    const system = createImportedDocument('rig-a');
    system.profile.runtime_profiles = { manual: 'config/anolis-runtime.bioreactor.manual.yaml' };
    render(Operate, { props: { projectName: 'rig-a', system, runtimeStatus: RUNNING } });

    expect(await screen.findByTestId('safe-state-fix')).toHaveTextContent(
      'config/anolis-runtime.bioreactor.manual.yaml',
    );
  });
});
