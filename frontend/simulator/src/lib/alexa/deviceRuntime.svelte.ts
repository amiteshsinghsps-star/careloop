/** The Device Lab store: drives the real voice path through a simulated Alexa+ device runtime.
 *
 *  Order of one turn, exactly as a device would do it: wake → listen → resolve the phrase against
 *  the shipped interaction model → call the tool over real JSON-RPC (the same `say()` the main
 *  simulator uses, so there is no second code path) → wrap the answer in an ASK response envelope →
 *  speak it → hold the window the model asks for. The machine's clock is advanced by a browser
 *  interval here, and by explicit `tick()` calls in tests.
 */
import type { Slot } from 'ask-sdk-model';
import type { AskIntentName } from './interactionModel';
import { DELIVERY, PROMPTS, PROACTIVE, VOICE_BUDGET, VOICE_PROFILE } from './interactionModel';
import { DeviceMachine, SIM_MAX_REPROMPTS, type MachineEvent } from './machine';
import {
  buildAskTurn, buildDialogTurn, fillPrompt, heldDialog,
  type AskConnection, type AskDelivery, type AskTurn,
} from './response';
import { escapeXml, analyze } from './ssml';
import { fillSlot, resolveUtterance, toAskSlots, toToolArgs, type SluResult, type SluSlot } from './slu';
import {
  declaredRouting, declaredSlots, intentRequest, nextDialogStep, sessionEndedRequest,
  sessionResumedRequest, slotPrompt, type DialogStep, type EnvelopeOptions,
} from './envelope';
import { route, type ResultView, type ToolResult } from '@careloop/client';
import { refreshPeople, say, device } from '../device.svelte';
import { session } from '../session.svelte';
import { cancelSpeech, speak } from '../speech.svelte';
import { downloadText } from '../format';
import type { Turn } from '../session.svelte';

/** What both devices write on the one turn nobody spoke, so `--compare` lines the rows up. */
const RESUMED_LABEL = '(the device resumed its own session)';

/** A connection this device started and has not come back from yet. It carries the read the reminder
 *  repeats, because the resumed turn asks the same question the customer already asked — it does not
 *  invent a new one. */
export interface ArmedReminder extends AskConnection {
  tool: string;
  args: Record<string, unknown>;
  /** The device clock reading the connection comes due at. */
  dueAtMs: number;
}

export interface LabTurn {
  id: string;
  atMs: number;
  utterance: string;
  /** What the model-driven resolver heard. */
  slu: SluResult;
  /** What the production router in frontend/client/src/utterance.ts chose. */
  routed: { tool: string; args: Record<string, unknown>; read: string };
  /** The two must agree on the tool, or the Lab says so out loud. */
  agree: boolean;
  requestEnvelope: unknown;
  ask: AskTurn | null;
  /** True when the shipped model did not match: the answer came from the device, not the skill. */
  local: boolean;
  /** True when a request actually left the device. The router row below names a tool even on a turn
   *  the model held back, so "was it sent" has to be recorded, not inferred. */
  called: boolean;
  /** Set when the model is still holding this exchange: asking for a slot, or for a yes or no. No
   *  tool was called on such a turn, which is the whole point of dialog delegation. */
  dialog: { stage: DialogStep['stage'] | 'refused'; slot: string | null; promptId: string } | null;
  /** The connection this answer put on the device, if any: the one thing it may set up for later. */
  connection: AskConnection | null;
  /** True for the turn the device started itself, on that connection. */
  proactive: boolean;
  spoken: string;
  /** How this answer was spoken, off `presentation.delivery.styles`: the pace, the level and the
   *  word ceiling the style applied. Null on every turn nobody asked to be spoken to differently. */
  delivery: AskDelivery | null;
  /** The MCP `params._meta` this turn sent: the speaker claim, or nothing. Recorded so the two
   *  devices can be compared on *what was claimed*, not on what one of them inferred. */
  meta: Record<string, string> | null;
  /** The service's own sentence for a refusal, read out of its `structuredContent`. Null whenever it
   *  answered. A device never writes this text — an out-of-scope answer is refused by the service. */
  refused: string | null;
  ms: number;
  events: MachineEvent[];
  problem: string | null;
}

export interface Scenario {
  id: string;
  label: string;
  /** Plain-language description of what this proves, shown beside the button. */
  note: string;
  steps: Array<{ say?: string; waitMs?: number; bargeIn?: boolean; silence?: boolean; reminder?: boolean }>;
}

