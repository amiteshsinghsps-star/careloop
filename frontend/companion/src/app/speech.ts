import { Injectable, NgZone, inject } from '@angular/core';

/** Minimal shape of the vendor-prefixed Web Speech recognition objects. Not in the DOM lib. */
interface RecognitionAlt { readonly transcript: string; }
interface RecognitionResult { readonly 0: RecognitionAlt; readonly isFinal: boolean; length: number; }
interface RecognitionEvent { readonly resultIndex: number; readonly results: ArrayLike<RecognitionResult>; }
interface RecognitionErrorEvent { readonly error: string; }
interface RecognitionInstance {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((e: RecognitionEvent) => void) | null;
  onerror: ((e: RecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
}
type RecognitionCtor = new () => RecognitionInstance;

function ctor(): RecognitionCtor | null {
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export interface SpeechHandlers {
  onPartial: (text: string) => void;
  onFinal: (text: string) => void;
  onError: (message: string) => void;
  onEnd: () => void;
}

@Injectable({ providedIn: 'root' })
/** Thin, disposable wrapper over SpeechRecognition + speechSynthesis with capability detection.
 *  Mic callbacks are registered outside the Angular zone; every signal write re-enters via run(). */
export class SpeechService {
  private readonly zone = inject(NgZone);
  private rec: RecognitionInstance | null = null;

  get recogniserSupported(): boolean {
    return ctor() !== null;
  }

  get synthesisSupported(): boolean {
    return typeof window !== 'undefined' && 'speechSynthesis' in window;
  }

  start(h: SpeechHandlers): boolean {
    const C = ctor();
    if (!C) {
      h.onError('Recording is not supported in this browser. Paste the visit text instead.');
      return false;
    }
    this.stop();
    const rec = new C();
    rec.lang = 'en-US';
    rec.continuous = true;
    rec.interimResults = true;
    this.rec = rec;

    // Register listeners outside the zone so a steady stream of interim results does not
    // thrash change detection; we deliberately re-enter the zone only on writes.
    this.zone.runOutsideAngular(() => {
      rec.onresult = (e: RecognitionEvent) => {
        let interim = '';
        let final = '';
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const r = e.results[i];
          const text = r[0]?.transcript ?? '';
          if (r.isFinal) final += text;
          else interim += text;
        }
        this.zone.run(() => {
          if (final) h.onFinal(final.trim());
          if (interim) h.onPartial(interim.trim());
        });
      };
      rec.onerror = (e: RecognitionErrorEvent) => {
        this.zone.run(() => h.onError(this.explain(e.error)));
      };
      rec.onend = () => {
        this.zone.run(() => h.onEnd());
      };
    });

    try {
      rec.start();
      return true;
    } catch {
      this.rec = null;
      h.onError('The microphone could not be started. Check browser permissions and try again.');
      return false;
    }
  }

  stop(): void {
    const rec = this.rec;
    this.rec = null;
    if (!rec) return;
    rec.onresult = null;
    rec.onerror = null;
    rec.onend = null;
    try {
      rec.stop();
    } catch {
      try { rec.abort(); } catch { /* already closed */ }
    }
  }

  speak(text: string): void {
    if (!this.synthesisSupported || !text) return;
    this.zone.runOutsideAngular(() => {
      try {
        window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
      } catch { /* not critical */ }
    });
  }

  cancelSpeak(): void {
    if (this.synthesisSupported) {
      try { window.speechSynthesis.cancel(); } catch { /* noop */ }
    }
  }

  dispose(): void {
    this.stop();
    this.cancelSpeak();
  }

  private explain(code: string): string {
    switch (code) {
      case 'not-allowed':
      case 'service-not-allowed':
        return 'Microphone access was blocked. Allow it in your browser, or paste the visit text.';
      case 'no-speech':
        return 'No speech was heard. Try speaking closer to the microphone.';
      case 'audio-capture':
        return 'No microphone was found. Connect one, or paste the visit text.';
      case 'network':
        return 'The speech service could not be reached. Check your connection, or paste the visit text.';
      default:
        return 'Recording stopped unexpectedly. You can paste the visit text instead.';
    }
  }
}
