import { describe, expect, it } from 'vitest';
import { initialPrivacyState, privacyReducer, privacySummary, textBytes } from '@careloop/client';
import type { PrivacyState } from '@careloop/client';

/** M2: the privacy meter's numbers come from these transitions, not from a decorative label. */

describe('privacyReducer (M2)', () => {
  it('starts clean: nothing has left the device', () => {
    const s = initialPrivacyState();
    expect(s).toMatchObject({
      stage: 'idle', audioBytesSent: 0, transcriptBytesLocal: 0, transcriptBytesSent: 0, sentForAnalysis: false,
    });
  });

  it('records locally — audio bytes sent stay 0 through the whole flow', () => {
    let s: PrivacyState = initialPrivacyState();
    s = privacyReducer(s, { type: 'start_record' });
    expect(s.stage).toBe('recording');
    expect(s.audioBytesSent).toBe(0);
    s = privacyReducer(s, { type: 'stop_record' });
    expect(s.stage).toBe('idle'); // no text yet
  });

  it('keeps text on-device until "send for analysis" is pressed', () => {
    let s = privacyReducer(initialPrivacyState(), { type: 'set_transcript', bytes: 128 });
    expect(s.stage).toBe('review');
    expect(s.transcriptBytesLocal).toBe(128);
    expect(s.transcriptBytesSent).toBe(0);
    expect(s.sentForAnalysis).toBe(false);
    expect(s.audioBytesSent).toBe(0);

    s = privacyReducer(s, { type: 'send_for_analysis' });
    expect(s.sentForAnalysis).toBe(true);
    expect(s.transcriptBytesSent).toBe(128); // exactly what was local
    expect(s.audioBytesSent).toBe(0);        // audio still never sent
    expect(s.stage).toBe('sent');
  });

  it('reset returns to a clean meter but remembers the on-device preference', () => {
    let s = privacyReducer(initialPrivacyState(), { type: 'enable_on_device' });
    s = privacyReducer(s, { type: 'set_transcript', bytes: 40 });
    s = privacyReducer(s, { type: 'send_for_analysis' });
    s = privacyReducer(s, { type: 'reset' });
    expect(s.transcriptBytesSent).toBe(0);
    expect(s.stage).toBe('idle');
    expect(s.onDeviceRecognition).toBe(true);
  });

  it('summarises honestly, and textBytes measures UTF-8 length', () => {
    let s = privacyReducer(initialPrivacyState(), { type: 'set_transcript', bytes: textBytes('héllo') });
    expect(s.transcriptBytesLocal).toBe(6); // é is 2 UTF-8 bytes
    expect(privacySummary(s)).toContain('still only on this device');
    s = privacyReducer(s, { type: 'send_for_analysis' });
    expect(privacySummary(s)).toContain('bytes of text sent for analysis');
    expect(privacySummary(s)).toContain('0 bytes of audio sent');
  });
});
