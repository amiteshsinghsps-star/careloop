import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vitest/config';

/** Test-only config: the app's vite.config.mts is left untouched, and these two suites are
 *  pure functions (no DOM, no Svelte), so a plain node environment is enough. */
const r = (p: string) => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig({
  resolve: {
    alias: {
      '@careloop/client': r('../../frontend/client/src/index.ts'),
      '@careloop/ui': r('../../frontend/ui/src/index.ts'),
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
