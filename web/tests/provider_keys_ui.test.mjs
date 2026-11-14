// R-205 (D-154): the BYOK card of the real Account.jsx in jsdom, against a fake
// /api/v2. What is proven is what the user sees and what stays in the browser:
//   - the key is typed into a password field and leaves the browser ONCE, in
//     the body of the add/replace request — never in a URL or header;
//   - after saving, the field is empty; the page shows only ••••last4;
//   - nothing in localStorage/sessionStorage, the DOM, or any later request
//     contains the key;
//   - test / replace / revoke / delete call the right routes and show the result;
//   - a server without a vault key gets an honest message.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import { transformWithEsbuild } from 'vite';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'https://app.test/' });
for (const [k, v] of Object.entries({
  window: dom.window, document: dom.window.document, navigator: dom.window.navigator,
  HTMLElement: dom.window.HTMLElement, Element: dom.window.Element, Node: dom.window.Node,
  Event: dom.window.Event, CustomEvent: dom.window.CustomEvent,
  getComputedStyle: dom.window.getComputedStyle, sessionStorage: dom.window.sessionStorage,
  localStorage: dom.window.localStorage, location: dom.window.location, history: dom.window.history,
  requestAnimationFrame: (cb) => setTimeout(cb, 0),
})) {
  try { globalThis[k] = v; } catch { /* read-only global */ }
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
dom.window.confirm = () => true;

const React = (await import('react')).default ?? await import('react');
const { act } = await import('react');
const { createRoot } = await import('react-dom/client');

// ------------------------------------------------------------ fake /api/v2
const ME = { id: 'u-owner', email: 'owner@x.test' };
const KEY = 'sk-FAKE-browser-0123456789abcdefWXYZ';
const NEWKEY = 'sk-FAKE-replaced-9876543210zyxQRST';
let server;
function freshServer() {
  return {
    config: { configured: true, email: true, signup_enabled: true, email_confirmation_required: true,
      providers: { google: false, github: false }, authorize_url: null },
    cookie: false,
    workspaces: [{ id: 'w-own', name: 'مساحتي', role: 'owner', created_at: '2026-11-13T00:00:00Z' }],
    keys: [], vault: true, n: 0,
  };
}
const calls = [];
const json = (status, body) => ({ ok: status >= 200 && status < 300, status, statusText: String(status), json: async () => body });
const session = () => ({ access_token: 'acc-1', expires_at: Math.floor(Date.now() / 1000) + 3600, user: ME });
const PROVIDERS = [
  { id: 'openai', name: 'OpenAI', needs_key: true, needs_url: false },
  { id: 'ollama', name: 'Ollama (local URL)', needs_key: false, needs_url: true },
];
const meta = (k) => ({ ...k, has_secret: k.status === 'active' && Boolean(k.last4) });
globalThis.fetch = async (url, init = {}) => {
  const method = init.method || 'GET';
  const path = String(url);
  calls.push({ method, path, init });
  const body = init.body ? JSON.parse(init.body) : null;
  const authed = (init.headers || {}).Authorization === 'Bearer acc-1';
  if (path === '/api/v2/auth/config') return json(200, server.config);
  if (path === '/api/v2/auth/refresh') return server.cookie ? json(200, session()) : json(401, { detail: 'sign-in required' });
  if (path === '/api/v2/auth/login') { server.cookie = true; return json(200, session()); }
  if (!authed) return json(401, { detail: 'sign-in required' });
  if (path === '/api/v2/workspaces') return json(200, server.workspaces);
  if (path === '/api/v2/workspaces/w-own/members') return json(200, [{ user_id: 'u-owner', email: 'owner@x.test', role: 'owner' }]);
  if (path === '/api/v2/workspaces/w-own/invitations') return json(200, []);
  if (path === '/api/v2/provider-keys/providers') return json(200, PROVIDERS);
  if (!server.vault && path.includes('/provider-keys')) return json(503, { detail: 'key vault is not configured on this server (AGOS_BYOK_KEK)' });
  let m;
  if (path === '/api/v2/workspaces/w-own/provider-keys') {
    if (method === 'POST') {
      const k = { id: `k-${++server.n}`, provider: body.provider, label: body.label, base_url: body.base_url || null,
        status: 'active', last4: body.api_key ? body.api_key.slice(-4) : null, last_test_at: null,
        last_test_reason: null, use_count: 0, tokens_in: 0, tokens_out: 0, cost_usd: '0.000000' };
      server.keys.push(k);
      return json(201, meta(k));
    }
    return json(200, server.keys.map(meta));
  }
  if ((m = path.match(/^\/api\/v2\/workspaces\/w-own\/provider-keys\/([^/]+)(\/test|\/revoke)?$/))) {
    const k = server.keys.find((x) => x.id === m[1]);
    if (!k) return json(404, { detail: 'key not found' });
    if (m[2] === '/test') {
      k.last_test_at = '2026-11-14T00:00:00Z'; k.last_test_reason = k.last4 === 'WXYZ' ? 'ok' : 'invalid_key';
      return json(200, { ok: k.last_test_reason === 'ok', reason: k.last_test_reason, http_status: k.last_test_reason === 'ok' ? 200 : 401, tested_at: k.last_test_at });
    }
    if (m[2] === '/revoke') { k.status = 'revoked'; return json(200, meta(k)); }
    if (method === 'PATCH') { if (body.api_key) k.last4 = body.api_key.slice(-4); return json(200, meta(k)); }
    if (method === 'DELETE') { server.keys = server.keys.filter((x) => x !== k); return { ok: true, status: 204, json: async () => ({}) }; }
  }
  return json(404, { detail: 'not found' });
};

// --------------------------------------------------------- component loading
const tmp = mkdtempSync(join(fileURLToPath(new URL('.', import.meta.url)), '.ui-tmp-'));
const sessionAbs = pathToFileURL(new URL('../src/session.js', import.meta.url).pathname).href;
const src = readFileSync(new URL('../src/components/Account.jsx', import.meta.url), 'utf8');
const out = await transformWithEsbuild(src, 'component.jsx', { loader: 'jsx', jsx: 'automatic' });
const file = join(tmp, 'account.mjs');
writeFileSync(file, out.code.replaceAll("'../session.js'", JSON.stringify(sessionAbs)).replaceAll('"../session.js"', JSON.stringify(sessionAbs)));
const { default: Account } = await import(pathToFileURL(file).href);
const sessionMod = await import(sessionAbs);

const settle = async () => { for (let i = 0; i < 6; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };
async function render() {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(React.createElement(Account)); });
  await settle();
  return { root, container };
}
const teardown = async ({ root, container }) => { await act(async () => { root.unmount(); }); container.remove(); };
const setValue = (el, value) => {
  const proto = el.tagName === 'SELECT' ? dom.window.HTMLSelectElement.prototype : dom.window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
  el.dispatchEvent(new dom.window.Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
};
const button = (c, text) => [...c.querySelectorAll('button')].find((b) => b.textContent.trim() === text);
async function click(el) { await act(async () => { el.click(); }); await settle(); }
async function submit(form) {
  await act(async () => { form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true })); });
  await settle();
}
async function signIn(c, pw = 'correct-pw') {
  setValue(c.querySelector('#acc-email'), 'owner@x.test');
  setValue(c.querySelector('#acc-password'), pw);
  await submit(c.querySelector('form[aria-label="تسجيل الدخول"]'));
}

