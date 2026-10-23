/** Alexa request envelopes for the simulated device, typed against Amazon's own model package
 *  (`ask-sdk-model`, imported as types only so none of it reaches the browser bundle).
 *
 *  The shapes below are the ones the shipped skill would receive: a `version`, a `session`, a
 *  `context.System` and a `request`. Identifiers are obviously fake (`…-simulated`) because a
 *  simulator must not mint anything that looks like a real Alexa account or device.
 */
import type {
  dialog, interfaces, Intent, IntentRequest, LaunchRequest, RequestEnvelope,
  SessionEndedRequest, SessionResumedRequest, Slot, slu, Status,
} from 'ask-sdk-model';
import type { AskIntentName } from './interactionModel';
import { DIALOG, INTERACTION_MODEL, PROACTIVE } from './interactionModel';

export type AskDirective = dialog.ElicitSlotDirective | dialog.ConfirmIntentDirective | dialog.DelegateDirective;
/** The one directive that is not a dialog step: it asks the device to come back later. */
export type AskConnectionDirective = interfaces.connections.V1.StartConnectionDirective;
export type AskResolutions = slu.entityresolution.Resolutions;
export type AskSystemState = interfaces.system.SystemState;
export type AskSlotState = 'success' | 'resolved' | 'no-match';

/** The slot value names as they appear in the model, so nothing can ask for an undeclared slot. */
export const SLOT_TYPES = Object.fromEntries(
  INTERACTION_MODEL.languageModel.types.map((t) => [t.name, t]),
) as Record<string, (typeof INTERACTION_MODEL.languageModel.types)[number]>;

export const INTENT_BY_NAME = Object.fromEntries(
  INTERACTION_MODEL.languageModel.intents.map((i) => [i.name, i]),
) as Record<AskIntentName, (typeof INTERACTION_MODEL.languageModel.intents)[number]>;

/** One slot as the shipped model declares it. `required` is what dialog delegation elicits;
 *  `capture: listenOnce` is how a free-text slot is recorded; `wildcard` means the rest of the
 *  phrase belongs to it. */
export interface DeclaredSlot {
  name: string;
  type: string;
  required: boolean;
  capture?: string;
  wildcard?: boolean;
  /** The prompt id the model asks this slot with. */
  prompt?: string;
}

export interface DeclaredRouting {
  /** The MCP tool this intent routes at. Empty for an intent the device answers itself. */
  tool: string;
  /** Slot name → the tool argument it fills. A collected slot with no entry is spoken-only. */
  slotArguments: Record<string, string>;
  /** The argument the yes/no answer lands on, for an intent the model asks to confirm. */
  confirmArgument?: string;
  /** How the artifact routes it: `mcp-tool`, or `device-local` for an intent that calls nothing. */
  type?: string;
  /** True when the model routes the intent to no tool at all — it can read and write nothing. */
  deviceLocal?: boolean;
  /** What the device does with the answer instead of calling: pacing, or who it is answering for. */
  writesAnything?: boolean;
}

/** The slot list the model itself declares for an intent, in elicitation order. */
export function declaredSlots(intent: AskIntentName): DeclaredSlot[] {
  const declared = INTENT_BY_NAME[intent] as { slots?: unknown } | undefined;
  return (declared?.slots ?? []) as DeclaredSlot[];
}

export function declaredRouting(intent: AskIntentName): DeclaredRouting {
  const declared = INTENT_BY_NAME[intent] as { routing?: unknown } | undefined;
  const routing = declared?.routing as DeclaredRouting | undefined;
  // A device-local intent names no tool and maps no slot onto an argument, so the artifact carries
  // neither key. Normalised here rather than guessed at every call site, because "no tool" and
  // "unknown tool" must not look the same to the runtime.
  if (routing?.type === 'device-local' || routing?.tool === null) {
    return { ...routing, tool: '', slotArguments: {}, deviceLocal: true };
  }
  return (routing ?? { tool: '', slotArguments: {} }) as DeclaredRouting;
}

/** The slots a call cannot succeed without, in the model's own order. */
export function requiredSlots(intent: AskIntentName): string[] {
  return declaredSlots(intent).filter((slot) => slot.required).map((slot) => slot.name);
}

