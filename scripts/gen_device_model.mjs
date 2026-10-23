/** Regenerate `frontend/simulator/src/lib/alexa/interactionModel.ts` from the Alexa+
 *  interaction model that `scripts/make_addon.py` writes into `alexa/`.
 *
 *  The simulated device must resolve utterances and schedule timers from the SAME artifact the
 *  skill ships with, otherwise the simulator is inventing a model. So this file is generated, and
 *  `npm run model:check` fails when it has drifted. Run `npm run model:gen` after
 *  `python scripts/make_addon.py`.
 *
 *  Usage:  node scripts/gen_device_model.mjs [--check]
 */
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SOURCE = join(ROOT, 'alexa', 'interaction-model.json');
const TARGET = join(ROOT, 'frontend', 'simulator', 'src', 'lib', 'alexa', 'interactionModel.ts');

/** Only what the device runtime reads. Everything else in the model is console plumbing. */
const KEYS = [
  'schemaVersion', 'languageModel', 'dialog', 'proactive', 'presentation', 'voiceProfile',
  'promptCatalog', 'answerGuidance', 'guardrails', 'welcomePrompt', 'helpPrompt', 'fallbackPrompt',
  'intentsNotOnThisSurface',
];

const digest = createHash('sha256').update(readFileSync(SOURCE)).digest('hex');
const raw = JSON.parse(readFileSync(SOURCE, 'utf8'));
const picked = {};
for (const key of KEYS) {
  if (!(key in raw)) throw new Error(`interaction model is missing "${key}" — the generator and the model have drifted`);
  picked[key] = raw[key];
}

const intentCount = picked.languageModel.intents.length;
const slotTypeCount = picked.languageModel.types.length;

// `proactive.note`, `presentation.note` and `voiceProfile.note` explain those blocks to a human
// reading the artifact; the device runtime reads only `proactive.reminder`,
// `presentation.delivery` and `voiceProfile.assertion`. Every other key above follows the same rule
// — copy what is used — so the prose is not shipped to a browser to be re-parsed on every turn. It
// stays in `alexa/`.
picked.proactive = { reminder: picked.proactive.reminder };
picked.presentation = { delivery: picked.presentation.delivery };
picked.voiceProfile = { assertion: picked.voiceProfile.assertion };

const body = `/* GENERATED — do not edit. Source: alexa/interaction-model.json (sha256 ${digest})
 * Regenerate with \`npm run model:gen\`; drift is checked by \`npm run model:check\`.
 * ${intentCount} intents, ${slotTypeCount} slot types. The simulated device resolves utterances,
 * sets its silence/reprompt timers and arms its one device-initiated reminder from this artifact, so
 * what the demo does is what the skill ships.
 *
 * Health text never originates here: every string in \`promptCatalog\` is a closed-vocabulary
 * constant authored in \`scripts/make_addon.py\`, and a care answer always comes from the server.
 */
export const INTERACTION_MODEL = ${JSON.stringify(picked, null, 2)} as const;

export type AskIntentName = (typeof INTERACTION_MODEL.languageModel.intents)[number]['name'];
export type AskSlotTypeName = (typeof INTERACTION_MODEL.languageModel.types)[number]['name'];
export type AskPromptId = (typeof INTERACTION_MODEL.promptCatalog.prompts)[number]['id'];

/** The model's own voice budget, in one place, so the runtime cannot quietly loosen it. */
export const VOICE_BUDGET = {
  maxSpokenWords: INTERACTION_MODEL.answerGuidance.maxSpokenWords,
  maxSpokenSeconds: INTERACTION_MODEL.answerGuidance.maxSpokenSeconds,
  silenceTimeoutSeconds: INTERACTION_MODEL.answerGuidance.silenceTimeoutSeconds,
  repromptAfterSeconds: INTERACTION_MODEL.answerGuidance.repromptAfterSeconds,
} as const;

/** The dialog rules, keyed by intent, from \`dialog.*\` in the model. */
export const DIALOG = INTERACTION_MODEL.dialog as {
  confirmationRequired: Record<string, { defaultValue: string; prompt: string; type: string }>;
  confirmationResponses: Record<string, { accept: { renderPrompt: string; toolConfirm: boolean }; reject: { renderPrompt: string; toolConfirm: boolean } }>;
  delegation: { strategy: { type: string } };
  eloquenceStyle: string;
};

/** The device-initiated reminder, as the artifact declares it: which answer arms the connection,
 *  what the resumed session opens with and how long the device waits before it speaks. Nothing in
 *  the runtime may hard-code any of these. */
export const PROACTIVE = INTERACTION_MODEL.proactive.reminder as {
  causeType: string;
  connectionUri: string;
  directiveType: string;
  intent: AskIntentName;
  onCompletion: string;
  prompt: AskPromptId;
  requestType: string;
  resumeAfterSeconds: number;
  writesAnything: boolean;
};

/** Adaptive spoken delivery, as the artifact declares it. A style is rate, volume and a word
 *  ceiling; the sentence itself is always the one the server returned. The intent that collects a
 *  style routes to no tool, so nothing here can change a plan. */
export const DELIVERY = INTERACTION_MODEL.presentation.delivery as {
  appliesToHeldAnswer: boolean;
  defaultStyle: string;
  intent: AskIntentName;
  slot: string;
  styles: Record<string, { maxSpokenWords: number; prompt: AskPromptId; rate: string; volume: string | null }>;
  writesAnything: false;
};

/** The simulated voice profile: the two MCP \`_meta\` keys the device sets and the value that
 *  labels the profile as simulated. The device never decides what a member may hear — the server
 *  does, from the boundary the household stored. */
export const VOICE_PROFILE = INTERACTION_MODEL.voiceProfile.assertion as {
  deviceDecidesScope: boolean;
  intent: AskIntentName;
  profileMetaKey: string;
  profileValue: string;
  prompt: AskPromptId;
  sessionScopedOnly: boolean;
  simulated: boolean;
  slot: string;
  speakerMetaKey: string;
  writesAnything: false;
};

export const PROMPTS: Record<AskPromptId, string> = Object.fromEntries(
  INTERACTION_MODEL.promptCatalog.prompts.map((p) => [p.id, p.body[0]]),
) as Record<AskPromptId, string>;

export const MODEL_SHA256 = '${digest}';
`;

if (process.argv.includes('--check')) {
  if (!existsSync(TARGET)) {
    console.error(`model:check FAILED - ${TARGET.replace(ROOT, '.')} does not exist. Run \`npm run model:gen\`.`);
    process.exit(1);
  }
  if (readFileSync(TARGET, 'utf8') !== body) {
    console.error('model:check FAILED - the generated device model does not match alexa/interaction-model.json.');
    console.error('Run `npm run model:gen` (and `python scripts/make_addon.py` first if the model itself changed).');
    process.exit(1);
  }
  console.log(`model:check OK - ${intentCount} intents, ${slotTypeCount} slot types, sha256 ${digest.slice(0, 16)}...`);
} else {
  mkdirSync(dirname(TARGET), { recursive: true });
  writeFileSync(TARGET, body, 'utf8');
  console.log(`model:gen wrote ${TARGET.replace(ROOT, '.')} (${intentCount} intents, ${slotTypeCount} slot types)`);
}