beforeEach(() => {
  server = freshServer();
  calls.length = 0;
  sessionStorage.clear();
  localStorage.clear();
  sessionMod._resetForTests();
  history.replaceState(null, '', '/');
});

const card = (c) => c.querySelector('[aria-label="مفاتيح الذكاء الاصطناعي"]');
function storageDump() {
  const out = [];
  for (const s of [localStorage, sessionStorage]) for (let i = 0; i < s.length; i++) out.push(s.key(i), s.getItem(s.key(i)));
  return out.join('\n');
}
function assertNowhere(c, secret, fromCall = 0) {
  assert.equal(c.innerHTML.includes(secret), false, 'DOM holds the key');
  assert.equal(document.body.innerHTML.includes(secret), false, 'document holds the key');
  assert.equal(storageDump().includes(secret), false, 'storage holds the key');
  for (const x of calls.slice(fromCall)) {
    assert.equal(x.path.includes(secret), false, 'URL holds the key');
    assert.equal(JSON.stringify(x.init.headers || {}).includes(secret), false, 'header holds the key');
  }
}

test('add a key: sent once in the body, field emptied, only last4 shown, nothing stored', async () => {
  const ui = await render();
  try {
    await signIn(ui.container);
    const k = card(ui.container);
    assert.ok(k, 'the keys card is shown');
    const input = k.querySelector('input[aria-label="المفتاح"]');
    assert.equal(input.type, 'password');
    assert.equal(input.getAttribute('autocomplete'), 'off');
    setValue(k.querySelector('input[aria-label="اسم المفتاح"]'), 'عمل');
    setValue(input, KEY);
    await submit(k.querySelector('form[aria-label="إضافة مفتاح"]'));
    const sent = calls.filter((x) => x.init.body && x.init.body.includes(KEY));
    assert.equal(sent.length, 1, 'the key leaves the browser exactly once');
    assert.equal(sent[0].method, 'POST');
    assert.equal(sent[0].path, '/api/v2/workspaces/w-own/provider-keys');
    assert.deepEqual(JSON.parse(sent[0].init.body), { provider: 'openai', label: 'عمل', api_key: KEY });
    assert.equal(card(ui.container).querySelector('input[aria-label="المفتاح"]').value, '', 'the field is emptied');
    assert.ok(ui.container.textContent.includes('••••WXYZ'));
    assertNowhere(ui.container, KEY);
    assertNowhere(ui.container, KEY.slice(0, -4));
  } finally { await teardown(ui); }
});

