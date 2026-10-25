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

/** Open the stop row's click-open details (#613), once its state has rendered. */
async function openStopDetails() {
  await screen.findByTestId('estop-state');
  await waitFor(() => expect(screen.getByTestId('estop-state')).not.toHaveTextContent('Loading'));
  for (const button of screen.getAllByRole('button', { name: /^About / })) await fireEvent.click(button);
}

describe("Operate.svelte — the safe-state note (#346)", () => {
  it('names the runtime version in Runtime Status, and the rung and its file behind the ⓘ', async () => {
    renderOperate(status({ software_safe_state: 'setpoints', uncovered_actuating_functions: 0 }));

    expect(await screen.findByText(/Runtime v0\.1\.44 \| Mode MANUAL/)).toBeInTheDocument();
    await openStopDetails();
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
    await openStopDetails();

    expect(await screen.findByTestId('safe-state-fix')).toHaveTextContent(
      'config/anolis-runtime.automation.yaml',
    );
  });

  describe('at runtime 0.1.45, where the count is what the active rung will not drive', () => {
    const NEW = '0.1.45';

    it.each([
      ['hooks', 2],
      ['zero', 1],
    ])('under %s, shows the %i actuating function(s) the software stop will not drive', async (rung, n) => {
      renderOperate(status({ software_safe_state: rung, uncovered_actuating_functions: n }, NEW));
      await openStopDetails();

      expect(
        await screen.findByText(`${n} actuating function(s) the software stop will not drive.`),
      ).toBeInTheDocument();
    });

    it.each([
      ['hooks', /Latches actuation, then runs the declared safe-state hooks/],
      ['setpoints', /Latches actuation, then drives the declared safe setpoints/],
      ['zero', /latches actuation, then tries to drive every actuating function to zero/],
    ])('under %s with 0 undriven, shows its note and no count line', async (rung, note) => {
      renderOperate(status({ software_safe_state: rung, uncovered_actuating_functions: 0 }, NEW));
      await openStopDetails();

      expect(await screen.findByText(note)).toBeInTheDocument();
      expect(screen.queryByText(/actuating function\(s\)/)).not.toBeInTheDocument();
    });

    it('under none, says the software stop drives nothing and shows no count', async () => {
      renderOperate(status({ software_safe_state: 'none', uncovered_actuating_functions: 4 }, NEW));
      await openStopDetails();

      expect(await screen.findByText(/The software stop drives nothing/)).toBeInTheDocument();
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
      await openStopDetails();

      expect(screen.getByTestId('safe-state-fix')).toBeInTheDocument();
      expect(screen.queryByText(/actuating function\(s\)/)).not.toBeInTheDocument();
    });

    it('under none, shows the count as the reason setpoints are not used', async () => {
      renderOperate(status({ software_safe_state: 'none', uncovered_actuating_functions: 4 }, version));
      await openStopDetails();

      expect(
        await screen.findByText(
          /4 actuating function\(s\) have no declared safe setpoint, which is why setpoints are not used/,
        ),
      ).toBeInTheDocument();
      expect(screen.queryByText(/the software stop will not drive/)).not.toBeInTheDocument();
    });
  });

  it('says not observed when the runtime has not answered, and still names the file', async () => {
    renderOperate(jsonResponse(500, { error: 'runtime unreachable' }));
    await openStopDetails();

    expect(
      await screen.findByText('Software safe-state: not observed. The runtime has not reported it.'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('safe-state-fix')).toHaveTextContent('config/anolis-runtime.manual.yaml');
    expect(screen.queryByText(/Runtime v/)).not.toBeInTheDocument();
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

    await screen.findByText(/Runtime v0\.1\.44 \| Mode MANUAL/);
    expect(screen.queryByTestId('startup-safe-state')).not.toBeInTheDocument();
    expect(screen.queryByText(/startup/i)).not.toBeInTheDocument();
  });
});

describe("Operate.svelte — another project's runtime is running (#264)", () => {
  function renderForOtherProject(config: Record<string, unknown> = {}) {
    const posts: string[] = [];
    // The running runtime latches when the stop is pressed, not before.
    const runtime = { latched: false };
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = pathFromInput(input);
        if (init?.method === 'POST') posts.push(path);
        if (path === '/v0/estop') {
          runtime.latched = true;
          return jsonResponse(200, { latched: true, software_safe_state: 'zero', actions: [] });
        }
        if (path === '/v0/runtime/status')
          return status({
            latched: runtime.latched,
            latched_at_epoch_ms: runtime.latched ? 1 : null,
            software_safe_state: 'zero',
            uncovered_actuating_functions: 0,
          });
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
    return { posts, view, runtime };
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
    const { view, runtime } = renderForOtherProject();
    await fireEvent.click(screen.getByRole('button', { name: 'Software stop: demo' }));
    await screen.findByText(/Software stop engaged \(zero\)/);

    // foo's runtime is a different one, and it is not latched.
    runtime.latched = false;
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

describe('Operate.svelte — the software stop card shows one state (#553)', () => {
  const ESTOP = { software_safe_state: 'zero', uncovered_actuating_functions: 0 };

  /** Status answers `first` until a stop or clear is posted, then hangs. */
  function renderWithSlowStatusAfterAction(first: Record<string, unknown>) {
    let acted = false;
    const slow = deferred<Response>();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = pathFromInput(input);
        if (path === '/api/status') return jsonResponse(200, RUNNING);
        if (init?.method === 'POST' && path === '/v0/estop') {
          acted = true;
          return jsonResponse(200, { latched: true, software_safe_state: 'zero', actions: [] });
        }
        if (init?.method === 'POST' && path === '/v0/estop/clear') {
          acted = true;
          return jsonResponse(200, { latched: false });
        }
        if (path === '/v0/runtime/status') return acted ? slow.promise : status(first);
        return jsonResponse(200, {});
      }),
    );
    render(Operate, { props: { projectName: 'demo', system: createProjectDocument('demo'), runtimeStatus: RUNNING } });
  }

  it('shows the latch as soon as the stop reply reports it, without waiting for status', async () => {
    renderWithSlowStatusAfterAction({ ...ESTOP, latched: false });

    await fireEvent.click(await screen.findByRole('button', { name: 'SOFTWARE STOP' }));

    expect(await screen.findByText(/^SAFE-STATE ENGAGED at /)).toBeInTheDocument();
    expect(screen.queryByText('ENGAGING…')).not.toBeInTheDocument();
  });

  it('drops the latched box as soon as the clear reply reports it released', async () => {
    renderWithSlowStatusAfterAction({ ...ESTOP, latched: true, latched_at_epoch_ms: 1 });

    await fireEvent.click(await screen.findByRole('button', { name: 'Clear latch…' }));
    await fireEvent.click(screen.getByRole('button', { name: /Confirm clear/ }));

    expect(await screen.findByText('Latch cleared. Actuation re-enabled.')).toBeInTheDocument();
    expect(screen.queryByText(/^SAFE-STATE ENGAGED at /)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'SOFTWARE STOP' })).toBeInTheDocument();
  });

  it("shows another project's latch and links to that project's Operate view, with no clear here", async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        if (pathFromInput(input) === '/v0/runtime/status')
          return status({ ...ESTOP, latched: true, latched_at_epoch_ms: 1 });
        return jsonResponse(200, {});
      }),
    );
    const onNavigate = vi.fn();
    render(Operate, {
      props: { projectName: 'other', system: createProjectDocument('other'), runtimeStatus: RUNNING, onNavigate },
    });

    const latch = await screen.findByTestId('other-project-latch');
    expect(latch).toHaveTextContent('SAFE-STATE ENGAGED — actuation latched');
    expect(screen.queryByRole('button', { name: /Clear latch/ })).not.toBeInTheDocument();

    await fireEvent.click(screen.getByRole('link', { name: "demo's Operate view" }));
    expect(onNavigate).toHaveBeenCalledWith('/projects/demo/operate');
  });
});