export const SCENARIOS: Scenario[] = [
  {
    id: 'golden', label: 'Ask what is next', note: 'A clean one-shot answer, then the follow-up window.',
    steps: [{ say: 'what is next for {person}' }],
  },
  {
    id: 'elicitation', label: 'Make it ask who', note: `No person named → the server asks back → an ${VOICE_BUDGET.silenceTimeoutSeconds}s window stays open.`,
    steps: [{ say: 'what is next' }, { waitMs: 400 }, { say: 'for {person}' }],
  },
  {
    id: 'chain', label: 'Ask twice, then do it', note: 'Dialog delegation from the model: a required slot is missing, so the device elicits it, then asks for the yes. Nothing is written until the third phrase. The six phrases are the same script the headless device runs, so `--compare` can diff the two turn by turn.',
    steps: [
      { say: 'what is next for Papa' }, { waitMs: 400 },
      { say: 'remove that instruction' }, { waitMs: 400 },
      { say: 'the second one' }, { waitMs: 400 },
      { say: 'no, keep it' }, { waitMs: 400 },
      { say: 'remove the third one' }, { waitMs: 400 },
      { say: 'yes' },
    ],
  },
  {
    id: 'reminder', label: 'Let it come back on its own', note: `The plan is read, the session stays open and the device starts the connection the artifact names (${PROACTIVE.connectionUri}). After the declared ${PROACTIVE.resumeAfterSeconds}s it resumes its own session and reads the same plan again — no new phrase, no new slot, nothing written. This is the same two-phrase script the headless device runs, so --compare can diff the resumed turn too.`,
    steps: [{ say: 'what is on today for Papa' }, { waitMs: 400 }, { reminder: true }],
  },
  {
    id: 'delivery', label: 'Ask for it slower, then shorter', note: `Spoken delivery is an accessibility setting, not a care decision: ${DELIVERY.intent.replace('Intent', '')} routes to no tool at all, so each style re-speaks the answer the service already returned — slower in SSML rate, then cut to ${DELIVERY.styles.brief.maxSpokenWords} words, then back to the usual way. No new sentence is ever written, and the card keeps the whole one.`,
    steps: [
      { say: 'what is next for {person}' }, { waitMs: 400 },
      { say: 'say that slower' }, { waitMs: 400 },
      { say: 'say that shorter' }, { waitMs: 400 },
      { say: 'say that the usual way' },
    ],
  },
  {
    id: 'speaker', label: 'Claim a voice, let the service decide', note: 'The device asserts who is speaking and puts that claim on every request as MCP params._meta. It decides nothing: the household\'s stored scope does, so a name that isn\'t in it is refused in the service\'s own words and written to the cell ledger — then the claim is corrected and the answer comes.',
    steps: [
      { say: 'this is Mother' }, { waitMs: 400 },
      { say: 'what is next for {person}' }, { waitMs: 400 },
      { say: 'this is {person}' }, { waitMs: 400 },
      { say: 'what is next for {person}' },
    ],
  },
  {
    id: 'confirm', label: 'Delete with a yes or no', note: 'A destructive change asks for confirmation before anything is written.',
    steps: [{ say: 'remove the second one from the plan' }, { waitMs: 400 }, { say: 'yes' }],
  },
  {
    id: 'refuse', label: 'Say no to a delete', note: 'The read-back comes first, the no is the model’s own built-in, and the kept-line turn writes nothing — the item is still on the plan.',
    steps: [{ say: 'remove the second one from the plan' }, { waitMs: 400 }, { say: 'no' }],
  },
  {
    id: 'barge', label: 'Talk over the answer', note: 'The customer interrupts playback; the device stops and listens.',
    steps: [{ say: 'who is in my household' }, { waitMs: 120 }, { bargeIn: true }, { waitMs: 200 }, { say: 'what changed' }],
  },
  {
    id: 'silence', label: 'Say nothing', note: `Silence past ${VOICE_BUDGET.silenceTimeoutSeconds}s reprompts ${SIM_MAX_REPROMPTS} times, then ends with EXCEEDED_MAX_REPROMPTS.`,
    steps: [{ say: 'what is on today' }, { silence: true }],
  },
  {
    id: 'fallback', label: 'Ask something silly', note: 'Nothing in the model matches, so the fallback prompt path is what plays.',
    steps: [{ say: 'tell me a joke about trains' }],
  },
];

interface LabState {
  turns: LabTurn[];
  running: boolean;
  scenario: string | null;
  /** Accelerated clock for autoplay: 1 means real time. */
  speed: number;
  lastTrace: string | null;
  /** The exchange the shipped model is still holding: a slot it declared and has not been given, or
   *  the yes or no its dialog rules require before a destructive call. */
  pending: { intent: AskIntentName; openedBy: string; slots: SluSlot[]; step: DialogStep } | null;
  /** The connection the device started and has not come back from. */
  reminder: ArmedReminder | null;
  /** The spoken-delivery style in force, as `presentation.delivery.styles` keys it. Session-only:
   *  a way of speaking is not a care decision and nothing about it is written anywhere. */
  delivery: string | null;
  /** Whose voice this device is answering for, as the customer said it. Session-only, and only a
   *  *claim*: the service decides what that person may hear. */
  speaker: string | null;
  /** The last plain answer this device spoke, kept so "say that slower" can re-speak that answer
   *  instead of inventing a new one. Only ever an answer with nothing owed on it. */
  held: HeldAnswer | null;
}

/** The part of an answer a delivery style may act on: the sentence the service returned, and the
 *  render model that sentence came from. Never the customer's next question. */
interface HeldAnswer {
  tool: string;
  intent: AskIntentName | null;
  spoken: string;
  view: ResultView;
  result: ToolResult | undefined;
}

export const lab = $state<LabState>({
  turns: [], running: false, scenario: null, speed: 1, lastTrace: null, pending: null,
  reminder: null, delivery: null, speaker: null, held: null,
});

