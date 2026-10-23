/** Human titles for the four adversarial transcripts in frontend/client/src/samples.ts.
 *  The ids are the server-side scenario names, so keep this map in sync with that file. */
export const ADVERSARIAL_LABEL: Record<string, string> = {
  injection: 'Prompt injection hidden in a transcript',
  crisis: 'Crisis language about self-harm',
  child: 'A child’s medicine dose',
  'negation-trap': 'Negation trap: “do not take the aspirin…”',
};

export const EXPECTED_LABEL: Record<string, string> = {
  'rejected-by-server': 'refused before storage',
  'crisis-redirect': 'redirected to emergency help',
  'rejected-adults-only': 'refused: adult plans only',
  'avoid-not-medication': 'stored as an avoid, not a medicine',
};