/** The prompt the artifact declares for one slot of one intent, or null when it declares none. */
export function slotPrompt(intent: AskIntentName, slot: string | null): string | null {
  if (!slot) return null;
  return declaredSlots(intent).find((one) => one.name === slot)?.prompt ?? null;
}

/** One step of the dialog the model asks for, or null when the call can go out.
 *
 *  `strategy: skill` delegation means the skill drives this itself: it elicits each required slot the
 *  model declares, in the model's own order and with the model's own prompt, and only once nothing is
 *  missing does it ask for the confirmation the dialog rules require. Nothing here decides a question
 *  on its own — the slot list and the prompt ids come out of the shipped artifact. */
export interface DialogStep {
  stage: 'elicit' | 'confirm';
  slot: string | null;
  promptId: string;
}

export function nextDialogStep(intent: AskIntentName, filled: Array<{ name: string; resolved: string | null }>): DialogStep | null {
  for (const slot of declaredSlots(intent)) {
    if (!slot.required) continue;
    const have = filled.find((one) => one.name === slot.name);
    if (have && have.resolved !== null) continue;
    const promptId = slot.prompt;
    if (!promptId) return null;
    return { stage: 'elicit', slot: slot.name, promptId };
  }
  const rule = DIALOG.confirmationRequired[intent];
  if (rule) return { stage: 'confirm', slot: null, promptId: rule.prompt };
  return null;
}

export interface DeviceIdentity {
  readonly applicationId: string;
  readonly deviceId: string;
  readonly userId: string;
  readonly locale: string;
  readonly apiEndpoint: string;
  readonly apiAccessToken: string;
}

/** Fixed, clearly synthetic. `apiEndpoint` is the shape of the real one; the simulator never calls it. */
export const SIM_DEVICE: DeviceIdentity = {
  applicationId: 'amzn1.ask.skill.careloop-simulated',
  deviceId: 'careloop-simulated-device-0001',
  userId: 'amzn1.ask.account.CARELOOP-SIMULATED',
  locale: 'en-US',
  apiEndpoint: 'https://api.amazonalexa.com/v2/accounts/',
  apiAccessToken: 'simulated-access-token-never-sent',
};

let sequence = 0;

/** A fresh counter, or a fixed string when a test needs a reproducible envelope. */
export interface EnvelopeOptions {
  sessionId?: string;
  requestId?: string;
  timestamp?: string;
  device?: Partial<DeviceIdentity>;
  /** Session attributes the skill itself set on an earlier turn. */
  sessionAttributes?: Record<string, unknown>;
  isNewSession?: boolean;
}

function identity(options: EnvelopeOptions): DeviceIdentity {
  return { ...SIM_DEVICE, ...options.device };
}

function stamp(options: EnvelopeOptions, at: number): string {
  return options.timestamp ?? new Date(at).toISOString().replace(/\.\d+Z$/, 'Z');
}

function nextId(kind: string): string {
  sequence += 1;
  return `${kind}-${Date.now().toString(36)}-${sequence}`;
}

export function systemContext(options: EnvelopeOptions): AskSystemState {
  const who = identity(options);
  return {
    application: { applicationId: who.applicationId },
    user: { userId: who.userId },
    device: { deviceId: who.deviceId, supportedInterfaces: {} },
    apiEndpoint: who.apiEndpoint,
    apiAccessToken: who.apiAccessToken,
  };
}

/** Fallback authority name when a slot's type is unknown: still clearly this repo's own slot type,
 *  never an Amazon endpoint. */
const SIM_AUTHORITY = 'CareLoopUnresolved';

/** One slot, with the entity-resolution block a device sends when a custom slot type matched.
 *  The authority is named after the slot type this repo defined in `alexa/interaction-model.json`;
 *  it is not an Amazon endpoint, and the Lab labels it as the model's own slot type. */