export const machine = new DeviceMachine({
  speak: (text) => { if (text) speak(text); else cancelSpeech(); },
  reprompt: (text) => { if (text) speak(text); },
});

let timer: number | null = null;
let turnSeq = 0;

function ensureClock(): void {
  if (timer !== null || typeof window === 'undefined') return;
  timer = window.setInterval(() => {
    const produced = machine.tick(200 * lab.speed);
    const last = lab.turns[lab.turns.length - 1];
    if (produced.length && last) last.events = machine.toTrace();
    // A scripted run fires its own reminder step, in order; the live clock only takes over when
    // nobody is driving, which is the situation a reminder exists for.
    if (lab.reminder && !lab.running && machine.nowMs >= lab.reminder.dueAtMs) void fireReminder();
  }, 200);
}

export function stopClock(): void {
  if (timer !== null) window.clearInterval(timer);
  timer = null;
}

function currentSession(options: Partial<EnvelopeOptions>): EnvelopeOptions {
  return {
    sessionId: device.sessionId ?? 'careloop-sim-session',
    ...options,
  };
}

/** Put the machine into the states a spoken phrase puts a real device into. */
function listen(said: string): void {
  const fresh = machine.state === 'idle' || machine.state === 'closed';
  // A claim and the answer the device is holding belong to the session that is open. The artifact says
  // so (`voiceProfile.assertion.sessionScopedOnly`), and it matters: replaying the previous session's
  // plan, or carrying its speaker claim into a new customer's session, would speak care text that
  // nobody in this session asked for.
  if (fresh) {
    lab.speaker = null;
    lab.held = null;
  }
  if (fresh) machine.wake();
  else if (machine.state === 'awaiting' || machine.state === 'reprompting') machine.followUp(said);
  else machine.wake();
  machine.heard(said);
}

function nextTurnId(): string {
  turnSeq += 1;
  return `lab-${turnSeq}`;
}

/** `connection` and `proactive` are derived, `delivery` comes off the turn that was built, and `meta`
 *  is the only one the caller has to state — it is what went out on the wire, not what was answered. */
type RecordedTurn = Omit<LabTurn, 'connection' | 'proactive' | 'delivery' | 'meta'>
  & Partial<Pick<LabTurn, 'meta' | 'delivery'>>;

function record(entry: RecordedTurn): LabTurn {
  // Both are derived, never asserted: a turn left a connection on the device exactly when the answer
  // it built carries one, and it was started by the device exactly when its own envelope says so.
  const connection = entry.ask?.connection ?? null;
  const requestType = (entry.requestEnvelope as { request?: { type?: string } } | null)?.request?.type;
  const turn: LabTurn = {
    ...entry,
    meta: entry.meta ?? null,
    delivery: entry.delivery ?? entry.ask?.delivery ?? null,
    connection,
    proactive: requestType === PROACTIVE.requestType,
  };
  // The open connection is this turn's, not a permanent one: the next answer replaces it. A reminder
  // that survived a question the customer asked afterwards would speak an answer to a request that no
  // longer stands, and a device that re-arms itself every turn is a device that never stops talking.
  lab.reminder = connection && entry.called
    ? { ...connection, tool: entry.routed.tool, args: entry.routed.args, dueAtMs: machine.nowMs + connection.afterMs }
    : null;
  lab.turns.push(turn);
  if (lab.turns.length > 40) lab.turns.splice(0, lab.turns.length - 40);
  return turn;
}

/** The `params._meta` this device puts on every request once the customer has said who is speaking.
 *  The field names come out of the shipped artifact, which generates them from the service's own
 *  constants, so neither side can rename the field the other reads. Nothing here decides scope. */
export function speakerMeta(): Record<string, string> | null {
  const who = lab.speaker;
  if (!who) return null;
  return {
    [VOICE_PROFILE.speakerMetaKey]: who,
    [VOICE_PROFILE.profileMetaKey]: VOICE_PROFILE.profileValue,
  };
}

/** The service's own reason for refusing, read out of the answer's `structuredContent`. Null whenever
 *  it answered, and never a sentence this device wrote. */
function refusalOf(produced: Turn | null | undefined): string | null {
  const reason = (produced?.raw?.structuredContent as { speaker_refused?: unknown } | undefined)?.speaker_refused;
  return typeof reason === 'string' ? reason : null;
}

/** The style id the customer asked for, if the artifact has one by that name. A style nobody declared
 *  is not applied: the device has no vocabulary of its own for how to speak. */
function deliveryFor(slots: SluSlot[]): string | null {
  const slot = slots.find((one) => one.name === DELIVERY.slot);
  const id = slot ? slot.id ?? slot.resolved : null;
  return id && DELIVERY.styles[id] ? id : null;
}

/** The name the customer claimed, resolved the way a person slot is resolved everywhere: against the
 *  household's own roster when it is in it, and as said when it is not — which is what lets the
 *  service refuse a claim nobody in this household answers to. */
function speakerFor(slots: SluSlot[]): string | null {
  const slot = slots.find((one) => one.name === VOICE_PROFILE.slot);
  if (!slot || slot.state === 'no-match') return null;
  const said = slot.resolved ?? slot.id ?? slot.spoken;
  return said || null;
}

