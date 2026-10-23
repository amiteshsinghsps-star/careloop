/** Adherence data → chart + text alternative (M16). The chart is drawn straight from these
 *  rows in SVG; the SAME rows drive a real `<table>` in the DOM, so nothing critical lives
 *  only in the graphic. Pure and framework-free (used by the Angular companion and unit-tested). */

export interface AdherenceRow {
  /** Person name, or the "Everyone" roll-up label. */
  person: string;
  done: number;
  pending: number;
  /** done + pending — the items counted for adherence (superseded/removed are excluded). */
  total: number;
}

interface AdherenceItem {
  person: string;
  status: string;
}

/** Group items by person and count done vs pending. Only 'done' and 'pending' statuses are
 *  counted (a superseded or deleted item is neither done nor outstanding). Order follows the
 *  first appearance of each person, so the chart and the table never disagree. */
export function adherenceRows(items: AdherenceItem[]): AdherenceRow[] {
  const order: string[] = [];
  const byPerson = new Map<string, { done: number; pending: number }>();
  for (const it of items) {
    const name = it.person || 'Unassigned';
    let bucket = byPerson.get(name);
    if (!bucket) {
      bucket = { done: 0, pending: 0 };
      byPerson.set(name, bucket);
      order.push(name);
    }
    if (it.status === 'done') bucket.done += 1;
    else if (it.status === 'pending') bucket.pending += 1;
  }
  return order.map((person) => {
    const b = byPerson.get(person)!;
    return { person, done: b.done, pending: b.pending, total: b.done + b.pending };
  });
}

/** Whole-household roll-up, appended after the per-person rows for the chart. */
export function adherenceTotal(rows: AdherenceRow[]): { done: number; pending: number; total: number } {
  return rows.reduce(
    (acc, r) => ({ done: acc.done + r.done, pending: acc.pending + r.pending, total: acc.total + r.total }),
    { done: 0, pending: 0, total: 0 },
  );
}

/** Percentage done (0-100), guarding a zero total so the SVG never divides by nothing. */
export function donePercent(row: { done: number; total: number }): number {
  if (!row.total) return 0;
  return Math.round((row.done / row.total) * 100);
}

/** Plain-language description of one row — reused as the SVG `aria-label` and the cell text,
 *  so the graphic and the table carry the identical statement. */
export function adherenceRowLabel(row: AdherenceRow): string {
  if (!row.total) return `${row.person}: nothing outstanding today`;
  return `${row.person}: ${row.done} of ${row.total} done, ${row.pending} still to do`;
}
