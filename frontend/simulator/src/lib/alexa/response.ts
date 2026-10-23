/** One tool result in, one Alexa response out.
 *
 *  The sentence is `content[0].text`, unchanged — this file only wraps it in markup and decides
 *  which dialog directive the device would be holding. It never rewrites health text, and the card
 *  it produces carries the same sentence the speaker says, because both come from one
 *  `structuredContent` (README: voice and screen cannot disagree).
 */
import type { ResponseEnvelope, Slot } from 'ask-sdk-model';
import type { ResultView, ToolResult } from '@careloop/client';
import { toView } from '@careloop/client';
import { analyze, escapeXml, prosodize, Ssml } from './ssml';
import {
  confirmIntent, elicitSlot, slotToElicitFor, slotPrompt, startConnection,
  type AskConnectionDirective, type AskDirective, type DialogStep,
} from './envelope';
import { DELIVERY, DIALOG, MODEL_SHA256, PROMPTS, PROACTIVE, VOICE_BUDGET, type AskPromptId } from './interactionModel';
import type { AskIntentName } from './interactionModel';
import type { SluResult } from './slu';

/** The constant `careloop/policy_guard.py` substitutes for every quantity on a voice surface. */
export const MASK_TOKEN = 'your prescribed amount';

/** What the device is doing between turns. `end` closes the session; the rest keep the window open. */
export type AskState = 'end' | 'await' | 'elicit' | 'confirm';

export interface CardPayload {
  /** The MCP app resource the real surface renders, straight from `_meta.ui.resourceUri`. */
  resourceUri: string;
  title: string;
  body: string;
  items: number;
}

/** What a plain answer armed for later: the connection the device was told to start, the token it
 *  will hand back on, and how long the artifact says to wait. The headless driver writes the same
 *  three things into its trace, so `--compare` can check the two devices armed the same way. */
export interface AskConnection {
  token: string;
  uri: string;
  afterMs: number;
}

/** How one answer was spoken, read off `presentation.delivery.styles` in the shipped artifact. The
 *  style changes the pace, the level and the length only: every word is still the server's. */
export interface AskDelivery {
  style: string;
  rate: string | null;
  volume: string | null;
  maxSpokenWords: number;
  /** True when the style's ceiling dropped words off the end of the sentence. */
  truncated: boolean;
  /** `held` — this turn re-spoke the answer the customer already had; `next` — the style is in force
   *  and applies to the answer now being built; `only` — the turn said the style line and there was
   *  nothing held to re-speak. */
  appliedTo: 'held' | 'next' | 'only';
}

export interface AskTurn {
  envelope: ResponseEnvelope;
  ssml: string;
  spoken: string;
  words: number;
  estimateMs: number;
  directives: (AskDirective | AskConnectionDirective)[];
  /** Set on the turn that armed the device-initiated reminder, null on every other turn. */
  connection: AskConnection | null;
  /** Set on every turn a spoken-delivery style was in force for, null when the customer never asked. */
  delivery: AskDelivery | null;
  card: CardPayload;
  /** Where the mask token appears: a count and field paths only, never a quantity. */
  masked: { count: number; paths: string[] };
  problems: string[];
  state: AskState;
  slotToElicit: string | null;
}

export interface AskTurnInput {
  tool: string | null;
  spoken: string;
  view: ResultView;
  result: ToolResult | undefined;
  intent: AskIntentName | null;
  slu?: SluResult | null;
  /** Slots for `updatedIntent`, when the turn did not come from a resolved phrase (a dialog turn). */
  askSlots?: Record<string, Slot>;
  sessionAttributes?: Record<string, unknown>;
  /** Wrap the answer in `<speak>` only, with no pacing markup. Off, the text is byte-identical. */
  pace?: boolean;
  /** The slot the dialog decided to ask for. Given, it wins over reading the question back. */
  elicitSlot?: string | null;
  /** True on the turn the device started itself. It must never arm another reminder — that would
   *  be a conversation that speaks to itself forever. */
  resumed?: boolean;
  /** The spoken-delivery style in force, as the id `presentation.delivery.styles` is keyed by.
   *  Omitted, or the artifact's own default style, and the markup is exactly as it was. */
  deliveryStyle?: string | null;
  /** How that style reached this turn: it re-spoke the answer the customer already had, or it is in
   *  force from here on. Recorded, never inferred — "was it re-said" is a fact about this turn. */
  deliveryApplied?: AskDelivery['appliedTo'];
}

