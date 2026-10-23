/** Fuzzy matching + keyboard navigation for the Ctrl/Cmd+K command palette.
 *  Pure and framework-free so the palette's behaviour is testable without a DOM: the
 *  Svelte component owns focus/ARIA, this module owns "which commands match" and
 *  "what an arrow/enter/escape does to the active row". */

export interface PaletteCommand {
  id: string;
  /** Human label of the action the user can already do by voice or tap. */
  label: string;
  /** The equivalent utterance (from frontend/client/src/utterance.ts phrasing). */
  utterance: string;
  /** Extra search terms (tool name, person, etc.). */
  keywords?: string;
}

/** Subsequence score: how well `query`'s characters appear, in order, inside `target`.
 *  Returns -1 when they do not all appear. Consecutive matches and word-start matches are
 *  rewarded so "show plan" ranks "show the plan" above unrelated rows. */
export function fuzzyScore(query: string, target: string): number {
  const q = query.toLowerCase().trim();
  const t = target.toLowerCase();
  if (!q) return 0; // empty query → keep original order
  let score = 0;
  let ti = 0;
  let streak = 0;
  for (let qi = 0; qi < q.length; qi++) {
    const ch = q[qi];
    if (ch === ' ') {
      streak = 0;
      continue;
    }
    const found = t.indexOf(ch, ti);
    if (found < 0) return -1;
    score += 1;
    if (found === ti) {
      streak += 1;
      score += streak; // bonus for contiguous runs
    } else {
      streak = 0;
    }
    if (found === 0 || /[\s\W_]/.test(t[found - 1])) score += 3; // word-start bonus
    ti = found + 1;
  }
  return score;
}

/** Filter + rank the command list against a query. Empty query returns the list as given.
 *  Generic over the command shape so callers keep their own extra fields (e.g. `run`). */
export function filterPalette<T extends PaletteCommand>(commands: T[], query: string): T[] {
  if (!query.trim()) return [...commands];
  const scored = commands
    .map((cmd) => {
      const inLabel = fuzzyScore(query, cmd.label);
      const inUtterance = fuzzyScore(query, cmd.utterance);
      const inKeywords = cmd.keywords ? fuzzyScore(query, cmd.keywords) : -1;
      return { cmd, score: Math.max(inLabel, inUtterance, inKeywords) };
    })
    .filter((row) => row.score >= 0)
    .sort((a, b) => b.score - a.score);
  return scored.map((row) => row.cmd);
}

export type PaletteKeyAction = 'up' | 'down' | 'close' | 'select' | 'ignore';

/** Map a keydown to the palette's keyboard contract. Everything else is 'ignore' so the
 *  underlying page keeps its own keys (Escape always closes, Tab is deliberately ignored —
 *  the component traps focus itself). */
export function paletteKeyAction(key: string): PaletteKeyAction {
  switch (key) {
    case 'ArrowUp':
      return 'up';
    case 'ArrowDown':
      return 'down';
    case 'Escape':
      return 'close';
    case 'Enter':
      return 'select';
    default:
      return 'ignore';
  }
}

/** Move the active option, wrapping within `count` rows. With no rows the active index is -1. */
export function moveActive(active: number, delta: number, count: number): number {
  if (count <= 0) return -1;
  const base = active < 0 ? (delta > 0 ? -1 : 0) : active;
  let next = base + delta;
  if (next < 0) next = count - 1;
  else if (next >= count) next = 0;
  return next;
}

/** Clamp an arbitrary active index into range as the filtered list shrinks/grows. */
export function clampActive(active: number, count: number): number {
  if (count <= 0) return -1;
  if (active < 0) return 0;
  return active >= count ? count - 1 : active;
}
