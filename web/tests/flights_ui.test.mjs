// R-206b (D-166): behavioural test of the real Flights.jsx in jsdom (same
// harness as tools_ui.test.mjs). Proves: the map math round-trips; the first
// load asks for the visible area with a radius capped at 250 NM; aircraft with
// a position become markers (military marked) and a click shows details; tile
// and data attribution are shown; a fallback is announced; a failure is shown
// as an error (not as zero aircraft); a non-area search sends mode + q and lists
// results; the API helper turns 400/502 into clear Arabic errors.
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
const src = readFileSync(new URL('../src/components/Flights.jsx', import.meta.url), 'utf8');
const out = await transformWithEsbuild(src, 'c.jsx', { loader: 'jsx', jsx: 'automatic' });
const file = join(tmp, 'flights.mjs');
writeFileSync(file, out.code);
const { default: Flights, fetchFlights, project, unproject, radiusForView } = await import(pathToFileURL(file).href);
rmSync(tmp, { recursive: true, force: true });

const settle = async () => { for (let i = 0; i < 5; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };
async function render(load) {
  const c = document.createElement('div');
  document.body.appendChild(c);
  const root = createRoot(c);
  await act(async () => { root.render(React.createElement(Flights, { load, auto: false })); });
  await settle();
  return { c, done: async () => { await act(async () => root.unmount()); c.remove(); } };
}
const click = async (el) => { await act(async () => { el.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })); }); await settle(); };

const DATA = {
  ok: true, mode: 'point', provider: 'adsb.lol', fallback_used: false, cached: false, count: 3,
  attribution: { home: 'https://adsb.lol', license: 'ODbL 1.0', license_url: 'https://opendatacommons.org/licenses/odbl/1-0/' },
  aircraft: [
    { hex: '710001', flight: 'SVA123', r: 'HZ-AK11', t: 'B77W', lat: 21.7, lon: 39.2, alt_baro: 35000, gs: 480, track: 90, military: false },
    { hex: 'ae0001', flight: 'RCH1', lat: 21.5, lon: 39.0, military: true },
    { hex: '000001', flight: 'NOPOS' },
  ],
};

test('map math round-trips and the radius is capped', () => {
  const p = project(21.5, 39.2, 7);
  const back = unproject(p.x, p.y, 7);
  assert.ok(Math.abs(back.lat - 21.5) < 1e-9 && Math.abs(back.lon - 39.2) < 1e-9);
  assert.equal(radiusForView(0, 3, 4000, 4000), 250);
  assert.ok(radiusForView(21.5, 8, 800, 460) > 50 && radiusForView(21.5, 8, 800, 460) < 250);
});

test('first load asks for the visible area; markers, details and attribution', async () => {
  const calls = [];
  const { c, done } = await render(async (req) => { calls.push(req); return DATA; });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].mode, 'point');
  assert.ok(calls[0].radius >= 1 && calls[0].radius <= 250);
  const markers = c.querySelectorAll('.flights-marker');
  assert.equal(markers.length, 2, 'only aircraft with a position are drawn');
  assert.ok(c.querySelector('[data-hex="ae0001"]').className.includes('mil'));
  assert.match(c.querySelector('[data-testid=flights-summary]').textContent, /3 طائرة · المصدر adsb\.lol/);
  assert.match(c.querySelector('[data-testid=flights-summary]').textContent, /1 بلا موقع/);
  const attrib = c.querySelector('[data-testid=flights-attrib]').textContent;
  assert.match(attrib, /OpenStreetMap/);
  assert.match(attrib, /ODbL 1\.0/);
  assert.ok(c.querySelectorAll('.flights-tile').length > 0);
  assert.ok([...c.querySelectorAll('.flights-tile')].every((i) => i.src.startsWith('https://tile.openstreetmap.org/8/')));
  await click(c.querySelector('[data-hex="710001"]'));
  const d = c.querySelector('[data-testid=flight-detail]').textContent;
  assert.match(d, /SVA123/);
  assert.match(d, /HZ-AK11/);
  await done();
});

test('fallback is announced; failure is an error, not zero aircraft', async () => {
  let r1 = await render(async () => ({ ...DATA, provider: 'adsb.fi', fallback_used: true }));
  assert.match(r1.c.textContent, /استُخدم الاحتياطي/);
  await r1.done();
  r1 = await render(async () => { throw new Error('تعذر جلب بيانات الطائرات (502)'); });
  assert.match(r1.c.querySelector('[role=alert]').textContent, /502/);
  assert.equal(r1.c.querySelector('[data-testid=flights-summary]'), null);
  assert.equal(r1.c.querySelectorAll('.flights-marker').length, 0);
  await r1.done();
});

test('a callsign search sends mode + q and lists results', async () => {
  const calls = [];
  const { c, done } = await render(async (req) => { calls.push(req); return { ...DATA, mode: req.mode }; });
  const sel = c.querySelector('select');
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(dom.window.HTMLSelectElement.prototype, 'value').set;
    setter.call(sel, 'callsign');
    sel.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
  });
  const input = c.querySelector('input[aria-label="قيمة البحث"]');
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, 'value').set;
    setter.call(input, 'SVA123');
    input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
  });
  await act(async () => { c.querySelector('form').dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); });
  await settle();
  assert.deepEqual(calls.at(-1), { mode: 'callsign', q: 'SVA123' });
  assert.match(c.textContent, /النتائج/);
  assert.equal(c.querySelectorAll('.account-row').length, 3);
  await done();
});

test('fetchFlights builds the query and explains errors', async () => {
  let url;
  const ok = await fetchFlights({ mode: 'point', lat: '1.00', lon: '2.00', radius: 30, q: '' },
    async (u) => { url = u; return { ok: true, status: 200, json: async () => ({ ok: true }) }; });
  assert.deepEqual(ok, { ok: true });
  assert.equal(url, '/api/v2/flights?mode=point&lat=1.00&lon=2.00&radius=30');
  await assert.rejects(fetchFlights({ mode: 'hex', q: 'zz' },
    async () => ({ ok: false, status: 400, json: async () => ({ detail: 'invalid hex value' }) })), /مدخل غير صالح: invalid hex value/);
  await assert.rejects(fetchFlights({ mode: 'mil' },
    async () => ({ ok: false, status: 502, json: async () => ({ errors: [{ provider: 'adsb.lol', error: 'HTTP 500' }, { provider: 'adsb.fi', error: 'HTTP 500' }] }) })),
  /502.*adsb\.lol: HTTP 500 · adsb\.fi: HTTP 500/);
});
