import { svelte } from '@sveltejs/vite-plugin-svelte';
import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';
import { viteSingleFile } from 'vite-plugin-singlefile';

const r = (p: string) => fileURLToPath(new URL(p, import.meta.url));

/** The MCP App card is returned verbatim by `resources/read`, so it must be ONE
 *  self-contained HTML file with no external requests (offline-safe on any screen). */
export default defineConfig({
  base: './',
  plugins: [svelte(), viteSingleFile({ removeViteModuleLoader: true })],
  resolve: {
    alias: {
      '@careloop/client': r('../../frontend/client/src/index.ts'),
      '@careloop/ui': r('../../frontend/ui/src/index.ts'),
    },
  },
  build: {
    outDir: 'dist-card',
    emptyOutDir: true,
    assetsInlineLimit: 100_000_000,
    cssCodeSplit: false,
    target: 'es2022',
    rollupOptions: { input: { card: r('card.html') } },
  },
});
