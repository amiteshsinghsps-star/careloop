/** Copy the three built frontends into careloop/web/ so the Python server can serve
 *  everything from one origin (no CDN, works offline, `make demo` stays one command). */
import { cpSync, existsSync, mkdirSync, rmSync, writeFileSync, readFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const WEB = join(ROOT, 'careloop', 'web');

const jobs = [
  { from: 'frontend/simulator/dist', to: 'careloop/web/simulator', required: true,
    files: ['index.html', 'judge.html', 'assets'] },
  { from: 'frontend/companion/dist/companion/browser', to: 'careloop/web/companion', required: true,
    files: ['index.html', 'main.js', 'chunk-', 'styles'] },
  { from: 'frontend/site/dist', to: 'careloop/web/site', required: false, files: ['index.html', '_astro'] },
];

function copyDir(src, dest) {
  mkdirSync(dest, { recursive: true });
  cpSync(src, dest, { recursive: true, force: true });
}

let failures = 0;
for (const job of jobs) {
  const src = join(ROOT, job.from);
  const dest = join(ROOT, job.to);
  if (!existsSync(src)) {
    console.warn(`${job.required ? 'MISSING  ' : 'skipped  '} ${job.from}  (run the build first)`);
    if (job.required) failures++;
    continue;
  }
  rmSync(dest, { recursive: true, force: true });
  copyDir(src, dest);
  console.log(`copied     ${job.from}  ->  ${job.to}`);
}

/* The MCP App card must be ONE self-contained file: the server returns it verbatim from
   resources/read, so it is also copied to careloop/web/card.html. It is deliberately NOT
   copied again into careloop/web/simulator — the app links the /card route, and a second
   identical 74 kB file inside the simulator bundle was only ever double-counted. */
const cardSrc = ['card.html', 'index.html']
  .map((n) => join(ROOT, 'frontend/simulator/dist-card', n))
  .find((p) => existsSync(p));
if (cardSrc) {
  const html = readFileSync(cardSrc, 'utf8');
  writeFileSync(join(WEB, 'card.html'), html);
  rmSync(join(WEB, 'simulator', 'card.html'), { force: true });
  console.log(`copied     ${relative(ROOT, cardSrc)}  ->  careloop/web/card.html `
              + `(${(html.length / 1024).toFixed(1)} kB inline)`);
} else {
  console.warn('MISSING   frontend/simulator/dist-card/card.html  (vite build --config vite.card.config.mts)');
  failures++;
}

if (failures) {
  console.error(`\n${failures} required bundle(s) missing — run: npm run build`);
  process.exit(1);
}
console.log('\nFrontends are in place. Start the server with: make serve');
