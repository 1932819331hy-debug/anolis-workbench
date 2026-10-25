// operate-events.ts — SSE stream manager and event utilities for Operate workspace

import type { UnknownRecord } from "./contracts";

export const DEFAULT_SSE_EVENT_TYPES = [
  "state_update",
  "quality_change",
  "mode_change",
  "parameter_change",
  "automation_fault",
  "device_removed",
] as const;

export type OperateConnectionState = "connected" | "disconnected" | "reconnecting" | "stale";

export type OperateConnectionStatus = {
  state: OperateConnectionState;
  attempts: number;
  delay_ms?: number;
  idle_ms?: number;
};

export type OperateTraceEvent = {
  type: string;
  timestamp_ms: number;
  details: string;
  payload: UnknownRecord | null;
};

export type OperateEventStreamOptions = {
  url?: string;
  onEvent?: (eventType: string, payload: UnknownRecord) => void;
  onConnectionStatus?: (status: OperateConnectionStatus) => void;
  onParseError?: (error: unknown, context: string) => void;
  eventTypes?: readonly string[];
  reconnectDelayMs?: number;
  maxReconnectDelayMs?: number;
  staleAfterMs?: number;
  staleCheckIntervalMs?: number;
};

export type OperateEventStreamManager = {
  connect: () => void;
  disconnect: () => void;
  getState: () => OperateConnectionState;
};

export function appendEventTrace<T>(buffer: T[], event: T, maxEntries = 100): T[] {
  buffer.push(event);
  if (buffer.length > maxEntries) buffer.splice(0, buffer.length - maxEntries);
  return buffer;
}

export function describeEvent(eventType: string, payload: UnknownRecord | null): string {
  if (!payload || typeof payload !== "object") return eventType;
  if (eventType === "mode_change")
    return `${payload.previous_mode ?? "?"} -> ${payload.new_mode ?? "?"}`;
  if (eventType === "parameter_change") {
    const name = payload.parameter_name ?? payload.name ?? "parameter";
    return `${name}: ${payload.old_value ?? "?"} -> ${payload.new_value ?? "?"}`;
  }
  if (eventType === "automation_fault") {
    const locusRaw = payload.locus ?? payload.node;
    const locus = typeof locusRaw === "string" && locusRaw !== "" ? `${locusRaw}: ` : "";
    return `${locus}${payload.error ?? "Unknown automation fault"}`;
  }
  if (eventType === "device_removed") {
    return `${payload.provider_id ?? "provider"}/${payload.device_id ?? "device"} removed`;
  }
  if (eventType === "state_update") {
    return `${payload.provider_id ?? "provider"}/${payload.device_id ?? "device"} ${payload.signal_id ?? "signal"}`;
  }
  if (eventType === "quality_change") {
    return `${payload.provider_id ?? "provider"}/${payload.device_id ?? "device"} ${payload.signal_id ?? "signal"} -> ${payload.new_quality ?? "UNKNOWN"}`;
  }
  return JSON.stringify(payload);
}

/** Operate's per-device state, keyed by `provider_id/device_id`. */
export type OperateDeviceCache<D, S, C> = {
  devices: D[];
  deviceStates: Record<string, S>;
  capabilities: Record<string, C>;
  selectedKey: string;
};

/**
 * Drop a device a `device_removed` event names (wb#516): from the list, from
 * both caches, and from the selection, so a device that comes back under the
 * same key has its capabilities fetched again. A payload missing either id
 * names no key, so it changes nothing.
 */
export function removeDevice<D extends { provider_id: string; device_id: string }, S, C>(
  cache: OperateDeviceCache<D, S, C>,
  payload: UnknownRecord,
): OperateDeviceCache<D, S, C> {
  const providerId = typeof payload.provider_id === "string" ? payload.provider_id : "";
  const deviceId = typeof payload.device_id === "string" ? payload.device_id : "";
  const key = `${providerId}/${deviceId}`;
  const deviceStates = { ...cache.deviceStates };
  const capabilities = { ...cache.capabilities };
  delete deviceStates[key];
  delete capabilities[key];
  return {
    devices: cache.devices.filter((d) => d.provider_id !== providerId || d.device_id !== deviceId),
    deviceStates,
    capabilities,
    selectedKey: cache.selectedKey === key ? "" : cache.selectedKey,
  };
}