test('test, replace, revoke and delete use the right routes; the new key is never kept either', async () => {
  const ui = await render();
  try {
    await signIn(ui.container);
    let k = card(ui.container);
    setValue(k.querySelector('input[aria-label="اسم المفتاح"]'), 'x');
    setValue(k.querySelector('input[aria-label="المفتاح"]'), KEY);
    await submit(k.querySelector('form[aria-label="إضافة مفتاح"]'));
    await click(button(ui.container, 'اختبار المفتاح'));
    assert.ok(ui.container.textContent.includes('آخر اختبار: يعمل'));
    await click(button(ui.container, 'استبدال المفتاح'));
    const field = ui.container.querySelector('input[aria-label="المفتاح الجديد"]');
    assert.equal(field.type, 'password');
    setValue(field, NEWKEY);
    const from = calls.length;
    await submit(ui.container.querySelector('form[aria-label="استبدال المفتاح"]'));
    const patch = calls.slice(from).find((x) => x.method === 'PATCH');
    assert.deepEqual(JSON.parse(patch.init.body), { api_key: NEWKEY });
    assert.equal(ui.container.querySelector('input[aria-label="المفتاح الجديد"]'), null, 'the replace form is closed');
    assert.ok(ui.container.textContent.includes('••••QRST'));
    await click(button(ui.container, 'اختبار المفتاح'));
    assert.ok(ui.container.textContent.includes('المفتاح غير صحيح'));
    await click(button(ui.container, 'إبطال'));
    assert.ok(calls.some((x) => x.method === 'POST' && x.path.endsWith('/k-1/revoke')));
    assert.ok(ui.container.textContent.includes('مُبطل'));
    assert.equal(button(ui.container, 'اختبار المفتاح'), undefined, 'a revoked key cannot be tested');
    await click(button(ui.container, 'حذف'));
    assert.ok(calls.some((x) => x.method === 'DELETE' && x.path.endsWith('/provider-keys/k-1')));
    assert.ok(ui.container.textContent.includes('لا مفاتيح بعد'));
    assertNowhere(ui.container, KEY);
    assertNowhere(ui.container, NEWKEY);
  } finally { await teardown(ui); }
});

test('Ollama: a URL field, the key optional', async () => {
  const ui = await render();
  try {
    await signIn(ui.container);
    const k = card(ui.container);
    setValue(k.querySelector('select[aria-label="المزود"]'), 'ollama');
    await settle();
    const url = card(ui.container).querySelector('input[aria-label="عنوان Ollama"]');
    assert.ok(url);
    setValue(card(ui.container).querySelector('input[aria-label="اسم المفتاح"]'), 'محلي');
    setValue(url, 'http://my-host:11434');
    await submit(card(ui.container).querySelector('form[aria-label="إضافة مفتاح"]'));
    const post = calls.find((x) => x.method === 'POST' && x.path.endsWith('/provider-keys'));
    assert.deepEqual(JSON.parse(post.init.body), { provider: 'ollama', label: 'محلي', base_url: 'http://my-host:11434' });
    assert.ok(ui.container.textContent.includes('بلا مفتاح'));
  } finally { await teardown(ui); }
});

test('server without a vault key: an honest message, no crash', async () => {
  server.vault = false;
  const ui = await render();
  try {
    await signIn(ui.container);
    assert.ok(card(ui.container).textContent.includes('خزنة المفاتيح غير مفعلة على هذا الخادم بعد'));
  } finally { await teardown(ui); }
});

test.after(() => rmSync(tmp, { recursive: true, force: true }));
