/** Shared display vocabulary. Mirrors the closed enums in careloop/config.py — the
 *  renderer never invents text, it maps server enum values onto these constants. */

export const KINDS = ['medication', 'appointment', 'avoid', 'task', 'monitor', 'diet'] as const;
export type Kind = (typeof KINDS)[number];

export const KIND_LABEL: Record<Kind, string> = {
  medication: 'Medicine',
  appointment: 'Come back',
  avoid: 'Avoid',
  task: 'Do this',
  monitor: 'Keep track',
  diet: 'Food',
};

export const STATUS_LABEL: Record<string, string> = {
  pending: 'To do',
  done: 'Done',
  superseded: 'Replaced',
  deleted: 'Removed',
};

export const DAY_PARTS = [
  'MORNING', 'AFTER_FOOD', 'BEFORE_FOOD', 'MIDDAY', 'AFTERNOON',
  'EVENING', 'BEDTIME', 'NIGHTWAKING', 'DAILY', 'WEEKLY', 'MONTHLY',
  'ONCE', 'ASNEEDED', 'UNKNOWN',
] as const;

export const DAYPART_LABEL: Record<string, string> = {
  MORNING: 'Morning',
  AFTER_FOOD: 'After food',
  BEFORE_FOOD: 'Before food',
  MIDDAY: 'Midday',
  AFTERNOON: 'Afternoon',
  EVENING: 'Evening',
  BEDTIME: 'Bedtime',
  NIGHTWAKING: 'During the night',
  DAILY: 'Every day',
  WEEKLY: 'Every week',
  MONTHLY: 'Every month',
  ONCE: 'One time',
  ASNEEDED: 'When needed',
  UNKNOWN: 'Any time',
};

export const ORDINALS = ['first', 'second', 'third', 'fourth', 'fifth'];

export const ROLE_LABEL: Record<string, string> = {
  caregiver: 'Caregiver',
  elder: 'Care recipient',
  viewer: 'Can view',
};

export function kindLabel(kind?: string | null): string {
  return KIND_LABEL[kind as Kind] ?? 'Care item';
}

export function dayPartLabel(part?: string | null): string {
  return DAYPART_LABEL[part ?? ''] ?? 'Any time';
}

export function ordinal(index: number): string {
  return ORDINALS[index] ?? `item ${index + 1}`;
}
