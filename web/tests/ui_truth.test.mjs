// R-201 (M10/P0): the UI must not show anything that is not real.
// Behavioural tests on the real Dashboard / Agents / Specializations / Chat
// components (jsdom + vite's transformWithEsbuild, same harness as
// ui_consent.test.mjs) plus a static guard over web/src and index.html.
//
// Truth established by R-200 (docs/audit/M10/inventory/REPORT_AR.md):
//   - the "1000 agents" are 20 real handlers x 50 identical generated copies,
//     all idle unless a task is running;
//   - chat routes a message by keywords to ONE handler (no multi-agent plan);
//   - the 299 open-source entries are metadata only (never shown as agents).
// So every count on screen must come from a server field, the real unit is
// the handler (= specialization), and copies are labelled as copies.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import { transformWithEsbuild } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
for (const [k, v] of Object.entries({
  window: dom.window, document: dom.window.document, navigator: dom.window.navigator,
  HTMLElement: dom.window.HTMLElement, Element: dom.window.Element, Node: dom.window.Node,
  Event: dom.window.Event, CustomEvent: dom.window.CustomEvent,
  getComputedStyle: dom.window.getComputedStyle, sessionStorage: dom.window.sessionStorage,
  requestAnimationFrame: (cb) => setTimeout(cb, 0),
})) {
  try { globalThis[k] = v; } catch { /* read-only global */ }
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
dom.window.HTMLElement.prototype.scrollIntoView = function () {};

const React = (await import('react')).default ?? await import('react');
const { act } = await import('react');
const { createRoot } = await import('react-dom/client');

// ------------------------------------------------ server fixtures (not 20/1000)
// Deliberately NOT the production numbers, so a hard-coded 20 or 1000 in the
// UI cannot pass by coincidence.
const SPECS = { specializations: [
  { name: 'analyst', description: 'تحليل', icon: 'chart-line', agents: 3, tool: 'statistics' },
  { name: 'builder', description: 'بناء', icon: 'hammer', agents: 3, tool: null },
  { name: 'researcher', description: 'بحث', icon: 'search', agents: 3, tool: 'web' },
] };
const mk = (spec, i, status = 'idle', done = 0) => ({
  id: i, name: `${spec}_${String(i).padStart(4, '0')}`, specialization: spec, status,
  stats: { tasks_completed: done, tasks_failed: 0, total_time_s: 0 },
});
const AGENTS = { agents: [
  mk('analyst', 0), mk('analyst', 1, 'working'), mk('analyst', 2, 'idle', 4),
  mk('builder', 0), mk('builder', 1), mk('builder', 2),
  mk('researcher', 0), mk('researcher', 1), mk('researcher', 2),
  { id: -1, name: 'my-ollama', specialization: 'external', status: 'idle', url: 'http://x', model: 'm',
    stats: { tasks_completed: 0, tasks_failed: 0, total_time_s: 0 } },
] };
AGENTS.count = AGENTS.agents.length;
const STATUS = {
  status: 'healthy', uptime_s: 5, llm_configured: false, llm_model: '', llm_provider: 'none',
  public_llm_enabled: false, public_llm_requires_consent: false, supabase_configured: false,
  supabase_key: {}, specializations: 3, total_agents: 10, external_agents: 1,
  tasks_completed: 4, tasks_failed: 0, tasks_in_memory: 0, tasks_in_db: null,
  security: { authentication: 'disabled' }, experience: {},
};

globalThis.fetch = async (url) => {
  const u = String(url);
  const body = u.includes('/api/specializations') ? SPECS
    : u.includes('/api/agents') ? AGENTS
    : u.includes('/api/events') ? { events: [] }
    : STATUS;
  return { ok: true, status: 200, statusText: 'ok', json: async () => body };
};

const tmp = mkdtempSync(join(fileURLToPath(new URL('.', import.meta.url)), '.ui-truth-tmp-'));
let seq = 0;
async function loadComponent(relPath) {
  const src = readFileSync(new URL(relPath, import.meta.url), 'utf8');
  const out = await transformWithEsbuild(src, 'component.jsx', { loader: 'jsx', jsx: 'automatic' });
  const apiAbs = pathToFileURL(new URL('../src/api.js', import.meta.url).pathname).href;
  const code = out.code
    .replaceAll("'../api.js'", JSON.stringify(apiAbs))
    .replaceAll('"../api.js"', JSON.stringify(apiAbs));
  const file = join(tmp, `c${++seq}.mjs`);
  writeFileSync(file, code);
  return import(pathToFileURL(file).href);
}
const { default: Dashboard } = await loadComponent('../src/components/Dashboard.jsx');
const { default: Agents } = await loadComponent('../src/components/Agents.jsx');
const { default: Specializations } = await loadComponent('../src/components/Specializations.jsx');
const { default: Chat } = await loadComponent('../src/components/Chat.jsx');

async function render(Component, props) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(React.createElement(Component, props)); });
  // let the components' initial fetches resolve and re-render
  await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
  return { root, container };
}
const teardown = async ({ root, container }) => {
  await act(async () => { root.unmount(); });
  container.remove();
};