describe('Operate.svelte — the software stop never disappears (#553)', () => {
  it('offers the stop before status answers, and shows the latch from its reply', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = pathFromInput(input);
        if (path === '/api/status') return jsonResponse(200, RUNNING);
        if (init?.method === 'POST' && path === '/v0/estop')
          return jsonResponse(200, { latched: true, software_safe_state: 'zero', actions: [] });
        if (path === '/v0/runtime/status') return new Promise<Response>(() => {});
        return jsonResponse(200, {});
      }),
    );
    render(Operate, { props: { projectName: 'demo', system: createProjectDocument('demo'), runtimeStatus: RUNNING } });

    expect(screen.getByTestId('estop-state')).toHaveTextContent('Loading safe state…');
    await fireEvent.click(screen.getByRole('button', { name: 'SOFTWARE STOP' }));

    expect(await screen.findByText(/^SAFE-STATE ENGAGED at /)).toBeInTheDocument();
  });

  it("drops another project's latch once its runtime reports it cleared, keeping the stop throughout", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const runtime = { latched: true };
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        if (pathFromInput(input) === '/v0/runtime/status')
          return status({
            software_safe_state: 'zero',
            uncovered_actuating_functions: 0,
            latched: runtime.latched,
            latched_at_epoch_ms: runtime.latched ? 1 : null,
          });
        return jsonResponse(200, {});
      }),
    );
    render(Operate, { props: { projectName: 'other', system: createProjectDocument('other'), runtimeStatus: RUNNING } });

    expect(await screen.findByTestId('other-project-latch')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Software stop: demo' })).toBeInTheDocument();

    runtime.latched = false;
    await vi.advanceTimersByTimeAsync(5000);

    await waitFor(() => expect(screen.queryByTestId('other-project-latch')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Software stop: demo' })).toBeInTheDocument();
  });
});

