import type { MaskedItem } from './types';

export interface Intent {
  tool: string;
  args: Record<string, unknown>;
  /** Shown in the simulator so the reviewer can see how the phrase was understood. */
  read: string;
}

const ORDINAL_WORDS: Record<string, string> = {
  first: 'first', 1: 'first', '1st': 'first', one: 'first',
  second: 'second', 2: 'second', '2nd': 'second', two: 'second',
  third: 'third', 3: 'third', '3rd': 'third', three: 'third',
  fourth: 'fourth', 4: 'fourth', '4th': 'fourth',
  fifth: 'fifth', 5: 'fifth', '5th': 'fifth',
};

const DAY_HINTS = ['morning', 'after food', 'before food', 'midday', 'afternoon', 'evening',
  'bedtime', 'night', 'noon'];

/**
 * The simulator's intent router. Alexa+ normally does this with its own model; here a
 * deterministic matcher plays that role so the demo is reproducible offline. It only
 * chooses a tool and passes through what the speaker said — it never generates health text.
 */
export function route(utterance: string, people: string[] = []): Intent {
  const u = ` ${utterance.toLowerCase().replace(/[?!.,]/g, ' ').replace(/\s+/g, ' ').trim()} `;
  const person = findPerson(u, people);
  const ref = findRef(u);
  const who = person ?? undefined;

  if (/\b(help|what can (you|this) do|capabilities|how do you work|what do you do)\b/.test(u)) {
    return { tool: 'about_careloop', args: {}, read: 'You asked what CareLoop can do.' };
  }
  if (/\b(who|whos|who is|who's)\b.*\b(in|family|household|list)\b/.test(u) || /\bpeople\b|\bhousehold members\b/.test(u)) {
    return { tool: 'list_people', args: {}, read: 'You asked who is in this household.' };
  }
  if (/\b(what|show|tell).*(today|schedule|agenda)\b/.test(u) || /\btoday'?s plan\b/.test(u)) {
    return { tool: 'todays_plan', args: who ? { person: who } : {}, read: `Today's plan${who ? ` for ${who}` : ''}.` };
  }
  if (/\b(what changed|changed since|since (the )?last|new since|different since)\b/.test(u)) {
    return { tool: 'what_changed', args: who ? { person: who } : {}, read: `Comparing with the earlier visit${who ? ` for ${who}` : ''}.` };
  }
  if (/\b(prepare|questions|what to ask|next visit)\b/.test(u)) {
    return { tool: 'prepare_next_visit', args: who ? { person: who } : {}, read: 'Questions to take to the next visit.' };
  }
  if (/\b(how much|progress|digest|done so far|how am i doing)\b/.test(u)) {
    return { tool: 'daily_digest', args: who ? { person: who } : {}, read: `Today's progress${who ? ` for ${who}` : ''}.` };
  }
  if (/\b(mark|record|log|i (took|did|finished)|set)\b/.test(u) && /\b(done|finished|taken|complete)\b/.test(u)) {
    return { tool: 'mark_done', args: { ref: ref ?? 'first', ...(who ? { who } : {}) }, read: `Marking ${ref ?? 'the first'} item done.` };
  }
  if (/\bconfirm\b/.test(u)) {
    return { tool: 'confirm_instruction', args: { ref: ref ?? 'first' }, read: 'Recording that you confirmed it with the clinician.' };
  }
  if (/\b(delete|remove|cancel|forget)\b/.test(u)) {
    return { tool: 'delete_instruction', args: { ref: ref ?? 'first' }, read: 'Deleting needs one confirmation first.' };
  }
  if (/\bwhy\b/.test(u) || /\b(source|proof|recorded|clinician said)\b/.test(u)) {
    return { tool: 'explain_instruction', args: { ref: ref ?? 'first' }, read: `Showing the clinician's own words for ${ref ?? 'the first'} item.` };
  }
  if (/^\s*(add|note|remember|also said)\b/.test(u.trim())) {
    const text = utterance.replace(/^\s*(add|note|remember|also said)\s*(that|:|-)?\s*/i, '').trim();
    return { tool: 'add_note', args: { text, ...(who ? { person: who } : {}) }, read: 'Adding your note to the plan.' };
  }
  const args: Record<string, unknown> = {};
  if (who) args.person = who;
  if (/\bavoid|should not|don'?t\b/.test(u)) args.kind = 'avoid';
  if (/\bmedicine|medication|tablet|pill|dose\b/.test(u)) args.kind = 'medication';
  if (/\bcome back|appointment|follow[- ]?up|return\b/.test(u)) args.kind = 'appointment';
  return { tool: 'get_care_plan', args, read: `Care plan${who ? ` for ${who}` : ''}${args.kind ? ` — ${args.kind} only` : ''}.` };
}

function findRef(u: string): string | null {
  const m = u.match(/\bnumber\s+([1-5])\b/);
  if (m) return ORDINAL_WORDS[m[1]];
  // Rank words in rank order, and before the bare number words: "the third one" is item three,
  // and the filler word "one" must not read it as the first item. Same rule the voice device
  // applies (scripts/alexa_device.py _find_ordinal), so a phrase means one thing on both surfaces.
  for (const word of ['first', 'second', 'third', 'fourth', 'fifth',
                      '1st', '2nd', '3rd', '4th', '5th']) {
    if (new RegExp(`\\b${word}\\b`).test(u)) return ORDINAL_WORDS[word];
  }
  for (const word of ['one', 'two', 'three', 'four', 'five']) {
    if (new RegExp(`\\b${word}\\b`).test(u)) return ORDINAL_WORDS[word];
  }
  return null;
}

const RELATIONS: Record<string, string> = {
  papa: 'Papa', dad: 'Father', father: 'Father', mummy: 'Mummy', mom: 'Mother',
  mother: 'Mother', grandma: 'Grandmother', grandmother: 'Grandmother',
  grandpa: 'Grandfather', grandfather: 'Grandfather', wife: 'Spouse', husband: 'Spouse',
};

function findPerson(u: string, people: string[]): string | null {
  for (const p of people) {
    if (u.includes(` ${p.toLowerCase()} `)) return p;
  }
  for (const [word, mapped] of Object.entries(RELATIONS)) {
    if (new RegExp(`\\b${word}\\b`).test(u)) {
      const hit = people.find((p) => p.toLowerCase() === mapped.toLowerCase());
      if (hit) return hit;
    }
  }
  return null;
}

/** Which of the returned items a spoken phrase points at, for the card highlight.
 *  Ordinal words are resolved against the list, then day-part words, then kind words. */
export function highlightIndex(utterance: string, items: MaskedItem[]): number {
  const u = ` ${utterance.toLowerCase()} `;
  const rank: Record<string, number> = { first: 0, second: 1, third: 2, fourth: 3, fifth: 4 };
  const ord = findRef(u);
  if (ord && rank[ord] !== undefined && rank[ord] < items.length) return rank[ord];
  const part = DAY_HINTS.find((h) => u.includes(h));
  if (part) {
    const byPart = items.findIndex((i) => (i.schedule || '').toLowerCase().includes(part)
      || (part === 'night' && /bedtime|night/i.test(i.schedule || '')));
    if (byPart >= 0) return byPart;
  }
  const kind = ['medicine', 'tablet', 'pill'].some((w) => u.includes(w)) ? 'medication'
    : /avoid|don'?t|should not/.test(u) ? 'avoid'
    : /come back|appointment|follow/.test(u) ? 'appointment' : null;
  if (kind) {
    const byKind = items.findIndex((i) => i.kind === kind);
    if (byKind >= 0) return byKind;
  }
  return items.length === 1 ? 0 : -1;
}
