/** The simulated voice profile: the device *asserts*, the service *decides*.
 *
 *  On the browser and in a scripted driver nobody hears a voice, so who is speaking is stated and
 *  passed as the request's own `params._meta`. What that person may hear is not a device setting: it
 *  is the boundary the household stored, applied by the service, refused in the service's own
 *  sentences and recorded in the household's ledger (`tests/test_voice_profile.py` proves that half).
 *  This file proves the device half: the claim changes no argument, calls no tool, writes nothing and
 *  cannot make a decision up.
 */
import { describe, expect, it } from 'vitest';
import { DELIVERY, INTERACTION_MODEL, PROMPTS, VOICE_PROFILE } from './interactionModel';
import { fillPrompt } from './response';
import { INTENT_BY_NAME, declaredRouting, declaredSlots } from './envelope';
import { resolveUtterance, toToolArgs } from './slu';

const PEOPLE = ['Papa', 'Mother'];

describe('the assertion the artifact declares', () => {
  it('is a device-local intent with a real prompt and a real slot', () => {
    const intent = INTENT_BY_NAME[VOICE_PROFILE.intent];
    expect(declaredRouting(VOICE_PROFILE.intent).deviceLocal).toBe(true);
    expect(declaredRouting(VOICE_PROFILE.intent).tool).toBe('');
    expect(intent.routing.tool).toBeNull();
    expect(PROMPTS[VOICE_PROFILE.prompt]).toBeTruthy();
    expect(intent.slots?.[0]?.name).toBe(VOICE_PROFILE.slot);
    expect(intent.slots?.[0]?.required).toBe(true);
    expect(declaredRouting(VOICE_PROFILE.intent).slotArguments).toEqual({});
  });

  it('says out loud that it is simulated, that the device decides nothing and that it writes nothing', () => {
    // These four flags are the whole honesty of the feature. A browser device cannot hear a voice, so
    // a claim that did not label itself simulated would be a lie about biometrics.
    expect(VOICE_PROFILE.simulated).toBe(true);
    expect(VOICE_PROFILE.deviceDecidesScope).toBe(false);
    expect(VOICE_PROFILE.writesAnything).toBe(false);
    expect(VOICE_PROFILE.sessionScopedOnly).toBe(true);
    expect(INTERACTION_MODEL.guardrails.speakerScope.deviceDecides).toBe(false);
    expect(INTERACTION_MODEL.guardrails.speakerScope.refusalStoresAnything).toBe(false);
  });

  it('names its two _meta fields under our own namespace, and never the same key twice', () => {
    // MCP reserves a slash-namespaced `_meta` key for exactly this; a bare key would collide with the
    // protocol's own, and one key cannot carry both who is speaking and how they were identified.
    expect(VOICE_PROFILE.speakerMetaKey.startsWith('careloop/')).toBe(true);
    expect(VOICE_PROFILE.profileMetaKey.startsWith('careloop/')).toBe(true);
    expect(VOICE_PROFILE.speakerMetaKey).not.toBe(VOICE_PROFILE.profileMetaKey);
    expect(VOICE_PROFILE.profileValue).toBe('careloop-simulated/1');
  });

  it('speaks the person it was given, and only what the slot can hold', () => {
    const line = fillPrompt(PROMPTS[VOICE_PROFILE.prompt], [{ name: VOICE_PROFILE.slot, spoken: 'Mother' }]);
    expect(line).toContain('Mother');
    expect(line).not.toContain('{');
    expect(line.match(/\{Person\}/g)).toBeNull();
    // unfilled, it keeps the placeholder rather than claiming a name nobody said
    expect(fillPrompt(PROMPTS[VOICE_PROFILE.prompt], [])).toContain('{Person}');
  });
});

describe('a claim heard from speech', () => {
  it('resolves against the household roster, and reaches no tool', () => {
    for (const [word, want] of [['Papa', 'Papa'], ['papa', 'Papa'], ['Mother', 'Mother']]) {
      const result = resolveUtterance(`this is ${word}`, { people: PEOPLE });
      expect(result.intent, word).toBe(VOICE_PROFILE.intent);
      expect(result.tool, word).toBeNull();
      const slot = result.slots.find((one) => one.name === VOICE_PROFILE.slot);
      expect((slot?.resolved ?? '').toLowerCase(), word).toBe(want.toLowerCase());
    }
  });

  it('sends nothing: the artifact maps no slot of this intent onto an argument', () => {
    const result = resolveUtterance('answer as Mother', { people: PEOPLE });
    expect(result.intent).toBe(VOICE_PROFILE.intent);
    expect(toToolArgs(result)).toEqual({});
  });

  it('asks who was meant rather than claiming a name it cannot place', () => {
    // A relation the artifact knows is claimable even when this household has never used it — and it
    // is the service that refuses it then. A name that matches nothing at all is asked back: the
    // runtime elicits the slot, with the prompt this artifact declares for it.
    const result = resolveUtterance('this is Doctor Okafor', { people: PEOPLE });
    expect(result.intent).toBe(VOICE_PROFILE.intent);
    expect(result.slots.find((slot) => slot.name === VOICE_PROFILE.slot)?.resolved).toBeNull();
    expect(declaredSlots(VOICE_PROFILE.intent)[0].prompt).toBe('ClarifyPersonPrompt');
    const claim = resolveUtterance('this is Mother', { people: ['Papa'] });
    expect(claim.slots.find((slot) => slot.name === VOICE_PROFILE.slot)?.resolved).toBe('mother');
  });

  it('does not steal an ordinary question about who is about', () => {
    // "who is in my household" is ListPeopleIntent's own sample. A claim sample needs a *name* in it,
    // and one shared content word is not evidence, so the two must not trade places.
    expect(resolveUtterance('who is in my household', { people: PEOPLE }).intent).toBe('ListPeopleIntent');
    expect(resolveUtterance('who do we have here', { people: PEOPLE }).intent).toBeNull();
    // A question can only ever be scored at this intent, never matched — and with no name in it the
    // slot stays unresolved, so the device asks who is speaking instead of claiming somebody.
    const asked = resolveUtterance('who am i talking to', { people: PEOPLE });
    expect(asked.matched).not.toBe('literal');
    expect(asked.slots.find((slot) => slot.name === VOICE_PROFILE.slot)?.resolved ?? null).toBeNull();
  });

  it('is the only intent besides delivery that routes at no tool', () => {
    const unrouteable = INTERACTION_MODEL.languageModel.intents
      .filter((intent) => intent.routing.tool === null)
      .map((intent) => intent.name);
    expect(unrouteable.sort()).toEqual([DELIVERY.intent, VOICE_PROFILE.intent].sort());
  });
});
