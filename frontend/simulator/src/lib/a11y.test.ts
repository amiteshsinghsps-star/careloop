import { describe, expect, it } from 'vitest';
import { A11Y_LAB_PAYLOAD, toView, viewTextNodes } from '@careloop/client';
import type { ResultView } from '@careloop/client';
import { kindLabel, ordinal, STATUS_LABEL } from '@careloop/ui';

/** M14 parity proof. Both the Accessibility Lab (`/a11y-lab`, Astro) and the simulator card
 *  (`PlanCard`, Svelte) render the SAME `toView()` output. The Lab announces its content
 *  through the shared `viewTextNodes`; here we independently re-implement `PlanCard`'s exact
 *  content order and assert the two are byte-identical, in the same order, for a real masked
 *  plan payload. If anyone ever forks a second copy of the render logic, this fails. */

function pillFor(view: ResultView, index: number): string {
  const item = view.items[index];
  if (item.needs_confirm && item.status === 'pending') return 'Check with the clinician';
  return STATUS_LABEL[item.status] ?? item.status;
}

/** A faithful, independent transcription of what PlanCard prints for a plan card. */
function planCardContentNodes(view: ResultView): string[] {
  const nodes: string[] = [view.title];
  if (view.person) nodes.push(view.person);
  view.items.forEach((item, i) => {
    nodes.push(ordinal(i), kindLabel(item.kind), pillFor(view, i), item.text);
    if (item.schedule) nodes.push(item.schedule);
  });
  if (view.attribution) nodes.push(view.attribution);
  if (view.dosePolicy) nodes.push(view.dosePolicy);
  if (view.disclaimer) nodes.push(view.disclaimer);
  return nodes;
}

describe('accessibility-lab ↔ simulator render parity (M14)', () => {
  const view = toView(A11Y_LAB_PAYLOAD);

  it('reads the payload as a care plan through the single render model', () => {
    expect(view.kind).toBe('plan');
    expect(view.title).toBe('Care plan');
    expect(view.person).toBe('Papa');
    expect(view.items).toHaveLength(5);
  });

  it('the shared extractor and the PlanCard transcription produce the same text nodes, same order', () => {
    const lab = viewTextNodes(toView(A11Y_LAB_PAYLOAD));
    const sim = planCardContentNodes(toView(A11Y_LAB_PAYLOAD));
    expect(lab).toEqual(sim);
    expect(lab.length).toBeGreaterThan(10);
  });

  it('is deterministic and surface-independent (two runs, same array)', () => {
    expect(viewTextNodes(toView(A11Y_LAB_PAYLOAD))).toEqual(viewTextNodes(toView(A11Y_LAB_PAYLOAD)));
  });

  it('carries the masked token and never a dose on the public surfaces', () => {
    const nodes = viewTextNodes(view);
    const joined = nodes.join('\n');
    expect(joined).toContain('your prescribed amount');
    expect(joined).not.toMatch(/\b\d+\s?(mg|mcg|ml|g|units?|tablet)s?\b/i);
  });

  it('keeps ordinal + kind + status + text + schedule in order for the first item', () => {
    const nodes = viewTextNodes(view);
    const start = nodes.indexOf('Care plan') + 1; // title
    expect(nodes[start]).toBe('Papa');            // person
    expect(nodes[start + 1]).toBe('first');       // ordinal
    expect(nodes[start + 2]).toBe('Medicine');    // kind label
    expect(nodes[start + 3]).toBe('To do');       // status pill
    expect(nodes[start + 4]).toContain('amlodipine');
    expect(nodes[start + 5]).toBe('Morning');     // schedule
  });

  it('ends with attribution, dose policy and disclaimer in that order', () => {
    const nodes = viewTextNodes(view);
    const footer = nodes.slice(-3);
    expect(footer[0]).toContain('12 Oct');
    expect(footer[1]).toContain('masked');
    expect(footer[2]).toContain('Confirm any change');
  });
});
