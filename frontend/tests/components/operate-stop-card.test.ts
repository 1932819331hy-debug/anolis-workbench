import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import Operate from '../../src/routes/Operate.svelte';
import { createProjectDocument, deferred, jsonResponse, pathFromInput } from './helpers';

const RUNNING = { running: true, active_project: 'demo' };

function status(estop: Record<string, unknown>, extra: Record<string, unknown> = {}) {
  return {
    status: { code: 'OK' },
    version: '0.1.52',
    mode: 'MANUAL',
    uptime_seconds: 1,
    polling_interval_ms: 500,
    providers: [],
    device_count: 0,
    estop: { latched: false, latched_at_epoch_ms: null, uncovered_actuating_functions: 0, ...estop },
    ...extra,
  };
}

/** Operate for a running `demo`; `runtimeStatus` answers GET /v0/runtime/status. */
function renderOperate(
  runtimeStatus: () => Response | Promise<Response>,
  onEstop: () => Response = () => jsonResponse(200, { latched: true, software_safe_state: 'hooks', actions: [] }),
) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathFromInput(input);
      if (path === '/api/status') return jsonResponse(200, RUNNING);
      if (path === '/v0/runtime/status') return runtimeStatus();
      if (path === '/v0/estop' && init?.method === 'POST') return onEstop();
      return jsonResponse(200, {});
    }),
  );
  render(Operate, { props: { projectName: 'demo', system: createProjectDocument('demo'), runtimeStatus: RUNNING } });
}

/** The stop card's one row. */
const row = () => screen.getByRole('button', { name: /^SOFTWARE STOP/ }).closest('.estop-row') as HTMLElement;
const info = (label: string) => within(row()).getByRole('button', { name: label });

