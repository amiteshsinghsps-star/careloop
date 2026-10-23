/** The resolver is driven entirely by `alexa/interaction-model.json`. These phrases are that
 *  model’s own samples — if a test here invents wording, it is testing the test, not the skill. */
import { describe, expect, it } from 'vitest';
import { normalize, resolveUtterance, toToolArgs, INTENT_NAMES } from './slu';
import { INTERACTION_MODEL } from './interactionModel';

const PEOPLE = ['Papa', 'Mum'];

describe('the model itself', () => {
  it('is read in whole: every intent the model ships is addressable', () => {
    expect(INTENT_NAMES).toHaveLength(INTERACTION_MODEL.languageModel.intents.length);
    for (const intent of INTERACTION_MODEL.languageModel.intents) {
      expect(INTENT_NAMES).toContain(intent.name);
    }
  });

  it('resolves at least one sample per intent, from the model’s own wording', () => {
    const unresolvable: string[] = [];
    for (const intent of INTERACTION_MODEL.languageModel.intents) {
      const withoutSlots = intent.samples.find((sample) => !sample.includes('{'))
        ?? intent.samples[0];
      const filled = withoutSlots.replace(/\{Person\}/g, 'mother').replace(/\{ItemPosition\}/g, 'first')
        .replace(/\{Section\}/g, 'medicine').replace(/\{NoteText\}/g, 'the clinic moved the visit');
      const result = resolveUtterance(filled, { people: PEOPLE });
      if (result.intent !== intent.name) unresolvable.push(`${intent.name} <- "${filled}" got ${result.intent}`);
    }
    expect(unresolvable).toEqual([]);
  });
});

describe('slot resolution', () => {
  it('resolves an enumerated person to the model’s own id', () => {
    const result = resolveUtterance('what is next for mother', { people: PEOPLE });
    expect(result.intent).toBe('WhatsMyPlanIntent');
    expect(result.tool).toBe('get_care_plan');
    const person = result.slots.find((slot) => slot.name === 'Person');
    expect(person?.resolved).toBe('mother');
    expect(person?.id).toBe('mother');
    expect(person?.state).toBe('success');
  });

  it('answers a synonym with the name the household stores the person under', () => {
    // "papa" is a value the artifact ships, with the id "papa"; the member in this cell is Papa.
    // Sending the roster's own spelling is what lets the voice device and the headless one put the
    // identical argument on the wire for the identical phrase.
    const result = resolveUtterance('what is next for papa', { people: PEOPLE });
    const person = result.slots.find((slot) => slot.name === 'Person');
    expect(person?.resolved).toBe('Papa');
    expect(person?.id).toBe('Papa');
    expect(person?.state).toBe('success');
    // and a person nobody here is named for keeps the artifact's own value
    const stranger = resolveUtterance('what is next for grandmother', { people: PEOPLE });
    expect(stranger.slots.find((slot) => slot.name === 'Person')?.id).toBe('grandmother');
  });

  it('takes a name the household actually has, even when the model never saw it', () => {
    // 'Ravi' is in no slot type of the shipped model: only the runtime was told about him. The
    // roster's own spelling is what goes out as the argument, because that is the name the plan is
    // stored under — the device does not re-case a person it was handed.
    const result = resolveUtterance('what is the plan for Ravi', { people: ['Ravi'] });
    const person = result.slots.find((slot) => slot.name === 'Person');
    expect(person?.state).toBe('resolved');
    expect(person?.resolved).toBe('Ravi');
    expect(person?.spoken).toBe('Ravi');
  });

  it('forgives one mistavowel and says it was a near miss', () => {
    const result = resolveUtterance('what is next for muther', { people: PEOPLE });
    const person = result.slots.find((slot) => slot.name === 'Person');
    expect(person?.state).toBe('resolved');
    expect(person?.resolved).toBe('mother');
    expect(person?.confidence).toBeLessThan(1);
  });

  it('refuses to invent an ordinal the model does not define', () => {
    const result = resolveUtterance('explain the eleventh one', { people: PEOPLE });
    const position = result.slots.find((slot) => slot.name === 'ItemPosition');
    expect(position?.state).toBe('no-match');
    expect(result.askSlots.ItemPosition.resolutions?.resolutionsPerAuthority?.[0].status.code)
      .toBe('ER_SUCCESS_NO_MATCH');
  });

  it('keeps a wildcard note as spoken', () => {
    const result = resolveUtterance('remember that the clinic moved the Thursday visit', { people: PEOPLE });
    expect(result.intent).toBe('AddVerbalNoteIntent');
    const note = result.slots.find((slot) => slot.name === 'NoteText');
    expect(note?.state).toBe('wildcard');
    expect(note?.spoken).toBe('the clinic moved the Thursday visit');
  });

  it('maps slots onto the tool arguments the model routes to', () => {
    const result = resolveUtterance('mark the second one as done', { people: PEOPLE });
    expect(result.tool).toBe('mark_done');
    expect(toToolArgs(result)).toEqual({ ref: 'second' });
  });
});

describe('phrases with no slots', () => {
  it('matches a bare sample', () => {
    expect(resolveUtterance('who is in my household').intent).toBe('ListPeopleIntent');
    expect(resolveUtterance('what changed since the last visit').intent).toBe('WhatChangedIntent');
    expect(resolveUtterance('what can you do').intent).toBe('AboutCareLoopIntent');
  });

  it('has nothing for a phrase outside the model', () => {
    const result = resolveUtterance('tell me a joke about trains');
    expect(result.intent).toBeNull();
    expect(result.builtIn).toBeNull();
    expect(result.confidence).toBe(0);
  });

  it('is case and punctuation indifferent, the way a transcript is', () => {
    expect(normalize('  Who Is In MY Household?  ')).toBe('who is in my household');
    expect(resolveUtterance('WHO is in my household').intent).toBe('ListPeopleIntent');
  });
});

describe('platform built-ins', () => {
  // The model deliberately does not restate them (stopHandling.note), so the device supplies them.
  it.each([
    ['yes', 'AMAZON.YesIntent'],
    ['no thanks', 'AMAZON.NoIntent'],
    ['stop', 'AMAZON.StopIntent'],
    ['never mind', 'AMAZON.CancelIntent'],
    ['help', 'AMAZON.HelpIntent'],
  ] as const)('routes “%s” to %s', (phrase, name) => {
    const result = resolveUtterance(phrase);
    expect(result.builtIn).toBe(name);
    expect(result.intent).toBeNull();
  });

  it('carries no slots and no tool of its own', () => {
    const yes = resolveUtterance('yes');
    expect(yes.slots).toEqual([]);
    expect(yes.tool).toBeNull();
  });
});