function isDeviceLocal(intent: AskIntentName | null): boolean {
  return intent !== null && declaredRouting(intent).deviceLocal === true;
}

/** Keep the answer a delivery style may re-speak: only a plain one with nothing owed on it, so an
 *  adjustment can never turn a question the skill still has to ask into a statement it has answered. */
function holdAnswer(produced: Turn | null, intent: AskIntentName | null, ask: AskTurn): void {
  lab.held = ask.state === 'await' && produced
    ? { tool: produced.tool, intent, spoken: produced.spoken, view: produced.view, result: produced.raw }
    : null;
}

/** The connection the last answer put on the device, fired on the clock or by a scenario step. */
export function cancelReminder(): void {
  lab.reminder = null;
}

/** The one turn the customer never asked for. The device clock first moves by the interval the
 *  artifact declares — a reminder that arrived early is not the reminder that was asked for — then the
 *  session resumes on the token this device put on the directive, repeats the read-only call that
 *  armed it and speaks the artifact's own line in front of the server's answer. It arms nothing, so
 *  the conversation cannot go on reminding itself. */
export async function fireReminder(): Promise<LabTurn | null> {
  const armed = lab.reminder;
  if (!armed) return null;
  lab.reminder = null;
  if (machine.state === 'closed' || device.busy) return null;
  ensureClock();
  const wait = armed.dueAtMs - machine.nowMs;
  if (wait > 0) machine.tick(wait);
  machine.resumed();
  const meta = speakerMeta();
  const produced = await say(RESUMED_LABEL, { tool: armed.tool, args: armed.args, meta });
  if (!produced) return null;
  const spoken = `${PROMPTS[PROACTIVE.prompt]} ${produced.spoken}`.trim();
  const ask = buildAskTurn({
    tool: produced.tool,
    spoken,
    view: produced.view,
    result: produced.raw,
    intent: PROACTIVE.intent,
    sessionAttributes: { resumedToken: armed.token },
    resumed: true,
    deliveryStyle: lab.delivery,
  });
  machine.answered(ask);
  lab.pending = null;
  holdAnswer(produced, PROACTIVE.intent, ask);
  return record({
    id: nextTurnId(),
    atMs: machine.nowMs,
    utterance: RESUMED_LABEL,
    slu: resolveUtterance(''),
    routed: { tool: produced.tool, args: produced.args, read: produced.read },
    agree: produced.tool === armed.tool,
    requestEnvelope: sessionResumedRequest(armed.token, currentSession({})),
    ask,
    local: false,
    called: true,
    dialog: null,
    spoken,
    meta,
    refused: refusalOf(produced),
    ms: produced.ms,
    events: machine.toTrace(),
    problem: ask.problems[0] ?? null,
  });
}

/** What the skill would keep between turns: the intent it is holding and what it has collected for
 *  it. The Lab round-trips this through the next request envelope, the way a real session does. */
function dialogAttributes(intent: AskIntentName, slots: SluSlot[]): Record<string, unknown> {
  const collected: Record<string, string> = {};
  for (const slot of slots) {
    const value = slot.resolved ?? (slot.state === 'wildcard' ? slot.spoken : null);
    if (value) collected[slot.name] = value;
  }
  return { careloopPending: { intent, collected } };
}

/** The arguments the model's own routing builds out of what has been collected. `confirmed` flips the
 *  argument the model names for a yes, because that is the only way a removal is ever authorised. */
function collectedArgs(intent: AskIntentName, slots: SluSlot[], confirmed: boolean): Record<string, unknown> {
  const routing = declaredRouting(intent);
  const args: Record<string, unknown> = {};
  for (const slot of slots) {
    const argument = routing.slotArguments[slot.name];
    if (!argument) continue;
    const value = slot.id ?? slot.resolved ?? slot.spoken;
    if (value) args[argument] = value;
  }
  if (confirmed && routing.confirmArgument) args[routing.confirmArgument] = true;
  return args;
}

/** One turn that asks instead of calling: a slot the model declared and has not been given, the yes or
 *  no its dialog rules require, or the model's own "nothing was removed" after a no. */
function answerDialog(
  slu: SluResult,
  intent: AskIntentName,
  step: DialogStep | null,
  slots: SluSlot[],
  said: string,
  denied = false,
): LabTurn {
  const askSlots = toAskSlots(intent, slots);
  const attributes = dialogAttributes(intent, slots);
  const ask = buildDialogTurn({
    intent, step, slots, askSlots,
    sessionAttributes: { lastUtterance: said, ...attributes },
    denied,
    deliveryStyle: lab.delivery,
  });
  machine.answered(ask);
  const routing = declaredRouting(intent);
  const stage: LabTurn['dialog'] = denied
    ? { stage: 'refused', slot: null, promptId: 'KeptPrompt' }
    : { stage: step!.stage, slot: step!.slot, promptId: step!.promptId };
  lab.pending = denied || !step ? null : { intent, openedBy: said, slots, step };
  return record({
    id: nextTurnId(),
    atMs: machine.nowMs,
    utterance: said,
    slu,
    routed: {
      tool: routing.tool,
      args: collectedArgs(intent, slots, false),
      read: denied
        ? 'refused on the model’s own line — nothing was called'
        : `not called yet — the model is asking for ${step!.slot ?? 'a yes or no'}`,
    },
    agree: true,
    requestEnvelope: intentRequest(
      { intent, slots: askSlots, dialogState: 'IN_PROGRESS' },
      currentSession({ sessionAttributes: attributes }),
    ),
    ask,
    local: false,
    called: false,
    dialog: stage,
    spoken: ask.spoken,
    refused: null,
    ms: 0,
    events: machine.toTrace(),
    problem: ask.problems[0] ?? null,
  });
}