describe('Operate.svelte — the software stop card is one row (#613)', () => {
  it('while loading: the stop, the state, the short disclaimer, and no heading', async () => {
    const pending = deferred<Response>();
    renderOperate(() => pending.promise);

    expect(await screen.findByTestId('estop-state')).toHaveTextContent('Loading safe state…');
    expect(within(row()).getByRole('button', { name: 'SOFTWARE STOP' })).toBeEnabled();
    expect(within(row()).getByText('Not an emergency stop: use the hardware E-stop')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Software Stop' })).not.toBeInTheDocument();
    pending.resolve(jsonResponse(200, status({ software_safe_state: 'hooks' })));
  });

  it('when the runtime never reports it: says not observed, and names the file behind the ⓘ', async () => {
    renderOperate(() => jsonResponse(500, { error: 'runtime unreachable' }));

    await waitFor(() => expect(screen.getByTestId('estop-state')).toHaveTextContent('Safe state not observed'));
    expect(screen.queryByTestId('safe-state-fix')).not.toBeInTheDocument();
    await fireEvent.click(info('About the software stop'));
    expect(screen.getByTestId('safe-state-fix')).toHaveTextContent('safety.safe_state');
  });

  it.each([
    ['hooks', /Latches actuation, then runs the declared safe-state hooks/],
    ['setpoints', /Latches actuation, then drives the declared safe setpoints/],
    ['zero', /latches actuation, then tries to drive every actuating function to zero/],
  ])('under %s: Ready in the row, the rung behind its ⓘ', async (rung, note) => {
    renderOperate(() => jsonResponse(200, status({ software_safe_state: rung })));

    await waitFor(() => expect(screen.getByTestId('estop-state')).toHaveTextContent('Ready'));
    expect(screen.queryByText(note)).not.toBeInTheDocument();
    await fireEvent.click(info('About the software stop'));
    expect(within(row()).getByText(note)).toBeInTheDocument();
    expect(within(row()).getByTestId('safe-state-fix')).toBeInTheDocument();
  });

  it('under none: the error is in the row without opening anything', async () => {
    renderOperate(() => jsonResponse(200, status({ software_safe_state: 'none', uncovered_actuating_functions: 3 })));

    const state = await screen.findByText('No safe state declared: latch only');
    expect(state).toHaveClass('field-error');
    expect(within(row()).getByRole('button', { name: 'SOFTWARE STOP (latch only)' })).toBeInTheDocument();
    expect(screen.queryByTestId('estop-coverage')).not.toBeInTheDocument();
  });

  it('names uncovered functions in a chip, with the count as the software stop, not the e-stop', async () => {
    renderOperate(() => jsonResponse(200, status({ software_safe_state: 'hooks', uncovered_actuating_functions: 2 })));

    expect(await screen.findByTestId('estop-coverage')).toHaveTextContent('⚠ 2 not covered');
    await fireEvent.click(info('About uncovered functions'));
    expect(within(row()).getByText('2 actuating function(s) the software stop will not drive.')).toBeInTheDocument();
    expect(row().textContent).not.toMatch(/e-stop/);
  });

  it('latched: the same row turns red, keeps the stop, and offers the clear', async () => {
    renderOperate(() =>
      jsonResponse(200, status({ software_safe_state: 'hooks', latched: true, latched_at_epoch_ms: 1_700_000_000_000 })),
    );

    const state = await screen.findByText(/^SAFE-STATE ENGAGED at /);
    expect(row()).toHaveClass('estop-row-latched');
    expect(row()).toContainElement(state);
    expect(within(row()).getByRole('button', { name: 'SOFTWARE STOP' })).toBeEnabled();
    expect(within(row()).getByRole('button', { name: 'Clear latch…' })).toBeInTheDocument();
  });

  it("shows a failed press's feedback in the row", async () => {
    renderOperate(
      () => jsonResponse(200, status({ software_safe_state: 'hooks' })),
      () => jsonResponse(503, { status: { code: 'UNAVAILABLE', message: 'Runtime is not running' } }),
    );
    await waitFor(() => expect(screen.getByTestId('estop-state')).toHaveTextContent('Ready'));

    await fireEvent.click(within(row()).getByRole('button', { name: 'SOFTWARE STOP' }));

    expect(await within(row()).findByTestId('estop-feedback')).toHaveClass('field-error');
  });

  it('shows the startup safe state in the row only when it was not achieved', async () => {
    const startup = (achieved: boolean) => ({
      startup_safe_state: {
        on_startup: 'safe_state',
        ran: true,
        software_safe_state: 'hooks',
        safe_state_achieved: achieved,
        failed_actions: achieved ? [] : [{ device_handle: 'p0/heater', function: 'set_power', success: false, error: 'timeout' }],
      },
    });
    renderOperate(() => jsonResponse(200, status({ software_safe_state: 'hooks' }, startup(false))));

    expect(await screen.findByTestId('estop-startup')).toHaveTextContent('⚠ Startup safe state not achieved');
    expect(screen.getByTestId('startup-safe-state')).toHaveTextContent('1 call(s) failed: p0/heater/set_power: timeout');
  });

  it('keeps an achieved startup safe state out of the row, in Runtime Status', async () => {
    renderOperate(() =>
      jsonResponse(
        200,
        status(
          { software_safe_state: 'hooks' },
          {
            startup_safe_state: {
              on_startup: 'safe_state',
              ran: true,
              software_safe_state: 'hooks',
              safe_state_achieved: true,
              failed_actions: [],
            },
          },
        ),
      ),
    );

    expect(await screen.findByTestId('startup-safe-state')).toHaveTextContent(/every call it issued was accepted/);
    expect(row()).not.toContainElement(screen.getByTestId('startup-safe-state'));
    expect(screen.queryByTestId('estop-startup')).not.toBeInTheDocument();
  });

  it('opens a detail from the keyboard and closes it with Escape', async () => {
    renderOperate(() => jsonResponse(200, status({ software_safe_state: 'hooks' })));
    await waitFor(() => expect(screen.getByTestId('estop-state')).toHaveTextContent('Ready'));
    const toggle = info('Why this is not an emergency stop');

    // A native button: in the tab order, and a browser activates it with
    // Enter or Space by dispatching click. jsdom does not synthesize that
    // click from a key press, so the test dispatches it.
    expect(toggle.tagName).toBe('BUTTON');
    expect(toggle).toHaveAttribute('type', 'button');
    toggle.focus();
    expect(toggle).toHaveFocus();
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const panel = document.getElementById(toggle.getAttribute('aria-controls') ?? '');
    expect(panel).toHaveTextContent(/isolate power to the machine/);

    await fireEvent.keyDown(toggle, { key: 'Escape' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText(/isolate power to the machine/)).not.toBeInTheDocument();
  });
});
