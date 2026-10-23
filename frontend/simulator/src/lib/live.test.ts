import { describe, expect, it } from 'vitest';
import {
  diffLedgerSnapshot, ledgerValueText, parsePlanEvent, reducePlanEvent,
} from '@careloop/client';
import type { PlanEvent } from '@careloop/client';

/** M7 (SSE live sync) + M6 (time-travel ledger) reducers, kept framework-free. */

describe('parsePlanEvent (M7)', () => {
  it('ignores ping/comment/blank/[DONE]/garbage lines', () => {
    expect(parsePlanEvent(':ping')).toBeNull();
    expect(parsePlanEvent('')).toBeNull();
    expect(parsePlanEvent('   ')).toBeNull();
    expect(parsePlanEvent('data: [DONE]')).toBeNull();
    expect(parsePlanEvent('data: not-json')).toBeNull();
    expect(parsePlanEvent('data: {"type":"other","version":1}')).toBeNull();
  });

  it('reads a plan frame with or without the data: prefix', () => {
    const a = parsePlanEvent('data: {"type":"plan","version":3,"at":"2026-10-21T10:00:00Z"}');
    expect(a).toEqual({ type: 'plan', version: 3, at: '2026-10-21T10:00:00Z' });
    const b = parsePlanEvent('{"type":"plan","version":7,"at":"2026-10-21T11:00:00Z"}');
    expect(b?.version).toBe(7);
  });
});

describe('reducePlanEvent (M7)', () => {
  const ev = (version: number): PlanEvent => ({ type: 'plan', version, at: 'x' });

  it('treats the first event as a baseline (no "another caregiver" notice)', () => {
    expect(reducePlanEvent(null, ev(3))).toEqual({ lastSeen: 3, changed: false });
  });

  it('only bumps when the version actually differs', () => {
    expect(reducePlanEvent(3, ev(3)).changed).toBe(false);
    expect(reducePlanEvent(3, ev(5))).toEqual({ lastSeen: 5, changed: true });
  });
});

describe('diffLedgerSnapshot (M6)', () => {
  it('classifies past vs now into added / changed / stopped', () => {
    const past = [{ ref: 'a', status: 'pending' }, { ref: 'b', status: 'pending' }];
    const now = [{ ref: 'a', status: 'done' }, { ref: 'c', status: 'pending' }];
    expect(diffLedgerSnapshot(past, now)).toEqual({
      added: ['c'],
      changed: ['a'],
      stopped: ['b'],
    });
  });

  it('reports no diff when the snapshot equals now', () => {
    const items = [{ ref: 'a', status: 'pending' }];
    expect(diffLedgerSnapshot(items, items)).toEqual({ added: [], changed: [], stopped: [] });
  });
});

describe('ledgerValueText (M6)', () => {
  it('states the count grammatically and labels the number of items', () => {
    expect(ledgerValueText('2026-10-20T09:00:00Z', 1)).toContain('1 item existed');
    expect(ledgerValueText('2026-10-20T09:00:00Z', 4)).toContain('4 items existed');
    expect(ledgerValueText('', 2)).toContain('2 items existed');
  });
});
