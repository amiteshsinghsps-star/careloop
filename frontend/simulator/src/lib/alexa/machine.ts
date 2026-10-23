/** The device's own clock: a small deterministic state machine for a voice exchange.
 *
 *  Nothing here reads `Date.now()` or `setTimeout`. Time only moves when `tick(ms)` is called, so
 *  a test can prove that a silence gap produces exactly one reprompt and then a session end, and
 *  the browser store drives the same machine with a real interval. Timers come from the shipped
 *  interaction model, not from numbers invented here; the two that are ours are marked `SIM_`.
 */
import { PROMPTS, VOICE_BUDGET } from './interactionModel';
import type { AskState, AskTurn } from './response';

export type DeviceState =
  | 'idle'          // standby, nothing heard
  | 'wake'          // wake word landed, mic open
  | 'listening'     // capturing a phrase
  | 'processing'    // a tool call is in flight
  | 'speaking'      // output speech is playing
  | 'awaiting'      // spoken answer done, window open for a follow-up or an slot answer
  | 'reprompting'   // silence: the device asked again
  | 'interrupted'   // the customer barged in over the answer
  | 'closed';       // session ended

export type MachineEventKind =
  | 'state' | 'heard' | 'answer' | 'barge-in' | 'silence' | 'reprompt' | 'end' | 'problem'
  | 'arm' | 'resume';

export interface MachineEvent {
  atMs: number;
  kind: MachineEventKind;
  from: DeviceState;
  to: DeviceState;
  note: string;
}

export interface EndReason {
  reason: 'USER_INITIATED' | 'EXCEEDED_MAX_REPROMPTS' | 'ERROR';
}

/** The device gives up after this many unanswered silences. Ours, not the model's. */
export const SIM_MAX_REPROMPTS = 2;

export interface MachineHooks {
  /** Called when the machine wants the answer spoken. The store wires this to TTS. */
  speak?(text: string): void;
  /** Called when the machine asks the customer again. */
  reprompt?(text: string): void;
}

export class DeviceMachine {
  private clock = 0;
  private timeline: MachineEvent[] = [];
  private reprompts = 0;
  private speakUntil: number | null = null;
  private silenceUntil: number | null = null;
  private heldTurn: AskTurn | null = null;
  /** True while a connection the device started owns the open window. Silence is not the customer's
   *  to answer for in that case: the turn coming next is the device's own. */
  private connectionOpen = false;

  state: DeviceState = 'idle';
  readonly limits = {
    silenceMs: VOICE_BUDGET.silenceTimeoutSeconds * 1000,
    repromptMs: VOICE_BUDGET.repromptAfterSeconds * 1000,
    maxReprompts: SIM_MAX_REPROMPTS,
  };

  constructor(private readonly hooks: MachineHooks = {}) {}

  get nowMs(): number {
    return this.clock;
  }

  get events(): readonly MachineEvent[] {
    return this.timeline;
  }

  get repromptCount(): number {
    return this.reprompts;
  }

  get currentTurn(): AskTurn | null {
    return this.heldTurn;
  }

  private move(to: DeviceState, kind: MachineEventKind, note: string): void {
    const from = this.state;
    this.state = to;
    this.timeline.push({ atMs: this.clock, kind, from, to, note });
  }

  /** Wake word heard: the mic opens. */
  wake(): void {
    if (this.state === 'closed') this.reset();
    this.move('wake', 'state', 'wake word');
    this.move('listening', 'state', 'microphone open');
  }

  /** The customer finished a phrase; a tool call is about to go out. */
  heard(utterance: string): void {
    this.connectionOpen = false;      // a phrase takes the turn back from any pending connection
    this.move('processing', 'heard', `heard “${utterance}”`);
  }

  /** The tool answered. The device speaks, then holds the window the model asks for. */
  answered(turn: AskTurn): void {
    this.heldTurn = turn;
    this.reprompts = 0;
    this.move('speaking', 'answer', `${turn.words} words, about ${Math.round(turn.estimateMs / 100) / 10} s`);
    this.speakUntil = this.clock + turn.estimateMs;
    this.hooks.speak?.(turn.spoken);
    if (turn.state === 'end') {
      this.close({ reason: 'USER_INITIATED' });
      return;
    }
    for (const problem of turn.problems) this.move(this.state, 'problem', problem);
  }

