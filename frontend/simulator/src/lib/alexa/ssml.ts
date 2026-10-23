/** SSML for the simulated device: build it, parse it back, measure it, check it.
 *
 *  Two rules from README shape this file. Nothing here writes health text — a builder only
 *  wraps text that came from the server. And every budget the checker enforces is either read
 *  out of the shipped interaction model (`VOICE_BUDGET`) or marked `SIM_` to say it is a limit
 *  this repo chose, not a quota claimed from Amazon.
 */
import { VOICE_BUDGET } from './interactionModel';

/** The five entities `escapeXmlCharacters()` in ask-sdk-core escapes — tested against it. */
export function escapeXml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

const NAMED_ENTITIES: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" };

/** The inverse of `escapeXml`, so the string held up against the card is the sentence that was
 *  said and not the markup that carried it. One pass is enough: `escapeXml` escapes `&` first,
 *  so a double-encoded `&amp;lt;` decodes back to the literal `&lt;` the speaker meant. */
export function unescapeXml(value: string): string {
  return value.replace(/&(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);/g, (whole, body: string) => {
    if (body[0] === '#') {
      const code = body[1] === 'x' || body[1] === 'X'
        ? Number.parseInt(body.slice(2), 16)
        : Number.parseInt(body.slice(1), 10);
      return Number.isFinite(code) && code >= 0 && code <= 0x10ffff ? String.fromCodePoint(code) : whole;
    }
    return NAMED_ENTITIES[body] ?? whole;
  });
}

export type SayAs =
  | 'cardinal' | 'characters' | 'date' | 'digits' | 'duration'
  | 'fraction' | 'interjection' | 'ordinal' | 'spell-out' | 'telephone' | 'time';
export type Emphasis = 'none' | 'reduced' | 'moderate' | 'strong';
export type BreakStrength = 'x-weak' | 'weak' | 'medium' | 'strong' | 'x-strong';
export type Alphabet = 'ipa' | 'x-sampa' | 'arpabet';

/** The tags this simulator can emit or parse. Anything else fails the validator. */
export const SUPPORTED_TAGS = ['speak', 'p', 's', 'break', 'emphasis', 'prosody', 'phoneme', 'say-as', 'w', 'lang'] as const;
export type SupportedTag = (typeof SUPPORTED_TAGS)[number];

/** Rough speaking rate used for the timeline bar only, so a turn can be shown as a duration.
 *  A display assumption of this repo, measured off nothing — label it as such in the UI. */
export const SIM_WORDS_PER_MINUTE = 165;
/** A hard ceiling for the whole envelope, chosen here so a runaway response cannot hang the tab. */
export const SIM_MAX_SSML_CHARS = 10_000;

const BREAK_MS: Record<BreakStrength, number> = { 'x-weak': 100, weak: 250, medium: 400, strong: 650, 'x-strong': 900 };

export type Token =
  | { kind: 'text'; text: string }
  | { kind: 'break'; timeMs: number; strength?: BreakStrength }
  | { kind: 'mark'; name: string }
  | { kind: 'open'; tag: SupportedTag; attrs: Record<string, string> }
  | { kind: 'close'; tag: SupportedTag };

/** Fluent SSML builder. Every method escapes what it is given, so a quote from a transcript can
 *  never break out of the `<speak>` element or inject a tag of its own. */
export class Ssml {
  private parts: string[] = [];

  static fromText(text: string): Ssml {
    return new Ssml().text(text);
  }

  text(value: string, sayAs?: SayAs): this {
    const safe = escapeXml(value);
    this.parts.push(sayAs ? `<say-as interpret-as="${sayAs}">${safe}</say-as>` : safe);
    return this;
  }

  /** Read a number as a number, not as digits — the difference between "two twenty" and "2 20". */
  number(value: number | string): this {
    return this.text(String(value), 'cardinal');
  }

  date(value: string): this {
    return this.text(value, 'date');
  }

  time(value: string): this {
    return this.text(value, 'time');
  }

  spell(value: string): this {
    return this.text(value, 'spell-out');
  }

  break(strength: BreakStrength = 'medium'): this {
    this.parts.push(`<break strength="${strength}"/>`);
    return this;
  }

  pause(ms: number): this {
    this.parts.push(`<break time="${Math.max(0, Math.round(ms))}ms"/>`);
    return this;
  }

