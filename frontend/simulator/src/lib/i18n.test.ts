import { describe, expect, it } from 'vitest';
import {
  A11Y_LAB_PAYLOAD, LANGUAGES, MESSAGES, entryPlaceholders, t,
} from '@careloop/client';
import type { Language, MessageKey } from '@careloop/client';

/** M4: message catalogues cover the CHROME only. Health text never goes through t(), and
 *  every chrome key exists in each language with the same ICU placeholders. */

const EN_KEYS = Object.keys(MESSAGES.en) as MessageKey[];
const collectValues = (table: Record<string, unknown>): string[] => {
  const out: string[] = [];
  for (const v of Object.values(table)) {
    if (typeof v === 'string') out.push(v);
    else if (v && typeof v === 'object') for (const f of Object.values(v as Record<string, string>)) out.push(f);
  }
  return out;
};

describe('catalogue completeness (M4)', () => {
  it('lists en/hi/pa', () => {
    expect(LANGUAGES).toEqual(['en', 'hi', 'pa']);
  });

  it('every en key exists in hi and pa, with no extra keys', () => {
    for (const lang of ['hi', 'pa'] as Language[]) {
      const keys = Object.keys(MESSAGES[lang]);
      for (const key of EN_KEYS) expect(keys, `${lang} missing ${key}`).toContain(key);
      expect(new Set(keys)).toEqual(new Set(EN_KEYS));
    }
  });

  it('keeps the same placeholders per key across languages', () => {
    for (const key of EN_KEYS) {
      const base = entryPlaceholders(MESSAGES.en[key]);
      for (const lang of ['hi', 'pa'] as Language[]) {
        expect(entryPlaceholders(MESSAGES[lang][key]), `${lang}:${key} placeholders`).toEqual(base);
      }
    }
  });
});

describe('no health text is ever looked up through t() (M4)', () => {
  it('chrome values never carry a dose quantity', () => {
    for (const lang of LANGUAGES) {
      for (const value of collectValues(MESSAGES[lang] as Record<string, unknown>)) {
        expect(value).not.toMatch(/\b\d+\s?(mg|mcg|ml|units?)\b/i);
      }
    }
  });

  it('no chrome value equals (or contains) any server health string', () => {
    const healthStrings = (A11Y_LAB_PAYLOAD.items as { text: string }[]).map((i) => i.text);
    const allChrome = LANGUAGES.flatMap((lang) => collectValues(MESSAGES[lang] as Record<string, unknown>));
    for (const health of healthStrings) {
      expect(allChrome.some((c) => c.includes(health) || health.includes(c))).toBe(false);
    }
  });

  it('an unknown key — e.g. a raw health sentence — resolves to "", never echoed back', () => {
    const health = 'Take amlodipine 5 mg every morning';
    expect(t('en', health as MessageKey)).toBe('');
    expect(t('en', 'definitely_not_a_key' as MessageKey)).toBe('');
  });
});

describe('interpolation + plural (M4)', () => {
  it('substitutes a placeholder', () => {
    expect(t('en', 'trafficButton', { count: 5 })).toContain('5 frames');
  });

  it('chooses the singular form for a count of one', () => {
    expect(t('en', 'trafficButton', { count: 1 })).toBe('Show MCP traffic (1 frame)');
  });

  it('resolves the same key in another language', () => {
    expect(t('hi', 'sendButton')).toBe(MESSAGES.hi.sendButton);
    expect(t('pa', 'sendButton')).toBe(MESSAGES.pa.sendButton);
  });
});
