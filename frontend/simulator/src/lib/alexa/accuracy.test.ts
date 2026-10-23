/** How well the shipped interaction model actually understands speech — measured, not claimed.
 *
 *  Runs the same resolver the Device Lab drives (`resolveUtterance`) over the corpus in
 *  `accuracy.corpus.ts` and reports five numbers: intent accuracy on the model's own samples,
 *  generalisation to phrasings nobody wrote, slot resolution, false matches on out-of-domain speech
 *  and the platform built-ins. The floors below are set from what this machine measured; raising one
 *  without changing the matcher is not possible, and every phrase that misses is printed. */
import { describe, expect, it } from 'vitest';
import { FUZZY_MIN_OVERLAP, normalize, rankIntents, resolveUtterance, toToolArgs } from './slu';
import { INTENT_BY_NAME, declaredRouting } from './envelope';
import { HUMAN_CASES, PEOPLE, shippedCases, type VoiceCase } from './accuracy.corpus';

// FLOORS: measured on this machine 2026-10-22 by this very file, then rounded down to just under the
// next case so losing one case trips the build. The paraphrase floor is not a target — it is what
// 80 hand-written samples plus a scored fallback actually reach, and the phrases that miss are
// printed below rather than hidden.
const FLOORS = {
  shippedIntentAccuracy: 1,
  paraphraseIntentAccuracy: 0.4,
  slotAccuracy: 0.85,
  falseMatchesOnNegatives: 0,
};

interface Outcome {
  ok: boolean;
  got: string | null;
  slotFailures: string[];
  tool: string | null;
}

function run(one: VoiceCase): Outcome {
  const result = resolveUtterance(one.say, { people: PEOPLE });
  const got = result.builtIn ?? result.intent;
  const slotFailures: string[] = [];
  for (const [name, want] of Object.entries(one.slots ?? {})) {
    const slot = result.slots.find((s) => s.name === name);
    if (!slot) { slotFailures.push(`${name}: no slot returned`); continue; }
    if (want === '') {
      if (slot.resolved !== null) slotFailures.push(`${name}: expected a miss, resolved “${slot.resolved}”`);
    } else if ((slot.resolved ?? '').toLowerCase() !== want) {
      slotFailures.push(`${name}: expected “${want}”, got “${slot.resolved ?? 'nothing'}”`);
    }
  }
  return { ok: got === one.expect, got, slotFailures, tool: result.tool };
}

function score(cases: VoiceCase[]) {
  const outcomes = cases.map(run);
  const intentPass = outcomes.filter((o) => o.ok).length;
  const slotTotal = cases.filter((one) => one.slots).length;
  const slotPass = cases.filter((one, index) => one.slots && outcomes[index].slotFailures.length === 0).length;
  const failures = cases
    .map((one, index) => ({ one, outcome: outcomes[index] }))
    .filter((x) => !x.outcome.ok || x.outcome.slotFailures.length)
    .map((x) => `“${x.one.say}” → ${x.outcome.got ?? 'nothing'}${
      x.outcome.slotFailures.length ? ` [${x.outcome.slotFailures.join('; ')}]` : ''}`);
  return {
    total: cases.length,
    intentAccuracy: intentPass / cases.length,
    misses: cases.length - intentPass,
    slotTotal,
    slotAccuracy: slotTotal ? slotPass / slotTotal : 1,
    slotMisses: cases.filter((one, index) => one.slots && outcomes[index].slotFailures.length).length,
    failures,
  };
}

const SHIPPED = shippedCases();
const PARAPHRASE = HUMAN_CASES.filter((one) => one.group === 'paraphrase');
const SLOTS = HUMAN_CASES.filter((one) => one.group === 'slot');
const NEGATIVES = HUMAN_CASES.filter((one) => one.group === 'negative');
const BUILTINS = HUMAN_CASES.filter((one) => one.group === 'builtin');
const ALL = [...SHIPPED, ...HUMAN_CASES];

