/** Adaptive spoken delivery: how an answer is said, never what it says.
 *
 *  Every number a style applies — rate, volume, word ceiling — is read out of the shipped artifact
 *  (`presentation.delivery.styles`), and the sentence itself is always the one the service returned.
 *  These tests are the proof of that: the default path is byte-identical, a slower answer takes
 *  longer and says the same words, and a shorter answer is the same sentence cut from the end, with
 *  the card still carrying all of it. The headless driver applies the same rules to the same payload.
 */
import { describe, expect, it } from 'vitest';
import { toView } from '@careloop/client';
import type { ToolResult } from '@careloop/client';
import { DELIVERY, INTERACTION_MODEL, PROMPTS, VOICE_BUDGET } from './interactionModel';
import { buildAskTurn, buildDialogTurn, cutToWords, preservesText } from './response';
import { analyze, prosodize, SIM_WORDS_PER_MINUTE } from './ssml';
import { INTENT_BY_NAME, declaredRouting } from './envelope';
import { resolveUtterance } from './slu';

const ANSWER = 'Papa: Check the blood pressure every morning and write it down. '
  + 'Take your prescribed amount iron after dinner every day for two months. Come back after six weeks.';
const VIEW = toView({
  person: 'Papa',
  items: [{ text: 'Check the blood pressure every morning and write it down.', ref: 'i-abc123', kind: 'monitor' }],
});
const RESULT = {
  content: [{ type: 'text', text: ANSWER }],
  structuredContent: { person: 'Papa', items: [{ text: 'Check the blood pressure', ref: 'i-abc123' }] },
} as unknown as ToolResult;

function underStyle(style: string | null) {
  return buildAskTurn({
    tool: 'get_care_plan', intent: 'WhatsMyPlanIntent', spoken: ANSWER, view: VIEW, result: RESULT,
    deliveryStyle: style,
  });
}

describe('the delivery the artifact declares', () => {
  it('names a device-local intent, a real style slot and a prompt per style', () => {
    const intent = INTENT_BY_NAME[DELIVERY.intent];
    expect(declaredRouting(DELIVERY.intent).deviceLocal).toBe(true);
    expect(declaredRouting(DELIVERY.intent).writesAnything).toBe(false);
    expect(declaredRouting(DELIVERY.intent).tool).toBe('');
    expect(intent.routing.tool).toBeNull();
    expect(intent.slots?.[0]?.name).toBe(DELIVERY.slot);
    expect(intent.slots?.[0]?.required).toBe(true);
    expect(DELIVERY.writesAnything).toBe(false);
    expect(DELIVERY.appliesToHeldAnswer).toBe(true);
    for (const [id, style] of Object.entries(DELIVERY.styles)) {
      expect(PROMPTS[style.prompt], id).toBeTruthy();
      expect(style.maxSpokenWords, id).toBeGreaterThan(0);
      expect(style.maxSpokenWords, id).toBeLessThanOrEqual(VOICE_BUDGET.maxSpokenWords);
    }
  });

  it('covers every value of its own slot type, so no spoken style can be unheard', () => {
    const values = INTERACTION_MODEL.languageModel.types
      .find((type) => type.name === 'CareLoopDeliveryStyle');
    expect(values?.values.length).toBeGreaterThan(0);
    for (const value of values?.values ?? []) {
      expect(DELIVERY.styles[value.id], `style id ${value.id}`).toBeTruthy();
    }
    // and nothing in the other direction: a style no slot value can ask for is dead markup
    const ids = new Set<string>((values?.values ?? []).map((value) => value.id));
    for (const id of Object.keys(DELIVERY.styles)) expect(ids.has(id), id).toBe(true);
  });

  it('is not a new way of guessing: a style phrase resolves and reaches no tool', () => {
    for (const [word, id] of [['slower', 'slower'], ['louder', 'loud'], ['softer', 'soft'],
      ['shorter', 'brief'], ['the usual way', 'normal']]) {
      const result = resolveUtterance(`say that ${word}`, { people: ['Papa'] });
      expect(result.intent, word).toBe(DELIVERY.intent);
      expect(result.matched, word).toBe('literal');
      expect(result.tool, word).toBeNull();
      expect(result.slots.find((slot) => slot.name === DELIVERY.slot)?.id, word).toBe(id);
    }
  });
});

