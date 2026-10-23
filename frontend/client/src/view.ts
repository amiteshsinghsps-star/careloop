import { dayPartLabel, kindLabel } from '@careloop/ui';
import type { MaskedItem } from './types';

export type ViewKind =
  | 'about' | 'plan' | 'agenda' | 'diff' | 'quote' | 'people' | 'digest'
  | 'prep' | 'clarify' | 'confirm-delete' | 'deleted' | 'crisis' | 'done' | 'added' | 'error';

export interface Slot {
  part_of_day: string;
  items: MaskedItem[];
}

export interface ResultView {
  kind: ViewKind;
  title: string;
  person?: string | null;
  items: MaskedItem[];
  slots?: Slot[];
  people?: { name: string; role: string; pending_count: number }[];
  quote?: string;
  recordedOn?: string;
  attribution?: string;
  disclaimer?: string;
  dosePolicy?: string;
  note?: string;
  numbers?: { done: number; pending: number; last_done_at?: string | null };
  question?: string;
  capabilities?: string[];
  examples?: string[];
  raw: Record<string, unknown>;
}

/** Turns any tool's structuredContent into one render model, so voice and screen can
 *  never disagree (they are the same data) and so unknown shapes degrade to "no source". */
export function toView(structured: Record<string, unknown> | undefined, isError = false): ResultView {
  const s = (structured ?? {}) as any;
  const base = {
    person: s.person ?? null,
    attribution: s.attribution,
    disclaimer: s.disclaimer,
    dosePolicy: s.dose_policy,
    note: s.message ?? s.count_hint,
    raw: s,
  };
  if (isError || s.error) {
    return { ...base, kind: 'error', title: 'That did not work', items: [], note: s.error ?? 'Try again.' };
  }
  if (s.needs_clarification) {
    return { ...base, kind: 'clarify', title: 'One more thing', items: [], question: s.question };
  }
  if (s.crisis) {
    return { ...base, kind: 'crisis', title: 'Please get help now', items: [], note: s.message };
  }
  if (s.needs_confirmation) {
    const q = s.summary ? `${kindLabel(s.summary.kind)}: ${s.summary.text ?? s.summary.quote}` : 'this item';
    return {
      ...base, kind: 'confirm-delete', title: 'Confirm the removal', items: [],
      question: s.question ?? `Delete ${q}?`,
    };
  }
  if (s.deleted) return { ...base, kind: 'deleted', title: 'Removed from the plan', items: [] };
  if (s.status === 'done') {
    return {
      ...base, kind: 'done', title: 'Marked done', items: [],
      note: s.message ?? s.count_hint ??
        (Number.isFinite(s.remaining_count) ? `${s.remaining_count} still to do.` : undefined),
    };
  }
  if (s.status === 'rejected') {
    return { ...base, kind: 'error', title: 'Not added', items: [], note: s.message };
  }
  if (Array.isArray(s.items) && s.status === 'added') {
    return { ...base, kind: 'added', title: 'Added to the plan', items: s.items };
  }
  if (Array.isArray(s.items) && 'person' in s) {
    return { ...base, kind: 'plan', title: 'Care plan', items: s.items };
  }
  if (Array.isArray(s.slots)) {
    const slots: Slot[] = s.slots.map((x: Slot) => ({ part_of_day: dayPartLabel(x.part_of_day), items: x.items }));
    return {
      ...base, kind: 'agenda', title: 'Today',
      items: slots.flatMap((x: Slot) => x.items), slots,
    };
  }
  if (s.new || s.stopped || s.changed) {
    const items: MaskedItem[] = [
      ...(s.new ?? []).map((i: MaskedItem) => ({ ...i, kind: `${i.kind}` })),
      ...(s.stopped ?? []), ...(s.changed ?? []),
    ];
    return {
      ...base, kind: 'diff', title: 'What changed', items,
      note: `${(s.new ?? []).length} new · ${(s.stopped ?? []).length} stopped · ${(s.changed ?? []).length} changed`
        + ` · compared with ${s.compared_to ?? 'the earlier visit'}.`,
    };
  }
  if (s.quote) {
    return {
      ...base, kind: 'quote', title: 'The clinician’s own words', items: [],
      quote: s.quote, recordedOn: s.recorded_on,
    };
  }
  if (Array.isArray(s.people)) {
    return { ...base, kind: 'people', title: 'In this household', items: [], people: s.people };
  }
  if (typeof s.done === 'number') {
    return {
      ...base, kind: 'digest', title: 'Today so far', items: [],
      numbers: { done: s.done, pending: s.pending, last_done_at: s.last_done_at }, note: s.message,
    };
  }
  if (s.open_flags) {
    return {
      ...base, kind: 'prep', title: 'Ask at the next visit', items: s.open_flags ?? [],
      note: `${(s.skipped ?? []).length} replaced items and ${(s.notes ?? []).length} notes are listed too.`,
    };
  }
  if (s.capabilities) {
    return {
      ...base, kind: 'about', title: 'What CareLoop can do', items: [],
      capabilities: s.capabilities, examples: s.examples,
    };
  }
  return {
    ...base, kind: 'error', title: 'Nothing to show', items: [],
    note: 'I could not find a recorded answer for that.',
  };
}
