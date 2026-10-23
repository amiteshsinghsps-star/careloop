/** Care-sheet formatter for print + Web Share (M17). Runs on the authenticated companion
 *  surface, so doses are allowed here — but internal ids, refs and JSON must never appear.
 *  Pure and framework-free: the component maps `CareItem[]` into `SheetItem[]` and calls it. */

export interface SheetItem {
  person: string;
  /** Already-localised kind label (e.g. "Medicine"), not the raw enum. */
  kindLabel: string;
  /** The full, dose-bearing instruction text. */
  text: string;
  /** The clinician's own recorded words. */
  quote: string;
  /** One-line schedule, e.g. "Morning · 1× a day". */
  schedule: string;
  /** Human recorded date, already formatted by the caller (keeps this test deterministic). */
  recordedOn: string;
  statusLabel: string;
}

export interface SheetMeta {
  householdCode?: string;
  personFilter?: string | null;
  /** Formatted "now" the caller supplies so the output stays test-stable. */
  generatedOn: string;
}

export const SHEET_DISCLAIMER =
  'CareLoop repeats what your clinician recorded — it is not medical advice. '
  + 'Confirm every medicine, amount and timing with your clinician or pharmacist before acting. '
  + 'This sheet is for the household only and shows full doses, which the voice surface never does.';

/** Turn the (already filtered) plan into plain, printable text — no HTML, no ids, no JSON. */
export function buildCareSheet(items: SheetItem[], meta: SheetMeta): string {
  const lines: string[] = [];
  lines.push('CareLoop — care sheet');
  const who = meta.personFilter ? `for ${meta.personFilter}` : 'for the whole household';
  lines.push(who + (meta.householdCode ? ` · household ${meta.householdCode}` : ''));
  lines.push(`Recorded ${meta.generatedOn}`);
  lines.push('');

  if (!items.length) {
    lines.push('No care items are recorded for this view yet.');
    lines.push('');
    lines.push(SHEET_DISCLAIMER);
    return lines.join('\n');
  }

  items.forEach((it, i) => {
    lines.push(`${i + 1}. ${it.person} — ${it.kindLabel} (${it.statusLabel})`);
    lines.push(`   ${it.text}`);
    if (it.schedule) lines.push(`   When: ${it.schedule}`);
    lines.push(`   Clinician’s words: “${it.quote}”`);
    lines.push(`   Recorded: ${it.recordedOn}`);
    lines.push('');
  });

  lines.push(SHEET_DISCLAIMER);
  return lines.join('\n');
}

/** Does this browser expose the Web Share API? Kept separate so the caller can fall back to
 *  copying the exact same plain text when `navigator.share` is missing. */
export function canNativeShare(nav: Navigator | undefined = typeof navigator !== 'undefined' ? navigator : undefined): boolean {
  return typeof nav?.share === 'function';
}