/** The chain is complete: the model has every slot it requires and, where it asked, a yes. The call
 *  goes out with the arguments the model's routing maps, not with a re-guess of the answer phrase. */
async function callChain(
  intent: AskIntentName,
  slots: SluSlot[],
  said: string,
  confirmed: boolean,
  slu: SluResult,
): Promise<LabTurn | null> {
  const routing = declaredRouting(intent);
  const meta = speakerMeta();
  const produced = await say(said, { tool: routing.tool, args: collectedArgs(intent, slots, confirmed), meta });
  if (!produced) return null;
  const askSlots = toAskSlots(intent, slots);
  const attributes = dialogAttributes(intent, slots);
  const ask = buildAskTurn({
    tool: produced.tool,
    spoken: produced.spoken,
    view: produced.view,
    result: produced.raw,
    intent,
    askSlots,
    sessionAttributes: { lastUtterance: said, ...attributes },
    deliveryStyle: lab.delivery,
  });
  machine.answered(ask);
  // The read-back may itself leave the dialog open: the item the server named wants a yes.
  const held = heldDialog(intent, ask);
  lab.pending = held ? { intent, openedBy: said, slots, step: held } : null;
  holdAnswer(produced, intent, ask);
  return record({
    id: nextTurnId(),
    atMs: machine.nowMs,
    utterance: said,
    slu,
    routed: { tool: produced.tool, args: produced.args, read: produced.read },
    agree: produced.tool === routing.tool,
    requestEnvelope: intentRequest(
      { intent, slots: askSlots, dialogState: 'COMPLETED', confirmationStatus: confirmed ? 'CONFIRMED' : 'NONE' },
      currentSession({ sessionAttributes: attributes }),
    ),
    ask,
    local: false,
    called: true,
    dialog: held ? { stage: held.stage, slot: held.slot, promptId: held.promptId } : null,
    spoken: produced.spoken,
    meta,
    refused: refusalOf(produced),
    ms: produced.ms,
    events: machine.toTrace(),
    problem: ask.problems[0] ?? null,
  });
}

/** One turn the shipped model answers without a call, because its own routing says `device-local`.
 *  Both of them change *how* the device speaks or *who* it is speaking to, and neither may touch a
 *  plan — so this turn sends no request and writes nothing. It has two shapes:
 *  · a style, or a claim, with an answer held → the artifact's own line, then that same server
 *    sentence re-spoken under the style, so the response envelope is real and the words unchanged;
 *  · nothing held → the artifact's line is spoken locally, exactly as the fallback prompt is, because
 *    there is no answer to speak and this repo writes no health copy of its own. */
function answerDeviceLocal(
  slu: SluResult,
  intent: AskIntentName,
  said: string,
  slots: SluSlot[],
): LabTurn {
  const routing = declaredRouting(intent);
  const askSlots = toAskSlots(intent, slots);
  const attributes: Record<string, unknown> = { lastUtterance: said };
  const requestEnvelope = intentRequest(
    { intent, slots: askSlots, dialogState: 'COMPLETED' },
    currentSession({ sessionAttributes: attributes }),
  );
  const would = route(said, session.people);
  const base = {
    id: nextTurnId(),
    atMs: machine.nowMs,
    utterance: said,
    slu,
    // What the app's text router would have made of the phrase, shown beside the turn it never sent:
    // the artifact routes a device-local intent at no tool, so the two disagreeing is the point.
    routed: { tool: would.tool, args: would.args, read: 'what the app router would have called — never sent' },
    agree: would.tool === routing.tool,
    requestEnvelope,
    local: false,
    called: false,
    dialog: null,
    ms: 0,
  };

  if (intent === VOICE_PROFILE.intent) {
    const who = speakerFor(slots);
    if (!who) return askForMissingSlot(slu, intent, VOICE_PROFILE.slot, slots, said);
    // The claim is adopted here and nowhere else: the household's stored scope, read by the service
    // on the next request, is what decides what that person may hear.
    lab.speaker = who;
    const line = fillPrompt(PROMPTS[VOICE_PROFILE.prompt], [{ name: VOICE_PROFILE.slot, spoken: who }]);
    const speech = analyze(`<speak>${escapeXml(line)}</speak>`);
    machine.answeredLocal({ spoken: line, words: speech.words, estimateMs: speech.estimateMs });
    lab.pending = null;
    return record({
      ...base,
      ask: null,
      spoken: line,
      refused: null,
      events: machine.toTrace(),
      problem: null,
    });
  }

  const style = deliveryFor(slots);
  if (!style) return askForMissingSlot(slu, intent, DELIVERY.slot, slots, said);
  lab.delivery = style;
  const declared = DELIVERY.styles[style];
  const line = PROMPTS[declared.prompt];
  const held = DELIVERY.appliesToHeldAnswer ? lab.held : null;
  if (!held) {
    const speech = analyze(`<speak>${escapeXml(line)}</speak>`);
    machine.answeredLocal({ spoken: line, words: speech.words, estimateMs: speech.estimateMs });
    lab.pending = null;
    return record({
      ...base,
      ask: null,
      spoken: line,
      refused: null,
      delivery: {
        style, rate: declared.rate, volume: declared.volume,
        maxSpokenWords: declared.maxSpokenWords, truncated: false, appliedTo: 'only',
      },
      events: machine.toTrace(),
      problem: null,
    });
  }

  // The style line first, then the sentence the service already gave, cut to the style's ceiling and
  // slowed or raised only in markup: nothing here re-words an answer, because nothing here is allowed
  // to write one.
  const ask = buildAskTurn({
    tool: held.tool,
    spoken: `${line} ${held.spoken}`.trim(),
    view: held.view,
    result: held.result,
    intent,
    askSlots,
    sessionAttributes: attributes,
    deliveryStyle: style,
    deliveryApplied: 'held',
  });
  machine.answered(ask);
  lab.pending = null;
  return record({
    ...base,
    ask,
    spoken: ask.spoken,
    refused: null,
    events: machine.toTrace(),
    problem: ask.problems[0] ?? null,
  });
}

