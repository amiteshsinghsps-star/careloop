import { svelte } from '@sveltejs/vite-plugin-svelte';
import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';

const r = (p: string) => fileURLToPath(new URL(p, import.meta.url));
const API_ROUTES = ['/mcp', '/api', '/oauth', '/.well-known', '/metrics', '/healthz', '/readyz'];

export default defineConfig({
  base: '/simulator/',
  plugins: [svelte()],
  resolve: {
    alias: {
      '@careloop/client': r('../../frontend/client/src/index.ts'),
      '@careloop/ui': r('../../frontend/ui/src/index.ts'),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(API_ROUTES.map((p) => [p, { target: 'http://127.0.0.1:8080', changeOrigin: true }])),
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    target: 'es2022',
    rollupOptions: { input: { main: r('index.html'), judge: r('judge.html') } },
  },
});
