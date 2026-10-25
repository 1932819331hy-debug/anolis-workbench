import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

import Operate from '../../src/routes/Operate.svelte';
import { createProjectDocument, jsonResponse, pathFromInput } from './helpers';

const RUNNING = { running: true, active_project: 'demo' };
const DEVICES = { devices: [{ provider_id: 'bread0', device_id: 'dcmt0', display_name: 'Motor' }] };
const CAPS = {
  capabilities: {
    signals: [],
    functions: [
      // As the runtime emits them: `actuating` is true unless the category is
      // READ, so CONFIG (bread's setpoints) is actuating too.
      {
        function_id: 1,
        name: 'set_open_loop',
        category: 'ACTUATE',
        actuating: true,
        args: { motor1_pwm: { type: 'int64', required: true } },
      },
      {
        function_id: 2,
        name: 'set_mode',
        category: 'CONFIG',
        actuating: true,
        args: { mode: { type: 'string', required: true } },
      },
      {
        function_id: 3,
        name: 'set_setpoint',
        category: 'CONFIG',
        actuating: true,
        args: { motor1_target: { type: 'int64', required: true } },
      },
      {
        function_id: 4,
        name: 'set_trim',
        args: { trim: { type: 'int64', required: true } },
      },
      {
        function_id: 5,
        name: 'read_counter',
        category: 'READ',
        actuating: false,
        args: { channel: { type: 'int64', required: true } },
      },
    ],
  },
};

function statusBody(latched: boolean) {
  return {
    status: { code: 'OK' },
    version: '0.1.51',
    mode: 'MANUAL',
    providers: [],
    device_count: 1,
    estop: { latched, software_safe_state: 'zero', latched_at_epoch_ms: latched ? 1 : null, uncovered_actuating_functions: 0 },
  };
}

/** Operate for a running `demo` with one DCMT; `onCall` answers POST /v0/call. */
function renderOperate(onCall: () => Response) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathFromInput(input);
      if (path === '/api/status') return jsonResponse(200, RUNNING);
      if (path === '/v0/devices') return jsonResponse(200, DEVICES);
      if (path === '/v0/devices/bread0/dcmt0/capabilities') return jsonResponse(200, CAPS);
      if (path === '/v0/runtime/status') return jsonResponse(200, statusBody(false));
      if (path === '/v0/call' && init?.method === 'POST') return onCall();
      if (path === '/v0/estop' && init?.method === 'POST')
        return jsonResponse(200, { latched: true, software_safe_state: 'zero', actions: [] });
      return jsonResponse(200, {});
    }),
  );
  render(Operate, { props: { projectName: 'demo', system: createProjectDocument('demo'), runtimeStatus: RUNNING } });
}

const executeButtons = () => screen.getAllByRole('button', { name: 'Execute' });

describe('Operate.svelte — function calls (#580)', () => {
  it("shows the runtime's message when a call fails", async () => {
    renderOperate(() =>
      jsonResponse(409, { status: { code: 'FAILED_PRECONDITION', message: 'Actuation latched by e-stop' } }),
    );
    await fireEvent.input(await screen.findByLabelText(/motor1_pwm/), { target: { value: '60' } });
    await fireEvent.click(executeButtons()[0]);

    expect(await screen.findByText('Error: Actuation latched by e-stop')).toBeInTheDocument();
    expect(screen.queryByText(/HTTP 409/)).not.toBeInTheDocument();
  });

  it('keeps a successful call\'s result next to its form', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderOperate(() => jsonResponse(200, { status: { code: 'OK' } }));
    await fireEvent.input(await screen.findByLabelText(/motor1_pwm/), { target: { value: '60' } });
    await fireEvent.click(executeButtons()[0]);

    expect(await screen.findByText(/^Accepted at /)).toBeInTheDocument();
    await vi.advanceTimersByTimeAsync(5000);
    expect(screen.getByText(/^Accepted at /)).toBeInTheDocument();
  });

  it('on a software stop, clears every form but a READ one, setpoints included', async () => {
    renderOperate(() => jsonResponse(200, { status: { code: 'OK' } }));
    const field = (name: RegExp) => screen.getByLabelText(name) as HTMLInputElement;
    await screen.findByLabelText(/motor1_pwm/);
    await fireEvent.input(field(/motor1_pwm/), { target: { value: '60' } });
    await fireEvent.input(field(/^mode/), { target: { value: 'open_loop' } });
    await fireEvent.input(field(/motor1_target/), { target: { value: '400' } });
    await fireEvent.input(field(/trim/), { target: { value: '5' } });
    await fireEvent.input(field(/channel/), { target: { value: '2' } });

    await fireEvent.click(screen.getByRole('button', { name: 'SOFTWARE STOP' }));

    await waitFor(() => expect(field(/motor1_pwm/).value).toBe(''));
    expect(field(/motor1_target/).value).toBe('');
    expect(field(/^mode/).value).toBe('');
    expect(field(/trim/).value).toBe('');
    expect(field(/channel/).value).toBe('2');
  });
});
