/** Thin wrappers around the two browser APIs the simulated device needs: Web Speech
 *  recognition (mic) and speechSynthesis (TTS). Both are optional: if the browser has no
 *  `webkitSpeechRecognition` (Firefox, Safari desktop) or no `speechSynthesis` (some
 *  Linux builds) we report the reason and the UI hides the control with a `.cl-note`,
 *  rather than pretending the feature works. */

interface RecognitionAlternative { transcript: string; confidence: number }
interface RecognitionResult { readonly length: number; isFinal: boolean; [index: number]: RecognitionAlternative }
interface RecognitionResultList { readonly length: number; [index: number]: RecognitionResult }
interface RecognitionResultEvent extends Event { resultIndex: number; results: RecognitionResultList }
interface RecognitionErrorEvent extends Event { error: string; message?: string }
interface Recognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  abort(): void;
  onstart: (() => void) | null;
  onresult: ((event: RecognitionResultEvent) => void) | null;
  onerror: ((event: RecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
}
type RecognitionCtor = new () => Recognition;

function recognitionCtor(): RecognitionCtor | null {
  if (typeof window === 'undefined') return null;
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

const CTOR = recognitionCtor();
const HAS_TTS = typeof window !== 'undefined' && 'speechSynthesis' in window && 'SpeechSynthesisUtterance' in window;

export const speech = $state({
  listening: false,
  speaking: false,
  /** Live partial transcript shown under the mic button. */
  heard: '',
  micSupported: CTOR !== null,
  micReason: CTOR !== null
    ? ''
    : 'This browser has no SpeechRecognition API, so the microphone button is hidden. Type the phrase instead — the demo works the same.',
  ttsSupported: HAS_TTS,
  ttsReason: HAS_TTS
    ? ''
    : 'This browser has no speechSynthesis API, so spoken playback is unavailable. The exact same text is shown on screen.',
  lastError: '',
});

let recognition: Recognition | null = null;

export function startListening(onFinal: (text: string) => void): void {
  if (!CTOR || speech.listening) return;
  speech.lastError = '';
  speech.heard = '';
  recognition = new CTOR();
  recognition.lang = 'en-IN';
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;

  recognition.onstart = () => { speech.listening = true; };
  recognition.onresult = (event) => {
    let finalText = '';
    let partial = '';
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      const text = result[0]?.transcript ?? '';
      if (result.isFinal) finalText += text;
      else partial += text;
    }
    speech.heard = (finalText || partial).trim();
    if (finalText.trim()) {
      const said = finalText.trim();
      speech.heard = said;
      recognition?.stop();
      onFinal(said);
    }
  };
  recognition.onerror = (event) => {
    speech.lastError = describeRecognitionError(event.error);
    speech.listening = false;
  };
  recognition.onend = () => { speech.listening = false; };

  try {
    recognition.start();
  } catch {
    speech.listening = false;
    speech.lastError = 'The microphone could not be started. Check that this page has microphone permission.';
  }
}

export function stopListening(): void {
  recognition?.stop();
  recognition = null;
  speech.listening = false;
}

function describeRecognitionError(error: string): string {
  switch (error) {
    case 'not-allowed':
    case 'service-not-allowed':
      return 'Microphone access was blocked. Allow it for this page, or type the phrase.';
    case 'no-speech':
      return 'No speech was detected. Try again a little closer to the device.';
    case 'audio-capture':
      return 'No microphone was found on this device.';
    case 'network':
      return 'Browser speech recognition needs a network connection it could not reach.';
    default:
      return `Speech recognition stopped: ${error}`;
  }
}

let current: SpeechSynthesisUtterance | null = null;

/** Read back the tool's own `content[0].text`. We do not paraphrase: the voice and the
 *  screen must be the same sentence. */
export function speak(text: string): void {
  if (!HAS_TTS || !text.trim() || !sessionTtsEnabled()) return;
  cancelSpeech();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = 'en-IN';
  utterance.rate = 0.98;
  utterance.pitch = 1;
  utterance.onstart = () => { speech.speaking = true; };
  utterance.onend = () => { speech.speaking = false; current = null; };
  utterance.onerror = () => { speech.speaking = false; current = null; };
  current = utterance;
  try {
    window.speechSynthesis.speak(utterance);
  } catch {
    speech.speaking = false;
  }
}

export function cancelSpeech(): void {
  if (!HAS_TTS) return;
  try { window.speechSynthesis.cancel(); } catch { /* nothing queued */ }
  speech.speaking = false;
  current = null;
}

export function isSpeaking(): boolean {
  return speech.speaking;
}

/* The TTS switch lives in the session store; read it lazily so this module does not
   depend on store initialisation order. */
let ttsGate: () => boolean = () => true;
export function setTtsGate(fn: () => boolean): void { ttsGate = fn; }
function sessionTtsEnabled(): boolean { return ttsGate(); }

export function disposeSpeech(): void {
  stopListening();
  cancelSpeech();
  current = null;
}