/** A required slot that dialog delegation had not yet elicited, on an intent that calls nothing: the
 *  artifact's own question, asked the same way as any other held dialog. */
function askForMissingSlot(
  slu: SluResult,
  intent: AskIntentName,
  slot: string,
  slots: SluSlot[],
  said: string,
): LabTurn {
  const promptId = slotPrompt(intent, slot);
  if (!promptId) throw new Error(`intent ${intent} elicits ${slot} with no prompt the model declares`);
  return answerDialog(slu, intent, { stage: 'elicit', slot, promptId }, slots, said);
}


/** The open dialog's next phrase: an answer for the slot it asked about, or the yes or no. */
async function answerPending(
  pending: NonNullable<LabState['pending']>,
  slu: SluResult,
  said: string,
): Promise<LabTurn | null> {
  listen(said);
  if (pending.step.stage === 'confirm') {
    if (slu.builtIn === 'AMAZON.NoIntent') {
      return answerDialog(slu, pending.intent, null, pending.slots, said, true);
    }
    // Anything that is not a clear yes asks again. A destructive call is never inferred.
    if (slu.builtIn !== 'AMAZON.YesIntent') {
      return answerDialog(slu, pending.intent, pending.step, pending.slots, said);
    }
    return callChain(pending.intent, pending.slots, said, true, slu);
  }

  const asked = pending.step.slot;
  if (!asked) return callChain(pending.intent, pending.slots, said, false, slu);
  const filled = fillSlot(pending.intent, asked, said, session.people);
  const merged = pending.slots.map((slot) => (slot.name === asked ? filled : slot));
  const step = nextDialogStep(pending.intent, merged);
  // A still-missing required slot is asked again. What is left is only the confirmation, and that
  // is not asked from here: the read-only call brings back the clinician's own words to read out.
  if (step?.stage === 'elicit') return answerDialog(slu, pending.intent, step, merged, said);
  // The chain is complete on an intent the model routes at no tool: it is answered here, and a call
  // would be a call to nothing — the empty tool name would fall through to the text router and reach
  // a real tool with the customer's style or claim sentence as its argument.
  if (isDeviceLocal(pending.intent)) return answerDeviceLocal(slu, pending.intent, said, merged);
  return callChain(pending.intent, merged, said, false, slu);
}