export function buildTraceEvent(
  eventType: string,
  payload: UnknownRecord | null,
  nowMs = Date.now(),
): OperateTraceEvent {
  const tsRaw = Number(payload?.timestamp_ms);
  const timestampMs = Number.isFinite(tsRaw) && tsRaw > 0 ? tsRaw : nowMs;
  return {
    type: eventType,
    timestamp_ms: timestampMs,
    details: describeEvent(eventType, payload),
    payload,
  };
}

export function createOperateEventStreamManager(
  options: OperateEventStreamOptions = {},
): OperateEventStreamManager {
  const {
    url = "/v0/events",
    onEvent = () => {},
    onConnectionStatus = () => {},
    onParseError = () => {},
    eventTypes = DEFAULT_SSE_EVENT_TYPES,
    reconnectDelayMs = 3000,
    maxReconnectDelayMs = 10000,
    staleAfterMs = 30000,
    staleCheckIntervalMs = 5000,
  } = options;

  let active = false;
  let source: EventSource | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let staleTimer: ReturnType<typeof setInterval> | null = null;
  let reconnectAttempts = 0;
  let lastEventAt = 0;
  let currentState: OperateConnectionState = "disconnected";

  function emit(status: OperateConnectionStatus): void {
    currentState = status.state;
    onConnectionStatus(status);
  }

  function clearReconnect(): void {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  function clearStale(): void {
    if (staleTimer !== null) {
      clearInterval(staleTimer);
      staleTimer = null;
    }
  }

  function closeSource(): void {
    if (!source) return;
    source.onopen = null;
    source.onerror = null;
    try {
      source.close();
    } catch {
      // best-effort
    }
    source = null;
  }

  function touchEvent(): void {
    lastEventAt = Date.now();
    if (currentState === "stale") emit({ state: "connected", attempts: reconnectAttempts });
  }

  function startStaleTimer(): void {
    clearStale();
    staleTimer = setInterval(() => {
      if (!active || !source || currentState === "disconnected" || currentState === "reconnecting")
        return;
      const idleMs = Date.now() - lastEventAt;
      if (idleMs > staleAfterMs)
        emit({ state: "stale", attempts: reconnectAttempts, idle_ms: idleMs });
    }, staleCheckIntervalMs);
  }

  function scheduleReconnect(): void {
    if (!active) return;
    reconnectAttempts += 1;
    const delayMs = Math.min(reconnectDelayMs * reconnectAttempts, maxReconnectDelayMs);
    emit({ state: "disconnected", attempts: reconnectAttempts });
    emit({ state: "reconnecting", attempts: reconnectAttempts, delay_ms: delayMs });
    clearReconnect();
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      openSource();
    }, delayMs);
  }

  function openSource(): void {
    if (!active) return;
    clearReconnect();
    clearStale();
    closeSource();
    try {
      source = new EventSource(url);
    } catch (err) {
      onParseError(err, "event_source_init");
      scheduleReconnect();
      return;
    }

    source.onopen = () => {
      reconnectAttempts = 0;
      touchEvent();
      emit({ state: "connected", attempts: 0 });
      startStaleTimer();
    };

    source.onerror = () => {
      clearStale();
      closeSource();
      scheduleReconnect();
    };

    for (const et of eventTypes) {
      source.addEventListener(et, (event: MessageEvent<string>) => {
        try {
          touchEvent();
          const parsed = JSON.parse(event.data) as unknown;
          if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
            throw new Error("Event payload is not an object");
          }
          onEvent(et, parsed as UnknownRecord);
        } catch (err) {
          onParseError(err, et);
        }
      });
    }
  }

  return {
    connect() {
      if (active) return;
      active = true;
      reconnectAttempts = 0;
      openSource();
    },
    disconnect() {
      active = false;
      reconnectAttempts = 0;
      clearReconnect();
      clearStale();
      closeSource();
      emit({ state: "disconnected", attempts: 0 });
    },
    getState() {
      return currentState;
    },
  };
}
