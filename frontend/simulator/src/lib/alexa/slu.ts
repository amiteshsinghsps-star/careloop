/** The simulated ear. It resolves a spoken phrase against the skill's own interaction model —
 *  the 11 intents, their samples and the four slot types in `alexa/interaction-model.json` —
 *  and produces the slot block a device would send, including entity resolution.
 *
 *  This is a stand-in for Amazon's language understanding, running side by side with the
 *  deterministic `route()` in frontend/client/src/utterance.ts. Both are shown to the user, and
 *  when they disagree the Device Lab says so rather than quietly preferring one.
 */
import type { Slot } from 'ask-sdk-model';
import type { AskIntentName } from './interactionModel';
import { INTERACTION_MODEL } from './interactionModel';
import { buildSlot, declaredRouting, declaredSlots, unresolvedSlot } from './envelope';

export type BuiltInName =
  | 'AMAZON.YesIntent' | 'AMAZON.NoIntent' | 'AMAZON.StopIntent'
  | 'AMAZON.CancelIntent' | 'AMAZON.HelpIntent';

export interface SluSlot {
  name: string;
  /** What the customer actually said for this slot. */
  spoken: string;
  /** The canonical value the slot type resolved to, or null on a miss. */
  resolved: string | null;
  /** The model's own id for it — what the tool argument receives. */
  id: string | null;
  state: 'success' | 'resolved' | 'no-match' | 'wildcard';
  /** 0…1. A deterministic score from this matcher, not a model confidence. */
  confidence: number;
}

export interface SluResult {
  utterance: string;
  normalized: string;
  intent: AskIntentName | null;
  builtIn: BuiltInName | null;
  /** The sample that fired, so the UI can show which one and why. */
  matchedSample: string | null;
  /** How it fired: the literal sample pattern, the scoring fallback, or not at all. The Device Lab
   *  shows this, because a generalised guess and an exact sample are not the same event. */
  matched: 'literal' | 'fuzzy' | 'none';
  slots: SluSlot[];
  /** Slots keyed the way `ask-sdk-model` wants them, ready for an IntentRequest. */
  askSlots: Record<string, Slot>;
  /** The MCP tool the model routes this intent at, plus the argument mapping. */
  tool: string | null;
  confidence: number;
  reason: string;
}

type SlotTypeDef = (typeof INTERACTION_MODEL.languageModel.types)[number];

const TYPES: Record<string, SlotTypeDef> = Object.fromEntries(
  INTERACTION_MODEL.languageModel.types.map((t) => [t.name, t]),
);

const BUILT_INS: Array<{ name: BuiltInName; phrases: string[] }> = [
  { name: 'AMAZON.YesIntent', phrases: ['yes', 'yeah', 'yep', 'sure', 'please do', 'do it', 'go ahead', 'correct'] },
  { name: 'AMAZON.NoIntent', phrases: ['no', 'nope', 'dont', 'do not', 'no thanks', 'keep it', 'no keep it', 'cancel that'] },
  { name: 'AMAZON.StopIntent', phrases: ['stop', 'stop that', 'be quiet', 'shut up'] },
  { name: 'AMAZON.CancelIntent', phrases: ['cancel', 'never mind', 'forget it', 'back out'] },
  // Only the bare platform words. A question *about this skill* ("how does this work", "what do you
  // do") is AboutCareLoopIntent’s own sample and must not be stolen by the platform built-in.
  { name: 'AMAZON.HelpIntent', phrases: ['help', 'what can i ask'] },
];

