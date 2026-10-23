/** The one turn the customer never asked for: when it is armed, what it may say and how the clock
 *  treats the wait. Every rule here is read out of the shipped artifact (`proactive.reminder`), and
 *  the headless driver applies the same rules to the same payload — which is what makes the two
 *  devices comparable rather than merely similar.
 */
import { describe, expect, it } from 'vitest';
import { getRequestType } from 'ask-sdk-core';
import { toView } from '@careloop/client';
import type { ToolResult } from '@careloop/client';
import { PROMPTS, PROACTIVE, VOICE_BUDGET } from './interactionModel';
import { DeviceMachine } from './machine';
import { armReminder, buildAskTurn, hasPending } from './response';
import { sessionResumedRequest, startConnection } from './envelope';
import type { AskIntentName } from './interactionModel';

const SPOKEN = 'Today: morning — Check the blood pressure.';
const PENDING_RESULT = {
  content: [{ type: 'text', text: SPOKEN }],
  structuredContent: {
    person: 'Papa',
    items: [{ text: 'Check the blood pressure', ref: 'i-abc123', kind: 'task' }],
  },
  _meta: { ui: { resourceUri: 'ui://careloop/card' } },
} as unknown as ToolResult;

function planTurn(
  intent: AskIntentName,
  result: ToolResult,
  extra: Record<string, unknown> = {},
) {
  const structured = (result.structuredContent ?? {}) as Record<string, unknown>;
  return buildAskTurn({
    tool: 'todays_plan', intent, spoken: SPOKEN, result, view: toView(structured), ...extra,
  });
}

describe('the reminder the artifact declares', () => {
  it('is a resolvable connection on a read-only intent, with the artifact’s own line', () => {
    expect(PROACTIVE.connectionUri.startsWith('connection://')).toBe(true);
    // RESUME_SESSION is the only value that ever produces a resumed turn; SEND_ERRORS_ONLY would
    // arm a connection that never comes back to be spoken.
    expect(PROACTIVE.onCompletion).toBe('RESUME_SESSION');
    expect(PROACTIVE.prompt).toBe('ReminderPrompt');
    expect(PROMPTS[PROACTIVE.prompt]).toBeTruthy();
    expect(PROACTIVE.resumeAfterSeconds).toBeGreaterThan(0);
    expect(PROACTIVE.writesAnything).toBe(false);
  });

  it('arms on a plain answer that still has something pending', () => {
    const ask = planTurn(PROACTIVE.intent, PENDING_RESULT);
    expect(hasPending(PENDING_RESULT.structuredContent)).toBe(true);
    expect(ask.connection).toEqual({
      token: expect.stringMatching(/^careloop-reminder-\d+$/),
      uri: PROACTIVE.connectionUri,
      afterMs: PROACTIVE.resumeAfterSeconds * 1000,
    });
  });

  it('puts the connection directive in the response and keeps the session open to resume', () => {
    const ask = planTurn(PROACTIVE.intent, PENDING_RESULT);
    const directives = ask.envelope.response.directives ?? [];
    expect(directives.map((directive) => directive.type)).toEqual([PROACTIVE.directiveType]);
    expect(ask.envelope.response.shouldEndSession).toBe(false);
    // A session that has ended has nothing to come back to, and a connection directive travels alone.
    expect(ask.state).toBe('await');
  });

  it('does not arm on an empty plan, on a question it still owes, or on another intent', () => {
    const empty = { structuredContent: { person: 'Papa', items: [] } } as unknown as ToolResult;
    expect(planTurn(PROACTIVE.intent, empty).connection).toBeNull();

    const clarify = {
      structuredContent: { needs_clarification: true, question: 'Who is this about?' },
    } as unknown as ToolResult;
    expect(planTurn(PROACTIVE.intent, clarify).connection).toBeNull();

    expect(planTurn('WhatsMyPlanIntent', PENDING_RESULT).connection).toBeNull();
  });

  it('never arms the reminder turn itself — a reminder must not breed reminders', () => {
    const resumed = planTurn(PROACTIVE.intent, PENDING_RESULT, { resumed: true });
    expect(resumed.connection).toBeNull();
    expect(resumed.envelope.response.directives).toBeUndefined();
    // The rule is the function the store calls, not something only the builder knows.
    expect(armReminder({
      tool: 'todays_plan', spoken: SPOKEN, view: toView({ person: 'Papa' }),
      result: PENDING_RESULT, intent: PROACTIVE.intent, resumed: true,
    }, 'await')).toBeNull();
  });
});

describe('the request the device sends for itself', () => {
  it('carries the completed connection and the token that armed it', () => {
    const directive = startConnection('careloop-reminder-1');
    expect(directive).toEqual({
      type: PROACTIVE.directiveType,
      uri: PROACTIVE.connectionUri,
      onCompletion: PROACTIVE.onCompletion,
      token: 'careloop-reminder-1',
    });

    const envelope = sessionResumedRequest('careloop-reminder-1', {
      sessionId: 'simsession-fixed', requestId: 'simreq-fixed', timestamp: '2026-10-22T00:00:00Z',
    });
    const request = envelope.request as unknown as Record<string, unknown>;
    expect(request.type).toBe(PROACTIVE.requestType);
    expect(request.cause).toEqual({
      type: PROACTIVE.causeType, token: 'careloop-reminder-1', status: { code: 'SUCCESS' },
    });
    expect('intent' in request).toBe(false);        // nobody said anything, so there is no intent
    // Amazon’s own reader sees it as the request type the artifact names.
    expect(getRequestType(envelope)).toBe(PROACTIVE.requestType);
  });
});

describe('the device clock while a connection is open', () => {
  const armed = { uri: PROACTIVE.connectionUri, afterMs: PROACTIVE.resumeAfterSeconds * 1000 };

  it('does not reprompt a customer who has not been left a question, then resumes its own session', () => {
    const said: string[] = [];
    const m = new DeviceMachine({ speak: (t) => { if (t) said.push(t); }, reprompt: (t) => said.push(t) });
    m.wake();
    m.heard('what is on today for Papa');
    const ask = planTurn(PROACTIVE.intent, PENDING_RESULT);
    m.answered(ask);
    const armedAt = m.nowMs;
    m.armed(armed);
    // The interval has to outlast the speech, or "it came back on time" proves nothing.
    expect(armed.afterMs).toBeGreaterThan(ask.estimateMs);

    m.tick(armed.afterMs - 1000);        // speech finished, and still a second short
    expect(m.state).toBe('awaiting');
    expect(said[said.length - 1]).not.toBe(PROMPTS.HelpPrompt);   // silence did not reprompt
    m.tick(1000);                        // the declared interval comes due
    m.resumed();
    expect(m.state).toBe('processing');
    expect(m.events.at(-1)?.kind).toBe('resume');
    expect(m.nowMs - armedAt).toBe(armed.afterMs);
  });

  it('gives the window back to the customer when they speak first', () => {
    const m = new DeviceMachine();
    m.wake();
    m.heard('what is on today for Papa');
    m.answered(planTurn(PROACTIVE.intent, PENDING_RESULT));
    m.armed(armed);
    m.tick(armed.afterMs - 1000);
    expect(m.state).toBe('awaiting');
    // The customer speaks: the turn is theirs again, so the next answer opens the silence window.
    m.followUp('what changed');
    m.answered(planTurn('WhatsMyPlanIntent', PENDING_RESULT));   // this intent arms nothing
    m.speechFinished();
    m.tick(VOICE_BUDGET.silenceTimeoutSeconds * 1000);
    expect(m.state).toBe('reprompting');
  });
});
