/** The device clock: timers come from the shipped model, and a phrase the model cannot match is
 *  answered locally — spoken, timed, reprompted with the model's own prompt, never an invented one. */
import { describe, expect, it } from 'vitest';
import { DeviceMachine, SIM_MAX_REPROMPTS } from './machine';
import { PROMPTS, VOICE_BUDGET } from './interactionModel';

function machineSpeakingInto(said: string[]): DeviceMachine {
  return new DeviceMachine({
    speak: (text) => { if (text) said.push(text); },
    reprompt: (text) => said.push(text),
  });
}

describe('a device-local answer (no intent matched)', () => {
  it('is spoken, held for the model’s window and leaves no skill turn behind', () => {
    const said: string[] = [];
    const m = machineSpeakingInto(said);
    m.wake();
    m.heard('tell me a joke about trains');
    m.answeredLocal({ spoken: PROMPTS.FallbackPrompt, words: 12, estimateMs: 3000 });

    expect(m.state).toBe('speaking');
    expect(said).toEqual([PROMPTS.FallbackPrompt]);
    expect(m.currentTurn).toBeNull();          // there was never a response envelope to hold
    m.tick(3000);
    expect(m.state).toBe('awaiting');           // the silence window is the model's, not ours
  });

  it('reprompts with the model’s help prompt, then ends the session', () => {
    const said: string[] = [];
    const m = machineSpeakingInto(said);
    m.wake();
    m.heard('tell me a joke about trains');
    m.answeredLocal({ spoken: PROMPTS.FallbackPrompt, words: 12, estimateMs: 1000 });
    m.tick(1000);                                // speech finished → window open

    for (let i = 0; i < SIM_MAX_REPROMPTS; i += 1) {
      m.tick(VOICE_BUDGET.silenceTimeoutSeconds * 1000);
      expect(m.state).toBe('reprompting');
      expect(said[said.length - 1]).toBe(PROMPTS.HelpPrompt);
      m.tick(VOICE_BUDGET.repromptAfterSeconds * 1000);
    }
    m.tick(VOICE_BUDGET.silenceTimeoutSeconds * 1000);
    expect(m.state).toBe('closed');
    expect(m.events.at(-1)?.note).toContain('EXCEEDED_MAX_REPROMPTS');
  });
});
