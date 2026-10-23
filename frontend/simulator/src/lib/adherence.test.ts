import { describe, expect, it } from 'vitest';
import { adherenceRows, adherenceRowLabel, adherenceTotal, donePercent } from '@careloop/client';

/** M16: the data that drives the SVG chart is the same data that fills the accessible table. */

describe('adherenceRows (M16)', () => {
  const items = [
    { person: 'Papa', status: 'pending' },
    { person: 'Papa', status: 'done' },
    { person: 'Amma', status: 'pending' },
    { person: 'Papa', status: 'superseded' },
  ];

  it('groups by person in first-appearance order and counts done vs pending', () => {
    expect(adherenceRows(items)).toEqual([
      { person: 'Papa', done: 1, pending: 1, total: 2 },
      { person: 'Amma', done: 0, pending: 1, total: 1 },
    ]);
  });

  it('ignores statuses that are neither done nor pending (superseded/deleted)', () => {
    expect(adherenceRows([{ person: 'X', status: 'deleted' }])).toEqual([
      { person: 'X', done: 0, pending: 0, total: 0 },
    ]);
  });

  it('rolls up a correct household total', () => {
    expect(adherenceTotal(adherenceRows(items))).toEqual({ done: 1, pending: 2, total: 3 });
  });

  it('guards a zero total in the percentage', () => {
    expect(donePercent({ done: 1, total: 2 })).toBe(50);
    expect(donePercent({ done: 0, total: 0 })).toBe(0);
  });

  it('renders a plain-language label matching the table cell (no colour-only meaning)', () => {
    const rows = adherenceRows(items);
    expect(adherenceRowLabel(rows[0])).toBe('Papa: 1 of 2 done, 1 still to do');
    expect(adherenceRowLabel({ person: 'Nobody', done: 0, pending: 0, total: 0 }))
      .toBe('Nobody: nothing outstanding today');
  });
});