describe('Operate.svelte — no runtime running for this project (#510)', () => {
  it('names the two ways to reach a runtime deployed to another machine', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse(200, {})),
    );
    render(Operate, {
      props: {
        projectName: 'demo',
        system: createProjectDocument('demo'),
        runtimeStatus: { running: false, active_project: null },
      },
    });

    expect(screen.getByText(/Runtime is not running for this project/)).toBeInTheDocument();
    const routes = screen.getByTestId('operate-remote-routes');
    expect(routes).toHaveTextContent('ssh -L 3010:127.0.0.1:3010');
    expect(routes).toHaveTextContent('ANOLIS_WORKBENCH_RUNTIME_URL=http://127.0.0.1:8080');
    expect(routes).toHaveTextContent('ANOLIS_WORKBENCH_RUNTIME_TOKEN');
    expect(routes).toHaveTextContent('/etc/anolis/runtime.env');
  });

  it("does not show them when another project's runtime is running", () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse(200, {})),
    );
    render(Operate, {
      props: { projectName: 'other', system: createProjectDocument('other'), runtimeStatus: RUNNING },
    });

    expect(screen.queryByTestId('operate-remote-routes')).not.toBeInTheDocument();
  });
});

describe('Operate.svelte — a status reply older than the last stop or clear (#614)', () => {
  const ESTOP = { software_safe_state: 'zero', uncovered_actuating_functions: 0 };
  const LATCHED = /^SAFE-STATE ENGAGED at /;

  /** Each GET /v0/runtime/status waits on its own reply, in request order. */
  function renderWithHeldStatus(projectName = 'demo') {
    const reads: Array<ReturnType<typeof deferred<Response>>> = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = pathFromInput(input);
        if (path === '/api/status') return jsonResponse(200, RUNNING);
        if (init?.method === 'POST' && path === '/v0/estop')
          return jsonResponse(200, { latched: true, software_safe_state: 'zero', actions: [] });
        if (init?.method === 'POST' && path === '/v0/estop/clear') return jsonResponse(200, { latched: false });
        if (path === '/v0/runtime/status') {
          const read = deferred<Response>();
          reads.push(read);
          return read.promise;
        }
        return jsonResponse(200, {});
      }),
    );
    render(Operate, {
      props: { projectName, system: createProjectDocument(projectName), runtimeStatus: RUNNING },
    });
    return reads;
  }

  /** Let a resolved reply run through the page's promise chain. */
  const settle = () => new Promise((resolve) => setTimeout(resolve, 20));

  it('keeps the latch the stop reply reported when an older poll reply says unlatched', async () => {
    const reads = renderWithHeldStatus();
    await waitFor(() => expect(reads).toHaveLength(1));

    await fireEvent.click(screen.getByRole('button', { name: 'SOFTWARE STOP' }));
    expect(await screen.findByText(LATCHED)).toBeInTheDocument();
    await waitFor(() => expect(reads).toHaveLength(2));

    reads[0].resolve(status({ ...ESTOP, latched: false }, '0.1.44', { uptime_seconds: 4242 }));
    expect(await screen.findByText(/Uptime 4242s/)).toBeInTheDocument();

    expect(screen.getByText(LATCHED)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Clear latch…' })).toBeInTheDocument();
  });

  it('takes a reply whose request left after the stop reply', async () => {
    const reads = renderWithHeldStatus();
    await waitFor(() => expect(reads).toHaveLength(1));

    await fireEvent.click(screen.getByRole('button', { name: 'SOFTWARE STOP' }));
    await waitFor(() => expect(reads).toHaveLength(2));

    reads[1].resolve(status({ ...ESTOP, latched: false }));
    await settle();

    expect(screen.queryByText(LATCHED)).not.toBeInTheDocument();
  });

  it('keeps the release the clear reply reported when an older poll reply says latched', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const reads = renderWithHeldStatus();
    await waitFor(() => expect(reads).toHaveLength(1));
    reads[0].resolve(status({ ...ESTOP, latched: true, latched_at_epoch_ms: 1 }));
    expect(await screen.findByText(LATCHED)).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(5000);
    await waitFor(() => expect(reads).toHaveLength(2));
    await fireEvent.click(screen.getByRole('button', { name: 'Clear latch…' }));
    await fireEvent.click(screen.getByRole('button', { name: /Confirm clear/ }));
    expect(await screen.findByText('Latch cleared. Actuation re-enabled.')).toBeInTheDocument();
    await waitFor(() => expect(reads).toHaveLength(3));

    reads[1].resolve(status({ ...ESTOP, latched: true, latched_at_epoch_ms: 1 }, '0.1.44', { uptime_seconds: 4242 }));
    expect(await screen.findByText(/Uptime 4242s/)).toBeInTheDocument();

    expect(screen.queryByText(LATCHED)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'SOFTWARE STOP' })).toBeInTheDocument();
  });

  it("keeps the release when the read after the stop lands after the clear's reply", async () => {
    const reads = renderWithHeldStatus();
    await waitFor(() => expect(reads).toHaveLength(1));

    await fireEvent.click(screen.getByRole('button', { name: 'SOFTWARE STOP' }));
    await waitFor(() => expect(reads).toHaveLength(2));
    await fireEvent.click(await screen.findByRole('button', { name: 'Clear latch…' }));
    await fireEvent.click(screen.getByRole('button', { name: /Confirm clear/ }));
    expect(await screen.findByText('Latch cleared. Actuation re-enabled.')).toBeInTheDocument();
    await waitFor(() => expect(reads).toHaveLength(3));

    reads[1].resolve(status({ ...ESTOP, latched: true, latched_at_epoch_ms: 1 }));
    await settle();

    expect(screen.queryByText(LATCHED)).not.toBeInTheDocument();
  });

  it("keeps another project's latch the stop reply reported when an older read says unlatched", async () => {
    const reads = renderWithHeldStatus('other');
    await waitFor(() => expect(reads).toHaveLength(1));

    await fireEvent.click(screen.getByRole('button', { name: 'Software stop: demo' }));
    expect(await screen.findByTestId('other-project-latch')).toBeInTheDocument();
    await waitFor(() => expect(reads).toHaveLength(2));

    reads[0].resolve(status({ ...ESTOP, latched: false }));
    await settle();

    expect(screen.getByTestId('other-project-latch')).toBeInTheDocument();
  });
});

