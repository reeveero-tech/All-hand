// R-206d (D-165): behavioural test of the real Downloads.jsx in jsdom (same
// harness as movies_ui.test.mjs). Proves: the visitor's platform group comes
// first; every link points to our copy; each file shows its SHA256 and its
// verification mark; a mismatched file has NO download link and is listed in
// an alert; an unverified copy says so; a server error is shown; the licence
// (GPL-3.0) and the source copies are on the page.
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
const src = readFileSync(new URL('../src/components/Downloads.jsx', import.meta.url), 'utf8');
const out = await transformWithEsbuild(src, 'c.jsx', { loader: 'jsx', jsx: 'automatic' });
const file = join(tmp, 'downloads.mjs');
writeFileSync(file, out.code);
const { default: Downloads, fetchDownloads, fmtSize, guessPlatform, orderGroups } = await import(pathToFileURL(file).href);
rmSync(tmp, { recursive: true, force: true });

const settle = async () => { for (let i = 0; i < 5; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };
async function render(props) {
  const c = document.createElement('div');
  document.body.appendChild(c);
  const root = createRoot(c);
  await act(async () => { root.render(React.createElement(Downloads, props)); });
  await settle();
  return { c, done: async () => { await act(async () => root.unmount()); c.remove(); } };
}

const BASE = 'https://github.com/me/repo/releases/download/mirror-mdm-v0.5.2/';
const file_ = (name, verified = true) => ({ name, size: 2048, sha256: 'a'.repeat(64), upstream_sha256: 'a'.repeat(64), verified, url: BASE + name });
const PAGE = (over = {}) => ({
  ok: true, id: 'mdm', name: 'Mister Download Manager', license: 'GPL-3.0',
  license_url: 'https://www.gnu.org/licenses/gpl-3.0.html', license_note: 'برنامج مستقل برخصته وكوده',
  upstream_url: 'https://github.com/anefzaoui/misterdownloadmanager', upstream_state: 'active',
  release: { tag: 'v0.5.2', mirror_page: 'https://github.com/me/repo/releases/tag/mirror-mdm-v0.5.2' },
  verification: { state: 'verified', verified_at: '2026-11-18T00:00:00Z', sums_source: 'upstream:x@v0.5.2/SHA256SUMS.txt', mismatched: [], missing: [], error: null },
  groups: [
    { id: 'windows', label: 'ويندوز', files: [file_('MDM-setup.exe')] },
    { id: 'linux', label: 'لينكس', files: [file_('MDM.deb'), file_('MDM.rpm')] },
    { id: 'checksums', label: 'المجاميع والتوقيع', files: [file_('SHA256SUMS.txt', null)] },
  ],
  releases: [{ tag: 'v0.5.2', verified: true }, { tag: 'v0.5.1', mirror_page: 'https://github.com/me/repo/releases/tag/mirror-mdm-v0.5.1' }],
  source: { source_url: 'https://github.com/me/repo/releases/download/mirror-mdm/mdm-src.tar.gz', bundle_url: 'https://github.com/me/repo/releases/download/mirror-mdm/mdm.bundle' },
  ...over,
});

test('helpers: size, platform guess and ordering', () => {
  assert.equal(fmtSize(17065873), '16.3 MB');
  assert.equal(fmtSize(2048), '2 KB');
  assert.equal(fmtSize(null), '—');
  assert.equal(guessPlatform('Mozilla/5.0 (Windows NT 10.0; Win64; x64)'), 'windows');
  assert.equal(guessPlatform('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)'), 'macos');
  assert.equal(guessPlatform('Mozilla/5.0 (X11; Linux x86_64)'), 'linux');
  assert.equal(guessPlatform('Mozilla/5.0 (Linux; Android 14)'), null);
  const g = [{ id: 'windows' }, { id: 'macos' }, { id: 'linux' }];
  assert.deepEqual(orderGroups(g, 'linux').map((x) => x.id), ['linux', 'windows', 'macos']);
  assert.deepEqual(orderGroups(g, null).map((x) => x.id), ['windows', 'macos', 'linux']);
});

test('page: platform first, links to our copy, sha shown, licence and source', async () => {
  const { c, done } = await render({ load: async () => PAGE(), platform: 'linux' });
  const groups = [...c.querySelectorAll('[data-testid^=group-]')].map((e) => e.dataset.testid);
  assert.deepEqual(groups, ['group-linux', 'group-windows', 'group-checksums']);
  assert.match(c.querySelector('[data-testid=verify-state]').textContent, /طابقت مجاميع المصدر/);
  const links = [...c.querySelectorAll('[data-testid=dl-file] a')].map((a) => a.href);
  assert.equal(links.length, 4);
  assert.ok(links.every((u) => u.startsWith(BASE)), 'every file from our copy');
  assert.ok(c.textContent.includes('a'.repeat(64)));
  assert.match(c.textContent, /غير مدرج في المجاميع/);
  assert.ok([...c.querySelectorAll('a')].some((a) => a.href === 'https://www.gnu.org/licenses/gpl-3.0.html' && a.textContent === 'GPL-3.0'));
  assert.ok([...c.querySelectorAll('a')].some((a) => a.href.endsWith('mdm-src.tar.gz')));
  assert.ok([...c.querySelectorAll('a')].some((a) => a.href.endsWith('mirror-mdm-v0.5.1') && a.textContent.trim() === 'v0.5.1'));
  await done();
});

test('page: a mismatched file has no link and is announced', async () => {
  const p = PAGE({ verification: { state: 'failed', mismatched: ['MDM.deb'], missing: [] } });
  p.groups[1].files[0].verified = false;
  const { c, done } = await render({ load: async () => p, platform: null });
  assert.match(c.querySelector('[data-testid=verify-state]').textContent, /فشل التحقق/);
  assert.match([...c.querySelectorAll('[role=alert]')].map((e) => e.textContent).join(' '), /ملفات لا تطابق: MDM\.deb/);
  const deb = [...c.querySelectorAll('[data-testid=dl-file]')].find((e) => e.textContent.includes('MDM.deb'));
  assert.equal(deb.querySelector('a'), null, 'no download link for a file that does not match');
  assert.match(deb.textContent, /لا يطابق/);
  await done();
});

test('page: not verified yet, and a server error', async () => {
  const p = PAGE({ ok: false, groups: [], verification: { state: 'not_verified_yet', error: 'OSError: HTTP Error 404' } });
  let r = await render({ load: async () => p, platform: null });
  assert.match(r.c.querySelector('[data-testid=verify-state]').textContent, /لم يُتحقق/);
  assert.match(r.c.textContent, /تعذر قراءة ملف التحقق: OSError: HTTP Error 404/);
  assert.match(r.c.textContent, /لا قائمة ملفات متحققة بعد/);
  await r.done();
  const fake = async () => ({ ok: false, status: 503, json: async () => ({ error: 'no release copied yet' }) });
  r = await render({ load: (t) => fetchDownloads(t, fake), platform: null });
  assert.match(r.c.querySelector('[role=alert]').textContent, /تعذر تحميل صفحة التنزيل \(503\): no release copied yet/);
  await r.done();
});