// phrases that described the 1000 copies / the platform as something they are not
const BANNED = [
  'وكيل نشط', 'الوكلاء النشطون', 'وكلاء نشطون',
  'حضارة الوكلاء', 'وكلاء حقيقيين', 'لوحة التحكم',
  'AGOS Civilization', // R-203: the header is just "AGOS" (no civilization claim)
];

beforeEach(() => {});

test('R-201: Dashboard shows the real handler count from the server, and copies as copies', async () => {
  const ui = await render(Dashboard, { status: STATUS, liveEvent: null });
  try {
    const t = ui.container.textContent;
    for (const b of BANNED) assert.ok(!t.includes(b), `banned phrase on dashboard: ${b}`);
    const card = [...ui.container.querySelectorAll('.stat-card')]
      .find((c) => c.textContent.includes('المعالجات الحقيقية'));
    assert.ok(card, 'missing "المعالجات الحقيقية" card');
    assert.equal(card.querySelector('.stat-value').textContent.trim(), String(STATUS.specializations));
    // 10 total - 1 external = 9 local copies; labelled as copies, not agents
    assert.ok(card.textContent.includes('9 نسخة'), card.textContent);
    assert.ok(!t.includes('1000') && !t.includes('20 تخصص'), 'hard-coded production number leaked');
  } finally { await teardown(ui); }
});

test('R-201: Agents page groups identical copies under their one real handler and shows who is working', async () => {
  const ui = await render(Agents, {});
  try {
    const t = ui.container.textContent;
    for (const b of BANNED) assert.ok(!t.includes(b), `banned phrase on agents page: ${b}`);
    assert.ok(t.includes('3 معالجات حقيقية'), t.slice(0, 300));
    assert.ok(t.includes('9 نسخة مسجلة'), t.slice(0, 300));
    assert.ok(t.includes('1 تعمل الآن'), t.slice(0, 300));
    // one card per handler (3) + one per external agent (1), not one per copy (10)
    assert.equal(ui.container.querySelectorAll('.agent-card').length, 4);
    const analyst = [...ui.container.querySelectorAll('.agent-card')]
      .find((c) => c.textContent.includes('analyst'));
    assert.ok(analyst.textContent.includes('3 نسخ متطابقة'), analyst.textContent);
    assert.ok(analyst.textContent.includes('يعمل'), 'the working copy must be visible');
  } finally { await teardown(ui); }
});

test('R-201: Specializations counts come from the server, not a literal 20', async () => {
  const ui = await render(Specializations, { status: STATUS });
  try {
    const t = ui.container.textContent;
    for (const b of BANNED) assert.ok(!t.includes(b), `banned phrase on specializations: ${b}`);
    assert.ok(t.includes('3 تخصصات'), t.slice(0, 300));
    assert.ok(!t.includes('20 تخصص'), 'literal 20 is not a server value');
    assert.ok(t.includes('معالج واحد'), 'must say each specialization is one handler');
    assert.ok(t.includes('3 نسخ'), 'per-spec copy count must be labelled as copies');
  } finally { await teardown(ui); }
});

test('R-201: Chat says the message goes to one handler chosen by keywords', async () => {
  const ui = await render(Chat, { status: STATUS });
  try {
    const t = ui.container.textContent;
    for (const b of BANNED) assert.ok(!t.includes(b), `banned phrase in chat: ${b}`);
    assert.ok(t.includes('معالج واحد'), t.slice(0, 300));
    assert.ok(!t.includes('الحضارة'), 'no civilization claim in quick prompts');
  } finally { await teardown(ui); }
});

test('R-201: static guard — no banned phrase anywhere in web/src or index.html; monitor tab is named honestly', () => {
  const root = new URL('../', import.meta.url);
  const files = ['index.html'];
  const walk = (rel) => {
    for (const e of readdirSync(new URL(rel, root), { withFileTypes: true })) {
      const p = `${rel}/${e.name}`;
      if (e.isDirectory()) walk(p);
      else if (/\.(jsx?|html|css)$/.test(e.name)) files.push(p);
    }
  };
  walk('src');
  const hits = [];
  for (const f of files) {
    const s = readFileSync(new URL(f, root), 'utf8');
    for (const b of BANNED) if (s.includes(b)) hits.push(`${f}: ${b}`);
    if (/['"`>]\s*20 تخصص/.test(s)) hits.push(`${f}: literal "20 تخصص"`);
  }
  assert.deepEqual(hits, []);
  const app = readFileSync(new URL('src/App.jsx', root), 'utf8');
  assert.ok(app.includes("label: 'لوحة المراقبة'"), 'dashboard tab must be "لوحة المراقبة"');
  assert.ok(app.includes('<div className="app-title">AGOS</div>'), 'R-203: header title must be "AGOS"');
});

test('cleanup: transpiled temp files removed', () => {
  rmSync(tmp, { recursive: true, force: true });
  assert.ok(true);
});
