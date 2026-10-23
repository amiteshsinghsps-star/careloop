import { describe, expect, it, vi } from 'vitest';
import { openPlanStream, type PlanVersionInfo } from './planstream';

/** Proves the SSE WIRING (frame -> shared reducer -> refresh/notice/close), not the pure
 *  parsePlanEvent/reducePlanEvent (already covered in live.test.ts). A fake EventSource we
 *  control stands in for the browser, so no DOM is needed in the node test environment. */

class FakeEventSource {
  static readonly CLOSED = 2;
  static last: FakeEventSource;
  readyState = 1; // OPEN
  closeCount = 0;
  url: string;
  private handlers = new Map<string, ((ev: { data: string }) => void)[]>();
  constructor(url: string) { this.url = url; FakeEventSource.last = this; }
  addEventListener(type: string, cb: (ev: { data: string }) => void): void {
    const list = this.handlers.get(type) ?? [];
    list.push(cb);
    this.handlers.set(type, list);
  }
  emit(type: string, data: string): void {
    for (const cb of this.handlers.get(type) ?? []) cb({ data });
  }
  close(): void { this.closeCount += 1; this.readyState = FakeEventSource.CLOSED; }
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('openPlanStream (M7 live sync wiring)', () => {
  it('a plan frame past the baseline bumps the version and refreshes the visible plan', async () => {
    const versions: PlanVersionInfo[] = [];
    const refresh = vi.fn(() => 4);
    const statuses: string[] = [];

    const handle = openPlanStream({
      url: '/api/events',
      source: FakeEventSource as unknown as typeof EventSource,
      refresh,
      onFallback: () => {},
      onStatus: (s) => statuses.push(s),
      onVersion: (info) => versions.push(info),
    });
    const socket = FakeEventSource.last;

    socket.emit('open', '');
    // The hello frame carries the baseline version (no `type:plan`, so the reducer must ignore it).
    socket.emit('hello', '{"version":5}');
    // A real change arrives: the ledger version moved on from the baseline.
    socket.emit('plan', '{"type":"plan","version":6,"at":"2026-10-21T10:00:00Z","verified":true}');
    await flush();

    expect(handle).toBeTruthy();
    expect(socket.url).toBe('/api/events');
    expect(statuses).toContain('connecting');
    expect(statuses).toContain('live');
    expect(refresh).toHaveBeenCalledTimes(1);
    const last = versions[versions.length - 1];
    expect(last).toEqual({ version: 6, at: '2026-10-21T10:00:00Z', verified: true, changed: true, items: 4 });

    handle.close();
  });

  it('treats the first frame as a baseline: no refresh, no "updated" notice', async () => {
    const refresh = vi.fn(() => 9);
    const versions: PlanVersionInfo[] = [];
    const handle = openPlanStream({
      url: '/api/events',
      source: FakeEventSource as unknown as typeof EventSource,
      refresh,
      onFallback: () => {},
      onVersion: (info) => versions.push(info),
    });

    FakeEventSource.last.emit('plan', '{"type":"plan","version":3,"at":"2026-10-21T09:00:00Z"}');
    await flush();

    expect(refresh).not.toHaveBeenCalled();
    expect(versions[0]).toEqual({ version: 3, at: '2026-10-21T09:00:00Z', changed: false });
    handle.close();
  });

  it('ignores malformed / ping / non-plan frames instead of throwing or refreshing', async () => {
    const refresh = vi.fn(() => 1);
    const handle = openPlanStream({
      url: '/api/events',
      source: FakeEventSource as unknown as typeof EventSource,
      refresh,
      onFallback: () => {},
    });
    const socket = FakeEventSource.last;

    expect(() => {
      socket.emit('message', ':ping');
      socket.emit('message', '');
      socket.emit('message', 'data: not-json');
      socket.emit('message', 'data: [DONE]');
      socket.emit('message', 'data: {"type":"other","version":1}');
      socket.emit('hello', 'data: {broken json');
    }).not.toThrow();
    await flush();

    expect(refresh).not.toHaveBeenCalled();
    handle.close();
  });

  it('falls back to polling when the EventSource constructor throws (unsupported/blocked)', () => {
    class Blocked {
      constructor() { throw new Error('EventSource blocked'); }
    }
    const onFallback = vi.fn();
    const statuses: string[] = [];
    const handle = openPlanStream({
      url: '/api/events',
      source: Blocked as unknown as typeof EventSource,
      refresh: () => 0,
      onFallback,
      onStatus: (s) => statuses.push(s),
    });

    expect(onFallback).toHaveBeenCalledTimes(1);
    expect(statuses).toEqual(['polling']);
    expect(() => handle.close()).not.toThrow();
  });

  it('close() tears the socket down so no stream leaks', () => {
    const handle = openPlanStream({
      url: '/api/events',
      source: FakeEventSource as unknown as typeof EventSource,
      refresh: () => 0,
      onFallback: () => {},
    });
    const socket = FakeEventSource.last;
    expect(socket.url).toBe('/api/events');
    handle.close();
    expect(socket.closeCount).toBe(1);
  });
});
