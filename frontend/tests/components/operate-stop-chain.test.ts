import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import Operate from '../../src/routes/Operate.svelte';
import { createProjectDocument, jsonResponse, pathFromInput } from './helpers';

const RUNNING = { running: true, active_project: 'demo' };
const DEVICES = [
  { provider_id: 'bread0', device_id: 'dcmt0', display_name: 'Drive' },
  { provider_id: 'gpio0', device_id: 'estop', display_name: 'E-stop contact' },
];

function stopChain(state: string, extra: Record<string, unknown> = {}) {
  return {
    state,
    reason: state === 'in_force' ? 'gpio0/estop/chain_open reads true' : 'gpio0/estop/chain_open reads false',
    signal: 'gpio0/estop/chain_open',
    devices: ['bread0/dcmt0'],
    expected_losses: [],
    since_epoch_ms: 1_700_000_000_000,
    ...extra,
  };
}

function status(latched: boolean, chain: Record<string, unknown> | null, extra: Record<string, unknown> = {}) {
  return {
    status: { code: 'OK' },
    version: '0.1.53',
    mode: 'MANUAL',
    uptime_seconds: 1,
    polling_interval_ms: 500,
    providers: [],
    device_count: 2,
    estop: {
      latched,
      software_safe_state: 'hooks',
      latched_at_epoch_ms: latched ? 1_700_000_000_000 : null,
      uncovered_actuating_functions: 0,
    },
    device_loss_latched: [],
    ...(chain ? { stop_chain: chain } : {}),
    ...extra,
  };
}

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

/** Operate for a running `demo`. `runtimeStatus` answers each GET /v0/runtime/status. */
function renderOperate(
  runtimeStatus: () => unknown,
  onClear: () => Response = () => jsonResponse(200, { latched: false }),
) {
  vi.stubGlobal('EventSource', ListeningEventSource as unknown as typeof EventSource);
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = pathFromInput(input);
    if (path === '/api/status') return jsonResponse(200, RUNNING);
    if (path === '/v0/runtime/status') return jsonResponse(200, runtimeStatus());
    if (path === '/v0/devices') return jsonResponse(200, { devices: DEVICES });
    if (path === '/v0/estop/clear' && init?.method === 'POST') return onClear();
    return jsonResponse(200, {});
  });
  vi.stubGlobal('fetch', fetchMock);
  render(Operate, { props: { projectName: 'demo', system: createProjectDocument('demo'), runtimeStatus: RUNNING } });
  return fetchMock;
}

const row = () => screen.getByRole('button', { name: /^SOFTWARE STOP/ }).closest('.estop-row') as HTMLElement;
const deviceItem = (key: string) => screen.getByRole('button', { name: new RegExp(`\\(${key}\\)`) }).closest('li') as HTMLElement;

/** Clear the latch with the two-step clear. */
async function clearLatch(): Promise<void> {
  await fireEvent.click(within(row()).getByRole('button', { name: 'Clear latch…' }));
  await fireEvent.click(within(row()).getByRole('button', { name: 'Confirm clear — re-enables actuation' }));
}

