/** Live plan sync wiring: open the server's Server-Sent Events stream
 *  (`GET /api/events`, built by `CareLoopApi.eventsUrl()`), feed every frame through the
 *  shared `parsePlanEvent`/`reducePlanEvent` reducer (which stays the single source of truth)
 *  and, when it reports a real change, ask the caller to refresh the masked plan.
 *
 *  Deliberately framework-free (no runes, no DOM) so it is both the app wiring and unit-
 *  testable in a plain node environment with a controllable fake `EventSource`. */
import { parsePlanEvent, reducePlanEvent } from '@careloop/client';

/** Honest connection state shown to the caregiver. */
export type StreamStatus = 'connecting' | 'live' | 'polling' | 'stalled';

export interface PlanVersionInfo {
  /** The version we now consider current (from the server frame). */
  version: number;
  /** Server ISO timestamp of the change (a field that came from the server). */
  at: string;
  /** False means the audit chain itself no longer verifies — surface it, never hide it. */
  verified?: boolean;
  /** True when this frame means another caregiver changed the plan (not the first sync). */
  changed: boolean;
  /** Visible masked item count, resolved only when `changed` (the refresh result). */
  items?: number;
}

export interface PlanStreamDeps {
  /** Absolute-enough URL from `CareLoopApi.eventsUrl()`. */
  url: string;
  /** `EventSource` constructor; defaults to the global, overridable for tests or an
   *  environment where the browser blocks it. */
  source?: typeof EventSource;
  /** Runs when the reducer reports a real change; refreshes the masked plan and resolves to
   *  the new visible item count. */
  refresh: () => number | Promise<number>;
  /** Runs when `EventSource` is unsupported or throws on construction — start polling instead
   *  of crashing the app. */
  onFallback: () => void;
  /** Connection state changes (connecting/live/polling/stalled). */
  onStatus?: (status: StreamStatus) => void;
  /** Every valid plan frame (baseline and change) so the indicator can show version/time. */
  onVersion?: (info: PlanVersionInfo) => void;
}

export interface PlanStreamHandle {
  /** Close the socket and stop listening — safe to call more than once. */
  close: () => void;
}

/** Parse the `hello` frame the server sends on connect: `data: {"version": N}`. Any other
 *  payload (or garbage) yields null so we simply keep waiting for a real change. */
function helloVersion(raw: string): number | null {
  const line = (raw ?? '').trim();
  if (!line || line.startsWith(':')) return null;
  const json = line.startsWith('data:') ? line.slice(5).trim() : line;
  if (!json) return null;
  try {
    const o = JSON.parse(json) as { version?: unknown };
    return Number.isFinite(Number(o?.version)) ? Number(o.version) : null;
  } catch {
    return null;
  }
}

export function openPlanStream(deps: PlanStreamDeps): PlanStreamHandle {
  const Ctor = deps.source ?? (typeof EventSource !== 'undefined' ? EventSource : undefined);
  if (!Ctor) {                       // no EventSource at all — degrade to polling
    deps.onStatus?.('polling');
    deps.onFallback();
    return { close() { /* nothing to close */ } };
  }

  let es: EventSource;
  try {
    es = new Ctor(deps.url);
  } catch {
    // Construction threw (unsupported/blocked). Fall back rather than letting it bubble.
    deps.onStatus?.('polling');
    deps.onFallback();
    return { close() { /* nothing to close */ } };
  }

  deps.onStatus?.('connecting');

  // The reducer accumulator lives here: the first frame after connect is the baseline.
  let lastSeen: number | null = null;
  let closed = false;

  const listen = (type: string, onData: (data: string) => void): void => {
    // A minimal/foreign EventSource may not implement addEventListener — guard, don't crash.
    if (typeof es.addEventListener !== 'function') return;
    es.addEventListener(type, ((event: { data?: unknown }) => {
      if (closed) return;
      onData(typeof event?.data === 'string' ? event.data : '');
    }) as EventListener);
  };

  listen('open', () => { if (!closed) deps.onStatus?.('live'); });
  listen('error', () => {
    if (closed) return;
    // EventSource retries by itself; only when it is gone for good do we fall back.
    if (es.readyState === 2 /* EventSource.CLOSED */) {
      deps.onStatus?.('polling');
      deps.onFallback();
    } else {
      deps.onStatus?.('stalled');
    }
  });

  // `hello` seeds the baseline version so the next differing frame reads as a real change.
  listen('hello', (raw) => {
    const v = helloVersion(raw);
    if (v !== null) lastSeen = v;
  });

  const handle = (raw: string): void => {
    if (closed) return;
    const ev = parsePlanEvent(raw);   // ignores `:ping`, `hello` (no type:'plan'), garbage
    if (!ev) return;
    const bump = reducePlanEvent(lastSeen, ev);
    lastSeen = bump.lastSeen;
    if (!bump.changed) {              // baseline sync: record it, but do not say "updated"
      deps.onVersion?.({ version: bump.lastSeen, at: ev.at, verified: ev.verified, changed: false });
      return;
    }
    void Promise.resolve(deps.refresh())
      .then((items) => {
        if (closed) return;
        deps.onStatus?.('live');
        deps.onVersion?.({ version: bump.lastSeen, at: ev.at, verified: ev.verified, changed: true, items });
      })
      .catch(() => {
        // A failed refresh must not take the stream down; the poller will catch up.
        if (!closed) deps.onVersion?.({ version: bump.lastSeen, at: ev.at, verified: ev.verified, changed: true });
      });
  };

  listen('plan', handle);
  listen('message', handle);          // unnamed `data:` frames, for servers that omit the name

  return {
    close() {
      closed = true;
      try {
        es.close();
      } catch {
        /* already closed */
      }
    },
  };
}