describe('Operate.svelte — the Mode card without the automation layer (#582)', () => {
  const ESTOP = { software_safe_state: 'zero', uncovered_actuating_functions: 0 };

  /** `modeReply` answers GET /v0/mode; runtime status reports MANUAL. */
  function renderWithMode(modeReply: () => Response) {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const path = pathFromInput(input);
        if (path === '/api/status') return jsonResponse(200, RUNNING);
        if (path === '/v0/runtime/status') return status(ESTOP);
        if (path === '/v0/mode') return modeReply();
        return jsonResponse(200, {});
      }),
    );
    render(Operate, {
      props: { projectName: 'demo', system: createProjectDocument('demo'), runtimeStatus: RUNNING },
    });
  }

  it("shows the runtime-status mode and the runtime's reason in place of Set Mode", async () => {
    renderWithMode(() =>
      jsonResponse(503, { status: { code: 'UNAVAILABLE', message: 'Automation layer not enabled' } }),
    );

    expect(await screen.findByTestId('mode-fixed')).toHaveTextContent(
      "Mode can't be set: Automation layer not enabled.",
    );
    expect(screen.getByTestId('mode-badge')).toHaveTextContent('MANUAL');
    expect(screen.queryByRole('button', { name: 'Set Mode' })).not.toBeInTheDocument();
  });

  it('keeps Set Mode when /v0/mode fails for another reason, and shows the runtime-status mode', async () => {
    renderWithMode(() => jsonResponse(502, { error: 'Runtime proxy failed: timed out' }));

    expect(await screen.findByText(/Uptime 1s/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('mode-badge')).toHaveTextContent('MANUAL'));
    expect(screen.getByRole('button', { name: 'Set Mode' })).toBeInTheDocument();
    expect(screen.queryByTestId('mode-fixed')).not.toBeInTheDocument();
  });

  it('shows the mode /v0/mode reports when the automation layer is on', async () => {
    renderWithMode(() => jsonResponse(200, { status: { code: 'OK' }, mode: 'AUTO' }));

    await waitFor(() => expect(screen.getByTestId('mode-badge')).toHaveTextContent('AUTO'));
    expect(screen.getByRole('button', { name: 'Set Mode' })).toBeInTheDocument();
    expect(screen.queryByTestId('mode-fixed')).not.toBeInTheDocument();
  });
});

