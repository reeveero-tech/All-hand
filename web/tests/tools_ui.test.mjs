// R-206a (D-165): behavioural test of the real Tools.jsx in jsdom (same harness
// as account_ui.test.mjs). Proves: the hub lists the registry; a copied mirror
// shows its download links and the upstream state (archived stays visible);
// a not-yet-copied mirror shows no download links; a status failure and a
// failed copy are shown, not hidden; a failing API shows an error.
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
const src = readFileSync(new URL('../src/components/Tools.jsx', import.meta.url), 'utf8');
const out = await transformWithEsbuild(src, 'c.jsx', { loader: 'jsx', jsx: 'automatic' });
const file = join(tmp, 'tools.mjs');
writeFileSync(file, out.code);
const { default: Tools, fetchTools } = await import(pathToFileURL(file).href);
rmSync(tmp, { recursive: true, force: true });

const settle = async () => { for (let i = 0; i < 5; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };
async function render(load) {
  const c = document.createElement('div');
  document.body.appendChild(c);
  const root = createRoot(c);
  await act(async () => { root.render(React.createElement(Tools, { load })); });
  await settle();
  return { c, done: async () => { await act(async () => root.unmount()); c.remove(); } };
}

const DATA = {
  status: { error: null },
  tools: [
    { id: 'flights', item: 'R-206b', name: 'تتبع الطائرات المباشر', kind: 'service', source: 'https://airplanes.live', data_terms: 'non-commercial' },
    { id: 'robin', item: 'R-206e', name: 'Robin', kind: 'mirror', license: 'MIT', upstream_url: 'https://github.com/a/robin',
      mirror: { state: 'copied', upstream_state: 'archived', snapshot_head: 'abcdef1234', snapshot_at: '2026-11-18T00:00:00Z',
        checked_at: '2026-11-18T00:00:00Z', error: null, source_url: 'https://x/robin-src.tar.gz',
        bundle_url: 'https://x/robin.bundle', release_page: 'https://x/tag/mirror-robin', mirrored_releases: [] } },
    { id: 'mdm', item: 'R-206d', name: 'MDM', kind: 'mirror', license: 'GPL-3.0', upstream_url: 'https://github.com/a/mdm',
      mirror: { state: 'not_copied_yet', upstream_state: 'active', error: 'clone failed', mirrored_releases: [] } },
  ],
};

test('lists every tool with honest mirror state', async () => {
  const { c, done } = await render(async () => DATA);
  assert.deepEqual([...c.querySelectorAll('[data-tool]')].map((e) => e.dataset.tool), ['flights', 'robin', 'mdm']);
  const robin = c.querySelector('[data-tool="robin"]');
  assert.match(robin.textContent, /النسخة محفوظة لدينا/);
  assert.match(robin.textContent, /مؤرشف لدى مالكه/);
  assert.deepEqual([...robin.querySelectorAll('a.btn')].map((a) => a.getAttribute('href')),
    ['https://x/robin-src.tar.gz', 'https://x/robin.bundle', 'https://x/tag/mirror-robin']);
  const mdm = c.querySelector('[data-tool="mdm"]');
  assert.match(mdm.textContent, /لم تُنسخ بعد/);
  assert.equal(mdm.querySelectorAll('a.btn').length, 0);
  assert.match(mdm.querySelector('[role="alert"]').textContent, /clone failed/);
  assert.equal(c.querySelector('[data-tool="flights"] [data-testid="mirror"]'), null);
  assert.match(c.querySelector('[data-tool="flights"]').textContent, /قيد البناء \(R-206b\)/);
  await done();
});

test('status failure is shown, not hidden', async () => {
  const { c, done } = await render(async () => ({ ...DATA, status: { error: 'OSError: down' } }));
  assert.match(c.textContent, /حالة النسخ غير متاحة الآن: OSError: down/);
  await done();
});

test('API failure shows an error', async () => {
  const { c, done } = await render(() => fetchTools(async () => ({ ok: false, status: 503 })));
  assert.match(c.querySelector('[role="alert"]').textContent, /تعذر تحميل الأدوات \(503\)/);
  await done();
});

// R-206b (D-166): a tool with a page is marked active and opens its tab.
test('a tool with a page is active and opens its tab', async () => {
  const opened = [];
  const c = document.createElement('div');
  document.body.appendChild(c);
  const root = createRoot(c);
  const data = { status: { error: null }, tools: [{ ...DATA.tools[0], page: 'flights' }, DATA.tools[1]] };
  await act(async () => { root.render(React.createElement(Tools, { load: async () => data, onOpen: (p) => opened.push(p) })); });
  await settle();
  const card = c.querySelector('[data-tool=flights]');
  assert.match(card.textContent, /مفعّلة/);
  assert.doesNotMatch(card.textContent, /قيد البناء/);
  await act(async () => { card.querySelector('button').dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })); });
  assert.deepEqual(opened, ['flights']);
  assert.match(c.querySelector('[data-tool=robin]').textContent, /قيد البناء/);
  await act(async () => root.unmount()); c.remove();
});
