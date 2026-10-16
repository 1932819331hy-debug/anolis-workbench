import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
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

function status(
  estop: Record<string, unknown>,
  version = '0.1.44',
  extra: Record<string, unknown> = {},
): Response {
  return jsonResponse(200, {
    status: { code: 'OK' },
    version,
    mode: 'MANUAL',
    uptime_seconds: 1,
    polling_interval_ms: 500,
    providers: [],
    device_count: 0,
    estop: { latched: false, latched_at_epoch_ms: null, ...estop },
    ...extra,
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

describe("Operate.svelte — the startup safe state (#483)", () => {
  const ESTOP = { software_safe_state: 'hooks', uncovered_actuating_functions: 0 };

  function withStartup(startup: Record<string, unknown>): Response {
    return status(ESTOP, '0.1.45', {
      startup_safe_state: {
        on_startup: 'safe_state',
        ran: true,
        software_safe_state: 'hooks',
        safe_state_achieved: true,
        failed_actions: [],
        ...startup,
      },
    });
  }

  // safe_state_achieved is the runtime's verdict that every call the rung
  // issued was accepted by its provider (anolis run_ladder). It does not
  // say the outputs reached a safe state, so neither may the line.
  it.each(['hooks', 'setpoints', 'zero'])(
    'under %s, says every call was accepted, and claims no safe outputs',
    async (rung) => {
      renderOperate(withStartup({ software_safe_state: rung }));

      const line = await screen.findByTestId('startup-safe-state');
      expect(line).toHaveTextContent(
        `Startup safe state (${rung}): every call it issued was accepted by its provider.`,
      );
      expect(line).toHaveClass('field-note');
      expect(line.textContent).not.toMatch(/succeeded|achieved|outputs? (are|were|is) safe/i);
    },
  );

  it('warns when calls failed, naming each failed call', async () => {
    renderOperate(
      withStartup({
        software_safe_state: 'setpoints',
        safe_state_achieved: false,
        failed_actions: [
          { device_handle: 'p0/heater', function: 'set_power', success: false, error: 'timeout' },
          { device_handle: 'p1/pump', function: 'stop', success: false },
        ],
      }),
    );

    const line = await screen.findByTestId('startup-safe-state');
    expect(line).toHaveClass('field-error');
    expect(line).toHaveTextContent(
      'Startup safe state (setpoints): 2 call(s) failed: p0/heater/set_power: timeout; p1/pump/stop: failed',
    );
    expect(line.textContent).not.toMatch(/accepted/);
  });

  // The runtime's verdict is false with no failed call only when the rung
  // issued no call at all: zero, on a machine with no actuating function.
  it('under zero with no call issued, says nothing was driven, not that a call failed', async () => {
    renderOperate(withStartup({ software_safe_state: 'zero', safe_state_achieved: false }));

    const line = await screen.findByTestId('startup-safe-state');
    expect(line).toHaveClass('field-error');
    expect(line).toHaveTextContent(
      'Startup safe state (zero) ran but issued no call, so nothing was driven.',
    );
    expect(line.textContent).not.toMatch(/fail|accepted/i);
  });

  it('says outputs were left as they were under on_startup: leave', async () => {
    // The runtime reports the rung as none under leave: nothing ran.
    renderOperate(
      withStartup({
        on_startup: 'leave',
        ran: false,
        software_safe_state: 'none',
        safe_state_achieved: false,
      }),
    );

    const line = await screen.findByTestId('startup-safe-state');
    expect(line).toHaveTextContent('Outputs were left as they were at startup (on_startup: leave).');
    expect(line.textContent).not.toMatch(/No software safe state is declared/);
  });

  it('says nothing ran when no software safe state is declared', async () => {
    renderOperate(
      withStartup({ ran: false, software_safe_state: 'none', safe_state_achieved: false }),
    );

    expect(await screen.findByTestId('startup-safe-state')).toHaveTextContent(
      'No software safe state is declared, so nothing ran at startup.',
    );
  });

  it('shows nothing when the runtime does not report it', async () => {
    renderOperate(status(ESTOP, '0.1.44'));

    await screen.findByText('As reported by runtime v0.1.44.');
    expect(screen.queryByTestId('startup-safe-state')).not.toBeInTheDocument();
    expect(screen.queryByText(/startup/i)).not.toBeInTheDocument();
  });
});

describe("Operate.svelte — another project's runtime is running (#264)", () => {
  function renderForOtherProject(config: Record<string, unknown> = {}) {
    const posts: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = pathFromInput(input);
        if (init?.method === 'POST') posts.push(path);
        if (path === '/v0/estop')
          return jsonResponse(200, { latched: true, software_safe_state: 'zero', actions: [] });
        if (path === '/v0/runtime/status')
          return status({ latched: true, software_safe_state: 'zero', uncovered_actuating_functions: 0 });
        if (path === '/api/config') return jsonResponse(200, config);
        return jsonResponse(200, {});
      }),
    );
    const view = render(Operate, {
      props: {
        projectName: 'other',
        system: createProjectDocument('other'),
        runtimeStatus: RUNNING,
      },
    });
    return { posts, view };
  }

  it("offers a software stop labelled with the running project, which latches that runtime", async () => {
    const { posts } = renderForOtherProject();
    expect(screen.getByText(/Runtime is running for "demo", not this project/)).toBeInTheDocument();

    await fireEvent.click(screen.getByRole('button', { name: 'Software stop: demo' }));

    await waitFor(() => expect(posts).toEqual(['/v0/estop']));
    expect(await screen.findByText(/Software stop engaged \(zero\)/)).toBeInTheDocument();
  });

  it('offers no clear-latch control, even once the runtime reports the latch engaged', async () => {
    const { posts } = renderForOtherProject();
    await fireEvent.click(screen.getByRole('button', { name: 'Software stop: demo' }));
    await screen.findByText(/Software stop engaged/);

    expect(screen.queryByRole('button', { name: /Clear latch/ })).not.toBeInTheDocument();
    expect(posts).not.toContain('/v0/estop/clear');
  });

  it('says it is not an emergency stop before it is pressed', () => {
    renderForOtherProject();

    expect(
      screen.getByText('Not an emergency stop. For any physical hazard use the hardware emergency stop.'),
    ).toBeInTheDocument();
  });

  it("does not show one project's stop result under another project's name", async () => {
    const { view } = renderForOtherProject();
    await fireEvent.click(screen.getByRole('button', { name: 'Software stop: demo' }));
    await screen.findByText(/Software stop engaged \(zero\)/);

    await view.rerender({ runtimeStatus: { running: true, active_project: 'foo' } });

    expect(await screen.findByRole('button', { name: 'Software stop: foo' })).toBeInTheDocument();
    expect(screen.queryByText(/Software stop engaged/)).not.toBeInTheDocument();
  });

  it('does not name the launched project when the proxy targets a runtime URL override', async () => {
    renderForOtherProject({ telemetry_url: '', runtime_url_override: true });

    expect(
      await screen.findByRole('button', { name: 'Software stop: configured runtime URL' }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Software stop: demo' })).not.toBeInTheDocument();
  });
});
