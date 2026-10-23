import type { MaskedItem } from '@careloop/client';

/** Buttons a host may offer on a card. Each is optional: PlanCard only draws the ones the
 *  caller supplied, so the read-only judge preview cannot accidentally mutate a plan. */
export interface CardActions {
  done?: (ref: string) => void;
  explain?: (ref: string) => void;
  /** “Yes, delete” on a confirm-delete card. */
  confirmDelete?: (ref: string) => void;
  cancel?: () => void;
  /** Ask-back chips on a clarify card. */
  pickPerson?: (name: string) => void;
  /** Speak one of the example phrases (about-card chips). */
  say?: (text: string) => void;
}

/** One row of an agenda/plan list, keeping the flat index so the spoken ordinal matches. */
export type ItemRow = { item: MaskedItem; index: number };
