/** Dialog delegation, driven by the shipped artifact: the model says which slot is missing and what to
 *  ask with, the device asks, and nothing is sent to the skill until the chain is complete.
 *
 *  These are the pure parts of the chain (`nextDialogStep`, `fillSlot`, `buildDialogTurn`,
 *  `heldDialog`) — the same functions the Device Lab runtime calls, without the network. */
import { describe, expect, it } from 'vitest';
import { toView } from '@careloop/client';
import { PROMPTS, type AskIntentName } from './interactionModel';
import { declaredRouting, declaredSlots, nextDialogStep, slotToElicitFor } from './envelope';
import { fillSlot, resolveUtterance, toAskSlots } from './slu';
import { buildAskTurn, buildDialogTurn, fillPrompt, heldDialog } from './response';

const PEOPLE = ['mother', 'Josephine'];

/** Only the slots the model marked required, in the model's order. */
function requiredOf(intent: 'MarkDoneIntent' | 'RemoveInstructionIntent' | 'AddVerbalNoteIntent'): string[] {
  return declaredSlots(intent).filter((slot) => slot.required).map((slot) => slot.name);
}

describe('the dialog the model declares', () => {
  it('elicits a required slot a shipped sample never spoke', () => {
    const result = resolveUtterance('that one is sorted', { people: PEOPLE });
    expect(result.intent).toBe('MarkDoneIntent');
    const step = nextDialogStep('MarkDoneIntent', result.slots);
    expect(step).toEqual({ stage: 'elicit', slot: 'ItemPosition', promptId: 'ElicitItemPrompt' });
  });

  it('asks for every required slot in the model’s own order and never for an optional one', () => {
    expect(requiredOf('MarkDoneIntent')).toEqual(['ItemPosition']);
    expect(requiredOf('AddVerbalNoteIntent')).toEqual(['NoteText']);
    expect(requiredOf('RemoveInstructionIntent')).toEqual(['ItemPosition']);
    // Nothing is required to read a plan, so a plan question is never held back by the dialog.
    const read = resolveUtterance('what is next', { people: PEOPLE });
    expect(nextDialogStep('WhatsMyPlanIntent', read.slots)).toBeNull();
  });

  it('moves to the confirmation once the item is known, and only then', () => {
    const opened = resolveUtterance('that instruction is wrong, remove it', { people: PEOPLE });
    expect(nextDialogStep('RemoveInstructionIntent', opened.slots)?.stage).toBe('elicit');
    const item = fillSlot('RemoveInstructionIntent', 'ItemPosition', 'the second one', PEOPLE);
    const merged = opened.slots.map((slot) => (slot.name === 'ItemPosition' ? item : slot));
    expect(nextDialogStep('RemoveInstructionIntent', merged)).toEqual({
      stage: 'confirm', slot: null, promptId: 'ConfirmRemovePrompt',
    });
  });

  it('re-asks the same slot when the answer matches nothing', () => {
    const opened = resolveUtterance('mark the first one as done', { people: PEOPLE });
    const wrong = fillSlot('MarkDoneIntent', 'ItemPosition', 'the purple one', PEOPLE);
    expect(wrong.state).toBe('no-match');
    expect(wrong.resolved).toBeNull();
    // The collected item is still the one that was said, so the chain can be re-asked, not invented.
    const merged = opened.slots.map((slot) => (slot.name === 'ItemPosition' ? wrong : slot));
    expect(nextDialogStep('MarkDoneIntent', merged)?.slot).toBe('ItemPosition');
  });

  it('takes a free-text answer verbatim, because it becomes the stored record', () => {
    const filled = fillSlot('AddVerbalNoteIntent', 'NoteText', 'The nurse said the swelling went down', PEOPLE);
    expect(filled.state).toBe('wildcard');
    expect(filled.id).toBe('The nurse said the swelling went down');
    expect(nextDialogStep('AddVerbalNoteIntent', [filled])).toBeNull();
  });

  it('stops at the argument the model routes, not the synonym it heard', () => {
    const item = fillSlot('MarkDoneIntent', 'ItemPosition', 'the second one', PEOPLE);
    const person = fillSlot('MarkDoneIntent', 'Person', 'for Josephine', PEOPLE);
    const routing = declaredRouting('MarkDoneIntent');
    expect(routing.slotArguments).toEqual({ ItemPosition: 'ref', Person: 'who' });
    expect(item.id).toBe('second');
    // the household's own spelling, not the lower-cased synonym the artifact lists
    expect(person.resolved ?? person.id).toBe('Josephine');
  });

  it('picks the slot a server question is really asking about, then the required one', () => {
    expect(slotToElicitFor('AddVerbalNoteIntent', 'Who should I ask about?', [])).toBe('Person');
    expect(slotToElicitFor('AddVerbalNoteIntent', 'Who should I ask about?', ['Person'])).toBe('NoteText');
    expect(slotToElicitFor('RemoveInstructionIntent', 'anything at all', [])).toBe('ItemPosition');
    expect(slotToElicitFor('RemoveInstructionIntent', 'anything at all', ['ItemPosition'])).toBe('Person');
    expect(slotToElicitFor('AboutCareLoopIntent', 'anything', [])).toBeNull();
  });
});

