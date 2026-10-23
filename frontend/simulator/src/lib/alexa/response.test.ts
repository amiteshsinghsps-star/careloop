/** Response-envelope tests: one tool result in, one device response out — and the dialog state,
 *  the reprompt and the mask accounting all have to follow from it. */
import { describe, expect, it } from 'vitest';
import { toView } from '@careloop/client';
import {
  MASK_TOKEN, buildAskTurn, confirmationPromptId, findMasked, preservesText,
} from './response';
import { analyze, ssmlOf } from './ssml';
import type { AskIntentName } from './interactionModel';

const base = {
  tool: 'get_care_plan',
  intent: 'WhatsMyPlanIntent' as AskIntentName,
};

function turn(structured: Record<string, unknown>, spoken: string, intent = base.intent) {
  const result = {
    content: [{ type: 'text', text: spoken }],
    structuredContent: structured,
    _meta: { ui: { resourceUri: 'ui://careloop/card' } },
  };
  return buildAskTurn({
    ...base, intent, spoken, result, view: toView(structured),
  });
}

describe('an ordinary answer', () => {
  const ask = turn(
    { person: 'Mum', items: [{ text: `Take ${MASK_TOKEN} with food`, ref: 'i-abc123', kind: 'medication' }], dose_policy: 'Doses are masked here.' },
    `Mum has one thing left. Take ${MASK_TOKEN} with food.`,
  );

  it('keeps the server sentence as the only spoken text', () => {
    expect(ask.spoken).toContain(MASK_TOKEN);
    expect(ask.words).toBeGreaterThan(0);
    expect(ask.problems).toEqual([]);
  });

  it('wraps that sentence without changing a word of it', () => {
    expect(preservesText(ask.ssml, `Mum has one thing left. Take ${MASK_TOKEN} with food.`)).toBe(true);
    expect(analyze(ask.ssml).plain).toBe(ask.spoken);
  });

  it('holds a follow-up window instead of ending', () => {
    expect(ask.state).toBe('await');
    expect(ask.envelope.response.shouldEndSession).toBe(false);
    expect(ask.envelope.response.directives).toBeUndefined();
  });

  it('reps with the model’s own help prompt', () => {
    const reprompt = ask.envelope.response.reprompt?.outputSpeech;
    expect(reprompt?.type).toBe('SSML');
  });

  it('counts masked fields by path and carries no quantity', () => {
    expect(ask.masked.count).toBe(1);
    expect(ask.masked.paths).toContain('items[0].text');
    expect(JSON.stringify(ask.masked)).not.toMatch(/\d+\s?(mg|ml|mcg|g|tablet)/i);
  });

  it('points the card at the MCP app resource the server named', () => {
    expect(ask.card.resourceUri).toBe('ui://careloop/card');
    expect(ask.card.items).toBe(1);
    expect(ask.card.body).toBe(ask.spoken);
  });
});

describe('an ask-back', () => {
  const ask = turn({ needs_clarification: true, question: 'Who should I ask about?' }, 'Who should I ask about?');

  it('holds an ElicitSlot for the slot the model routes', () => {
    expect(ask.state).toBe('elicit');
    expect(ask.slotToElicit).toBe('Person');
    expect(ask.directives[0]?.type).toBe('Dialog.ElicitSlot');
  });

  it('keeps the session open and reprompts with the question', () => {
    expect(ask.envelope.response.shouldEndSession).toBe(false);
    expect(analyze(ssmlOf(ask.envelope.response.reprompt?.outputSpeech)).plain)
      .toBe('Who should I ask about?');
  });
});

describe('a destructive change', () => {
  const ask = turn(
    { needs_confirmation: true, question: 'Delete the first medicine for Mum?', summary: { kind: 'medication', text: 'Take it at night' } },
    'Delete the first medicine for Mum?',
    'RemoveInstructionIntent',
  );

  it('asks for a confirmation the model requires', () => {
    expect(ask.state).toBe('confirm');
    expect(ask.directives[0]?.type).toBe('Dialog.ConfirmIntent');
    expect(confirmationPromptId('RemoveInstructionIntent')).toBe('ConfirmRemovePrompt');
  });

  it('requires nothing on an intent that does not', () => {
    expect(confirmationPromptId('WhatsMyPlanIntent')).toBeNull();
  });
});

describe('a crisis and a stop', () => {
  it('ends the session on a crisis redirect', () => {
    const ask = turn({ crisis: true, message: 'Please call 911 or your crisis line now.' }, 'Please get help now.');
    expect(ask.state).toBe('end');
    expect(ask.envelope.response.shouldEndSession).toBe(true);
  });

  it('ends when the customer says stop', () => {
    const ask = buildAskTurn({
      ...base, spoken: 'Goodbye.', view: toView({ items: [], person: 'Mum' }),
      result: undefined,
      slu: { builtIn: 'AMAZON.StopIntent' } as never,
    });
    expect(ask.envelope.response.shouldEndSession).toBe(true);
  });
});

describe('what must never be spoken', () => {
  const cases: Array<[string, string]> = [
    ['an internal identifier', 'See i-abc123 for details'],
    ['a protocol string', 'jsonrpc 2.0 answered'],
    ['an unfilled placeholder', 'Ask {Person} about it'],
  ];

  it.each(cases)('flags %s', (_label, spoken) => {
    const ask = turn({ person: 'Mum', items: [] }, spoken);
    expect(ask.problems.length).toBeGreaterThan(0);
  });
});

describe('findMasked', () => {
  it('walks nested arrays and objects and reports paths only', () => {
    const found = findMasked({
      items: [{ text: MASK_TOKEN }, { note: `both ${MASK_TOKEN} and ${MASK_TOKEN}` }],
      summary: { dose: MASK_TOKEN },
    });
    expect(found.count).toBe(4);
    expect(found.paths).toEqual(['items[0].text', 'items[1].note', 'summary.dose']);
  });

  it('says nothing when nothing was masked', () => {
    expect(findMasked({ items: [{ text: 'rest the knee' }] })).toEqual({ count: 0, paths: [] });
  });
});
