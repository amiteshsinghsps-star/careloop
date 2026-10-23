<script lang="ts">
  /** Which device class we are pretending to be, and how it renders. These are real
   *  constraints (panel width, text size, theme, UI language) rather than a picture of a
   *  device. Only the CHROME (these labels) is translated — health text never comes here. */
  import { LANGUAGES, type Language } from '@careloop/client';
  import {
    session, setDevice, setTheme, setTextSize, setLanguage, tt,
  } from '../lib/session.svelte';
  import type { DeviceClass } from '../lib/session.svelte';

  const classes: DeviceClass[] = ['small', 'large'];
  const deviceKey: Record<DeviceClass, 'deviceSmall' | 'deviceLarge'> = {
    small: 'deviceSmall', large: 'deviceLarge',
  };
  const langKey: Record<Language, 'langEnglish' | 'langHindi' | 'langPunjabi'> = {
    en: 'langEnglish', hi: 'langHindi', pa: 'langPunjabi',
  };
</script>

<div class="u-between controls">
  <div role="group" aria-label={tt('devicePreview')}>
    <span class="cl-label">{tt('devicePreview')}</span>
    <div class="tiny-gap">
      {#each classes as value}
        <button class="chip" type="button" aria-pressed={session.device === value}
          onclick={() => setDevice(value)}>{tt(deviceKey[value])}</button>
      {/each}
    </div>
  </div>

  <div class="tiny-gap">
    <button class="cl-btn sm quiet" type="button" aria-pressed={session.text === 'large'}
      onclick={() => setTextSize(session.text === 'large' ? 'normal' : 'large')}>
      {session.text === 'large' ? tt('standardText') : tt('largeText')}
    </button>
    <button class="cl-btn sm quiet" type="button" aria-pressed={session.theme === 'dark'}
      onclick={() => setTheme(session.theme === 'dark' ? 'light' : 'dark')}>
      {session.theme === 'dark' ? tt('lightTheme') : tt('darkTheme')}
    </button>
  </div>

  <div role="group" aria-label={tt('languageLabel')}>
    <span class="cl-label">{tt('languageLabel')}</span>
    <div class="tiny-gap">
      {#each LANGUAGES as value}
        <button class="chip" type="button" aria-pressed={session.lang === value}
          lang={value} onclick={() => setLanguage(value)}>{tt(langKey[value])}</button>
      {/each}
    </div>
  </div>
</div>

<style>
  .controls { gap: var(--cl-sp-4); flex-wrap: wrap; }
</style>
