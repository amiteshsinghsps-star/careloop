import { describe, expect, it } from 'vitest';
import { highlightIndex, route } from '@careloop/client';
import type { MaskedItem } from '@careloop/client';

/** The router is the simulator's stand-in for Alexa's own model: it only picks a tool and
 *  passes the speaker's words through. These cases pin the phrase -> tool mapping so the
 *  demo behaves identically on every screen the judges look at. */
const HOUSEHOLD = ['Papa', 'Amma', 'Spouse'];

describe('route(): phrase to tool', () => {
  it.each([
    ['what can you do', 'about_careloop'],
    ['help', 'about_careloop'],
    ['who is in my household', 'list_people'],
    ["what's on today", 'todays_plan'],
    ['show me the schedule', 'todays_plan'],
    ['what changed since the last visit', 'what_changed'],
    ['what should I ask at the next visit', 'prepare_next_visit'],
    ['how am I doing today', 'daily_digest'],
    ['mark the first one as done', 'mark_done'],
    ['i finished the evening tablet', 'mark_done'],
    ['confirm the second item', 'confirm_instruction'],
    ['delete the third item', 'delete_instruction'],
    ['remove that one', 'delete_instruction'],
    ['why is this here', 'explain_instruction'],
    ['what is the source of that', 'explain_instruction'],
    ['remember to call the clinic about the rash', 'add_note'],
  ])('“%s” calls %s', (phrase, tool) => {
    expect(route(phrase, HOUSEHOLD).tool).toBe(tool);
  });

  it('passes the ordinal through as ref', () => {
    expect(route('confirm the second item', HOUSEHOLD).args).toMatchObject({ ref: 'second' });
    expect(route('delete number 3', HOUSEHOLD).args).toMatchObject({ ref: 'third' });
    expect(route('mark fourth as done', HOUSEHOLD).args).toMatchObject({ ref: 'fourth' });
  });

  it('reads a rank word as the rank, never as the filler word “one”', () => {
    // “the second one” names item two. The bare word one is an alias for the first item only when
    // no rank word is present, because a care item is named, not assumed — and this is the same
    // rule the voice device applies (scripts/alexa_device.py _find_ordinal), so a phrase means one
    // thing on both surfaces.
    expect(route('mark the second one done', HOUSEHOLD).args).toMatchObject({ ref: 'second' });
    expect(route('remove the third one', HOUSEHOLD).args).toMatchObject({ ref: 'third' });
    expect(route('remove that one', HOUSEHOLD).args).toMatchObject({ ref: 'first' });
  });

  it('resolves a relation word to a household member', () => {
    const intent = route('what is the plan for papa', HOUSEHOLD);
    expect(intent.tool).toBe('get_care_plan');
    expect(intent.args.person).toBe('Papa');
  });

  it('filters by kind when the phrase says medicine, appointment or avoid', () => {
    expect(route('what tablets does Amma take', HOUSEHOLD).args).toMatchObject({ kind: 'medication' });
    expect(route('when do we come back', HOUSEHOLD).args).toMatchObject({ kind: 'appointment' });
    expect(route('anything I should avoid', HOUSEHOLD).args).toMatchObject({ kind: 'avoid' });
  });

  it('never invents a person who is not in the household', () => {
    const intent = route('what is the plan for Uncle Roy', HOUSEHOLD);
    expect(intent.tool).toBe('get_care_plan');
    expect(intent.args.person).toBeUndefined();
  });

  it('explains itself in the read field, for the labelled UI', () => {
    expect(route('what can you do', HOUSEHOLD).read).toMatch(/careloop can do/i);
    expect(route("what's on today", HOUSEHOLD).read).toMatch(/today/i);
  });
});

describe('highlightIndex(): which item the phrase pointed at', () => {
  const items: MaskedItem[] = [
    { ref: 'r1', kind: 'medication', text: 'One tablet, masked dose', schedule: 'Morning', status: 'pending', needs_confirm: false },
    { ref: 'r2', kind: 'avoid', text: 'No ibuprofen', schedule: 'Any time', status: 'pending', needs_confirm: true },
    { ref: 'r3', kind: 'appointment', text: 'Come back in two weeks', schedule: 'Friday morning', status: 'pending', needs_confirm: false },
  ];

  it('maps an ordinal to that row', () => {
    expect(highlightIndex('the second item', items)).toBe(1);
    expect(highlightIndex('delete item number 3', items)).toBe(2);
  });

  it('maps a day part to the first item scheduled then', () => {
    expect(highlightIndex('what about the evening', items)).toBe(-1);
    expect(highlightIndex('took the morning one', items)).toBe(0);
  });

  it('maps a kind word to the first item of that kind', () => {
    expect(highlightIndex('do I still avoid things', items)).toBe(1);
    expect(highlightIndex('when is the appointment', items)).toBe(2);
  });

  it('falls back to the only item, or -1 when it cannot tell', () => {
    expect(highlightIndex('tell me more', items)).toBe(-1);
    expect(highlightIndex('tell me more', [items[0]])).toBe(0);
  });
});
