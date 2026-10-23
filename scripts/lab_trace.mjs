/** Capture the Device Lab's own trace out of the built page in a real browser, so `--compare` has a
 *  second device to hold against rather than a hand-written one.
 *
 *  This is DEV tooling, not a shipped path and not a CI gate: it drives the same buttons a person
 *  drives (scenario, then "Export trace JSON") in headless Chrome over the Chrome DevTools Protocol,
 *  and takes the file Chrome saves from the page's own blob download. Nothing about the app is
 *  instrumented for it — no hook, no query parameter, no automation-only code in the bundle.
 *
 *  The scenario labels are read out of `deviceRuntime.svelte.ts`, the file the page renders from, so
 *  this script cannot click a stale button and silently compare the wrong conversation: a label that
 *  is not in the page is a hard failure.
 *
 *  Usage (server must already be running on the URL):
 *      node scripts/lab_trace.mjs --scenario chain --scenario reminder
 *      node scripts/lab_trace.mjs --url http://127.0.0.1:8123/simulator/ --out data/sim
 *  Then:
 *      python -m scripts.alexa_device --url http://127.0.0.1:8123/mcp --compare data/sim/browser-chain.json
 */
import { mkdtempSync, readdirSync, readFileSync, existsSync, renameSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const LAB_SOURCE = join(ROOT, 'frontend', 'simulator', 'src', 'lib', 'alexa', 'deviceRuntime.svelte.ts');

const args = process.argv.slice(2);
function flag(name, fallback) {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : fallback;
}
function every(name) {
  const out = [];
  args.forEach((a, i) => { if (a === name) out.push(args[i + 1]); });
  return out;
}

const URL_PAGE = flag('--url', 'http://127.0.0.1:8123/simulator/');
const OUT_DIR = resolve(ROOT, flag('--out', 'data/sim'));
const CHROME = flag('--chrome', process.env.CHROME_PATH
  ?? (existsSync('C:/Program Files/Google/Chrome/Application/chrome.exe')
    ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : 'chrome'));
const SCENARIOS = every('--scenario').length ? every('--scenario') : ['chain'];
/** Milliseconds a scripted conversation is allowed to take before this script calls it hung. */
const TIMEOUT = Number(flag('--timeout', '60000'));

/** The `{ id, label }` the page renders its buttons from — the source is the only place to read it. */
function scenarioLabels() {
  const src = readFileSync(LAB_SOURCE, 'utf8');
  const table = src.slice(src.indexOf('export const SCENARIOS'), src.indexOf('interface LabState'));
  const found = {};
  for (const m of table.matchAll(/id:\s*'([^']+)'(?:[^}]*?)label:\s*'([^']+)'/gs)) found[m[1]] = m[2];
  return found;
}

function freePort() {
  return new Promise((done, fail) => {
    const s = createServer();
    s.on('error', fail);
    s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => done(p)); });
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const labels = scenarioLabels();
  for (const id of SCENARIOS) {
    if (!labels[id]) {
      console.error(`no scenario id "${id}" in ${LAB_SOURCE.replace(ROOT, '.')} (known: ${Object.keys(labels).join(', ')})`);
      return 2;
    }
  }

  const port = await freePort();
  const profile = mkdtempSync(join(tmpdir(), 'careloop-lab-profile-'));
  const downloads = mkdtempSync(join(tmpdir(), 'careloop-lab-downloads-'));
  const chrome = (await import('node:child_process')).spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--mute-audio', '--no-first-run', '--no-default-browser-check',
    `--user-data-dir=${profile}`, `--remote-debugging-port=${port}`,
    `--remote-allow-origins=*`, 'about:blank',
  ], { stdio: ['ignore', 'ignore', 'pipe'] });
  let stderr = '';
  chrome.stderr.on('data', (b) => { stderr += b.toString(); });
  chrome.on('exit', (code) => { if (code) console.error(`chrome exited ${code}\n${stderr.slice(-400)}`); });

  // The DevTools endpoint appears a moment after the process does.
  let targets = null;
  for (let i = 0; i < 60 && !targets; i += 1) {
    await sleep(250);
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/list`);
      const list = await r.json();
      targets = list.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
    } catch { /* not up yet */ }
  }
  if (!targets) { console.error('no CDP page target'); chrome.kill(); return 1; }

  const ws = new WebSocket(targets.webSocketDebuggerUrl);
  await new Promise((done, fail) => { ws.onopen = done; ws.onerror = fail; });
  let nextId = 0;
  const waiting = new Map();
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.id && waiting.has(msg.id)) { waiting.get(msg.id)(msg); waiting.delete(msg.id); }
  };
  function send(method, params = {}) {
    const id = ++nextId;
    ws.send(JSON.stringify({ id, method, params }));
    return new Promise((done) => waiting.set(id, done));
  }
  async function command(method, params) {
    const got = await send(method, params);
    if (got.error) throw new Error(`${method}: ${JSON.stringify(got.error)}`);
    return got.result;
  }
  async function evaluate(expression, awaitPromise = false) {
    const got = await command('Runtime.evaluate', {
      expression, returnByValue: true, awaitPromise,
    });
    if (got.exceptionDetails) throw new Error(`page threw: ${got.exceptionDetails.text}`);
    return got.result.value;
  }

  await command('Page.enable');
  await command('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: downloads });
  await command('Runtime.enable');
  await command('Page.navigate', { url: URL_PAGE });

  // Wait for the app to boot and for the Device Lab to render its scripted-conversation buttons.
  for (let i = 0; i < TIMEOUT / 250; i += 1) {
    const ready = await evaluate(
      `!!document.querySelector('.scenarios button')`);
    if (ready) break;
    await sleep(250);
  }
  if (!(await evaluate(`!!document.querySelector('.scenarios button')`))) {
    console.error(`no scripted-conversation buttons at ${URL_PAGE} — is the server up and the bundle synced?`);
    chrome.kill(); return 1;
  }

  const written = [];
  for (const id of SCENARIOS) {
    const label = labels[id];
    const clicked = await evaluate(`(() => {
      const want = ${JSON.stringify(label)};
      const b = [...document.querySelectorAll('.scenarios button')]
        .find((x) => x.textContent.trim() === want);
      if (!b) return 'MISSING';
      b.click();
      return 'CLICKED';
    })()`);
    if (clicked !== 'CLICKED') {
      console.error(`scenario "${id}" expects a button labelled "${label}" in the page; it is not there.`);
      console.error('Rendered labels: ' + await evaluate(
        `JSON.stringify([...document.querySelectorAll('.scenarios button')].map(b => b.textContent.trim()))`));
      chrome.kill(); return 1;
    }

    // The run disables every scenario button while it plays; wait for it to hand control back, then
    // let the last turn's envelope settle.
    for (let waited = 0; waited < TIMEOUT; waited += 250) {
      await sleep(250);
      const busy = await evaluate(
        `!!document.querySelector('.scenarios button[disabled]')`);
      if (!busy) break;
    }
    await sleep(500);

    const file = `careloop-device-${id}.json`;
    await evaluate(`(() => {
      const b = [...document.querySelectorAll('button')]
        .find((x) => x.textContent.trim() === 'Export trace JSON');
      if (!b || b.disabled) return 'NO_EXPORT';
      b.click();
      return 'EXPORTED';
    })()`);
    let landed = null;
    for (let waited = 0; waited < 15000 && !landed; waited += 250) {
      await sleep(250);
      landed = readdirSync(downloads).find((n) => n === file || n.startsWith(file));
    }
    if (!landed) { console.error(`the page never wrote ${file} — the export did not run`); chrome.kill(); return 1; }
    const target = join(OUT_DIR, `browser-${id}.json`);
    renameSync(join(downloads, landed), target);
    written.push(target);
  }

  chrome.kill();
  ws.close();
  for (const path of written) console.log(`wrote ${path}`);
  console.log('now: python -m scripts.alexa_device --url <the /mcp url> --compare <file>');
  return 0;
}

main().then((code) => process.exit(code)).catch((e) => {
  console.error(`lab_trace failed: ${e.message}`);
  process.exit(1);
});
