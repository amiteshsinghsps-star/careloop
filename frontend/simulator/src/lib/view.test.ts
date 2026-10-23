import { describe, expect, it } from 'vitest';
import { toView } from '@careloop/client';
import type { MaskedItem } from '@careloop/client';

/** toView() is the only render model in this product: the sentence the device speaks and
 *  the card on screen come from the same object. If a shape is unknown it must degrade to a
 *  visible “nothing to show”, never to a blank screen or an invented answer. */
const masked = (over: Partial<MaskedItem> = {}): MaskedItem => ({
  ref: 'r1',
  kind: 'medication',
  text: 'Take one tablet [dose masked] every morning',
  schedule: 'Morning',
  status: 'pending',
  needs_confirm: false,
  ...over,
});

describe('toView(): shape -> kind', () => {
  it('reads a care plan', () => {
    const view = toView({
      person: 'Papa',
      items: [masked(), masked({ ref: 'r2', kind: 'avoid', text: 'No ibuprofen [dose masked]' })],
      attribution: 'From your father’s clinic visit on 12 Oct.',
      disclaimer: 'CareLoop repeats what the clinician recorded.',
      dose_policy: 'Doses are masked on this surface.',
    });
    expect(view.kind).toBe('plan');
    expect(view.title).toBe('Care plan');
    expect(view.items).toHaveLength(2);
    expect(view.person).toBe('Papa');
    expect(view.dosePolicy).toBe('Doses are masked on this surface.');
    expect(view.attribution).toContain('12 Oct');
  });

  it('groups an agenda and labels the day parts', () => {
    const view = toView({
      slots: [
        { part_of_day: 'MORNING', items: [masked()] },
        { part_of_day: 'BEDTIME', items: [masked({ ref: 'r9', schedule: 'Bedtime' })] },
      ],
    });
    expect(view.kind).toBe('agenda');
    expect(view.slots?.map((s) => s.part_of_day)).toEqual(['Morning', 'Bedtime']);
    expect(view.items).toHaveLength(2);
  });

  it('summarises a diff between visits', () => {
    const view = toView({
      new: [masked({ ref: 'n1' })],
      stopped: [masked({ ref: 's1', kind: 'avoid' })],
      changed: [],
      compared_to: 'the visit on 2 Oct',
    });
    expect(view.kind).toBe('diff');
    expect(view.title).toBe('What changed');
    expect(view.items.map((i) => i.ref)).toEqual(['n1', 's1']);
    expect(view.raw.compared_to).toBe('the visit on 2 Oct');
  });

  it('shows the clinician’s own words', () => {
    const view = toView({ quote: 'Do not take the ibuprofen any more.', recorded_on: '2026-10-12' });
    expect(view.kind).toBe('quote');
    expect(view.quote).toContain('ibuprofen');
    expect(view.recordedOn).toBe('2026-10-12');
  });

  it('lists the household, the digest and the prep questions', () => {
    expect(toView({ people: [{ name: 'Amma', role: 'elder', pending_count: 3 }] }).kind).toBe('people');
    const digest = toView({ done: 2, pending: 4, last_done_at: '08:15' });
    expect(digest.kind).toBe('digest');
    expect(digest.numbers).toEqual({ done: 2, pending: 4, last_done_at: '08:15' });
    expect(toView({ open_flags: [masked({ ref: 'f1' })], skipped: [], notes: [] }).kind).toBe('prep');
  });

  it('turns the guard payloads into their own kinds', () => {
    const clarify = toView({ needs_clarification: true, question: 'Who is this about?' });
    expect(clarify.kind).toBe('clarify');
    expect(clarify.question).toBe('Who is this about?');

    const crisis = toView({ crisis: true, message: 'Call your local emergency number.', items: [] });
    expect(crisis.kind).toBe('crisis');
    expect(crisis.note).toContain('emergency');

    const confirm = toView({
      needs_confirmation: true,
      summary: { ref: 'r7', kind: 'medication', quote: 'amlodipine [dose masked]' },
    });
    expect(confirm.kind).toBe('confirm-delete');
    expect(confirm.question).toContain('Medicine');

    expect(toView({ deleted: true, ref: 'r7' }).kind).toBe('deleted');
    expect(toView({ status: 'done', remaining_count: 3 }).raw.remaining_count).toBe(3);
    expect(toView({ status: 'rejected', message: 'Adult plans only.' }).note).toBe('Adult plans only.');
    expect(toView({ status: 'added', items: [masked()] }).kind).toBe('added');
    expect(toView({ capabilities: ['Read the plan'], examples: ['what is next?'] }).kind).toBe('about');
  });

  it('takes every note from the payload, never invents one', () => {
    // Notes are server wording or a server count restated — no dosage, no invented text.
    expect(toView({ crisis: true, message: 'Call 112.' }).note).toBe('Call 112.');
    const done = toView({ status: 'done', remaining_count: 3 });
    expect(done.note).toBe('3 still to do.');
    expect(done.raw.remaining_count).toBe(3);
    expect(toView({ status: 'done', count_hint: 'All clear.' }).note).toBe('All clear.');
    expect(toView({ status: 'done' }).note).toBeUndefined();
    expect(toView({ error: 'No such item.' }, true).note).toBe('No such item.');
    expect(toView({ deleted: true, ref: 'r7' }).note).toBeUndefined();
    // an unrecognised shape degrades to the honest "no source" line, not a blank card
    expect(String(toView(undefined).note)).toContain('recorded answer');
  });

  it('marks tool errors and unknown shapes distinctly', () => {
    const boom = toView({ error: 'I couldn’t find that item.' }, true);
    expect(boom.kind).toBe('error');
    expect(boom.title).toBe('That did not work');
    expect(String(boom.raw.error)).toContain('couldn’t find');

    const blank = toView(undefined);
    expect(blank.kind).toBe('error');
    expect(blank.title).toBe('Nothing to show');
  });

  it('never un-masks what the server sent', () => {
    const view = toView({ person: 'Papa', items: [masked({ text: 'Take [dose masked] at night' })] });
    expect(view.items[0].text).toBe('Take [dose masked] at night');
    expect(JSON.stringify(view.items)).not.toMatch(/\b\d+\s?(mg|ml|units)\b/i);
  });
});