/** One exchange, driven end to end. Returns the lab turn, or null when the device is busy. */
export async function labSay(utterance: string): Promise<LabTurn | null> {
  const said = utterance.trim();
  if (!said || device.busy) return null;
  ensureClock();
  // The device knows whose plan it reads before it hears a word (see runScenario): a person named
  // in the first phrase of a fresh session must resolve to that name, not to the artifact synonym.
  if (!session.people.length) await refreshPeople();

  const slu = resolveUtterance(said, { people: session.people });
  const pending = lab.pending;
  // The window is open for what it asked about. A phrase that matches some *other* intent by an exact
  // shipped sample is a new request, so the chain is dropped and that request is answered instead.
  const startingFresh = pending !== null && slu.matched === 'literal'
    && slu.intent !== null && slu.intent !== pending.intent;
  if (pending && !startingFresh) return answerPending(pending, slu, said);

  listen(said);
  if (startingFresh) lab.pending = null;

  // A phrase the shipped model cannot match never reaches the skill: a real device plays the
  // model's own fallback prompt locally. So the Lab does not call the tool either — speaking the
  // router's answer would be an answer the skill was never asked for, and a phrase that routes to
  // a write tool would write. The router's guess is still shown, because that disagreement is the
  // point of the panel.
  if (!slu.intent && !slu.builtIn) {
    const speech = analyze(`<speak>${escapeXml(PROMPTS.FallbackPrompt)}</speak>`);
    machine.answeredLocal({ spoken: PROMPTS.FallbackPrompt, words: speech.words, estimateMs: speech.estimateMs });
    const would = route(said, session.people);
    return record({
      id: nextTurnId(),
      atMs: machine.nowMs,
      utterance: said,
      slu,
      routed: { tool: would.tool, args: would.args, read: 'what the app router would have called — never sent' },
      agree: false,
      requestEnvelope: null,
      ask: null,
      local: true,
      called: false,
      dialog: null,
      spoken: PROMPTS.FallbackPrompt,
      refused: null,
      ms: 0,
      events: machine.toTrace(),
      problem: null,
    });
  }

  // Dialog delegation, driven by the model: while a slot it marked required is still empty, nothing is
  // sent. The device asks, with the prompt the artifact names. A confirmation the model requires is
  // *not* asked here: a destructive item has to be read back first, and only the server has the
  // clinician's recorded words to read back, so that yes arrives with the item instead of guessing it.
  const step = slu.intent ? nextDialogStep(slu.intent, slu.slots) : null;
  if (slu.intent && step?.stage === 'elicit') return answerDialog(slu, slu.intent, step, slu.slots, said);

  const intent = slu.intent;
  // An intent the artifact routes at no tool is answered here, before anything is sent: its declared
  // tool is empty, and `say()` with an empty tool falls through to the text router, which would call
  // a real one with a pacing request as its argument.
  if (intent && isDeviceLocal(intent)) return answerDeviceLocal(slu, intent, said, slu.slots);
  // A request carries the speaker claim this device was given, and nothing else: the field names come
  // from the shipped artifact and the service decides what the claim is worth.
  const meta = speakerMeta();
  // A phrase the artifact resolved is sent the way the artifact routes it: its declared tool, with
  // each collected slot mapped onto the argument the model names. Sending the app text router's
  // guess instead is what made this device and the headless one answer the same words with
  // different arguments; the router's own reading is still shown beside the turn, unchanged.
  const produced: Turn | null = await say(said, intent
    ? { tool: declaredRouting(intent).tool, args: collectedArgs(intent, slu.slots, false), meta }
    : { meta });
  if (!produced) return null;

  // A phrase the shipped model cannot match never reaches the skill: a device plays the fallback
  // prompt locally, so there is no IntentRequest to show — and the router calling a tool anyway is
  // the disagreement the Lab is there to expose, not to hide.
  const requestEnvelope = intent
    ? intentRequest(
      { intent, slots: slu.askSlots },
      currentSession({ sessionAttributes: { lastUtterance: said } }),
    )
    : null;

  const ask = buildAskTurn({
    tool: produced.tool,
    spoken: produced.spoken,
    view: produced.view,
    result: produced.raw,
    intent,
    slu,
    sessionAttributes: { lastUtterance: said },
    deliveryStyle: lab.delivery,
  });

  machine.answered(ask);
  // What the answer left open is held between turns, the way a skill holds it in sessionAttributes:
  // the slot the server asked about, or the yes a destructive read-back wants. Without this, the
  // customer's answer would be read as a brand-new request and the deletion would never land.
  const held = intent ? heldDialog(intent, ask) : null;
  lab.pending = intent && held ? { intent, openedBy: said, slots: slu.slots, step: held } : null;
  holdAnswer(produced, intent, ask);
  return record({
    id: nextTurnId(),
    atMs: machine.nowMs,
    utterance: said,
    slu,
    routed: { tool: produced.tool, args: produced.args, read: produced.read },
    agree: !slu.tool || slu.tool === produced.tool,
    requestEnvelope,
    ask,
    local: false,
    called: true,
    dialog: held ? { stage: held.stage, slot: held.slot, promptId: held.promptId } : null,
    spoken: produced.spoken,
    meta,
    refused: refusalOf(produced),
    ms: produced.ms,
    events: machine.toTrace(),
    problem: ask.problems[0] ?? null,
  });
}

/** When the model names a person the household does not have, use a real one from the plan. */
function bindPerson(phrase: string): string {
  const who = session.people[0] ?? 'mother';
  return phrase.replace(/\{person\}/g, who);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms / Math.max(1, lab.speed)));
}

