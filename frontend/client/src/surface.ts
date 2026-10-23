import { kindLabel, ordinal, ROLE_LABEL, STATUS_LABEL } from '@careloop/ui';
import type { MaskedItem } from './types';
import type { ResultView } from './view';

/**
 * The single, framework-free source of the *ordered content text* a faithful renderer
 * prints for one `ResultView`. Every surface that must "say the same thing" (the Astro
 * Accessibility Lab live-region log, the Svelte `PlanCard`, the MCP App card) reads its
 * announced content through here, so the spoken/announced order can never drift from the
 * drawn order. This is content only — it deliberately excludes interactive chrome
 * (button labels like "Mark done") and it NEVER invents text: every string is either a
 * closed-vocabulary label from `@careloop/ui` or a verbatim field the server returned.
 *
 * It is the parity guarantee made testable: give it the same `structuredContent` and the
 * Accessibility Lab and the simulator yield byte-identical, same-order text nodes.
 */

function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status;
}

/** Mirrors `PlanCard.pillFor()` exactly, so the announced status word matches the pill. */
function itemPillLabel(item: MaskedItem): string {
  if (item.needs_confirm && item.status === 'pending') return 'Check with the clinician';
  return statusLabel(item.status);
}

function itemNodes(item: MaskedItem, index: number): string[] {
  const nodes: string[] = [
    ordinal(index),
    kindLabel(item.kind),
    itemPillLabel(item),
    item.text,
  ];
  if (item.schedule) nodes.push(item.schedule);
  return nodes;
}

function footerNodes(view: ResultView): string[] {
  const out: string[] = [];
  if (view.attribution) out.push(view.attribution);
  if (view.dosePolicy) out.push(view.dosePolicy);
  if (view.disclaimer) out.push(view.disclaimer);
  return out;
}

/**
 * Ordered content text nodes for any `ResultView`. The order matches what the surfaces
 * render top-to-bottom: title, optional person, then the kind-specific body, then the
 * attribution / dose-policy / disclaimer footer. Unknown shapes fall back to the same
 * honest title + note the card shows — never blank, never invented.
 */
export function viewTextNodes(view: ResultView): string[] {
  const nodes: string[] = [view.title];
  if (view.person) nodes.push(view.person);

  switch (view.kind) {
    case 'agenda': {
      for (const slot of view.slots ?? []) {
        nodes.push(slot.part_of_day);
        if (!slot.items.length) nodes.push('Nothing at this time.');
        slot.items.forEach((item, i) => nodes.push(...itemNodes(item, i)));
      }
      break;
    }

    case 'quote':
      if (view.quote) nodes.push(view.quote);
      nodes.push(`Recorded ${view.recordedOn ?? 'on the visit'}`);
      break;

    case 'people':
      for (const p of view.people ?? []) {
        nodes.push(p.name, ROLE_LABEL[p.role] ?? p.role, String(p.pending_count));
      }
      break;

    case 'digest':
      nodes.push(
        String(view.numbers?.done ?? 0),
        String(view.numbers?.pending ?? 0),
        view.numbers?.last_done_at ?? '—',
      );
      if (view.note) nodes.push(view.note);
      break;

    case 'about':
      for (const cap of view.capabilities ?? []) nodes.push(cap);
      for (const ex of view.examples ?? []) nodes.push(ex);
      break;

    case 'clarify':
    case 'confirm-delete':
      if (view.question) nodes.push(view.question);
      break;

    case 'error':
      nodes.push(String(view.raw?.error ?? view.note ?? 'Nothing to show'));
      break;

    case 'crisis':
      if (view.note) nodes.push(view.note);
      break;

    case 'prep':
      view.items.forEach((item, i) => nodes.push(...itemNodes(item, i)));
      if (view.note) nodes.push(view.note);
      break;

    default: {
      // plan / added / diff and anything item-shaped.
      if (view.kind === 'diff' && view.note) nodes.push(view.note);
      if (view.items.length) {
        view.items.forEach((item, i) => nodes.push(...itemNodes(item, i)));
      } else if (view.note) {
        nodes.push(view.note);
      }
    }
  }

  for (const f of footerNodes(view)) nodes.push(f);
  return nodes;
}
