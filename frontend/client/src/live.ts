/** Live multi-caregiver sync (SSE) and the time-travel ledger reducer.
 *
 *  Both endpoints are owned by the server and may be missing on an older build, so every
 *  helper here is defensive: it degrades to a no-op / empty result rather than throwing,
 *  and the UI shows an honest one-line note instead of a hung spinner. */
import type { LedgerHistoryRow, LedgerSnapshot } from './types';

export type { LedgerHistoryRow, LedgerSnapshot };

/** One `data:` line from `GET /api/events` — a plan changed in this household. */
export interface PlanEvent {
  type: 'plan';
  version: number;
  at: string;
  /** False means the audit chain itself no longer verifies: say so, do not hide it. */
  verified?: boolean;
}

/** Parse one SSE line. Comment/ping lines (`:ping`), the `event:`/`id:`/`retry:` fields
 *  and any non-plan payload return null so the caller can simply ignore them. */
export function parsePlanEvent(raw: string): PlanEvent | null {
  const line = (raw ?? '').trim();
  if (!line || line.startsWith(':')) return null; // blank or `:ping`
  const json = line.startsWith('data:') ? line.slice(5).trim() : line;
  if (!json || json === '[DONE]') return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(json);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== 'object') return null;
  const o = parsed as Record<string, unknown>;
  if (o.type !== 'plan' || !Number.isFinite(Number(o.version))) return null;
  return {
    type: 'plan',
    version: Number(o.version),
    at: String(o.at ?? ''),
    verified: typeof o.verified === 'boolean' ? o.verified : undefined,
  };
}

export interface PlanBump {
  /** The version we now consider current. */
  lastSeen: number;
  /** True when this event means another caregiver changed the plan (not the first sync). */
  changed: boolean;
}

/** Reducer behind M7. The first event seen after connect is the baseline (changed=false);
 *  a later event whose version differs bumps the plan and raises the polite notice. */
export function reducePlanEvent(lastSeen: number | null, ev: PlanEvent): PlanBump {
  if (lastSeen === null) return { lastSeen: ev.version, changed: false };
  return { lastSeen: ev.version, changed: ev.version !== lastSeen };
}

export interface LedgerDiff {
  /** present now but not in the past snapshot — added after that moment. */
  added: string[];
  /** present in both but its status changed later. */
  changed: string[];
  /** present in the past snapshot but gone now — stopped after that moment. */
  stopped: string[];
}

type ComparableItem = { ref: string; status: string };

/** Classify the past snapshot against the live plan so the scrubber can highlight what
 *  new/changed/stopped happened after the scrubbed-to moment. Order of `refs` is stable. */
export function diffLedgerSnapshot(past: ComparableItem[], now: ComparableItem[]): LedgerDiff {
  const p = new Map(past.map((i) => [i.ref, i.status]));
  const n = new Map(now.map((i) => [i.ref, i.status]));
  const added: string[] = [];
  const changed: string[] = [];
  const stopped: string[] = [];
  for (const ref of n.keys()) if (!p.has(ref)) added.push(ref);
  for (const [ref, status] of p) {
    if (!n.has(ref)) stopped.push(ref);
    else if (n.get(ref) !== status) changed.push(ref);
  }
  return { added, changed, stopped };
}

/** Human `aria-valuetext` for the scrubber slider: the date + how many items existed. */
export function ledgerValueText(asOf: string, itemCount: number): string {
  const when = asOf ? new Date(asOf) : null;
  const stamp = when && !Number.isNaN(when.getTime())
    ? when.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    : 'that moment';
  const items = itemCount === 1 ? '1 item existed' : `${itemCount} items existed`;
  return `${stamp} · ${items}`;
}