/** Play a scripted conversation, one step at a time, so a demo can be reproduced exactly. */
export async function runScenario(scenario: Scenario): Promise<void> {
  if (lab.running) return;
  lab.running = true;
  lab.scenario = scenario.id;
  machine.reset();
  lab.pending = null;
  lab.reminder = null;
  // A scripted run starts from the same place the headless twin does: no style asked for, no voice
  // claimed, no answer held. Otherwise a scenario would inherit how the last one was spoken.
  lab.delivery = null;
  lab.speaker = null;
  lab.held = null;
  lab.turns.length = 0;
  // A real device already knows whose plan it is reading: the household comes from the linked
  // account, not from a phrase the customer had to say first. Without this the first scripted
  // phrase resolves a person to the artifact's synonym instead of the name the plan is stored
  // under, and the headless twin - which asks for the roster before its first turn - disagrees.
  await refreshPeople();
  machine.wake();
  try {
    for (const step of scenario.steps) {
      if (step.waitMs) await sleep(step.waitMs);
      if (step.bargeIn) { machine.bargeIn(); continue; }
      if (step.reminder) { await fireReminder(); continue; }
      if (step.silence) {
        // Burn the silence window and both reprompts without speaking anything.
        for (let i = 0; i < 120; i += 1) {
          machine.tick(VOICE_BUDGET.silenceTimeoutSeconds * 1000);
          if (machine.state === 'closed') break;
          await sleep(10);
        }
        if (machine.state !== 'closed') {
          machine.close({ reason: 'EXCEEDED_MAX_REPROMPTS' });
        }
        // Recorded, not pushed: a session that ended has nothing left to resume, and `record` is
        // where an open connection is dropped when a turn replaces it.
        record({
          id: `lab-silence-${Date.now().toString(36)}`, atMs: machine.nowMs, utterance: '(silence)',
          slu: resolveUtterance(''), routed: { tool: '—', args: {}, read: 'nothing heard' }, agree: true,
          requestEnvelope: sessionEndedRequest('EXCEEDED_MAX_REPROMPTS', currentSession({})),
          ask: null, local: false, called: false,
          dialog: null, spoken: '', refused: null, ms: 0, events: machine.toTrace(), problem: null,
        });
        continue;
      }
      if (step.say) await labSay(bindPerson(step.say));
    }
    lab.lastTrace = exportTrace();
  } finally {
    lab.running = false;
  }
}

/** A trace shaped like the headless driver's, so both sides can be diffed. */
export function exportTrace(): string {
  const trace = {
    kind: 'careloop-alexa-device-trace',
    version: 1,
    scenario: lab.scenario ?? 'manual',
    created_at: new Date().toISOString(),
    device: { locale: 'en-US', model: 'simulated-browser-device' },
    turns: lab.turns.map((entry, index) => ({
      index,
      utterance: entry.utterance,
      at_ms: entry.atMs,
      state: machine.state,
      slu: { intent: entry.slu.intent, builtIn: entry.slu.builtIn, matched: entry.slu.matched, confidence: entry.slu.confidence, slots: entry.slu.slots },
      routed: { tool: entry.routed.tool, args: entry.routed.args },
      agrees: entry.agree,
      local: entry.local,
      called: entry.called,
      dialog: entry.dialog,
      // The reminder bookkeeping, in the same shape the headless driver writes it: what this turn
      // armed for later, and whether this turn is the one the device started by itself.
      connection: entry.connection,
      proactive: entry.proactive,
      // The two features the artifact answers without a tool call, written the way the headless
      // driver writes them: how this answer was spoken, what the device claimed about who spoke, and
      // what the service refused. `meta` is what went on the wire, `refused` is the service's sentence.
      delivery: entry.delivery,
      meta: entry.meta,
      refused: entry.refused,
      request_envelope: entry.requestEnvelope,
      response_envelope: entry.ask?.envelope ?? null,
      spoken_text: entry.spoken,
      words: entry.ask?.words ?? 0,
      estimate_ms: entry.ask?.estimateMs ?? 0,
      masked_fields: entry.ask?.masked.paths ?? [],
      problems: entry.ask?.problems ?? [],
      tool_ms: entry.ms,
    })),
    events: machine.toTrace(),
    summary: {
      turns: lab.turns.length,
      calls: lab.turns.filter((entry) => entry.called).length,
      local_fallbacks: lab.turns.filter((entry) => entry.local).length,
      elicits: lab.turns.filter((entry) => entry.dialog?.stage === 'elicit').length,
      confirmations_asked: lab.turns.filter((entry) => entry.dialog?.stage === 'confirm').length,
      confirmations_refused: lab.turns.filter((entry) => entry.dialog?.stage === 'refused').length,
      reminders_armed: lab.turns.filter((entry) => entry.connection !== null).length,
      reminders: lab.turns.filter((entry) => entry.proactive).length,
      // Counted from the same three per-turn fields, so the two devices agree on what happened:
      // a spoken style is one that reached an answer, a claim is one that rode a request, and a
      // refusal is the service's, never a word this device wrote.
      delivery_turns: lab.turns.filter((entry) => entry.delivery !== null).length,
      speaker_turns: lab.turns.filter((entry) => entry.slu.intent === VOICE_PROFILE.intent).length,
      claims_sent: lab.turns.filter((entry) => entry.meta !== null).length,
      refusals: lab.turns.filter((entry) => entry.refused !== null).length,
      dose_mask_events: lab.turns.reduce((total, entry) => total + (entry.ask?.masked.count ?? 0), 0),
      problems: lab.turns.reduce((total, entry) => total + (entry.ask?.problems.length ?? 0), 0),
    },
  };
  return JSON.stringify(trace, null, 2);
}

export function downloadTrace(): void {
  downloadText(`careloop-device-${lab.scenario ?? 'manual'}.json`, exportTrace());
}

export function clearLab(): void {
  lab.turns.length = 0;
  lab.lastTrace = null;
  lab.scenario = null;
  lab.pending = null;
  lab.reminder = null;
  lab.delivery = null;
  lab.speaker = null;
  lab.held = null;
  machine.reset();
}

/** The argument mapping the model declares, for the tool-call row in the UI. */
export function modelArgsFor(entry: LabTurn): Record<string, unknown> {
  return toToolArgs(entry.slu);
}

export type { AskIntentName };
