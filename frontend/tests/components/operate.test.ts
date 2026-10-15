import { render, screen } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import Operate from '../../src/routes/Operate.svelte';
import type { ProjectDocument } from '../../src/lib/contracts';
import { createProjectDocument, jsonResponse, pathFromInput } from './helpers';

const RUNNING = { running: true, active_project: 'demo' };

/** Operate for a running `demo`; `runtimeStatus` answers GET /v0/runtime/status. */
function renderOperate(
  runtimeStatus: Response,
  system: ProjectDocument = createProjectDocument('demo'),
) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const path = pathFromInput(input);
      if (path === '/api/status') return jsonResponse(200, RUNNING);
      if (path === '/v0/runtime/status') return runtimeStatus;
      return jsonResponse(200, {});
    }),
  );
  render(Operate, { props: { projectName: 'demo', system, runtimeStatus: RUNNING } });
}

function status(estop: Record<string, unknown>, version = '0.1.44'): Response {
  return jsonResponse(200, {
    status: { code: 'OK' },
    version,
    mode: 'MANUAL',
    uptime_seconds: 1,
    polling_interval_ms: 500,
    providers: [],
    device_count: 0,
    estop: { latched: false, latched_at_epoch_ms: null, ...estop },
  });
}

describe("Operate.svelte — the safe-state note (#346)", () => {
  it('attributes the rung to the runtime version and names where to declare it', async () => {
    renderOperate(status({ software_safe_state: 'setpoints', uncovered_actuating_functions: 0 }));

    expect(await screen.findByText('As reported by runtime v0.1.44.')).toBeInTheDocument();
    expect(screen.getByText(/Latches actuation, then drives the declared safe setpoints/)).toBeInTheDocument();
    const fix = screen.getByTestId('safe-state-fix');
    expect(fix).toHaveTextContent('safety.safe_state');
    expect(fix).toHaveTextContent("the project's config/anolis-runtime.manual.yaml for dev-launch");
    expect(fix).toHaveTextContent('/opt/anolis/config/runtime.yaml on an installed target');
  });

  it('names the variant file of the variant that launches', async () => {
    const system = createProjectDocument('demo');
    system.variants.automation = structuredClone(system.variants.manual);
    system.launch = { variant: 'automation' };
    renderOperate(status({ software_safe_state: 'zero', uncovered_actuating_functions: 0 }), system);

    expect(await screen.findByTestId('safe-state-fix')).toHaveTextContent(
      'config/anolis-runtime.automation.yaml',
    );
  });

  describe('at runtime 0.1.45, where the count is what the active rung will not drive', () => {
    const NEW = '0.1.45';

    it.each([
      ['hooks', 2],
      ['zero', 1],
    ])('under %s, shows the %i actuating function(s) the e-stop will not drive', async (rung, n) => {
      renderOperate(status({ software_safe_state: rung, uncovered_actuating_functions: n }, NEW));

      expect(
        await screen.findByText(`${n} actuating function(s) the e-stop will not drive.`),
      ).toBeInTheDocument();
    });

    it.each([
      ['hooks', /Latches actuation, then runs the declared safe-state hooks/],
      ['setpoints', /Latches actuation, then drives the declared safe setpoints/],
      ['zero', /latches actuation, then tries to drive every actuating function to zero/],
    ])('under %s with 0 undriven, shows its note and no count line', async (rung, note) => {
      renderOperate(status({ software_safe_state: rung, uncovered_actuating_functions: 0 }, NEW));

      expect(await screen.findByText(note)).toBeInTheDocument();
      expect(screen.queryByText(/actuating function\(s\)/)).not.toBeInTheDocument();
    });

    it('under none, says the e-stop drives nothing and shows no count', async () => {
      renderOperate(status({ software_safe_state: 'none', uncovered_actuating_functions: 4 }, NEW));

      expect(await screen.findByText(/The e-stop drives nothing/)).toBeInTheDocument();
      expect(screen.queryByText(/actuating function\(s\)/)).not.toBeInTheDocument();
    });
  });

  describe.each([
    ['0.1.44', '0.1.44'],
    ['an unknown version', ''],
    ['an unparsable version', 'dev-build'],
  ])('at %s, where the count is functions with no matching setpoint', (_label, version) => {
    it.each([
      ['hooks', 7],
      ['zero', 3],
    ])('under %s, shows no count line for %i', async (rung, n) => {
      renderOperate(status({ software_safe_state: rung, uncovered_actuating_functions: n }, version));

      expect(await screen.findByText(/^As reported by /)).toBeInTheDocument();
      expect(screen.queryByText(/actuating function\(s\)/)).not.toBeInTheDocument();
    });

    it('under none, shows the count as the reason setpoints are not used', async () => {
      renderOperate(status({ software_safe_state: 'none', uncovered_actuating_functions: 4 }, version));

      expect(
        await screen.findByText(
          /4 actuating function\(s\) have no declared safe setpoint, which is why setpoints are not used/,
        ),
      ).toBeInTheDocument();
      expect(screen.queryByText(/the e-stop will not drive/)).not.toBeInTheDocument();
    });
  });

  it('says not observed when the runtime has not answered, and still names the file', async () => {
    renderOperate(jsonResponse(500, { error: 'runtime unreachable' }));

    expect(
      await screen.findByText('Software safe-state: not observed. The runtime has not reported it.'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('safe-state-fix')).toHaveTextContent('config/anolis-runtime.manual.yaml');
    expect(screen.queryByText(/As reported by/)).not.toBeInTheDocument();
  });
});
