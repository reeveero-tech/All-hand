// R-206c (D-165): behavioural test of the real Movies.jsx in jsdom (same
// harness as flights_ui.test.mjs). Proves: the server arrays decode into films;
// search matches Arabic and English titles, prefix first; suggestions sum the
// neighbour scores of the films you like and never suggest them back; the page
// shows the counts, a search hit opens the film with its 20 neighbours, «أعجبني»
// fills «اقتراحات لك» and is kept in localStorage; a 503 is shown as an error.
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
const src = readFileSync(new URL('../src/components/Movies.jsx', import.meta.url), 'utf8');
const out = await transformWithEsbuild(src, 'c.jsx', { loader: 'jsx', jsx: 'automatic' });
const file = join(tmp, 'movies.mjs');
writeFileSync(file, out.code);
const { default: Movies, fetchMap, decode, searchFilms, suggest, fitView, labelPlacer } = await import(pathToFileURL(file).href);
rmSync(tmp, { recursive: true, force: true });

const settle = async () => { for (let i = 0; i < 5; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };
async function render(load) {
  const c = document.createElement('div');
  document.body.appendChild(c);
  const root = createRoot(c);
  await act(async () => { root.render(React.createElement(Movies, { load })); });
  await settle();
  return { c, done: async () => { await act(async () => root.unmount()); c.remove(); } };
}
const click = async (el) => { await act(async () => { el.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true })); }); await settle(); };
const type = async (el, v) => {
  await act(async () => {
    Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, 'value').set.call(el, v);
    el.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
  });
  await settle();
};

const FIELDS = ['qid', 'title_ar', 'title_en', 'year', 'imdb', 'genres', 'x', 'y', 'island', 'sitelinks'];
const DATA = {
  format: 1, generated_at: '2026-11-18T00:00:00Z', source: 'Wikidata', license: 'CC0-1.0', fields: FIELDS,
  genres: [['Q1', 'جريمة'], ['Q2', 'دراما']],
  films: [
    ['Q47703', 'العراب', 'The Godfather', 1972, 'tt0068646', [0, 1], 0, 0, 0, 133],
    ['Q184768', 'العراب: الجزء الثاني', 'The Godfather Part II', 1974, 'tt0071562', [0, 1], 1, 1, 0, 100],
    ['Q1', '', 'Goodfellas', 1990, 'tt0099685', [0], 2, 0, 0, 90],
    ['Q2', 'تيتانيك', 'Titanic', 1997, 'tt0120338', [1], 50, 50, 1, 138],
  ],
  neighbours: [[[1, 0.9], [2, 0.5]], [[0, 0.9], [2, 0.6]], [[1, 0.6], [0, 0.5]], [[0, 0.1]]],
  islands: [{ id: 0, label: 'جريمة / دراما', size: 3, x: 1, y: 0.3 }, { id: 1, label: 'دراما', size: 1, x: 50, y: 50 }],
};

test('decode, search and suggestions', () => {
  const m = decode(DATA);
  assert.equal(m.films.length, 4);
  assert.deepEqual(m.films[0].genres, ['جريمة', 'دراما']);
  assert.deepEqual(m.bounds, { minX: 0, maxX: 50, minY: 0, maxY: 50 });
  assert.deepEqual(searchFilms(m, 'العراب').map((f) => f.qid), ['Q47703', 'Q184768']);
  assert.deepEqual(searchFilms(m, 'godfather part').map((f) => f.qid), ['Q184768']);
  assert.deepEqual(searchFilms(m, 'g'), [], 'one letter is too short');
  const s = suggest(m, [0, 1]);
  assert.deepEqual(s.map((x) => x.film.qid), ['Q1'], 'liked films are never suggested back');
  assert.ok(Math.abs(s[0].score - 1.1) < 1e-9);
  const v = fitView(m.bounds, 500, 500);
  assert.equal(v.cx, 25);
  assert.ok(v.scale > 0);
});

test('labels never overlap and stay on screen', () => {
  const place = labelPlacer(300, 200);
  assert.equal(place(100, 100, 60), true);
  assert.equal(place(110, 104, 60), false, 'overlapping box refused');
  assert.equal(place(100, 140, 60), true, 'a free row is accepted');
  assert.equal(place(-200, 100, 60), false, 'off screen refused');
});

test('page: counts, search opens a film with neighbours, like fills suggestions and persists', async () => {
  globalThis.localStorage = dom.window.localStorage;
  localStorage.clear();
  const { c, done } = await render(async () => DATA);
  assert.match(c.querySelector('[data-testid=movies-summary]').textContent, /(4|٤) فيلمًا · 2 جزيرة · حُدّثت 2026-11-18/);
  assert.match(c.querySelector('[data-testid=movies-attrib]').textContent, /Wikidata.*CC0/);
  assert.match(c.querySelector('[data-testid=movie-suggestions]').textContent, /اختر أفلامًا أعجبتك/);
  await type(c.querySelector('input[aria-label="بحث عن فيلم"]'), 'Godfather');
  const hits = c.querySelectorAll('[data-testid=movies-hits] button');
  assert.equal(hits.length, 2);
  await click(hits[0]);
  const detail = c.querySelector('[data-testid=movie-detail]');
  assert.match(detail.textContent, /العراب \(1972\)/);
  assert.match(detail.textContent, /The Godfather/);
  assert.ok(detail.querySelector('a[href="https://www.wikidata.org/wiki/Q47703"]'));
  assert.ok(detail.querySelector('a[href="https://www.imdb.com/title/tt0068646/"]'));
  assert.equal(c.querySelectorAll('[data-testid=movie-neighbours] li').length, 2);
  await click([...detail.querySelectorAll('button')].find((b) => b.textContent === 'أعجبني'));
  assert.deepEqual(JSON.parse(localStorage.getItem('agos.movies.liked')), ['Q47703']);
  const sug = c.querySelector('[data-testid=movie-suggestions]').textContent;
  assert.match(sug, /العراب: الجزء الثاني/);
  assert.match(sug, /قائمتي: 1/);
  await done();
  const again = await render(async () => DATA);
  assert.match(again.c.querySelector('[data-testid=movie-suggestions]').textContent, /قائمتي: 1/, 'list survives a reload');
  await again.done();
});

test('a missing map is an error, and fetchMap explains 503', async () => {
  const r = await render(async () => { throw new Error('خريطة الأفلام غير متاحة بعد: HTTP 404'); });
  assert.match(r.c.querySelector('[role=alert]').textContent, /غير متاحة بعد/);
  await r.done();
  await assert.rejects(fetchMap(async () => ({ ok: false, status: 503, json: async () => ({ detail: 'HTTPError: 404' }) })),
    /غير متاحة بعد: HTTPError: 404/);
  let url;
  const ok = await fetchMap(async (u) => { url = u; return { ok: true, status: 200, json: async () => DATA }; });
  assert.equal(url, '/api/v2/movies/map');
  assert.equal(ok.films.length, 4);
});