describe('the shipped model, resolved by our own matcher', () => {
  const shipped = score(SHIPPED);
  const paraphrase = score(PARAPHRASE);
  const slotGroup = score([...SLOTS, ...PARAPHRASE.filter((one) => one.slots)]);
  const negatives = score(NEGATIVES);
  const builtins = score(BUILTINS);

  it('reads out the five measured numbers', () => {
    const paths = PARAPHRASE.map((one) => resolveUtterance(one.say, { people: PEOPLE }));
    const viaLiteral = paths.filter((r) => r.matched === 'literal').length;
    const viaFuzzy = paths.filter((r) => r.matched === 'fuzzy').length;
    const line = (label: string, s: ReturnType<typeof score>, withSlots = true) =>
      `${label.padEnd(30)} ${String(s.total).padStart(3)} cases   intent ${(100 * s.intentAccuracy).toFixed(1)} %` +
      (withSlots && s.slotTotal ? `   slots ${(100 * s.slotAccuracy).toFixed(1)} % (${s.slotTotal})` : '');
    console.info([
      '', '  voice resolution, measured against alexa/interaction-model.json',
      line('model’s own samples', shipped),
      // The one slot-bearing paraphrase is counted in the slot row below, not twice here.
      line('human paraphrases', paraphrase, false),
      line('slot resolution', slotGroup),
      line('out of domain (must not match)', negatives),
      line('platform built-ins', builtins),
      paraphrase.failures.length ? `  did not generalise:\n    ${paraphrase.failures.join('\n    ')}` : '  every paraphrase generalised',
      slotGroup.slotMisses
        ? `  slot misses (${slotGroup.slotMisses} of ${slotGroup.slotTotal}):\n    ${
            slotGroup.failures.filter((line) => line.includes('[')).join('\n    ')}`
        : '  every expected slot value resolved',
      `  match path on the paraphrases: ${viaLiteral} by a shipped sample, ${viaFuzzy} only by the scored fallback`,
      negatives.failures.length ? `  FALSE MATCHES:\n    ${negatives.failures.join('\n    ')}` : '  zero false matches on out-of-domain speech',
    ].join('\n'));
    expect(true).toBe(true);
  });

  it('matches every one of the model’s own samples', () => {
    expect(shipped.intentAccuracy).toBeGreaterThanOrEqual(FLOORS.shippedIntentAccuracy);
    expect(shipped.failures, shipped.failures.join('\n')).toEqual([]);
  });

  it('does not answer out-of-domain speech with a care intent', () => {
    // For this group “correct” means *no* intent, so every miss is a false match. It is a hard zero:
    // guessing at a care plan is worse than asking again.
    expect(negatives.misses, negatives.failures.join('\n'))
      .toBeLessThanOrEqual(FLOORS.falseMatchesOnNegatives);
  });

  it('generalises to phrasings nobody wrote into the model, at the measured floor', () => {
    expect(paraphrase.intentAccuracy).toBeGreaterThanOrEqual(FLOORS.paraphraseIntentAccuracy);
  });

  it('resolves slots at the measured floor', () => {
    expect(slotGroup.slotAccuracy).toBeGreaterThanOrEqual(FLOORS.slotAccuracy);
  });

  it('hears the platform built-ins', () => {
    expect(builtins.intentAccuracy).toBe(1);
  });

  it('reaches the shipped half without the fallback', () => {
    // The exact path is not being propped up by the scored one.
    for (const one of SHIPPED) {
      expect(resolveUtterance(one.say, { people: PEOPLE }).matched, one.say).toBe('literal');
    }
  });

  it('refuses a phrase whose only evidence is one shared word', () => {
    // This is *why* the fallback needs two shared content words: every out-of-domain phrase here
    // collides with at most one care word ("stop", "off", "read", "today"), which is not evidence.
    for (const one of NEGATIVES) {
      const top = rankIntents(normalize(one.say))[0];
      expect(top?.overlap.length ?? 0, one.say).toBeLessThan(FUZZY_MIN_OVERLAP);
    }
  });

  it('labels a generalised match and refuses to invent the note it was not given', () => {
    const result = resolveUtterance('note that the swelling went down', { people: PEOPLE });
    expect(result.intent).toBe('AddVerbalNoteIntent');
    expect(result.matched).toBe('fuzzy');
    expect(result.reason).toContain('no sample matched');
    const note = result.slots.find((slot) => slot.name === 'NoteText');
    // A wildcard becomes transcript text, so the fallback leaves it unresolved and the device asks.
    expect(note?.state).toBe('no-match');
    expect(note?.resolved).toBeNull();
    expect(toToolArgs(result)).toEqual({});
  });

  it('routes every intent it resolves to the tool that intent declares', () => {
    for (const one of ALL) {
      const result = resolveUtterance(one.say, { people: PEOPLE });
      if (!result.intent) continue;
      // Not “the tool the corpus wanted” — a phrase that generalises to the wrong intent is already
      // counted by the accuracy numbers. This checks the mapping itself never breaks.
      const declared = INTENT_BY_NAME[result.intent]?.routing?.tool ?? null;
      expect(result.tool, one.say).toBe(declared);
      if (declared === null) {
        // A null tool here is not a gap in the mapping: it is the artifact routing the phrase at no
        // tool at all, which is what makes an intent unable to change a plan. Proved, not allowed.
        expect(declaredRouting(result.intent).deviceLocal, one.say).toBe(true);
        expect(declaredRouting(result.intent).writesAnything, one.say).toBe(false);
      } else {
        expect(result.tool, one.say).toBeTruthy();
      }
    }
  });

  it('carries a resolved slot into the tool argument the model maps it to', () => {
    for (const one of SHIPPED) {
      const result = resolveUtterance(one.say, { people: PEOPLE });
      if (!result.intent) continue;
      const mapping = declaredRouting(result.intent).slotArguments;
      const args = toToolArgs(result);
      for (const slot of result.slots) {
        const argument = mapping[slot.name];
        // The argument takes the value the slot type resolves to — its `id`, the name the tool's own
        // contract uses — not the synonym a family happened to say.
        const want = slot.id ?? slot.resolved;
        if (argument && want) expect(args[argument], `${one.say} → ${argument}`).toBe(want);
      }
    }
  });

  it('sends the kind the contract names, never the synonym it heard', () => {
    const result = resolveUtterance('list the medicine items for mum', { people: PEOPLE });
    expect(result.tool).toBe('get_care_plan');
    expect(toToolArgs(result)).toEqual({ kind: 'medication', person: 'mum' });
  });

  it('reports a required slot the phrase never spoke, and only that one', () => {
    // "that one is sorted" is a shipped MarkDone sample with no item in it: the item is a dialog gap,
    // the optional person is not. An unsaid optional slot must not be shown as a failure to hear.
    const result = resolveUtterance('that one is sorted', { people: PEOPLE });
    expect(result.intent).toBe('MarkDoneIntent');
    expect(result.slots.map((slot) => slot.name)).toEqual(['ItemPosition']);
    expect(result.slots[0].state).toBe('no-match');
    expect(result.confidence).toBeGreaterThan(0.5);
  });
});