describe('a turn that asks instead of calling', () => {
  const opened = resolveUtterance('that one is sorted', { people: PEOPLE });
  const step = nextDialogStep('MarkDoneIntent', opened.slots)!;
  const turn = buildDialogTurn({
    intent: 'MarkDoneIntent', step, slots: opened.slots,
    askSlots: toAskSlots('MarkDoneIntent', opened.slots),
    sessionAttributes: { lastUtterance: 'that one is sorted' },
  });

  it('speaks the model’s own prompt and nothing else', () => {
    expect(turn.spoken).toBe(PROMPTS.ElicitItemPrompt);
    expect(turn.state).toBe('elicit');
    expect(turn.directives).toEqual([
      { type: 'Dialog.ElicitSlot', slotToElicit: 'ItemPosition',
        updatedIntent: { name: 'MarkDoneIntent', confirmationStatus: 'NONE',
          slots: toAskSlots('MarkDoneIntent', opened.slots) } },
    ]);
    expect(turn.envelope.response.shouldEndSession).toBe(false);
    expect(turn.card.body).toBe(turn.spoken);
  });

  it('names no tool, because nothing was called', () => {
    expect(turn.envelope.sessionAttributes?.careloop.tool).toBeNull();
    expect(turn.masked.count).toBe(0);
    expect(turn.problems).toEqual([]);
  });

  it('round-trips the pending intent through the session attributes it returns', () => {
    const attrs = turn.envelope.sessionAttributes as Record<string, unknown>;
    expect(attrs.lastUtterance).toBe('that one is sorted');
  });

  it('says the model’s kept-line on a refusal and asks for no directive', () => {
    const removed = resolveUtterance('remove the second one from the plan', { people: PEOPLE });
    const confirm = nextDialogStep('RemoveInstructionIntent', removed.slots)!;
    expect(confirm.stage).toBe('confirm');
    const asked = buildDialogTurn({
      intent: 'RemoveInstructionIntent', step: confirm, slots: removed.slots,
      askSlots: toAskSlots('RemoveInstructionIntent', removed.slots),
    });
    expect(asked.state).toBe('confirm');
    expect(asked.directives[0]?.type).toBe('Dialog.ConfirmIntent');
    const refused = buildDialogTurn({
      intent: 'RemoveInstructionIntent', step: null, slots: removed.slots,
      askSlots: toAskSlots('RemoveInstructionIntent', removed.slots), denied: true,
    });
    expect(refused.spoken).toBe(PROMPTS.KeptPrompt);
    expect(refused.directives).toEqual([]);
    expect(refused.state).toBe('await');
    expect(refused.envelope.response.shouldEndSession).toBe(false);
  });

  it('is speakable from what the dialog guarantees: no placeholder survives the confirmation', () => {
    const removed = resolveUtterance('remove the second one from the plan', { people: PEOPLE });
    const body = fillPrompt(PROMPTS.ConfirmRemovePrompt, removed.slots);
    expect(body).toBe(PROMPTS.ConfirmRemovePrompt.replace('{ItemPosition}', 'second'));
    expect(body).not.toMatch(/[{}]/);
    const turn = buildDialogTurn({
      intent: 'RemoveInstructionIntent', step: nextDialogStep('RemoveInstructionIntent', removed.slots)!,
      slots: removed.slots, askSlots: toAskSlots('RemoveInstructionIntent', removed.slots),
    });
    expect(turn.problems).toEqual([]);
    expect(turn.words).toBeGreaterThan(3);
  });
});

describe('the dialog a server answer leaves open', () => {
  /** One server answer, wrapped the way the device wraps it. */
  const asked = (structured: Record<string, unknown>, intent: AskIntentName, spoken: string) =>
    buildAskTurn({
      tool: 'x', spoken, view: toView(structured), result: undefined, intent,
      askSlots: toAskSlots(intent, resolveUtterance(spoken, { people: PEOPLE }).slots),
    });

  it('holds the slot the server asked about, with the artifact’s own prompt', () => {
    const turn = asked({ needs_clarification: true, question: 'Who should I ask about?' },
      'WhatsMyPlanIntent', 'Who should I ask about?');
    expect(heldDialog('WhatsMyPlanIntent', turn)).toEqual({
      stage: 'elicit', slot: 'Person', promptId: 'ClarifyPersonPrompt',
    });
  });

  it('holds the yes a destructive read-back asked for', () => {
    const turn = asked({ needs_confirmation: true, question: 'Remove the second item?' },
      'RemoveInstructionIntent', 'Remove the second item?');
    expect(heldDialog('RemoveInstructionIntent', turn)).toEqual({
      stage: 'confirm', slot: null, promptId: 'ConfirmRemovePrompt',
    });
  });

  it('holds nothing after a plain answer, so the next phrase is a new request', () => {
    const turn = asked({ items: [], person: 'mother' }, 'WhatsMyPlanIntent', 'Nothing is pending.');
    expect(heldDialog('WhatsMyPlanIntent', turn)).toBeNull();
  });
});