  emphasis(value: string, level: Emphasis = 'moderate'): this {
    this.parts.push(`<emphasis level="${level}">${escapeXml(value)}</emphasis>`);
    return this;
  }

  prosody(value: string, opts: { rate?: string; pitch?: string; volume?: string } = {}): this {
    const attrs = Object.entries(opts)
      .filter(([, v]) => Boolean(v))
      .map(([k, v]) => `${k}="${escapeXml(String(v))}"`)
      .join(' ');
    this.parts.push(`<prosody${attrs ? ` ${attrs}` : ''}>${escapeXml(value)}</prosody>`);
    return this;
  }

  phoneme(value: string, alphabet: Alphabet, ph: string): this {
    this.parts.push(`<phoneme alphabet="${alphabet}" ph="${escapeXml(ph)}">${escapeXml(value)}</phoneme>`);
    return this;
  }

  sentence(value: string): this {
    this.parts.push(`<s>${escapeXml(value)}</s>`);
    return this;
  }

  paragraph(sentences: string[]): this {
    this.parts.push(`<p>${sentences.map((s) => escapeXml(s)).join(' ')}</p>`);
    return this;
  }

  get inner(): string {
    return this.parts.join(' ');
  }

  build(): string {
    return `<speak>${this.inner}</speak>`;
  }
}

export interface SsmlAnalysis {
  ssml: string;
  /** The words a listener hears: tags dropped, `<break>` and `<mark>` contributing nothing. */
  plain: string;
  tokens: Token[];
  words: number;
  characters: number;
  breakMs: number;
  /** Words plus the silence the markup asks for, at `SIM_WORDS_PER_MINUTE`. */
  estimateMs: number;
  /** Every unsupported tag or malformed markup found; empty means the markup is sound. */
  problems: string[];
}

const TOKEN_RE = /<\s*([a-zA-Z][\w:.-]*)([^>]*?)\s*(\/?)>|<\s*\/\s*([a-zA-Z][\w:.-]*)\s*>|([^<]+)/g;

/** A deliberately small tokenizer: enough to see structure, not a general XML parser. */
export function tokenize(ssml: string): Token[] {
  const tokens: Token[] = [];
  const seen = new Set<string>();
  TOKEN_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  let dirty = ssml;
  // Comments would be data the model never emits; strip them before tokenising.
  if (dirty.includes('<!--')) {
    dirty = dirty.replace(/<!--[\s\S]*?-->/g, '');
    seen.add('comments-stripped');
  }
  while ((match = TOKEN_RE.exec(dirty)) !== null) {
    const [, openName, rawAttrs, selfClose, closeName, textRun] = match;
    if (textRun !== undefined) {
      const trimmed = textRun.replace(/\s+/g, ' ');
      if (trimmed.trim()) tokens.push({ kind: 'text', text: trimmed });
      continue;
    }
    if (closeName !== undefined) {
      tokens.push({ kind: 'close', tag: closeName.toLowerCase() as SupportedTag });
      continue;
    }
    const tagName = openName.toLowerCase();
    const tag = tagName as SupportedTag;
    const attrs: Record<string, string> = {};
    for (const [, key, value] of rawAttrs.matchAll(/([\w-]+)\s*=\s*"([^"]*)"/g)) {
      attrs[key] = value;
    }
    if (tagName === 'break') {
      tokens.push({ kind: 'break', timeMs: breakToMs(attrs), strength: attrs.strength as BreakStrength | undefined });
    } else if (tagName === 'mark') {
      tokens.push({ kind: 'mark', name: attrs.name ?? '' });
    } else if (selfClose === '/') {
      tokens.push({ kind: 'open', tag, attrs });
      tokens.push({ kind: 'close', tag });
    } else {
      tokens.push({ kind: 'open', tag, attrs });
    }
  }
  return tokens;
}

function breakToMs(attrs: Record<string, string>): number {
  if (attrs.time) {
    const value = Number.parseFloat(attrs.time);
    if (Number.isFinite(value)) return attrs.time.endsWith('ms') ? value : value * 1000;
  }
  return BREAK_MS[(attrs.strength ?? 'medium') as BreakStrength] ?? 400;
}

/** Unescaped markup damage a listener would hear as a literal ampersand or a swallowed word. */
function strayEntities(ssml: string): string[] {
  const problems: string[] = [];
  const badAmpersand = ssml.match(/&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)/g);
  if (badAmpersand) problems.push(`${badAmpersand.length} unescaped & in the markup`);
  if (ssml.includes('${') || /\{[A-Z][A-Za-z]*\}/.test(ssml)) problems.push('an unreplaced {Placeholder} survived into speech');
  return problems;
}