/** Sentence-split for pacing only. Each piece keeps its trailing space, so stripping the markup
 *  back out reproduces the server's exact sentence. */
function paced(value: string): Ssml {
  const builder = new Ssml();
  const pieces = value.match(/[^.!?]+[.!?]*\s*/g) ?? [value];
  pieces.forEach((piece, index) => {
    if (index > 0) builder.break('x-weak');
    builder.text(piece);
  });
  return builder;
}

/** Walk the payload for the mask constant so the UI can show how much was hidden — without ever
 *  copying a quantity anywhere. */
export function findMasked(structured: Record<string, unknown> | undefined): { count: number; paths: string[] } {
  const paths: string[] = [];
  let count = 0;
  const walk = (node: unknown, trail: string): void => {
    if (typeof node === 'string') {
      const hits = node.split(MASK_TOKEN).length - 1;
      if (hits > 0) {
        count += hits;
        paths.push(trail || '(root)');
      }
      return;
    }
    if (Array.isArray(node)) {
      node.forEach((item, index) => walk(item, `${trail}[${index}]`));
      return;
    }
    if (node && typeof node === 'object') {
      for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
        walk(value, trail ? `${trail}.${key}` : key);
      }
    }
  };
  walk(structured, '');
  return { count, paths };
}

function repromptFor(view: ResultView): string {
  if (view.kind === 'clarify' || view.kind === 'confirm-delete') return view.question ?? PROMPTS.FallbackPrompt;
  if (view.kind === 'crisis') return '';
  if (view.kind === 'error') return PROMPTS.FallbackPrompt;
  return PROMPTS.HelpPrompt;
}

/** The model's own confirmation prompt id for this intent, if its dialog rules require one. */
export function confirmationPromptId(intent: AskIntentName | null): string | null {
  if (!intent) return null;
  const rule = (DIALOG.confirmationRequired as Record<string, { prompt: string }>)[intent];
  return rule ? rule.prompt : null;
}

function cardResourceUri(result: ToolResult | undefined): string {
  const meta = result?._meta as { ui?: { resourceUri?: string } } | undefined;
  return meta?.ui?.resourceUri ?? 'ui://careloop/card';
}

/** Whether a plan read came back with something still to do. The headless driver applies the same
 *  rule to the same payload: a reminder armed on an empty answer would speak a prompt about
 *  nothing, and an unnecessary reminder about someone's medicine is exactly the noise this skill
 *  has promised not to make. */
export function hasPending(structured: unknown): boolean {
  if (!structured || typeof structured !== 'object') return false;
  const node = structured as Record<string, unknown>;
  if (Array.isArray(node.items) && node.items.length) return true;
  if (Array.isArray(node.pending) && node.pending.length) return true;
  const slots = Array.isArray(node.slots) ? node.slots : [];
  return slots.some((slot) => {
    const items = (slot as Record<string, unknown> | null)?.items;
    return Array.isArray(items) && items.length > 0;
  });
}

let connectionSeq = 0;

/** Whether this answer arms the one device-initiated turn. Every part of the rule is the
 *  artifact's: `proactive.reminder` names the intent whose plain answer arms it, the connection
 *  the device is asked to start and how long the device waits before it comes back. Never on a
 *  turn that still owes the customer a question, because a connection directive travels alone. */
export function armReminder(input: AskTurnInput, state: AskState): AskConnection | null {
  if (input.resumed || state !== 'await' || input.intent !== PROACTIVE.intent) return null;
  if (!hasPending(input.result?.structuredContent)) return null;
  connectionSeq += 1;
  return {
    token: `careloop-reminder-${connectionSeq}`,
    uri: PROACTIVE.connectionUri,
    afterMs: PROACTIVE.resumeAfterSeconds * 1000,
  };
}

/** Cut a sentence to the ceiling a style asks for. Words are dropped from the end and nothing is
 *  added, so a shorter answer is the same answer told for less time — never a new sentence, because
 *  this repo does not write health text (README). */
export function cutToWords(value: string, maxWords: number): string {
  const words = value.split(/\s+/).filter(Boolean);
  return words.length <= maxWords ? value : words.slice(0, Math.max(0, maxWords)).join(' ');
}

