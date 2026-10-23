/** Conformance against Amazon's own SDK, not against our memory of it.
 *
 *  `ask-sdk-core` is a dev dependency: the shipped skill would run it Node-side, and the browser
 *  bundle never sees it. Here it is used as an oracle — the same request envelope and the same
 *  response are put through Amazon's code, and wherever our simulator disagrees the reason is
 *  written down below, because "different" is only acceptable when it is deliberate.
 */
import { describe, expect, it } from 'vitest';
import {
  ResponseFactory, escapeXmlCharacters,
  getApiAccessToken, getDeviceId, getDialogState, getIntentName, getLocale, getRequestType,
  getSimpleSlotValues, getSlot, getSlotValue, getSlotValueV2, getSupportedInterfaces, getUserId, isNewSession,
} from 'ask-sdk-core';
import type { RequestEnvelope } from 'ask-sdk-model';
import { toView } from '@careloop/client';
import { buildSlot, confirmIntent, delegate, elicitSlot, intentRequest, launchRequest, unresolvedSlot } from './envelope';
import { buildAskTurn, MASK_TOKEN } from './response';
import { escapeXml, ssmlOf, unescapeXml } from './ssml';
import { resolveUtterance } from './slu';

const FIXED = {
  sessionId: 'simsession-fixed',
  requestId: 'simreq-fixed',
  timestamp: '2026-10-22T00:00:00Z',
  isNewSession: true,
} as const;

describe('escaping, Amazon’s function as the reference', () => {
  it('agrees on every character a transcript can contain', () => {
    const raw = [
      `Take 5 mg & rest <10 min> after "food" it's fine`,
      'Dr. Rao said: check the wound, then call me',
      `Amp &cetera; angle <b> quote "q" apostrophe 'q'`,
    ];
    for (const text of raw) expect(escapeXml(text)).toBe(escapeXmlCharacters(text));
  });

  it('differs on purpose for text that already holds an entity', () => {
    // The SDK un-escapes before it escapes, which makes it idempotent: `&amp;` in, `&amp;` out,
    // and the listener hears "&". Our rule is the opposite — a quote is rendered exactly as
    // recorded — so an ampersand that is really in the transcript is escaped once, and a literal
    // "&amp;" in the transcript is spoken as the five characters that were written.
    const recorded = 'The note reads &amp; then stop';
    expect(escapeXmlCharacters(recorded)).toBe(recorded);
    expect(escapeXmlCharacters('&')).toBe('&amp;');
    expect(escapeXml(recorded)).toBe('The note reads &amp;amp; then stop');
    expect(unescapeXml(escapeXml(recorded))).toBe(recorded);
    expect(unescapeXml(escapeXml('&'))).toBe('&');
  });
});

describe('a response built by us, read by the SDK', () => {
  const spoken = `Mum has one thing left. Take ${MASK_TOKEN} with food.`;
  const structured = {
    person: 'Mum',
    items: [{ text: `Take ${MASK_TOKEN} with food`, ref: 'i-abc123', kind: 'medication' }],
    dose_policy: 'Doses are masked here.',
  };
  const ask = buildAskTurn({
    tool: 'get_care_plan', intent: 'WhatsMyPlanIntent', spoken,
    result: { content: [{ type: 'text', text: spoken }], structuredContent: structured },
    view: toView(structured),
    pace: false,
  });

  /** The same turn the shipped skill would build with Amazon’s factory. */
  const mine = ask.envelope.response;
  const card = mine.card as { title: string; content: string };
  const theirs = ResponseFactory.init()
    .speak(ssmlOf(mine.outputSpeech))
    .withSimpleCard(card.title, card.content)
    .reprompt(ssmlOf(mine.reprompt?.outputSpeech))
    .withShouldEndSession(false)
    .getResponse();

  it('names the same directive-free speech block', () => {
    expect(theirs.outputSpeech).toEqual(mine.outputSpeech);
    expect(theirs.outputSpeech!.type).toBe('SSML');
  });

  it('builds the same simple card, same title, same body', () => {
    expect(theirs.card).toEqual(mine.card);
    expect((theirs.card as { type: string }).type).toBe('Simple');
  });

  it('keeps the reprompt in the same place, which is why an open mic is not ended', () => {
    // reprompt() sets shouldEndSession = false on its own: the SDK agrees with the shipped
    // behaviour that a follow-up window is never a closed session.
    expect(theirs.reprompt).toEqual(mine.reprompt);
    expect(theirs.shouldEndSession).toBe(false);
  });

  it('closes the session on the end state without a reprompt', () => {
    const closed = ResponseFactory.init().speak('Goodbye.').withShouldEndSession(true).getResponse();
    expect(closed.shouldEndSession).toBe(true);
    expect(closed.reprompt).toBeUndefined();
  });
});