export function analyze(ssml: string): SsmlAnalysis {
  const tokens = tokenize(ssml);
  const problems: string[] = [];

  if (!ssml.trim().startsWith('<speak>') || !ssml.trim().endsWith('</speak>')) {
    problems.push('speech must be wrapped in <speak>…</speak>');
  }
  const stack: SupportedTag[] = [];
  for (const token of tokens) {
    if (token.kind === 'open') {
      if (!SUPPORTED_TAGS.includes(token.tag)) problems.push(`unsupported tag <${token.tag}>`);
      stack.push(token.tag);
    } else if (token.kind === 'close') {
      const opened = stack.pop();
      if (opened !== token.tag) problems.push(`closing </${token.tag}> does not match <${opened ?? 'nothing'}>`);
    }
  }
  if (stack.length) problems.push(`${stack.length} tag(s) never closed (${stack.join(', ')})`);
  problems.push(...strayEntities(ssml));

  const plain = unescapeXml(
    tokens
      .filter((t): t is { kind: 'text'; text: string } => t.kind === 'text')
      .map((t) => t.text)
      .join(''),
  )
    .replace(/\s+/g, ' ')
    .trim();
  const wordList = plain ? plain.split(' ') : [];
  const breakMs = tokens.reduce((total, t) => total + (t.kind === 'break' ? t.timeMs : 0), 0);
  const words = wordList.length;
  // A `<prosody rate>` makes the same words take longer: "slower" that asks for 80% of the usual
  // pace really does run long, and the timeline bar has to show that rather than the old estimate.
  // Only a percentage rate is scaled — a word like "x-slow" has no number to multiply by.
  const pacing = tokens.reduce((total, token) => {
    if (token.kind !== 'open' || token.tag !== 'prosody' || !token.attrs.rate) return total;
    const value = Number.parseFloat(token.attrs.rate);
    return Number.isFinite(value) && value > 0 ? total * (100 / value) : total;
  }, 1);
  const estimateMs = Math.round((words / SIM_WORDS_PER_MINUTE) * 60_000 * pacing) + breakMs;

  if (words > VOICE_BUDGET.maxSpokenWords) {
    problems.push(`${words} words is over the model's ${VOICE_BUDGET.maxSpokenWords}-word voice budget`);
  }
  if (estimateMs > VOICE_BUDGET.maxSpokenSeconds * 1000) {
    problems.push(`about ${Math.round(estimateMs / 1000)} s is over the model's ${VOICE_BUDGET.maxSpokenSeconds} s limit`);
  }
  if (ssml.length > SIM_MAX_SSML_CHARS) {
    problems.push(`${ssml.length} characters is over this simulator's ${SIM_MAX_SSML_CHARS} ceiling`);
  }

  return { ssml, plain, tokens, words, characters: plain.length, breakMs, estimateMs, problems };
}

/** Turn markup back into the sentence a customer heard — the string the card is held against. */
export function toPlainText(ssml: string): string {
  return analyze(ssml).plain;
}

export function speakInto(value: string): string {
  return Ssml.fromText(value).build();
}

/** The markup inside an `outputSpeech` block. A plain-text block has none, and this is the only
 *  place that has to know the union: `ask-sdk-model` types it as Ssml | PlainText. */
export function ssmlOf(speech: { type?: string; ssml?: string } | undefined): string {
  return typeof speech?.ssml === 'string' ? speech.ssml : '';
}

/** The same markup, spoken differently: the words, the escaping and every `<break>` stay exactly as
 *  they were, and a `<prosody>` element is put around what is already inside `<speak>`. This is
 *  presentation only — nothing here can add, remove or re-word a sentence, which is what the
 *  round-trip test below proves. */
export function prosodize(ssml: string, opts: { rate?: string | null; volume?: string | null } = {}): string {
  const attrs = ([['rate', opts.rate], ['volume', opts.volume]] as Array<[string, string | null | undefined]>)
    .filter(([, value]) => Boolean(value))
    .map(([key, value]) => `${key}="${escapeXml(String(value))}"`)
    .join(' ');
  if (!attrs) return ssml;
  const inner = ssml.replace(/^<speak>/, '').replace(/<\/speak>$/, '');
  return `<speak><prosody ${attrs}>${inner}</prosody></speak>`;
}
