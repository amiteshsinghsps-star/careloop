/** Markup tests: the text a device says must survive the round trip through SSML, and the checks
 *  must actually be able to fail. */
import { describe, expect, it } from 'vitest';
import {
  Ssml, analyze, escapeXml, speakInto, toPlainText, SIM_MAX_SSML_CHARS, SIM_WORDS_PER_MINUTE,
} from './ssml';
import { VOICE_BUDGET } from './interactionModel';

describe('escapeXml', () => {
  it('covers the five entities a voice payload can carry', () => {
    expect(escapeXml(`a & b < c > d " e ' f`)).toBe('a &amp; b &lt; c &gt; d &quot; e &apos; f');
  });

  it('escapes once, never twice', () => {
    expect(escapeXml('&amp;')).toBe('&amp;amp;');
  });
});

describe('round trip', () => {
  const sentences = [
    'Take your prescribed amount, then rest the knee.',
    'Your clinician said: "walk twenty minutes, twice a day."',
    'Mum’s physio moved to Thursday & the gym closed.',
    'One. Two. Three.',
  ];

  for (const sentence of sentences) {
    it(`keeps “${sentence.slice(0, 24)}…” byte for byte`, () => {
      expect(toPlainText(speakInto(sentence))).toBe(sentence);
      expect(analyze(Ssml.fromText(sentence).build()).plain).toBe(sentence);
    });
  }

  it('keeps the words when it adds a breath between sentences', () => {
    const builder = new Ssml();
    builder.text('Take it with food. ');
    builder.break('x-weak');
    builder.text('Then drink water.');
    expect(toPlainText(builder.build())).toBe('Take it with food. Then drink water.');
  });
});

describe('the checks', () => {
  it('rejects a tag the device cannot speak', () => {
    const result = analyze('<speak><marquee>hello</marquee></speak>');
    expect(result.problems.join(' ')).toContain('unsupported tag');
  });

  it('rejects markup that never closes', () => {
    expect(analyze('<speak><p>hello</speak>').problems.length).toBeGreaterThan(0);
  });

  it('rejects a bare ampersand a listener would hear as a word', () => {
    const result = analyze('<speak>Tom & Jerry</speak>');
    expect(result.problems.join(' ')).toContain('unescaped');
  });

  it('rejects a placeholder that was never filled in', () => {
    expect(analyze('<speak>Ask {Person} first</speak>').problems.join(' ')).toContain('Placeholder');
  });

  it('rejects anything over the model’s own word budget', () => {
    const long = Array.from({ length: VOICE_BUDGET.maxSpokenWords + 12 }, (_, i) => `word${i}`).join(' ');
    const result = analyze(speakInto(long));
    expect(result.words).toBeGreaterThan(VOICE_BUDGET.maxSpokenWords);
    expect(result.problems.join(' ')).toContain('voice budget');
  });

  it('rejects speech wrapped in nothing', () => {
    expect(analyze('no wrapper at all').problems.join(' ')).toContain('<speak>');
  });

  it('holds its own ceiling', () => {
    const huge = `<speak>${'x'.repeat(SIM_MAX_SSML_CHARS + 10)}</speak>`;
    expect(analyze(huge).problems.join(' ')).toContain('ceiling');
  });
});

describe('measurement', () => {
  it('counts a timed break in milliseconds', () => {
    const builder = new Ssml().text('Pause. ').pause(500).text('After.');
    const result = analyze(builder.build());
    expect(result.breakMs).toBe(500);
    expect(result.plain).toBe('Pause. After.');
  });

  it('knows the default length of a strength break', () => {
    expect(analyze(new Ssml().text('A ').break('strong').text('B').build()).breakMs).toBe(650);
  });

  it('estimates longer speech for more words, at the stated rate', () => {
    const few = analyze(speakInto('One two three'));
    const many = analyze(speakInto(Array.from({ length: 30 }, () => 'word').join(' ')));
    expect(many.estimateMs).toBeGreaterThan(few.estimateMs);
    expect(few.estimateMs).toBe(Math.round((3 / SIM_WORDS_PER_MINUTE) * 60_000));
  });

  it('keeps say-as markup out of the words it counts twice', () => {
    const result = analyze(new Ssml().number('400').build());
    expect(result.plain).toBe('400');
    expect(result.words).toBe(1);
  });
});