export function buildSlot(
  name: string,
  spoken: string,
  resolution: { slotType: string; value: string; id?: string; state: AskSlotState },
): Slot {
  const matched = resolution.state !== 'no-match';
  const perAuthority = [{
    authority: resolution.slotType,
    status: { code: matched ? ('ER_SUCCESS_MATCH' as const) : ('ER_SUCCESS_NO_MATCH' as const) },
    values: matched
      ? [{ value: { name: resolution.value, id: resolution.id ?? resolution.value } }]
      : [],
  }];
  return {
    name,
    value: spoken,
    confirmationStatus: 'NONE',
    slotValue: { type: 'Simple', value: spoken },
    resolutions: { resolutionsPerAuthority: perAuthority } as AskResolutions,
  };
}

/** A slot the device heard but could not resolve. It still carries the resolution block, with an
 *  empty `values` — that is how a real device says "I heard it, it matched nothing". */
export function unresolvedSlot(name: string, spoken: string, authority = SIM_AUTHORITY): Slot {
  return {
    name,
    value: spoken,
    confirmationStatus: 'NONE',
    slotValue: { type: 'Simple', value: spoken },
    resolutions: {
      resolutionsPerAuthority: [{ authority, status: { code: 'ER_SUCCESS_NO_MATCH' as const }, values: [] }],
    } as AskResolutions,
  };
}

export function launchRequest(options: EnvelopeOptions = {}): RequestEnvelope {
  const who = identity(options);
  const sessionId = options.sessionId ?? nextId('simsession');
  return {
    version: '1.0',
    session: {
      new: options.isNewSession ?? true,
      sessionId,
      application: { applicationId: who.applicationId },
      user: { userId: who.userId },
      attributes: { ...options.sessionAttributes },
    },
    context: { System: systemContext(options) },
    request: {
      type: 'LaunchRequest',
      requestId: options.requestId ?? nextId('simreq'),
      timestamp: stamp(options, Date.now()),
      locale: who.locale,
    } as LaunchRequest,
  };
}

export interface IntentRequestInput {
  intent: AskIntentName;
  slots: Record<string, Slot>;
  dialogState?: 'STARTED' | 'IN_PROGRESS' | 'COMPLETED';
  confirmationStatus?: 'NONE' | 'IN_PROGRESS' | 'CONFIRMED' | 'DENIED';
}

export function intentRequest(input: IntentRequestInput, options: EnvelopeOptions = {}): RequestEnvelope {
  const who = identity(options);
  const declared = INTENT_BY_NAME[input.intent];
  // `IntentConfirmationStatus` in ask-sdk-model is NONE | DENIED | CONFIRMED: a device has either
  // asked about confirmation and got an answer, or not. There is no IN_PROGRESS on the request.
  const status = input.confirmationStatus === 'CONFIRMED' || input.confirmationStatus === 'DENIED'
    ? input.confirmationStatus
    : 'NONE';
  const intent: Intent = { name: input.intent, confirmationStatus: status, slots: input.slots };
  if (!declared) throw new Error(`intent ${input.intent} is not in the shipped interaction model`);
  return {
    version: '1.0',
    session: {
      new: options.isNewSession ?? false,
      sessionId: options.sessionId ?? nextId('simsession'),
      application: { applicationId: who.applicationId },
      user: { userId: who.userId },
      attributes: { ...options.sessionAttributes },
    },
    context: { System: systemContext(options) },
    request: {
      type: 'IntentRequest',
      requestId: options.requestId ?? nextId('simreq'),
      timestamp: stamp(options, Date.now()),
      locale: who.locale,
      dialogState: input.dialogState ?? 'COMPLETED',
      intent,
    } as IntentRequest,
  };
}

/** The one directive that is not a dialog step. It does not ask the customer anything: it asks the
 *  device to start the connection the artifact names and to resume *this* session when that
 *  connection completes, which is the only way this skill can ever say something after the
 *  conversation moved on. A real response carries it alone, so nothing else is open at the time. */
export function startConnection(token: string): AskConnectionDirective {
  return {
    type: PROACTIVE.directiveType as 'Connections.StartConnection',
    uri: PROACTIVE.connectionUri,
    onCompletion: PROACTIVE.onCompletion as interfaces.connections.OnCompletion,
    token,
  };
}

