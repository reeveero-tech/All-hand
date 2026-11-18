// R-206e (D-165): behavioural test of the real Robin.jsx in jsdom (same
// harness as downloads_ui.test.mjs). Proves: signed-out users see only the
// sign-in hint (no models call); the personal-identity preset is never
// offered; no models -> the owner hint and a disabled button; the request
// carries the chosen query/model/preset; the report is text (an HTML string is
// NOT parsed) and .onion addresses are not links; a stage failure is shown.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import { transformWithEsbuild } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'https://app.test/' });
for (const [k, v] of Object.entries({ window: dom.window, document: dom.window.document,
  navigator: dom.window.navigator, HTMLElement: dom.window.HTMLElement, Node: dom.window.Node })) {
  try { globalThis[k] = v; } catch { /* read-only */ }
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const React = (await import('react')).default ?? await import('react');
const { act } = await import('react');
const { createRoot } = await import('react-dom/client');

const tmp = mkdtempSync(join(fileURLToPath(new URL('.', import.meta.url)), '.ui-tmp-'));
const src = readFileSync(new URL('../src/components/Robin.jsx', import.meta.url), 'utf8');
const out = await transformWithEsbuild(src, 'c.jsx', { loader: 'jsx', jsx: 'automatic' });
const file = join(tmp, 'robin.mjs');
writeFileSync(file, out.code);
const { default: Robin, PRESETS, serviceLine } = await import(pathToFileURL(file).href);
rmSync(tmp, { recursive: true, force: true });

const settle = async () => { for (let i = 0; i < 6; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };
async function render(props) {
  const c = document.createElement('div');
  document.body.appendChild(c);
  const root = createRoot(c);
  await act(async () => { root.render(React.createElement(Robin, props)); });
  await settle();
  return { c, done: async () => { await act(async () => root.unmount()); c.remove(); } };
}

const UP = { configured: true, reachable: true, service_configured: true, tor_bootstrapped: true, busy: false };
function fakeApi({ session = { access_token: 't' }, status = UP,
  models = { models: ['openrouter/m1', 'openrouter/m2'] }, answer = null } = {}) {
  const calls = [];
  return {
    calls,
    getSession: () => session,
    onSessionChange: () => () => {},
    status: async () => { calls.push('status'); return status; },
    models: async () => { calls.push('models'); return models; },
    investigate: async (b) => { calls.push(['investigate', b]); return answer; },
  };
}

function setValue(el, v) {
  const proto = el.tagName === 'SELECT' ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
  el.dispatchEvent(new window.Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
}

test('service line covers every state', () => {
  assert.match(serviceLine({ configured: false })[1], /غير مهيأة/);
  assert.equal(serviceLine({ configured: true, reachable: false })[0], 'badge-error');
  assert.match(serviceLine(UP)[1], /اتصال Tor جاهز/);
  assert.ok(!('personal_identity' in PRESETS));
});

test('signed out: sign-in hint only, models never requested', async () => {
  const api = fakeApi({ session: null });
  const { c, done } = await render({ api });
  assert.ok(c.querySelector('[data-testid=robin-signin]'));
  assert.equal(c.querySelector('[data-testid=robin-form]'), null);
  assert.deepEqual(api.calls, ['status']);
  await done();
});

test('no models: owner hint and the button stays disabled', async () => {
  const api = fakeApi({ models: { models: [] } });
  const { c, done } = await render({ api });
  assert.ok(c.querySelector('[data-testid=robin-no-models]'));
  assert.equal(c.querySelector('button[type=submit]').disabled, true);
  await done();
});

test('investigation: request shape, text-only report, onion addresses are not links', async () => {
  const answer = { ok: true, seconds: 42, record: {
    status: 'ok', refined_query: 'lockbit leak', results_count: 12, scraped_count: 4, sources_dropped: 1,
    summary: '<img src=x onerror=alert(1)> ## Findings', pivots: ['next question'],
    sources: [{ title: 'Leak blog', link: 'http://abc.onion/x' }] } };
  const api = fakeApi({ answer });
  const { c, done } = await render({ api });
  const opts = [...c.querySelectorAll('select')[0].options].map((o) => o.value);
  assert.deepEqual(opts, Object.keys(PRESETS));
  await act(async () => { setValue(c.querySelector('input'), 'lockbit leak site'); });
  await act(async () => { setValue(c.querySelectorAll('select')[0], 'ransomware_malware'); });
  await act(async () => { setValue(c.querySelectorAll('select')[1], 'openrouter/m2'); });
  await act(async () => { c.querySelector('form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })); });
  await settle();
  const sent = api.calls.find((x) => Array.isArray(x));
  assert.deepEqual(sent[1], { query: 'lockbit leak site', model: 'openrouter/m2', preset: 'ransomware_malware' });
  const sum = c.querySelector('[data-testid=robin-summary]');
  assert.equal(sum.querySelector('img'), null);
  assert.match(sum.textContent, /<img src=x/);
  const srcs = c.querySelector('[data-testid=robin-sources]');
  assert.equal(srcs.querySelector('a'), null);
  assert.match(srcs.textContent, /abc\.onion/);
  assert.match(c.textContent, /حُذف 1 مصدر/);
  await done();
});

test('stage failure is shown', async () => {
  const api = fakeApi({ answer: { ok: false, stage: 'search', error: 'Failed to search the dark web: tor' } });
  const { c, done } = await render({ api });
  await act(async () => { setValue(c.querySelector('input'), 'abc query'); });
  await act(async () => { c.querySelector('form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })); });
  await settle();
  assert.match(c.querySelector('[data-testid=robin-failed]').textContent, /search/);
  await done();
});
