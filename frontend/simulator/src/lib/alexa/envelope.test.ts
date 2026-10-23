/** Envelope tests: the shape a device would hand the skill. Everything here is checked against
 *  `ask-sdk-model`’s field names (camelCase), because a wrong key is a rejected token in the real
 *  world and a silent fiction here. */
import { describe, expect, it } from 'vitest';
import {
  SIM_DEVICE, buildSlot, confirmIntent, delegate, elicitSlot, intentRequest,
  launchRequest, sessionEndedRequest, systemContext, unresolvedSlot, slotToElicitFor,
} from './envelope';

const FIXED = { timestamp: '2026-10-22T04:00:00Z', requestId: 'simreq-fixed', sessionId: 'simsession-fixed' };

describe('request envelopes', () => {
  it('are camelCase, with the four top-level keys the model declares', () => {
    const envelope = launchRequest(FIXED);
    expect(Object.keys(envelope).sort()).toEqual(['context', 'request', 'session', 'version']);
    expect(envelope.version).toBe('1.0');
    expect(envelope.session?.sessionId).toBe('simsession-fixed');
    expect(envelope.context.System.apiEndpoint).toBe(SIM_DEVICE.apiEndpoint);
    expect(envelope.context.System.apiAccessToken).toBe(SIM_DEVICE.apiAccessToken);
  });

  it('stamp a launch as a new session and an intent as a continuation', () => {
    expect(launchRequest(FIXED).session?.new).toBe(true);
    expect(intentRequest({ intent: 'ListPeopleIntent', slots: {} }, FIXED).session?.new).toBe(false);
  });

  it('carry the identity as obviously simulated', () => {
    const envelope = launchRequest(FIXED);
    expect(envelope.session?.application.applicationId).toContain('simulated');
    expect(envelope.context.System.user.userId).toContain('SIMULATED');
    expect(envelope.context.System.device?.deviceId).toContain('simulated');
  });

  it('refuse an intent the shipped model does not have', () => {
    expect(() => intentRequest({ intent: 'WeatherIntent' as never, slots: {} }, FIXED))
      .toThrow(/not in the shipped interaction model/);
  });

  it('only report a confirmation status the model can express', () => {
    const IN_PROGRESS = intentRequest(
      { intent: 'ListPeopleIntent', slots: {}, confirmationStatus: 'IN_PROGRESS' }, FIXED,
    );
    expect(IN_PROGRESS.request.type).toBe('IntentRequest');
    if (IN_PROGRESS.request.type !== 'IntentRequest') throw new Error('unreachable');
    expect(IN_PROGRESS.request.intent.confirmationStatus).toBe('NONE');
    const confirmed = intentRequest(
      { intent: 'RemoveInstructionIntent', slots: {}, confirmationStatus: 'CONFIRMED' }, FIXED,
    );
    if (confirmed.request.type !== 'IntentRequest') throw new Error('unreachable');
    expect(confirmed.request.intent.confirmationStatus).toBe('CONFIRMED');
  });

  it('keep session attributes the skill set on an earlier turn', () => {
    const envelope = intentRequest({ intent: 'ListPeopleIntent', slots: {} }, {
      ...FIXED, sessionAttributes: { lastUtterance: 'what is next' },
    });
    expect(envelope.session?.attributes).toEqual({ lastUtterance: 'what is next' });
  });

  it('end a session with a reason the platform defines', () => {
    const ended = sessionEndedRequest('EXCEEDED_MAX_REPROMPTS', FIXED);
    if (ended.request.type !== 'SessionEndedRequest') throw new Error('unreachable');
    expect(ended.request.reason).toBe('EXCEEDED_MAX_REPROMPTS');
  });

  it('expose the same system block on every request kind', () => {
    expect(systemContext(FIXED)).toEqual(launchRequest(FIXED).context.System);
  });
});

describe('slots and their resolution', () => {
  it('records an exact match as ER_SUCCESS_MATCH with the model id', () => {
    const slot = buildSlot('Person', 'mother', { slotType: 'CareLoopPerson', value: 'mother', id: 'mother', state: 'success' });
    const authority = slot.resolutions?.resolutionsPerAuthority?.[0];
    expect(authority?.authority).toBe('CareLoopPerson');
    expect(authority?.status.code).toBe('ER_SUCCESS_MATCH');
    expect(authority?.values?.[0].value.name).toBe('mother');
    expect((slot.slotValue as { value?: string } | undefined)?.value).toBe('mother');
  });

  it('records a miss with an empty value list, never a made-up one', () => {
    const slot = buildSlot('ItemPosition', 'eleventh', { slotType: 'CareLoopItemPosition', value: 'eleventh', state: 'no-match' });
    const authority = slot.resolutions?.resolutionsPerAuthority?.[0];
    expect(authority?.status.code).toBe('ER_SUCCESS_NO_MATCH');
    expect(authority?.values).toEqual([]);
  });

  it('keeps an unresolved slot honest', () => {
    const slot = unresolvedSlot('Person', 'cousin');
    expect(slot.value).toBe('cousin');
    const authority = slot.resolutions?.resolutionsPerAuthority?.[0];
    expect(authority?.status.code).toBe('ER_SUCCESS_NO_MATCH');
    expect(authority?.values).toEqual([]);
    expect(authority?.authority).toBe('CareLoopUnresolved');
  });

  it('names the slot type as the authority when one is known', () => {
    const slot = unresolvedSlot('ItemPosition', 'eleventh', 'CareLoopItemPosition');
    expect(slot.resolutions?.resolutionsPerAuthority?.[0].authority).toBe('CareLoopItemPosition');
  });
});

describe('dialog directives', () => {
  it('name themselves the way ask-sdk-core names them', () => {
    expect(elicitSlot('AddVerbalNoteIntent', 'NoteText').type).toBe('Dialog.ElicitSlot');
    expect(confirmIntent('RemoveInstructionIntent').type).toBe('Dialog.ConfirmIntent');
    expect(delegate('WhatsMyPlanIntent').type).toBe('Dialog.Delegate');
  });

  it('carry the intent they are asking about', () => {
    const directive = elicitSlot('AddVerbalNoteIntent', 'NoteText', {});
    expect(directive.updatedIntent?.name).toBe('AddVerbalNoteIntent');
    expect('slotToElicit' in directive ? directive.slotToElicit : null).toBe('NoteText');
  });

  it('ask for the slot the model actually routes for that intent', () => {
    expect(slotToElicitFor('AddVerbalNoteIntent', 'Who should I ask about?')).toBe('Person');
    expect(slotToElicitFor('RemoveInstructionIntent', 'Which item should I remove?')).toBe('ItemPosition');
    expect(slotToElicitFor('AboutCareLoopIntent', 'anything')).toBeNull();
  });
});