  /** A phrase the shipped model cannot match never reaches the skill, so there is no response
   *  envelope to hold: the device plays its own fallback prompt. Timed like any other answer. */
  answeredLocal(speech: { spoken: string; words: number; estimateMs: number }): void {
    this.heldTurn = null;
    this.reprompts = 0;
    this.move('speaking', 'answer',
      `${speech.words} words, about ${Math.round(speech.estimateMs / 100) / 10} s, spoken locally: the model did not match`);
    this.speakUntil = this.clock + speech.estimateMs;
    this.hooks.speak?.(speech.spoken);
  }

  /** The speech finished: the follow-up or elicitation window opens. */
  speechFinished(): void {
    if (this.state !== 'speaking') return;
    this.speakUntil = null;
    const label: Record<AskState, string> = {
      end: 'session closing',
      await: 'answer finished, waiting for a follow-up',
      elicit: `waiting for the ${this.heldTurn?.slotToElicit ?? 'missing'} slot`,
      confirm: 'waiting for a yes or no',
    };
    const next: DeviceState = this.heldTurn?.state === 'end' ? 'closed' : 'awaiting';
    this.move(next, 'state', label[this.heldTurn?.state ?? 'await']);
    if (next === 'awaiting' && !this.connectionOpen) this.silenceUntil = this.clock + this.limits.silenceMs;
  }

  /** The customer talks over the answer — the device stops speaking and listens. */
  bargeIn(): void {
    if (this.state !== 'speaking' && this.state !== 'awaiting') return;
    this.speakUntil = null;
    this.hooks.speak?.('');           // the store reads an empty string as "stop"
    this.move('listening', 'barge-in', 'customer interrupted the playback');
  }

  /** A phrase heard while the machine was waiting: back into processing. */
  followUp(utterance: string): void {
    this.silenceUntil = null;
    this.heard(utterance);
  }

  /** The answer held a `Connections.StartConnection` directive: the device scheduled its own next
   *  turn. Nothing is spoken and no state changes — the session simply stays open — so this is a
   *  note on the timeline, not a transition. */
  armed(connection: { uri: string; afterMs: number }): void {
    this.connectionOpen = true;
    this.silenceUntil = null;
    this.move(this.state, 'arm', `connection started: ${connection.uri}, resumes in ${connection.afterMs / 1000} s`);
  }

  /** The connection completed, so the device resumed its own session. The customer never asked for
   *  this turn: it comes from the scheduled connection, not from a phrase. */
  resumed(): void {
    this.connectionOpen = false;
    this.silenceUntil = null;
    this.speakUntil = null;
    this.move('processing', 'resume', 'the device resumed its own session: a connection it started completed');
  }

  /** Silence ran out. One reprompt at a time; after `maxReprompts` the session ends. */
  private onSilence(): void {
    this.silenceUntil = null;
    this.reprompts += 1;
    if (this.reprompts > this.limits.maxReprompts) {
      this.move('reprompting', 'silence', `no answer after ${this.limits.maxReprompts} reprompts`);
      this.close({ reason: 'EXCEEDED_MAX_REPROMPTS' });
      return;
    }
    const ask = this.heldTurn?.envelope.response.reprompt?.outputSpeech;
    const text = ask && 'ssml' in (ask as object) ? String((ask as { ssml?: string }).ssml ?? '') : '';
    this.move('reprompting', 'silence', `silence for ${this.limits.silenceMs / 1000} s`);
    this.hooks.reprompt?.(text.replace(/<\/?speak>/g, '') || PROMPTS.HelpPrompt);
    this.silenceUntil = this.clock + this.limits.repromptMs;
  }

  close(end: EndReason): void {
    this.speakUntil = null;
    this.silenceUntil = null;
    this.connectionOpen = false;    // a closed session has nothing left to resume
    this.move('closed', 'end', `session ended: ${end.reason}`);
  }

  /** Advance the clock and fire whatever came due. Returns the events this tick produced. */
  tick(ms: number): MachineEvent[] {
    if (ms <= 0) return [];
    const before = this.timeline.length;
    this.clock += ms;
    if (this.speakUntil !== null && this.clock >= this.speakUntil) this.speechFinished();
    if (this.silenceUntil !== null && this.clock >= this.silenceUntil) this.onSilence();
    return this.timeline.slice(before);
  }

  reset(): void {
    this.clock = 0;
    this.timeline = [];
    this.reprompts = 0;
    this.speakUntil = null;
    this.silenceUntil = null;
    this.heldTurn = null;
    this.connectionOpen = false;
    this.state = 'idle';
  }

  /** The transcript-shaped event list the Device Lab renders and can export. */
  toTrace(): MachineEvent[] {
    return this.timeline.map((event) => ({ ...event }));
  }
}