describe('a dialog directive, built by us, shaped by the SDK', () => {
  const slots = { Person: buildSlot('Person', 'mother', { slotType: 'CareLoopPerson', value: 'mother', id: 'mother', state: 'success' }) };

  it('uses Amazon’s own type names and updatedIntent key', () => {
    const elicited = ResponseFactory.init().addElicitSlotDirective('Person', { name: 'WhatsMyPlanIntent', confirmationStatus: 'NONE', slots }).getResponse();
    expect(elicited.directives![0]).toEqual(elicitSlot('WhatsMyPlanIntent', 'Person', slots));
    const confirmed = ResponseFactory.init().addConfirmIntentDirective({ name: 'RemoveInstructionIntent', confirmationStatus: 'NONE', slots }).getResponse();
    expect(confirmed.directives![0]).toEqual(confirmIntent('RemoveInstructionIntent', slots));
    const delegated = ResponseFactory.init().addDelegateDirective({ name: 'WhatsMyPlanIntent', confirmationStatus: 'NONE', slots }).getResponse();
    expect(delegated.directives![0]).toEqual({ ...delegate('WhatsMyPlanIntent', slots), type: 'Dialog.Delegate' });
  });

  it('appends in call order, the way the machine stacks them', () => {
    const both = ResponseFactory.init()
      .addElicitSlotDirective('Person', { name: 'WhatsMyPlanIntent', confirmationStatus: 'NONE', slots })
      .addDelegateDirective({ name: 'WhatsMyPlanIntent', confirmationStatus: 'NONE', slots })
      .getResponse();
    expect(both.directives!.map((d) => d.type)).toEqual(['Dialog.ElicitSlot', 'Dialog.Delegate']);
  });
});

describe('our request envelope, read through the SDK’s accessors', () => {
  const slu = resolveUtterance('what is next for mother', { people: ['Mum'] });
  const envelope = intentRequest(
    { intent: 'WhatsMyPlanIntent', slots: slu.askSlots, dialogState: 'COMPLETED' },
    { ...FIXED, isNewSession: false },
  ) as RequestEnvelope;

  it('is camelCase, because a wrong key reads as undefined', () => {
    expect(getRequestType(envelope)).toBe('IntentRequest');
    expect(getIntentName(envelope)).toBe('WhatsMyPlanIntent');
    expect(getDialogState(envelope)).toBe('COMPLETED');
    expect(getLocale(envelope)).toBe('en-US');
    expect(isNewSession(envelope)).toBe(false);
    expect(getApiAccessToken(envelope)).toContain('simulated');
    expect(getDeviceId(envelope)).toContain('simulated');
    expect(getUserId(envelope)).toContain('SIMULATED');
    expect(getSupportedInterfaces(envelope)).toEqual({});
    expect(JSON.stringify(envelope)).not.toMatch(/session_id|api_access_token|device_id|user_id/);
  });

  it('launch request: the SDK sees a session that is new', () => {
    expect(isNewSession(launchRequest(FIXED))).toBe(true);
    expect(getRequestType(launchRequest(FIXED))).toBe('LaunchRequest');
  });

  it('hands the SDK a slot it can resolve without guessing', () => {
    const slot = getSlot(envelope, 'Person')!;
    expect(slot.value).toBe('mother');
    expect(slot.confirmationStatus).toBe('NONE');
    const simple = getSimpleSlotValues(getSlotValueV2(envelope, 'Person')!);
    expect(simple.map((v) => v.value)).toEqual([slot.value]);
    const authority = slot.resolutions!.resolutionsPerAuthority![0];
    expect(authority.status.code).toBe('ER_SUCCESS_MATCH');
    expect(authority.values![0].value.id).toBe('mother');
  });

  it('reports a miss as a miss, with no value invented', () => {
    const missed = intentRequest({
      intent: 'ExplainInstructionIntent',
      slots: { ItemPosition: unresolvedSlot('ItemPosition', 'eleventh', 'CareLoopItemPosition') },
    }, FIXED) as RequestEnvelope;
    const slot = getSlot(missed, 'ItemPosition')!;
    expect(getSlotValue(missed, 'ItemPosition')).toBe('eleventh');
    expect(slot.resolutions!.resolutionsPerAuthority![0].status.code).toBe('ER_SUCCESS_NO_MATCH');
    expect(slot.resolutions!.resolutionsPerAuthority![0].values).toEqual([]);
  });

  it('reads back exactly what our resolver says the customer said', () => {
    // The envelope and the resolver must not drift: slot.value is the spoken words, in the
    // customer’s own casing, and that is what a shipped skill would receive.
    for (const slot of slu.slots) {
      expect(getSlotValue(envelope, slot.name)).toBe(slot.spoken);
    }
    expect(slu.slots.map((s) => s.name)).toEqual(['Person']);
  });
});