describe('a style that is not a different sentence', () => {
  it('leaves the usual way byte-identical to no style at all', () => {
    // The default style must not mark up anything: every envelope and trace recorded before this
    // feature exists stays exactly as it was for a customer who never asked.
    const plain = buildAskTurn({ tool: 'get_care_plan', intent: 'WhatsMyPlanIntent', spoken: ANSWER, view: VIEW, result: RESULT });
    const usual = underStyle(DELIVERY.defaultStyle);
    expect(usual.ssml).toBe(plain.ssml);
    expect(usual.ssml).not.toContain('prosody');
    expect(usual.delivery?.style).toBe(DELIVERY.defaultStyle);
    expect(usual.delivery?.truncated).toBe(false);
    expect(usual.estimateMs).toBe(plain.estimateMs);
  });

  it('says an unknown style exactly as the usual way, rather than inventing one', () => {
    const ask = underStyle('whispered');
    expect(ask.delivery).toBeNull();
    expect(ask.ssml).not.toContain('prosody');
  });

  it('slows the same words down, and takes longer saying them', () => {
    const usual = underStyle(null);
    const ask = underStyle('slower');
    const rate = DELIVERY.styles.slower.rate;
    expect(ask.ssml).toContain(`<prosody rate="${rate}"`);
    expect(preservesText(ask.ssml, ANSWER)).toBe(true);
    expect(ask.words).toBe(usual.words);
    expect(ask.estimateMs).toBeGreaterThan(usual.estimateMs);
    expect(ask.problems).toEqual([]);
    // The artifact's percentage applies to the words, not to the pauses already in the markup:
    // `analyze` scales the word clock and adds the breaks untouched, so the pause component has to
    // come off the usual answer before the ratio can be checked.
    const wordClock = (words: number) => (words / SIM_WORDS_PER_MINUTE) * 60_000;
    const pauseMs = usual.estimateMs - Math.round(wordClock(usual.words));
    expect(ask.estimateMs).toBe(Math.round(wordClock(ask.words) * (100 / Number.parseFloat(rate))) + pauseMs);
  });

  it('raises and lowers the level without touching a word', () => {
    for (const style of ['loud', 'soft'] as const) {
      const ask = underStyle(style);
      expect(ask.ssml).toContain(`<prosody volume="${DELIVERY.styles[style].volume}"`);
      expect(preservesText(ask.ssml, ANSWER)).toBe(true);
      expect(ask.delivery?.truncated).toBe(false);
    }
  });

  it('cuts the tail off, never the middle, and never writes a word of its own', () => {
    const ask = underStyle('brief');
    const ceiling = DELIVERY.styles.brief.maxSpokenWords;
    expect(ask.words).toBeLessThanOrEqual(ceiling);
    expect(ask.delivery?.truncated).toBe(true);
    expect(ask.delivery?.appliedTo).toBe('next');
    const kept = analyze(ask.ssml).plain.split(' ');
    const whole = ANSWER.split(/\s+/).filter(Boolean);
    expect(kept.length).toBe(ceiling);
    expect(kept).toEqual(whole.slice(0, ceiling));
    // the screen is not the speaker: the card keeps the sentence the service returned
    expect(ask.card.body).toBe(ANSWER);
  });

  it('asks for a slower answer without hiding one that was already too long', () => {
    // A style only ever tightens: a looser ceiling must not be used to make an over-budget answer
    // look as if it fits, so the budget problem is still reported under "slower".
    const long = Array.from({ length: VOICE_BUDGET.maxSpokenWords + 6 }, (_, index) => `item${index}`).join(' ');
    const ask = buildAskTurn({ tool: 'get_care_plan', intent: 'WhatsMyPlanIntent', spoken: long, view: VIEW, result: RESULT, deliveryStyle: 'slower' });
    expect(ask.delivery?.truncated).toBe(false);
    expect(ask.words).toBe(VOICE_BUDGET.maxSpokenWords + 6);
    expect(ask.problems.join(' ')).toContain('over the model');
  });

  it('carries the style into the question the device asks next', () => {
    const ask = buildDialogTurn({
      intent: 'WhatsMyPlanIntent',
      step: { stage: 'elicit', slot: 'Person', promptId: 'ClarifyPersonPrompt' },
      slots: [], askSlots: {}, deliveryStyle: 'slower',
    });
    expect(ask.ssml).toContain(`<prosody rate="${DELIVERY.styles.slower.rate}"`);
    expect(preservesText(ask.ssml, PROMPTS.ClarifyPersonPrompt)).toBe(true);
  });
});

describe('the markup helpers', () => {
  it('leaves markup alone when there is nothing to change', () => {
    const one = '<speak>Two items left.</speak>';
    expect(prosodize(one, {})).toBe(one);
    expect(prosodize(one, { rate: null, volume: null })).toBe(one);
  });

  it('wraps existing markup instead of replacing it', () => {
    const inner = '<speak><break time="300ms"/>Two items left.</speak>';
    const wrapped = prosodize(inner, { rate: '80%' });
    expect(wrapped).toContain('<prosody rate="80%">');
    expect(wrapped.indexOf('<speak>')).toBe(0);
    expect(wrapped).toContain('<break time="300ms"/>');
    expect(analyze(wrapped).plain).toBe('Two items left.');
    expect(analyze(wrapped).problems).toEqual([]);
  });

  it('cuts to a word count by dropping words, and not at all when there is nothing to drop', () => {
    expect(cutToWords('one two three', 5)).toBe('one two three');
    expect(cutToWords('one two three', 2)).toBe('one two');
    expect(cutToWords('one two three', 0)).toBe('');
  });

  it('keeps the mask constant intact through a cut, so a shortened dose answer still reads as masked', () => {
    const masked = 'Take your prescribed amount iron after dinner every day for two months.';
    const cut = cutToWords(masked, 6);
    expect(cut).toBe('Take your prescribed amount iron after');
    expect(cut).toContain('your prescribed amount');
  });
});