/** What the artifact's style does to one sentence, and what it declared it would do. Everything
 *  comes off `presentation.delivery.styles`: the rate, the level and the word ceiling are read from
 *  the shipped artifact, never written here. */
function applyDelivery(
  value: string,
  id: string | null | undefined,
  appliedTo: AskDelivery['appliedTo'],
): { delivery: AskDelivery | null; said: string; rate: string | null; volume: string | null } {
  const style = id ? DELIVERY.styles[id] : undefined;
  if (!id || !style) return { delivery: null, said: value, rate: null, volume: null };
  const usual = DELIVERY.styles[DELIVERY.defaultStyle] ?? { rate: '100%', volume: '100%', maxSpokenWords: VOICE_BUDGET.maxSpokenWords };
  const rate = style.rate === usual.rate ? null : style.rate;
  const volume = style.volume === usual.volume ? null : style.volume;
  // Only a style *tighter* than the usual way and than the model's own budget cuts anything, so
  // asking for it slower or louder can never hide an answer that was already too long to speak.
  const tighter = style.maxSpokenWords < usual.maxSpokenWords && style.maxSpokenWords < VOICE_BUDGET.maxSpokenWords;
  const said = tighter ? cutToWords(value, style.maxSpokenWords) : value;
  return {
    delivery: {
      style: id, rate: style.rate, volume: style.volume, maxSpokenWords: style.maxSpokenWords,
      truncated: said !== value, appliedTo,
    },
    said, rate, volume,
  };
}

export function buildAskTurn(input: AskTurnInput): AskTurn {
  const view = input.view;
  const question = view.question ?? '';
  const applied = applyDelivery(input.spoken, input.deliveryStyle, input.deliveryApplied ?? 'next');
  const marked = input.pace === false
    ? `<speak>${escapeXml(applied.said)}</speak>`
    : paced(applied.said).build();
  const ssml = (applied.rate || applied.volume)
    ? prosodize(marked, { rate: applied.rate, volume: applied.volume })
    : marked;
  const analysis = analyze(ssml);

  const directives: Array<AskDirective | AskConnectionDirective> = [];
  let slotToElicit: string | null = null;
  let state: AskState = 'await';

  const builtIn = input.slu?.builtIn ?? null;
  const heldSlots = input.askSlots ?? input.slu?.askSlots ?? {};
  if (builtIn === 'AMAZON.StopIntent' || builtIn === 'AMAZON.CancelIntent' || view.kind === 'crisis') {
    state = 'end';
  } else if (view.kind === 'clarify') {
    state = 'elicit';
    if (input.intent) {
      slotToElicit = input.elicitSlot
        ?? slotToElicitFor(input.intent, question || PROMPTS.ClarifyPersonPrompt, []);
      if (slotToElicit) directives.push(elicitSlot(input.intent, slotToElicit, heldSlots));
    }
  } else if (view.kind === 'confirm-delete') {
    state = 'confirm';
    if (input.intent) directives.push(confirmIntent(input.intent, heldSlots));
  }

  const shouldEndSession = state === 'end';
  const connection = armReminder(input, state);
  if (connection) directives.push(startConnection(connection.token));
  const reprompt = repromptFor(view);
  const masked = findMasked(input.result?.structuredContent);

  const envelope: ResponseEnvelope = {
    version: '1.0',
    sessionAttributes: {
      ...input.sessionAttributes,
      careloop: {
        model: MODEL_SHA256.slice(0, 12),
        tool: input.tool,
        intent: input.intent,
        view: view.kind,
        state,
      },
    },
    response: {
      shouldEndSession,
      outputSpeech: { type: 'SSML', ssml },
      card: { type: 'Simple', title: view.title, content: input.spoken },
      ...(reprompt ? { reprompt: { outputSpeech: { type: 'SSML', ssml: `<speak>${escapeXml(reprompt)}</speak>` } } } : {}),
      ...(directives.length ? { directives } : {}),
    },
  };

  const problems = [...analysis.problems];
  if (applied.said.toLowerCase().includes('jsonrpc')) problems.push('a protocol string leaked into speech');
  if (/[a-z]+-[0-9a-f]{6,}/i.test(applied.said)) problems.push('an internal identifier leaked into speech');
  if (applied.said.includes('{') || applied.said.includes('}')) problems.push('an unrendered placeholder was spoken');

  return {
    envelope,
    ssml,
    spoken: applied.said,
    words: analysis.words,
    estimateMs: analysis.estimateMs,
    directives,
    connection,
    delivery: applied.delivery,
    // The screen is not the speaker: a style that shortened the *spoken* answer leaves the card
    // carrying the whole sentence the server returned.
    card: { resourceUri: cardResourceUri(input.result), title: view.title, body: input.spoken, items: view.items.length },
    masked,
    problems,
    state,
    slotToElicit,
  };
}