/** The request a device sends when it starts the turn itself. There is no intent and no slot in it,
 *  because nobody said anything: what it carries is the connection that completed and the token this
 *  device put on the directive — the echo that ties the reminder back to the answer that armed it. */
export function sessionResumedRequest(token: string, options: EnvelopeOptions = {}): RequestEnvelope {
  const who = identity(options);
  // `message` is required by the TypeScript type but optional on the wire: Amazon's own serialiser
  // omits it, and the headless device's envelope — proved byte-for-byte equal to the SDK's — carries
  // only the code. Inventing a message here would be the two devices disagreeing about a success.
  const status = { code: 'SUCCESS' } as Status;
  return {
    version: '1.0',
    session: {
      new: options.isNewSession ?? false,
      sessionId: options.sessionId ?? nextId('simsession'),
      application: { applicationId: who.applicationId },
      user: { userId: who.userId },
      attributes: { ...options.sessionAttributes },
    },
    context: { System: systemContext(options) },
    request: {
      type: PROACTIVE.requestType as 'SessionResumedRequest',
      requestId: options.requestId ?? nextId('simreq'),
      timestamp: stamp(options, Date.now()),
      locale: who.locale,
      cause: { type: PROACTIVE.causeType as 'ConnectionCompleted', token, status },
    } as SessionResumedRequest,
  };
}

export function sessionEndedRequest(
  reason: 'USER_INITIATED' | 'EXCEEDED_MAX_REPROMPTS' | 'ERROR',
  options: EnvelopeOptions = {},
): RequestEnvelope {
  const who = identity(options);
  const request: SessionEndedRequest = {
    type: 'SessionEndedRequest',
    requestId: options.requestId ?? nextId('simreq'),
    timestamp: stamp(options, Date.now()),
    locale: who.locale,
    reason,
  };
  return {
    version: '1.0',
    session: {
      new: false,
      sessionId: options.sessionId ?? nextId('simsession'),
      application: { applicationId: who.applicationId },
      user: { userId: who.userId },
      attributes: { ...options.sessionAttributes },
    },
    context: { System: systemContext(options) },
    request,
  };
}

/** The elicit/confirm directives, exactly as `ask-sdk-core` names them: `Dialog.ElicitSlot` and
 *  `Dialog.ConfirmIntent`, both carrying `updatedIntent`. */
export function elicitSlot(intent: AskIntentName, slotToElicit: string, slots: Record<string, Slot> = {}): AskDirective {
  return {
    type: 'Dialog.ElicitSlot',
    slotToElicit,
    updatedIntent: { name: intent, confirmationStatus: 'NONE', slots },
  } as AskDirective;
}

export function confirmIntent(intent: AskIntentName, slots: Record<string, Slot> = {}): AskDirective {
  return {
    type: 'Dialog.ConfirmIntent',
    updatedIntent: { name: intent, confirmationStatus: 'NONE', slots },
  } as AskDirective;
}

export function delegate(intent: AskIntentName, slots: Record<string, Slot> = {}): AskDirective {
  return {
    type: 'Dialog.Delegate',
    updatedIntent: { name: intent, confirmationStatus: 'NONE', slots },
  } as AskDirective;
}

/** Which slot to ask for next, walking the model's declared slots in order. `have` is what the
 *  session already collected, so a second elicitation follows the first instead of repeating it.
 *  A server question that plainly asks who/which item picks that slot first, because the question
 *  came from the plan and knows more than the slot list does. */
export function slotToElicitFor(intent: AskIntentName, question: string, have: string[] = []): string | null {
  const slots = declaredSlots(intent);
  const remaining = slots.filter((slot) => !have.includes(slot.name));
  if (!remaining.length) return null;
  const names = remaining.map((slot) => slot.name);
  const lower = question.toLowerCase();
  if (/who|whose|person|which family member/.test(lower) && names.includes('Person')) return 'Person';
  if (/which item|the first|the second|which one/.test(lower) && names.includes('ItemPosition')) return 'ItemPosition';
  if (/what should i (remember|note)|note/.test(lower) && names.includes('NoteText')) return 'NoteText';
  // A required slot outranks an optional one: the call cannot be made without it.
  return (remaining.find((slot) => slot.required) ?? remaining[0]).name;
}
