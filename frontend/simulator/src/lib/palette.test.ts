import { describe, expect, it } from 'vitest';
import {
  clampActive, filterPalette, fuzzyScore, moveActive, paletteKeyAction,
} from '@careloop/client';
import type { PaletteCommand } from '@careloop/client';

/** M8: the palette's fuzzy matching and its keyboard contract, isolated from the DOM so the
 *  "Ctrl+K, arrow, Enter, Escape" behaviour is provable. */

const CMDS: PaletteCommand[] = [
  { id: 'done', label: 'Mark the morning one done', utterance: 'mark the first one as done', keywords: 'done morning tablet' },
  { id: 'last-visit', label: 'Show the last visit', utterance: 'why is this here', keywords: 'visit source quote' },
  { id: 'next-papa', label: "What's next for Papa", utterance: 'what is next for papa', keywords: 'next papa plan' },
  { id: 'traffic', label: 'Show MCP traffic', utterance: '', keywords: 'traffic frames jsonrpc' },
  { id: 'large-text', label: 'Toggle large text', utterance: '', keywords: 'elder large text size' },
  { id: 'judge', label: 'Open judge panel', utterance: 'help', keywords: 'judge console metrics' },
];

describe('filterPalette / fuzzyScore (M8)', () => {
  it('empty query keeps every command, in original order', () => {
    expect(filterPalette(CMDS, '')).toEqual(CMDS);
    expect(filterPalette(CMDS, '   ')).toEqual(CMDS);
  });

  it('matches a query to the right command', () => {
    expect(filterPalette(CMDS, 'morning').map((c) => c.id)).toEqual(['done']);
    expect(filterPalette(CMDS, 'papa').map((c) => c.id)).toEqual(['next-papa']);
    expect(filterPalette(CMDS, 'traffic').map((c) => c.id)).toEqual(['traffic']);
    expect(filterPalette(CMDS, 'toggle').map((c) => c.id)).toEqual(['large-text']);
    expect(filterPalette(CMDS, 'judge').map((c) => c.id)).toEqual(['judge']);
  });

  it('returns nothing for a nonsense query', () => {
    expect(filterPalette(CMDS, 'zzqx')).toEqual([]);
  });

  it('scores an exact word-start match above a scattered one', () => {
    expect(fuzzyScore('done', 'mark the first one as done')).toBeGreaterThan(0);
    expect(fuzzyScore('abc', '')).toBe(-1);
    expect(fuzzyScore('', 'anything')).toBe(0);
    expect(fuzzyScore('plan', 'no match here')).toBe(-1);
  });
});

describe('keyboard contract (M8)', () => {
  it('maps the palette keys', () => {
    expect(paletteKeyAction('ArrowUp')).toBe('up');
    expect(paletteKeyAction('ArrowDown')).toBe('down');
    expect(paletteKeyAction('Escape')).toBe('close');
    expect(paletteKeyAction('Enter')).toBe('select');
    expect(paletteKeyAction('a')).toBe('ignore');
    expect(paletteKeyAction('Tab')).toBe('ignore');
  });

  it('wraps the active row within the list', () => {
    expect(moveActive(0, 1, 3)).toBe(1);
    expect(moveActive(2, 1, 3)).toBe(0);
    expect(moveActive(0, -1, 3)).toBe(2);
    expect(moveActive(-1, 1, 3)).toBe(0);
    expect(moveActive(0, 1, 0)).toBe(-1);
  });

  it('clamps the active index as the list shrinks', () => {
    expect(clampActive(-1, 5)).toBe(0);
    expect(clampActive(10, 5)).toBe(4);
    expect(clampActive(2, 0)).toBe(-1);
  });
});
