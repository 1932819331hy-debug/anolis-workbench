import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import Operate from '../../src/routes/Operate.svelte';
import type { ProjectDocument } from '../../src/lib/contracts';
import { createProjectDocument, deferred, jsonResponse, pathFromInput } from './helpers';

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

  // anolis#461: a call whose device its provider reported unreachable at
  // startup is skipped, not failed. That device is not driven when it
  // appears later, so a skip is never ok.
  const SKIPPED = [
    { device_handle: 'bread0/rlht0', function: 'set_power', reason: 'probe failed (version_read_failed): rlht0 did not answer' },
    { device_handle: 'bread0/dcmt0', function: 'set_open_loop', reason: 'probe failed (version_read_failed): dcmt0 did not answer' },
  ];

  it('with every device absent, names each skipped call and its reason', async () => {
    renderOperate(withStartup({ safe_state_achieved: false, skipped_actions: SKIPPED }));

    const line = await screen.findByTestId('startup-safe-state');
    expect(line).toHaveClass('field-error');
    expect(line).toHaveTextContent(
      'Startup safe state (hooks) issued no call: 2 call(s) skipped, device absent at startup: ' +
        'bread0/rlht0/set_power: probe failed (version_read_failed): rlht0 did not answer; ' +
        'bread0/dcmt0/set_open_loop: probe failed (version_read_failed): dcmt0 did not answer',
    );
    expect(line.textContent).not.toMatch(/nothing was driven|accepted|call\(s\) failed/);
  });

  it('with the calls that ran accepted and one skipped, says both and is not ok', async () => {
    renderOperate(withStartup({ skipped_actions: SKIPPED.slice(0, 1) }));

    const line = await screen.findByTestId('startup-safe-state');
    expect(line).toHaveClass('field-error');
    expect(line).toHaveTextContent(
      'Startup safe state (hooks): every call it issued was accepted by its provider. ' +
        '1 call(s) skipped, device absent at startup: ' +
        'bread0/rlht0/set_power: probe failed (version_read_failed): rlht0 did not answer',
    );
  });

  it('with calls failed and one skipped, names both', async () => {
    renderOperate(
      withStartup({
        safe_state_achieved: false,
        failed_actions: [{ device_handle: 'p0/heater', function: 'set_power', success: false, error: 'timeout' }],
        skipped_actions: SKIPPED.slice(1),
      }),
    );

    const line = await screen.findByTestId('startup-safe-state');
    expect(line).toHaveClass('field-error');
    expect(line).toHaveTextContent(
      'Startup safe state (hooks): 1 call(s) failed: p0/heater/set_power: timeout. ' +
        '1 call(s) skipped, device absent at startup: ' +
        'bread0/dcmt0/set_open_loop: probe failed (version_read_failed): dcmt0 did not answer',
    );
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

describe('Operate.svelte — the Runtime Status badge (#557)', () => {
  const ESTOP = { software_safe_state: 'hooks', uncovered_actuating_functions: 0 };

  it('reads OK when the request succeeded and nothing needs attention', async () => {
    renderOperate(status(ESTOP));

    expect(await screen.findByTestId('runtime-health')).toHaveTextContent('OK');
  });

  it('does not read OK while the software stop is latched', async () => {
    renderOperate(status({ ...ESTOP, latched: true, latched_at_epoch_ms: 1 }));

    await waitFor(() => expect(screen.getByTestId('runtime-health')).toHaveTextContent('ATTENTION'));
    expect(screen.getByText('software stop latched.')).toBeInTheDocument();
  });

  it('does not read OK when the startup safe state failed', async () => {
    renderOperate(
      status(ESTOP, '0.1.51', {
        startup_safe_state: {
          on_startup: 'safe_state',
          ran: true,
          software_safe_state: 'hooks',
          safe_state_achieved: false,
          failed_actions: [
            {
              device_handle: 'bread0/dcmt0',
              function: 'set_mode',
              success: false,
              error: 'SET command refused: WATCHDOG_TRIP',
            },
          ],
        },
      }),
    );

    await waitFor(() => expect(screen.getByTestId('runtime-health')).toHaveTextContent('ATTENTION'));
    expect(screen.getByText('startup safe state not achieved.')).toBeInTheDocument();
  });
});

describe('Operate.svelte — switching devices with a request in flight (#552)', () => {
  const DEVICES = {
    devices: [
      { provider_id: 'bread0', device_id: 'rlht0', display_name: 'Heater' },
      { provider_id: 'bread0', device_id: 'dcmt0', display_name: 'Motor' },
    ],
  };
  const state = (signal: string) =>
    jsonResponse(200, { values: [{ signal_id: signal, value: 1, quality: 'OK', timestamp_ms: 0 }] });
  const caps = (fn: string) =>
    jsonResponse(200, { capabilities: { signals: [], functions: [{ function_id: 1, name: fn, args: {} }] } });
  const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

  it.each([['state'], ['capabilities']])(
    "files the heater's late %s reply under the heater, not the motor now selected",
    async (held) => {
      const late = deferred<Response>();
      vi.stubGlobal(
        'fetch',
        vi.fn(async (input: RequestInfo | URL) => {
          const path = pathFromInput(input);
          if (path === '/api/status') return jsonResponse(200, RUNNING);
          if (path === '/v0/devices') return jsonResponse(200, DEVICES);
          if (path === '/v0/state/bread0/rlht0')
            return held === 'state' ? late.promise.then(() => state('tc1_temp')) : state('tc1_temp');
          if (path === '/v0/devices/bread0/rlht0/capabilities')
            return held === 'capabilities'
              ? late.promise.then(() => caps('set_setpoints'))
              : caps('set_setpoints');
          if (path === '/v0/state/bread0/dcmt0') return state('motor1_pwm');
          if (path === '/v0/devices/bread0/dcmt0/capabilities') return caps('set_brake');
          return jsonResponse(200, {});
        }),
      );
      render(Operate, {
        props: { projectName: 'demo', system: createProjectDocument('demo'), runtimeStatus: RUNNING },
      });

      await fireEvent.click(await screen.findByRole('button', { name: /Motor \(bread0\/dcmt0\)/ }));
      expect(await screen.findByText('set_brake')).toBeInTheDocument();
      late.resolve(jsonResponse(200, {}));
      await tick();
      await tick();

      expect(screen.getByRole('heading', { name: 'Motor (bread0/dcmt0)' })).toBeInTheDocument();
      expect(screen.getByText('motor1_pwm')).toBeInTheDocument();
      expect(screen.getByText('set_brake')).toBeInTheDocument();
      expect(screen.queryByText('tc1_temp')).not.toBeInTheDocument();
      expect(screen.queryByText('set_setpoints')).not.toBeInTheDocument();

      await fireEvent.click(screen.getByRole('button', { name: /Heater \(bread0\/rlht0\)/ }));
      expect(await screen.findByText('set_setpoints')).toBeInTheDocument();
      expect(screen.getByText('tc1_temp')).toBeInTheDocument();
      expect(screen.queryByText('set_brake')).not.toBeInTheDocument();
    },
  );
});

describe('Operate.svelte — the /v0/events stream across mounts (#551)', () => {
  class CountingEventSource {
    static instances: CountingEventSource[] = [];
    url: string;
    closed = false;
    onopen: ((event: Event) => void) | null = null;
    onerror: ((event: Event) => void) | null = null;
    constructor(url: string) {
      this.url = url;
      CountingEventSource.instances.push(this);
    }
    addEventListener(): void {}
    close(): void {
      this.closed = true;
    }
  }

  const open = () => CountingEventSource.instances.filter((s) => !s.closed).length;

  it('leaves no stream open when it unmounts during a status poll', async () => {
    CountingEventSource.instances = [];
    vi.stubGlobal('EventSource', CountingEventSource as unknown as typeof EventSource);
    const polls: ReturnType<typeof deferred<Response>>[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        if (pathFromInput(input) === '/api/status') {
          const poll = deferred<Response>();
          polls.push(poll);
          return poll.promise;
        }
        return jsonResponse(200, {});
      }),
    );

    for (let mount = 0; mount < 3; mount += 1) {
      const view = render(Operate, {
        props: { projectName: 'demo', system: createProjectDocument('demo'), runtimeStatus: RUNNING },
      });
      await waitFor(() => expect(polls.length).toBe(mount + 1));
      view.unmount();
      polls[mount].resolve(jsonResponse(200, RUNNING));
      await new Promise((resolve) => setTimeout(resolve, 0));
    }

    expect(CountingEventSource.instances.length).toBeGreaterThan(0);
    expect(open()).toBe(0);

    render(Operate, {
      props: { projectName: 'demo', system: createProjectDocument('demo'), runtimeStatus: RUNNING },
    });
    await waitFor(() => expect(polls.length).toBe(4));
    polls[3].resolve(jsonResponse(200, RUNNING));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(open()).toBe(1);
  });
});
