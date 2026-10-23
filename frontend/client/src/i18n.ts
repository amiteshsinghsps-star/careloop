import en from './messages/en.json';
import hi from './messages/hi.json';
import pa from './messages/pa.json';

/** Message catalogues for the simulator/companion CHROME only (buttons, labels, status
 *  lines). Health content NEVER comes through here — it is always the server's verbatim
 *  strings on the `ResultView`. `translate` is typed to a closed key union so a raw health
 *  sentence cannot even be passed to it, and at runtime an unknown key yields "" (never the
 *  input echoed back). That is the "no health text is looked up through t()" guarantee. */

export const MESSAGES = { en, hi, pa } as const;
export type Language = keyof typeof MESSAGES;
export const LANGUAGES: Language[] = ['en', 'hi', 'pa'];

export type MessageKey = keyof typeof en;

type MessageEntry = string | { one: string; other: string };
export type TParams = Record<string, string | number>;

const PLACEHOLDER_RE = /\{(\w+)\}/g;

/** Every `{token}` in one entry, for the parity test. */
export function entryPlaceholders(entry: MessageEntry | undefined): string[] {
  if (entry === undefined) return [];
  const texts = typeof entry === 'string' ? [entry] : [entry.one, entry.other];
  const out: string[] = [];
  for (const text of texts) {
    for (const match of text.matchAll(PLACEHOLDER_RE)) out.push(match[1]);
  }
  return [...new Set(out)].sort();
}

/** Pick the plural form for a count. English/Devanagari/Gurmukhi here all use one-vs-other. */
function pickForm(entry: { one: string; other: string }, params: TParams): string {
  const raw = params.count;
  const count = typeof raw === 'number' ? raw : Number(raw);
  return count === 1 ? entry.one : entry.other;
}

export function translate(lang: Language, key: MessageKey, params: TParams = {}): string {
  const table = (MESSAGES[lang] ?? MESSAGES.en) as unknown as Record<string, MessageEntry>;
  const fallback = MESSAGES.en as unknown as Record<string, MessageEntry>;
  const entry = table[key] ?? fallback[key];
  if (entry === undefined) return ''; // unknown / health text → nothing, never the raw input
  const template = typeof entry === 'string' ? entry : pickForm(entry, params);
  return template.replace(PLACEHOLDER_RE, (whole, name: string) =>
    name in params ? String(params[name]) : whole);
}

/** Convenience alias matching the requirement's `t()` naming. */
export const t = translate;
