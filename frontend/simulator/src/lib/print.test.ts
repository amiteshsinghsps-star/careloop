import { describe, expect, it } from 'vitest';
import { SHEET_DISCLAIMER, buildCareSheet, canNativeShare } from '@careloop/client';
import type { SheetItem } from '@careloop/client';

/** M17: the printable / shareable plain-text care sheet. It is the authenticated companion
 *  surface, so doses may appear — but internal ids and JSON must never leak. */

const item: SheetItem = {
  person: 'Papa',
  kindLabel: 'Medicine',
  text: 'Take amlodipine 5 mg, one tablet every morning',
  quote: 'I am starting amlodipine 5 mg, one tablet every morning',
  schedule: 'Morning · 1× a day',
  recordedOn: '12 Oct 2026',
  statusLabel: 'To do',
};

const meta = { householdCode: 'CASA01', personFilter: null, generatedOn: '21 Oct 2026' };

describe('buildCareSheet (M17)', () => {
  it('includes the dose text, the clinician quote and the recorded date', () => {
    const sheet = buildCareSheet([item], meta);
    expect(sheet).toContain('amlodipine 5 mg');
    expect(sheet).toContain('I am starting amlodipine');
    expect(sheet).toContain('Recorded: 12 Oct 2026');
    expect(sheet).toContain('household CASA01');
    expect(sheet).toContain('Recorded 21 Oct 2026');
  });

  it('carries the disclaimer footer', () => {
    expect(buildCareSheet([item], meta)).toContain(SHEET_DISCLAIMER);
  });

  it('never contains internal refs or JSON braces', () => {
    const sheet = buildCareSheet([item], meta);
    expect(sheet).not.toMatch(/[{}]/);
    expect(sheet).not.toMatch(/\bref\b/i);
    expect(sheet).not.toContain('"');
  });

  it('handles an empty plan with an honest line, still with the disclaimer', () => {
    const sheet = buildCareSheet([], meta);
    expect(sheet).toContain('No care items');
    expect(sheet).toContain(SHEET_DISCLAIMER);
  });

  it('numbers each item', () => {
    const two = buildCareSheet([item, { ...item, person: 'Amma' }], meta);
    expect(two).toContain('1. Papa');
    expect(two).toContain('2. Amma');
  });
});

describe('canNativeShare (M17)', () => {
  it('detects navigator.share and falls back when missing', () => {
    expect(canNativeShare({ share: () => Promise.resolve() } as unknown as Navigator)).toBe(true);
    expect(canNativeShare({} as unknown as Navigator)).toBe(false);
    expect(canNativeShare(undefined)).toBe(false);
  });
});