/** Lower-case, drop punctuation, fold contractions the way a transcript does ("doctor's" → "doctors"). */
export function normalize(utterance: string): string {
  return utterance
    .toLowerCase()
    .replace(/[’']/g, '')
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/** The same folding, one spoken word at a time, keeping the index each folded word came from.
 *  That mapping is what lets a wildcard slot report the customer's own wording — capital letters
 *  and all — instead of the lower-cased key it matched on. */
function foldWords(utterance: string): { keys: string[]; source: number[] } {
  const spoken = utterance.split(/\s+/).filter(Boolean);
  const keys: string[] = [];
  const source: number[] = [];
  spoken.forEach((word, index) => {
    const key = word.toLowerCase().replace(/[^a-z0-9]/g, '');
    if (!key) return;
    keys.push(key);
    source.push(index);
  });
  return { keys, source };
}

/** Cut a word range out of the folded sentence and map it back to the words exactly as said. */
function asSpoken(spoken: string[], source: number[], from: number, to: number): string {
  if (to <= from) return '';
  return spoken.slice(source[from], source[to - 1] + 1).join(' ');
}

function editDistance(a: string, b: string): number {
  if (a === b) return 0;
  let previous = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i += 1) {
    const current = [i];
    for (let j = 1; j <= b.length; j += 1) {
      current.push(Math.min(
        previous[j] + 1,
        current[j - 1] + 1,
        previous[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1),
      ));
    }
    previous = current;
  }
  return previous[b.length];
}

/** Near-miss tolerance for a spoken name. Short values are left alone so "mum" never becomes "mom". */
function closest(spoken: string, candidates: string[]): { value: string; distance: number } | null {
  if (spoken.length < 4) return null;
  let best: { value: string; distance: number } | null = null;
  for (const candidate of candidates) {
    if (Math.abs(candidate.length - spoken.length) > 3) continue;
    const distance = editDistance(spoken, candidate);
    if (distance <= 2 && (!best || distance < best.distance)) best = { value: candidate, distance };
  }
  return best;
}

/** One value from a custom slot type, with the same priority a device uses: exact first, then a
 *  near miss, then a household name the runtime was told about, then a miss. */
function resolveSlotValue(
  typeName: string,
  spoken: string,
  extraValues: string[],
  verbatimValue = spoken,
): { resolved: string | null; id: string | null; state: SluSlot['state']; confidence: number } {
  const type = TYPES[typeName];
  if (!type) return { resolved: null, id: null, state: 'no-match', confidence: 0 };
  if ((type as { wildcard?: boolean }).wildcard) {
    // A wildcard is passed through as said: it becomes transcript text, so nothing here may
    // re-case or re-word it.
    return { resolved: verbatimValue, id: verbatimValue, state: 'wildcard', confidence: 1 };
  }

  const values = type.values.map((v) => ({ name: v.name.value.toLowerCase(), id: v.id }));
  const exact = values.find((v) => v.name === spoken);
  if (exact) {
    // The household roster is the authority for a person. The artifact lists each synonym with its
    // own text as the id ("papa" → id "papa"), but a member is stored under the spelling the family
    // uses, so that is what the argument carries. Nothing is invented: the value comes from the
    // cell's own people list, and a person nobody here is named for keeps the artifact's value.
    const roster = extraValues.find((v) => v.toLowerCase() === (exact.id ?? exact.name));
    const value = roster ?? exact.name;
    return { resolved: value, id: roster ?? exact.id ?? value, state: 'success', confidence: 1 };
  }

  const contained = values.filter((v) => spoken.includes(v.name) || v.name.includes(spoken));
  if (contained.length === 1) {
    return { resolved: contained[0].name, id: contained[0].id, state: 'success', confidence: 0.9 };
  }

  const extra = extraValues.find((v) => v.toLowerCase() === spoken || spoken.includes(v.toLowerCase()));
  if (extra) return { resolved: extra, id: extra, state: 'resolved', confidence: 0.8 };

  const near = closest(spoken, [...values.map((v) => v.name), ...extraValues.map((v) => v.toLowerCase())]);
  if (near) {
    const match = values.find((v) => v.name === near.value);
    return { resolved: near.value, id: match?.id ?? near.value, state: 'resolved', confidence: 0.6 };
  }
  return { resolved: null, id: null, state: 'no-match', confidence: 0.2 };
}

interface CompiledSample {
  intent: AskIntentName;
  sample: string;
  slotNames: string[];
  pattern: RegExp;
  /** The folded literal runs around the placeholders, `slotNames.length + 1` of them. They pin
   *  each spoken value to a word range, which is how the wording can be recovered verbatim. */
  literals: string[][];
  /** Literal characters in the sample — more of them means a more specific match. */
  specificity: number;
}

function escapeRe(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function compile(sample: string, intent: AskIntentName): CompiledSample {
  const slotNames: string[] = [];
  const literals: string[][] = [];
  for (const piece of sample.split(/(\{[A-Za-z]+\})/)) {
    const placeholder = /^\{([A-Za-z]+)\}$/.exec(piece);
    if (placeholder) slotNames.push(placeholder[1]);
    else literals.push(normalize(piece).split(' ').filter(Boolean));
  }
  // literal (.+) literal (.+) literal — empty literal runs contribute nothing to the spacing.
  const parts: string[] = [];
  literals.forEach((words, index) => {
    const run = words.map(escapeRe).join(' ');
    if (run) parts.push(run);
    if (index < slotNames.length) parts.push('(.+)');
  });
  return {
    intent,
    sample,
    slotNames,
    literals,
    pattern: new RegExp(`^${parts.join(' ')}$`),
    specificity: literals.reduce((total, words) => total + words.join('').length, 0) + slotNames.length * 0.001,
  };
}

/** Which word range of the folded sentence each placeholder covers. */
function groupRanges(literals: string[][], keys: string[]): Array<[number, number]> {
  const ranges: Array<[number, number]> = [];
  const tail = literals[literals.length - 1]?.length ?? 0;
  let cursor = 0;
  for (let i = 0; i < literals.length - 1; i += 1) {
    cursor += literals[i].length;
    const last = i === literals.length - 2;
    let end = keys.length - tail;
    if (!last) {
      const next = literals[i + 1];
      end = cursor;
      for (let k = cursor; k + next.length <= keys.length; k += 1) {
        if (next.every((word, j) => keys[k + j] === word)) {
          end = k;
          break;
        }
      }
    }
    ranges.push([cursor, Math.max(cursor, end)]);
    // The next iteration adds `literals[i + 1]` itself, so stop at the end of this placeholder.
    cursor = end + (last ? tail : 0);
  }
  return ranges;
}

const SAMPLES: CompiledSample[] = INTERACTION_MODEL.languageModel.intents.flatMap(
  (intent) => intent.samples.map((sample) => compile(sample, intent.name as AskIntentName)),
).sort((a, b) => b.specificity - a.specificity);

/** Function words a care question shares with every other question: they carry no intent signal, so
 *  the scoring path ignores them. Deliberately not a stemmer — "today" stays, because it separates
 *  *what is on today* from *what is next*. */
const STOP = new Set(['a', 'an', 'and', 'any', 'are', 'as', 'at', 'be', 'been', 'but', 'by', 'can',
  'did', 'do', 'does', 'done', 'for', 'from', 'get', 'got', 'had', 'has', 'have', 'he', 'her', 'him',
  'how', 'i', 'if', 'in', 'is', 'it', 'its', 'just', 'me', 'my', 'of', 'on', 'or', 'our', 'please',
  'she', 'should', 'so', 'than', 'that', 'the', 'their', 'them', 'there', 'they', 'this', 'to', 'us',
  'was', 'we', 'were', 'what', 'when', 'where', 'which', 'who', 'whom', 'why', 'will', 'with', 'you',
  'your', 'yours']);

function contentWords(words: string[]): string[] {
  return words.filter((word) => word && !STOP.has(word));
}

/** Inverse document frequency over the model's own samples: a word that appears in one sample is
 *  worth far more than one that appears in eight. */
const IDF: Record<string, number> = (() => {
  const documentCount = SAMPLES.length;
  const inDocs: Record<string, number> = {};
  for (const entry of SAMPLES) {
    for (const word of new Set(contentWords(entry.literals.flat()))) {
      inDocs[word] = (inDocs[word] ?? 0) + 1;
    }
  }
  const table: Record<string, number> = {};
  for (const [word, count] of Object.entries(inDocs)) {
    table[word] = Math.log(1 + documentCount / (1 + count));
  }
  return table;
})();

interface IntentProfile {
  intent: AskIntentName;
  /** Content words across every sample of this intent — the scoring set. */
  words: Set<string>;
  /** The single sample whose content words overlap the phrase most: what the answer is keyed on, and
   *  what the UI shows as the reason. */
  bestSample: (query: Set<string>) => CompiledSample;
  /** Every placeholder name this intent declares, from its samples. */
  slotNames: string[];
  /** Size of the scoring set, for the length normalisation. */
  size: number;
}

const PROFILES: IntentProfile[] = INTERACTION_MODEL.languageModel.intents.map((intent) => {
  const samples = intent.samples.map((sample) => compile(sample, intent.name as AskIntentName));
  const words = new Set(samples.flatMap((s) => contentWords(s.literals.flat())));
  const slotNames = [...new Set(samples.flatMap((s) => s.slotNames))];
  return {
    intent: intent.name as AskIntentName,
    words,
    slotNames,
    size: Math.max(1, words.size),
    bestSample: (query) => {
      let best = samples[0];
      let overlap = -1;
      for (const entry of samples) {
        const set = new Set(contentWords(entry.literals.flat()));
        const hits = [...query].filter((w) => set.has(w)).length;
        if (hits > overlap) { overlap = hits; best = entry; }
      }
      return best;
    },
  };
});

/** A generalised match needs at least this much idf-weighted overlap, must beat the runner-up this
 *  clearly, and must share at least this many content words. Tuned against the corpus in
 *  `accuracy.corpus.ts`: below any of the three the phrase is reported as no match, and the device
 *  answers locally instead of guessing at somebody's care plan. */
export const FUZZY_MIN_SCORE = 0.5;
export const FUZZY_MIN_MARGIN = 1.2;
export const FUZZY_MIN_OVERLAP = 2;

export interface IntentScore {
  intent: AskIntentName;
  score: number;
  /** The content words the phrase and this intent's samples share — the evidence behind the number. */
  overlap: string[];
}

/** Score a phrase against every intent's own samples. Same arithmetic a tf-idf classifier would use,
 *  run on 80 hand-written samples — a stand-in for Amazon's language understanding, not a claim to
 *  have rebuilt it. Exported because a threshold is only honest if you can see what it rejects. */
export function rankIntents(normalized: string): IntentScore[] {
  const query = [...new Set(contentWords(normalized.split(' ')))];
  if (!query.length) return [];
  return PROFILES.map((profile) => {
    let weight = 0;
    const overlap: string[] = [];
    for (const word of query) {
      if (profile.words.has(word)) { weight += IDF[word] ?? 0; overlap.push(word); }
    }
    return {
      intent: profile.intent,
      score: Number((weight / Math.sqrt(profile.size)).toFixed(3)),
      overlap: overlap.sort(),
    };
  }).sort((a, b) => b.score - a.score);
}

interface FuzzyHit {
  intent: AskIntentName;
  /** The closest sample, compiled — so the scoring path fills exactly the slots that sample declares. */
  entry: CompiledSample;
  score: number;
  margin: number;
  overlap: string[];
}

function fuzzyMatch(normalized: string): FuzzyHit | null {
  const ranked = rankIntents(normalized);
  const top = ranked[0];
  const second = ranked[1]?.score ?? 0;
  if (!top || top.score < FUZZY_MIN_SCORE) return null;
  if (top.overlap.length < FUZZY_MIN_OVERLAP) return null;
  if (second > 0 && top.score < second * FUZZY_MIN_MARGIN) return null;
  const profile = PROFILES.find((candidate) => candidate.intent === top.intent)!;
  const query = new Set(contentWords(normalized.split(' ')));
  return {
    intent: top.intent,
    entry: profile.bestSample(query),
    score: top.score,
    margin: second > 0 ? Number((top.score / second).toFixed(2)) : 1,
    overlap: top.overlap,
  };
}

/** Every value of one slot type that appears as a consecutive word run in the phrase. Values are
 *  matched as written — no fuzzy slot filling, because a wrong person or a wrong item on a health
 *  request is not a rounding error. */
function findValueRuns(keys: string[], values: string[]): Array<{ value: string; from: number; to: number }> {
  const found: Array<{ value: string; from: number; to: number }> = [];
  for (const value of values) {
    const words = value.split(' ').filter(Boolean);
    if (!words.length) continue;
    for (let start = 0; start + words.length <= keys.length; start += 1) {
      let hit = true;
      for (let j = 0; j < words.length; j += 1) {
        if (keys[start + j] !== words[j]) { hit = false; break; }
      }
      if (hit) found.push({ value, from: start, to: start + words.length });
    }
  }
  return found;
}

/** Fill the slots an intent declares by looking for its values in the phrase, for the scoring path. */
function scanSlots(
  intent: AskIntentName,
  slotNames: string[],
  keys: string[],
  spoken: string[],
  source: number[],
  people: string[],
): SluSlot[] {
  return slotNames.map((name) => {
    const typeName = slotTypeFor(intent, name);
    const type = TYPES[typeName];
    const range = (from: number, to: number) => asSpoken(spoken, source, from, to) || keys.slice(from, to).join(' ');
    if (!type) return { name, spoken: '', resolved: null, id: null, state: 'no-match' as const, confidence: 0 };
    if ((type as { wildcard?: boolean }).wildcard) {
      // Transcript text is never guessed: the device asks for it instead of inventing a note.
      return { name, spoken: '', resolved: null, id: null, state: 'no-match' as const, confidence: 0 };
    }
    const values = [...type.values.map((v) => v.name.value.toLowerCase()), ...people.map((p) => p.toLowerCase())]
      .sort((a, b) => b.length - a.length);
    const runs = findValueRuns(keys, values);
    if (!runs.length) return { name, spoken: '', resolved: null, id: null, state: 'no-match' as const, confidence: 0 };
    const longest = runs.reduce((best, run) => (run.value.length > best.value.length ? run : best), runs[0]);
    const inModel = type.values.find((v) => v.name.value.toLowerCase() === longest.value);
    return {
      name,
      spoken: range(longest.from, longest.to),
      resolved: longest.value,
      id: inModel?.id ?? longest.value,
      state: inModel ? ('success' as const) : ('resolved' as const),
      confidence: inModel ? 0.85 : 0.7,
    };
  });
}

/** The answer to one elicited slot, filled from the phrase on its own: the device is no longer
 *  choosing an intent, it is completing one. An enumerated slot is matched against the slot type's
 *  own values (nothing fuzzy — a wrong person or a wrong item on a health request is not a rounding
 *  error), and a wildcard slot takes the whole phrase exactly as said, because that text becomes the
 *  stored record. A miss stays a miss, so the dialog asks again instead of guessing. */
export function fillSlot(intent: AskIntentName, slotName: string, phrase: string, people: string[] = []): SluSlot {
  const said = phrase.trim();
  const typeName = slotTypeFor(intent, slotName);
  const type = TYPES[typeName];
  const miss: SluSlot = { name: slotName, spoken: said, resolved: null, id: null, state: 'no-match', confidence: 0 };
  if (!said || !type) return miss;
  if (declaredSlots(intent).find((one) => one.name === slotName)?.wildcard) {
    return { name: slotName, spoken: said, resolved: said, id: said, state: 'wildcard', confidence: 1 };
  }
  const { keys, source } = foldWords(said);
  const spokenWords = said.split(/\s+/).filter(Boolean);
  const values = [...type.values.map((v) => v.name.value.toLowerCase()), ...people.map((p) => p.toLowerCase())]
    .sort((a, b) => b.length - a.length);
  const runs = findValueRuns(keys, values);
  if (!runs.length) return miss;
  const longest = runs.reduce((best, run) => (run.value.length > best.value.length ? run : best), runs[0]);
  const folded = keys.slice(longest.from, longest.to).join(' ');
  const verbatim = asSpoken(spokenWords, source, longest.from, longest.to) || folded;
  const inModel = type.values.find((v) => v.name.value.toLowerCase() === longest.value);
  return {
    name: slotName,
    spoken: verbatim,
    ...resolveSlotValue(typeName, folded, people, verbatim),
    confidence: inModel ? 0.9 : 0.75,
  };
}

export function slotTypeFor(intent: AskIntentName, slotName: string): string {
  // Read off the intent's own `slots` list in the shipped artifact — the pairing the console would
  // validate. Never derived from the slot's name: an unmapped slot resolves as a miss rather than
  // against the wrong value list.
  return declaredSlots(intent).find((slot) => slot.name === slotName)?.type ?? '';
}

/** Resolve a spoken phrase into an intent plus device-shaped slots. */
export function resolveUtterance(utterance: string, options: { people?: string[] } = {}): SluResult {
  const spoken = utterance.split(/\s+/).filter(Boolean);
  const { keys, source } = foldWords(utterance);
  const normalized = keys.join(' ');
  const empty: SluResult = {
    utterance, normalized, intent: null, builtIn: null, matchedSample: null, matched: 'none',
    slots: [], askSlots: {}, tool: null, confidence: 0,
    reason: 'nothing matched the shipped samples',
  };
  if (!normalized) return empty;

  const builtIn = BUILT_INS.find((entry) => entry.phrases.includes(normalized));
  if (builtIn) {
    return {
      ...empty, builtIn: builtIn.name, confidence: 1,
      reason: `platform built-in ${builtIn.name}`,
    };
  }

  // One answer shape, two ways to get there: a shipped sample's literal pattern first, and only when
  // nothing matches, the idf-weighted fallback over the same samples. The fallback is labelled, so a
  // generalised guess is never displayed as an exact hit.
  let entry: CompiledSample;
  let slots: SluSlot[];
  let matched: SluResult['matched'];
  let scored: FuzzyHit | null = null;
  const literal = SAMPLES.find((candidate) => candidate.pattern.test(normalized));
  if (literal) {
    entry = literal;
    const ranges = groupRanges(literal.literals, keys);
    slots = literal.slotNames.map((name, index) => {
      const [from, to] = ranges[index] ?? [0, keys.length];
      const folded = keys.slice(from, to).join(' ');
      const said = asSpoken(spoken, source, from, to) || folded;
      const typeName = slotTypeFor(literal.intent, name);
      const extra = typeName === 'CareLoopPerson' ? (options.people ?? []) : [];
      return { name, spoken: said, ...resolveSlotValue(typeName, folded, extra, said) };
    });
    matched = 'literal';
  } else {
    const fuzzy = fuzzyMatch(normalized);
    if (!fuzzy) return empty;
    entry = fuzzy.entry;
    scored = fuzzy;
    slots = scanSlots(fuzzy.intent, fuzzy.entry.slotNames, keys, spoken, source, options.people ?? []);
    matched = 'fuzzy';
  }

  // A *required* slot this sample never speaks is still a slot the skill is missing: dialog delegation
  // asks for it instead of calling with half the arguments. An optional slot nobody mentioned is simply
  // not part of the call, so it is not reported as a miss.
  const unfilled = declaredSlots(entry.intent)
    .filter((slot) => slot.required)
    .map((slot) => slot.name)
    .filter((name) => !slots.some((slot) => slot.name === name))
    .map((name): SluSlot => ({
      name, spoken: '', resolved: null, id: null, state: 'no-match', confidence: 0,
    }));
  if (unfilled.length) slots = [...slots, ...unfilled];

  const routing = declaredRouting(entry.intent);
  const askSlots = toAskSlots(entry.intent, slots);

  // Confidence is the average over the words actually heard for a slot. An unfilled required slot is
  // a dialog gap, not evidence that the phrase was misheard, so it does not drag this down.
  const heard = slots.filter((slot) => slot.spoken !== '');
  const meanConfidence = heard.length
    ? heard.reduce((total, slot) => total + slot.confidence, 0) / heard.length
    : 1;
  const missed = slots.filter((slot) => slot.state === 'no-match').map((slot) => slot.name);
  const base = matched === 'fuzzy'
    ? `no sample matched; scored ${scored!.score} on ${scored!.overlap.join(', ')} against "${entry.sample}" (×${scored!.margin} over the runner-up)`
    : `matched "${entry.sample}"`;

  return {
    utterance,
    normalized,
    intent: entry.intent,
    builtIn: null,
    matchedSample: entry.sample,
    matched,
    slots,
    askSlots,
    tool: routing.tool || null,
    confidence: Number((meanConfidence * (matched === 'fuzzy' ? 0.8 : 0.95)).toFixed(3)),
    reason: missed.length ? `${base}, but ${missed.join(', ')} did not resolve` : base,
  };
}

/** The slots the way `ask-sdk-model` wants them, including the entity-resolution block a custom slot
 *  type carries. Exported because a chained dialog rebuilds them from slots collected over two turns. */
export function toAskSlots(intent: AskIntentName, slots: SluSlot[]): Record<string, Slot> {
  const askSlots: Record<string, Slot> = {};
  for (const slot of slots) {
    const slotType = slotTypeFor(intent, slot.name);
    askSlots[slot.name] = slot.state === 'no-match'
      ? unresolvedSlot(slot.name, slot.spoken, slotType)
      : buildSlot(slot.name, slot.spoken, {
        slotType,
        value: slot.resolved ?? slot.spoken,
        id: slot.id ?? undefined,
        state: slot.state === 'resolved' ? 'resolved' : 'success',
      });
  }
  return askSlots;
}

/** Slot value → the tool argument name the model routes it to (`routing.slotArguments`). The slot's
 *  `id` is what goes in the argument, because that is the value the tool's own contract names; the
 *  family's wording stays in `spoken` for the read-back. */
export function toToolArgs(result: SluResult): Record<string, unknown> {
  const mapping = result.intent ? declaredRouting(result.intent).slotArguments : {};
  const args: Record<string, unknown> = {};
  for (const slot of result.slots) {
    const argument = mapping[slot.name];
    if (!argument) continue;
    const value = slot.id ?? slot.resolved ?? slot.spoken;
    if (value) args[argument] = value;
  }
  return args;
}

/** Every intent the model ships, for the picker in the Device Lab. */
export const INTENT_NAMES = INTERACTION_MODEL.languageModel.intents.map((i) => i.name as AskIntentName);