/** The dialog an answer left open, named by the artifact rather than by this file: the prompt id
 *  comes from the slot declaration or the confirmation rule, and a slot with no declared prompt is
 *  not held. A held dialog is what makes the customer's next phrase an answer rather than a new
 *  request — without it a "yes" after a read-back would be read as a request and nothing would land. */
export function heldDialog(intent: AskIntentName, ask: AskTurn): DialogStep | null {
  if (ask.state === 'confirm') {
    const promptId = confirmationPromptId(intent);
    return promptId ? { stage: 'confirm', slot: null, promptId } : null;
  }
  if (ask.state === 'elicit') {
    const promptId = slotPrompt(intent, ask.slotToElicit);
    return promptId && ask.slotToElicit
      ? { stage: 'elicit', slot: ask.slotToElicit, promptId }
      : null;
  }
  return null;
}

/** The model's own placeholders filled with the customer's own words for those slots. A slot nothing
 *  was said for keeps its placeholder, so the sentence never claims something it does not know. */
export function fillPrompt(template: string, slots: Array<{ name: string; spoken: string }>): string {
  return template.replace(/\{([A-Za-z][A-Za-z0-9]*)\}/g, (marker, name: string) => {
    const said = slots.find((slot) => slot.name === name)?.spoken;
    return said ? said : marker;
  });
}

export interface DialogTurnInput {
  intent: AskIntentName;
  /** What dialog delegation decided next, straight from the model's slot list. */
  step: DialogStep | null;
  /** Everything collected so far, for the prompt's placeholders and for `updatedIntent`. */
  slots: Array<{ name: string; spoken: string }>;
  askSlots: Record<string, Slot>;
  sessionAttributes?: Record<string, unknown>;
  /** The customer said no to a confirmation: the model's own "nothing was removed" line, and no call. */
  denied?: boolean;
  /** The spoken-delivery style in force. A question the device asks is spoken the way the customer
   *  asked to be spoken to, not the way it was before. */
  deliveryStyle?: string | null;
}

/** A turn with no tool result, because the dialog is not finished: the skill is asking for a slot or
 *  for a yes. The sentence spoken is a prompt from the shipped artifact — never written here, never a
 *  health statement — and the card shows the same sentence through the same shared renderer, so voice
 *  and screen still cannot disagree. */
export function buildDialogTurn(input: DialogTurnInput): AskTurn {
  if (input.denied) {
    const spoken = PROMPTS.KeptPrompt;
    return buildAskTurn({
      tool: null,
      spoken,
      view: toView({ deleted: true, items: [] }),
      result: undefined,
      intent: input.intent,
      sessionAttributes: input.sessionAttributes,
      deliveryStyle: input.deliveryStyle,
    });
  }
  const step = input.step;
  if (!step) throw new Error('buildDialogTurn needs a dialog step');
  const spoken = fillPrompt(PROMPTS[step.promptId as AskPromptId] ?? PROMPTS.FallbackPrompt, input.slots);
  const view = step.stage === 'confirm'
    ? toView({ needs_confirmation: true, question: spoken })
    : toView({ needs_clarification: true, question: spoken });
  return buildAskTurn({
    tool: null,
    spoken,
    view,
    result: undefined,
    intent: input.intent,
    askSlots: input.askSlots,
    sessionAttributes: input.sessionAttributes,
    elicitSlot: step.slot,
    deliveryStyle: input.deliveryStyle,
  });
}

/** The pacing rule, asserted in test: stripped markup equals the server's exact sentence. */
export function preservesText(ssml: string, expected: string): boolean {
  return analyze(ssml).plain === expected.replace(/\s+/g, ' ').trim();
}

export const VOICE_LIMITS = VOICE_BUDGET;
