/** Privacy meter state machine for the capture screen (M2). The numbers shown ("0 bytes of
 *  audio left the device", "text still on this device") come from these real transitions,
 *  never from a label that could lie. Pure + framework-free so the sequence is unit-tested.
 *
 *  Contract the UI relies on:
 *   - audio bytes uploaded are ALWAYS 0 until a network call is made (we only ever send text,
 *     never the microphone audio), so `audioBytesSent` stays 0 through recording and review;
 *   - the transcript stays local until the explicit "Send for analysis" transition;
 *   - `bytesSent` after send equals exactly the transcript bytes that were local. */

export type CaptureStage = 'idle' | 'recording' | 'review' | 'sent';

export interface PrivacyState {
  stage: CaptureStage;
  /** Bytes of audio ever transmitted off-device. Stays 0 unless a network call sends audio (none do). */
  audioBytesSent: number;
  /** Bytes of transcript currently held only on this device. */
  transcriptBytesLocal: number;
  /** Bytes of transcript that have left the device via an analysis call. */
  transcriptBytesSent: number;
  /** True once "Send for analysis" has actually fired a request. */
  sentForAnalysis: boolean;
  /** Whether the browser's own on-device recogniser (processLocally) is preferred. */
  onDeviceRecognition: boolean;
}

export type CaptureAction =
  | { type: 'enable_on_device' }
  | { type: 'start_record' }
  | { type: 'stop_record' }
  | { type: 'set_transcript'; bytes: number }
  | { type: 'send_for_analysis' }
  | { type: 'reset' };

export function initialPrivacyState(onDevice = false): PrivacyState {
  return {
    stage: 'idle',
    audioBytesSent: 0,
    transcriptBytesLocal: 0,
    transcriptBytesSent: 0,
    sentForAnalysis: false,
    onDeviceRecognition: onDevice,
  };
}

/** UTF-8 byte length of a transcript — the honest measure of "what would leave the device". */
export function textBytes(text: string): number {
  if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(text).length;
  // Node ≥ 18 always has TextEncoder; this fallback keeps the function total for odd runtimes.
  return Array.from(unescape(encodeURIComponent(text))).length;
}

export function privacyReducer(state: PrivacyState, action: CaptureAction): PrivacyState {
  switch (action.type) {
    case 'enable_on_device':
      return { ...state, onDeviceRecognition: true };
    case 'start_record':
      // Recording is local: audio is never uploaded by this app.
      return { ...state, stage: 'recording', audioBytesSent: 0 };
    case 'stop_record':
      return { ...state, stage: state.transcriptBytesLocal > 0 ? 'review' : 'idle' };
    case 'set_transcript': {
      const bytes = Math.max(0, Math.floor(action.bytes));
      const stage: CaptureStage = bytes > 0 ? 'review' : 'idle';
      // Editing after a send keeps the previously-sent figure; the local copy reflects the edit.
      return { ...state, stage: state.sentForAnalysis ? state.stage : stage, transcriptBytesLocal: bytes };
    }
    case 'send_for_analysis': {
      // Only the transcript text leaves; audio count is untouched (still 0).
      return {
        ...state,
        stage: 'sent',
        sentForAnalysis: true,
        transcriptBytesSent: state.transcriptBytesLocal,
        audioBytesSent: 0,
      };
    }
    case 'reset':
      return initialPrivacyState(state.onDeviceRecognition);
    default:
      return state;
  }
}

/** One-line, non-lying summary of what has left the device, for the meter UI. */
export function privacySummary(state: PrivacyState): string {
  const bits: string[] = [];
  bits.push(`${state.audioBytesSent} bytes of audio sent`);
  bits.push(
    state.sentForAnalysis
      ? `${state.transcriptBytesSent} bytes of text sent for analysis`
      : state.transcriptBytesLocal > 0
        ? `text (${state.transcriptBytesLocal} bytes) still only on this device`
        : 'no text captured yet',
  );
  return bits.join(' · ');
}