describe('Operate.svelte — the observed emergency stop (#631)', () => {
  it('shows nothing new for a runtime without a stop chain', async () => {
    renderOperate(() => status(true, null, { device_loss_latched: ['bread0/dcmt0'] }));

    await screen.findByText(/^SAFE-STATE ENGAGED at /);
    await screen.findByRole('button', { name: /\(bread0\/dcmt0\)/ });
    expect(screen.queryByTestId('stop-chain-state')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'About the emergency stop' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('device-stopped')).not.toBeInTheDocument();
    expect(screen.getByText(/device bread0\/dcmt0 lost \(latched\)/)).toBeInTheDocument();
  });

  it('in force: says so beside the software stop, as an observed stop, and marks its devices stopped', async () => {
    renderOperate(() =>
      status(true, stopChain('in_force', { expected_losses: ['bread0/dcmt0'] }), {
        device_loss_latched: ['bread0/dcmt0'],
      }),
    );

    const chip = await within(row()).findByTestId('stop-chain-state');
    expect(chip).toHaveTextContent('Emergency stop in force');
    expect(chip).toHaveClass('field-error');
    await fireEvent.click(within(row()).getByRole('button', { name: 'About the emergency stop' }));
    expect(row()).toHaveTextContent('observes the emergency stop through gpio0/estop/chain_open and never commands it');
    expect(row()).toHaveTextContent('Reading: gpio0/estop/chain_open reads true.');

    await waitFor(() => expect(within(deviceItem('bread0/dcmt0')).getByTestId('device-stopped')).toHaveTextContent('STOPPED'));
    expect(within(deviceItem('gpio0/estop')).queryByTestId('device-stopped')).not.toBeInTheDocument();
    expect(screen.getByText(/device bread0\/dcmt0 stopped by the emergency stop/)).toBeInTheDocument();
    expect(screen.queryByText(/device bread0\/dcmt0 lost \(latched\)/)).not.toBeInTheDocument();
  });

  it("in force: the reset is the clear, and the runtime's refusal reason is shown", async () => {
    const reason = 'the emergency stop is in force (gpio0/estop/chain_open reads true); release it first';
    renderOperate(
      () => status(true, stopChain('in_force')),
      () => jsonResponse(409, { status: { code: 'FAILED_PRECONDITION', message: reason } }),
    );
    await within(row()).findByTestId('stop-chain-state');

    await clearLatch();

    expect(await within(row()).findByTestId('estop-feedback')).toHaveTextContent(`Failed to clear latch: ${reason}`);
    expect(screen.getByText(/^SAFE-STATE ENGAGED at /)).toBeInTheDocument();
  });

  it('unknown: warns in the row, and says why the latch is held', async () => {
    renderOperate(() =>
      status(true, stopChain('unknown', { reason: 'gpio0/estop/chain_open is stale (3000 ms old)' })),
    );

    const chip = await within(row()).findByTestId('stop-chain-state');
    expect(chip).toHaveTextContent('⚠ Emergency stop status unknown');
    expect(chip).toHaveClass('field-error');
    await fireEvent.click(within(row()).getByRole('button', { name: 'About the emergency stop' }));
    expect(row()).toHaveTextContent('Reading: gpio0/estop/chain_open is stale (3000 ms old).');
    expect(row()).toHaveTextContent('may be an emergency stop or a power fault');
    expect(screen.queryByTestId('device-stopped')).not.toBeInTheDocument();
  });

  it('released while latched: guides to the clear, which then resets', async () => {
    let latched = true;
    renderOperate(
      () => status(latched, stopChain('clear')),
      () => {
        latched = false;
        return jsonResponse(200, { latched: false });
      },
    );

    expect(await within(row()).findByTestId('stop-chain-state')).toHaveTextContent(
      'Emergency stop released: clear the latch to reset',
    );
    await clearLatch();

    expect(await within(row()).findByTestId('estop-feedback')).toHaveTextContent('Latch cleared. Actuation re-enabled.');
    await waitFor(() => expect(within(row()).getByTestId('stop-chain-state')).toHaveTextContent('Emergency stop clear'));
  });

  it('clear and not latched: a quiet chip, no devices stopped', async () => {
    renderOperate(() => status(false, stopChain('clear')));

    const chip = await within(row()).findByTestId('stop-chain-state');
    expect(chip).toHaveTextContent('Emergency stop clear');
    expect(chip).not.toHaveClass('field-error');
    await screen.findByRole('button', { name: /\(bread0\/dcmt0\)/ });
    expect(screen.queryByTestId('device-stopped')).not.toBeInTheDocument();
  });

  it('re-reads the status on a stop_chain_change event', async () => {
    let chain = stopChain('clear');
    const fetchMock = renderOperate(() => status(false, chain));
    await waitFor(() => expect(within(row()).getByTestId('stop-chain-state')).toHaveTextContent('Emergency stop clear'));

    chain = stopChain('in_force');
    ListeningEventSource.last?.emit('stop_chain_change', {
      previous_state: 'clear',
      new_state: 'in_force',
      reason: 'gpio0/estop/chain_open reads true',
      timestamp_ms: 1_700_000_000_000,
    });

    await waitFor(() => expect(within(row()).getByTestId('stop-chain-state')).toHaveTextContent('Emergency stop in force'));
    expect(fetchMock.mock.calls.filter(([input]) => pathFromInput(input) === '/v0/runtime/status').length).toBe(2);
  });
});
