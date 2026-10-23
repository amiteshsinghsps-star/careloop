import { defineConfig } from 'astro/config';

/** Set CARELOOP_PUBLIC_URL to the deployed origin before publishing; a placeholder domain
 *  would ship into the canonical link and the store listing, so the default is the local one. */
const site = process.env.CARELOOP_PUBLIC_URL || 'http://localhost:8080';

export default defineConfig({
  output: 'static',
  site,
  build: { inlineStylesheets: 'auto' },
  devToolbar: { enabled: false },
});