describe('Operate.svelte — a device_removed event (#516)', () => {
  /** An EventSource that records its listeners, so the test can deliver a named event. */
  class ListeningEventSource {
    static last: ListeningEventSource | null = null;
    listeners: Record<string, (event: MessageEvent<string>) => void> = {};
    onmessage: ((event: MessageEvent<string>) => void) | null = null;
    onerror: ((event: Event) => void) | null = null;
    onopen: ((event: Event) => void) | null = null;
    constructor(public url: string) {
      ListeningEventSource.last = this;
    }
    addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void {
      this.listeners[type] = listener;
    }
    removeEventListener(): void {}
    close(): void {}
    emit(type: string, data: unknown): void {
      this.listeners[type]?.(new MessageEvent(type, { data: JSON.stringify(data) }));
    }
  }

  const DEV0 = { provider_id: 'sim0', device_id: 'dev0', display_name: 'Heater' };
  const DEV1 = { provider_id: 'sim0', device_id: 'dev1', display_name: 'Pump' };

  it('drops the selected device at once, and fetches its capabilities anew when it returns', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.stubGlobal('EventSource', ListeningEventSource as unknown as typeof EventSource);
    const runtime = { devices: [DEV0, DEV1], capabilityReads: [] as string[] };
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const path = pathFromInput(input);
        if (path === '/api/status') return jsonResponse(200, RUNNING);
        if (path === '/v0/runtime/status') return status({ software_safe_state: 'zero', uncovered_actuating_functions: 0 });
        if (path === '/v0/devices') return jsonResponse(200, { devices: runtime.devices });
        const caps = path.match(/^\/v0\/devices\/sim0\/(dev\d)\/capabilities$/);
        if (caps) {
          runtime.capabilityReads.push(caps[1]);
          return jsonResponse(200, { capabilities: { signals: [], functions: [] } });
        }
        return jsonResponse(200, {});
      }),
    );
    render(Operate, { props: { projectName: 'demo', system: createProjectDocument('demo'), runtimeStatus: RUNNING } });

    expect(await screen.findByRole('button', { name: 'Heater (sim0/dev0)' })).toBeInTheDocument();
    await waitFor(() => expect(runtime.capabilityReads).toEqual(['dev0']));
    expect(screen.getByRole('heading', { name: 'Heater (sim0/dev0)' })).toBeInTheDocument();

    // A supervised restart republished the inventory without dev0.
    runtime.devices = [DEV1];
    ListeningEventSource.last?.emit('device_removed', { provider_id: 'sim0', device_id: 'dev0', timestamp_ms: 1 });

    await waitFor(() => expect(screen.queryByRole('button', { name: 'Heater (sim0/dev0)' })).not.toBeInTheDocument());
    expect(screen.getByText('Select a device to inspect signals and execute functions.')).toBeInTheDocument();
    expect(screen.getByText('sim0/dev0 removed')).toBeInTheDocument();

    // A later restart brings dev0 back; the next poll lists and selects it.
    runtime.devices = [DEV0, DEV1];
    await vi.advanceTimersByTimeAsync(5000);

    await waitFor(() => expect(runtime.capabilityReads).toEqual(['dev0', 'dev0']));
    expect(screen.getByRole('heading', { name: 'Heater (sim0/dev0)' })).toBeInTheDocument();
  });

  it('drops a capabilities reply that was in flight when the device was removed', async () => {
    // The reply for dev0's capabilities lands after its device_removed. Filing
    // it would put the removed device's capabilities back, and a returning
    // dev0 would never fetch its own.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.stubGlobal('EventSource', ListeningEventSource as unknown as typeof EventSource);
    const runtime = { devices: [DEV0, DEV1], capabilityReads: [] as string[] };
    const firstCaps = deferred<Response>();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const path = pathFromInput(input);
        if (path === '/api/status') return jsonResponse(200, RUNNING);
        if (path === '/v0/runtime/status') return status({ software_safe_state: 'zero', uncovered_actuating_functions: 0 });
        if (path === '/v0/devices') return jsonResponse(200, { devices: runtime.devices });
        const caps = path.match(/^\/v0\/devices\/sim0\/(dev\d)\/capabilities$/);
        if (caps) {
          runtime.capabilityReads.push(caps[1]);
          if (runtime.capabilityReads.length === 1) return firstCaps.promise;
          return jsonResponse(200, { capabilities: { signals: [], functions: [] } });
        }
        return jsonResponse(200, {});
      }),
    );
    render(Operate, { props: { projectName: 'demo', system: createProjectDocument('demo'), runtimeStatus: RUNNING } });
    await waitFor(() => expect(runtime.capabilityReads).toEqual(['dev0']));

    runtime.devices = [DEV1];
    ListeningEventSource.last?.emit('device_removed', { provider_id: 'sim0', device_id: 'dev0', timestamp_ms: 1 });
    firstCaps.resolve(jsonResponse(200, { capabilities: { signals: [], functions: [] } }));
    await new Promise((resolve) => setTimeout(resolve, 20));

    runtime.devices = [DEV0, DEV1];
    await vi.advanceTimersByTimeAsync(5000);
    await waitFor(() => expect(runtime.capabilityReads).toEqual(['dev0', 'dev0']));
  });
});
