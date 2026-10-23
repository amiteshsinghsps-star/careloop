import { dayPartLabel, kindLabel, STATUS_LABEL } from '@careloop/ui';
import type { CareItem, LedgerResult, Schedule } from '@careloop/client';

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

/** Render a care item's schedule object as one plain-language line. */
export function scheduleLine(s: Schedule | undefined | null): string {
  if (!s) return 'Any time';
  const bits: string[] = [];
  const parts = (s.day_parts ?? []).map((p) => dayPartLabel(p));
  if (parts.length) bits.push(parts.join(' · '));
  if (s.times_per_day && s.times_per_day > 1) bits.push(`${s.times_per_day}× a day`);
  if (s.every && s.every.n) bits.push(`every ${s.every.n} ${s.every.unit}${s.every.n > 1 ? 's' : ''}`);
  if (s.weekdays && s.weekdays.length) bits.push('on ' + s.weekdays.map((w) => WEEKDAYS[w] ?? '?').join(', '));
  if (s.once_on) bits.push('on ' + s.once_on);
  if (!bits.length && s.raw) bits.push(s.raw);
  return bits.length ? bits.join(' · ') : 'Any time';
}

export function statusText(item: CareItem): string {
  return STATUS_LABEL[item.status] ?? item.status;
}

/** Map a status onto a shared pill tone (never invents a colour, only reuses existing pills). */
export function statusPill(item: CareItem): string {
  switch (item.status) {
    case 'done':
      return 'done';
    case 'superseded':
      return 'info';
    case 'deleted':
      return 'bad';
    default:
      return item.needs_confirm ? 'warn' : 'ok';
  }
}

export function confidencePct(item: CareItem): number {
  const c = item.confidence;
  if (c == null || Number.isNaN(c)) return 0;
  const pct = c <= 1 ? c * 100 : c;
  return Math.max(0, Math.min(100, Math.round(pct)));
}

export function formatTime(secs: number): string {
  if (!secs) return '—';
  const ms = secs < 1e12 ? secs * 1000 : secs;
  const d = new Date(ms);
  return d.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function ledgerDetail(l: LedgerResult | null): string {
  if (!l) return 'Not checked yet.';
  if (l.ok) return `Intact · ${l.events ?? 0} events`;
  return `Broken at event ${l.broken_at ?? '?'}${l.detail ? ' — ' + l.detail : ''}`;
}

export { kindLabel };
