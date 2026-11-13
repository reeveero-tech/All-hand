// R-204 (D-153): behavioural tests of the real Account.jsx rendered in jsdom
// (same harness as ui_consent.test.mjs: vite's transformWithEsbuild, no bundler),
// against a fake /api/v2 that answers like the real server. What is proven is
// what the user sees and what leaves the browser:
//   - accounts not configured on the server -> an honest message, no form;
//   - sign-in form; Google/GitHub buttons only when the server enables them;
//   - after sign-in: workspace switcher, members, invitations; the controls
//     follow the role the server reports (owner / admin / member);
//   - an invitation link is shown once and is gone after switching workspace;
//   - an invitation from the address bar can be accepted after sign-in;
//   - sign-out calls the server and returns to the sign-in form;
//   - losing membership (404) is reported and the list reloaded.
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
let server;
function freshServer() {
  return {
    config: { configured: true, email: true, signup_enabled: true, email_confirmation_required: true,
      providers: { google: false, github: false }, authorize_url: 'https://p.supabase.co/auth/v1/authorize' },
    cookie: false, // the HttpOnly refresh cookie the browser would hold
    workspaces: [
      { id: 'w-own', name: 'مساحتي', role: 'owner', created_at: '2026-11-13T00:00:00Z' },
      { id: 'w-mem', name: 'مساحة زميل', role: 'member', created_at: '2026-11-13T01:00:00Z' },
    ],
    members: {
      'w-own': [{ user_id: 'u-owner', email: 'owner@x.test', role: 'owner' }, { user_id: 'u-2', email: 'two@x.test', role: 'member' }],
      'w-mem': [{ user_id: 'u-boss', email: 'boss@x.test', role: 'owner' }, { user_id: 'u-owner', email: 'owner@x.test', role: 'member' }],
    },
    invitations: { 'w-own': [] },
    gone: new Set(),
  };
}
const calls = [];
const json = (status, body) => ({ ok: status >= 200 && status < 300, status, statusText: String(status), json: async () => body });
const session = () => ({ access_token: 'acc-1', expires_at: Math.floor(Date.now() / 1000) + 3600, user: ME });
globalThis.fetch = async (url, init = {}) => {
  const method = init.method || 'GET';
  const path = String(url);
  calls.push({ method, path, init });
  const body = init.body ? JSON.parse(init.body) : null;
  const authed = (init.headers || {}).Authorization === 'Bearer acc-1';
  if (path === '/api/v2/auth/config') return json(200, server.config);
  if (path === '/api/v2/auth/refresh') return server.cookie ? json(200, session()) : json(401, { detail: 'sign-in required' });
  if (path === '/api/v2/auth/login') {
    if (body.password !== 'correct-pw') return json(401, { detail: 'invalid email or password' });
    server.cookie = true;
    return json(200, session());
  }
  if (path === '/api/v2/auth/logout') { server.cookie = false; return { ok: true, status: 204, json: async () => ({}) }; }
  if (!authed) return json(401, { detail: 'sign-in required' });
  if (path === '/api/v2/workspaces' && method === 'GET') return json(200, server.workspaces.filter((w) => !server.gone.has(w.id)));
  let m;
  if ((m = path.match(/^\/api\/v2\/workspaces\/([^/]+)\/members$/))) {
    if (server.gone.has(m[1])) return json(404, { detail: 'workspace not found' });
    return json(200, server.members[m[1]] || []);
  }
  if ((m = path.match(/^\/api\/v2\/workspaces\/([^/]+)\/invitations$/))) {
    if (method === 'POST') {
      const inv = { id: `i-${calls.length}`, email: body.email, role: body.role, state: 'pending',
        expires_at: '2026-11-16T00:00:00Z' };
      server.invitations[m[1]].unshift(inv);
      return json(201, { ...inv, token: 'tok-secret-once', accept_url: 'https://app.test/#invite=tok-secret-once' });
    }
    return json(200, server.invitations[m[1]] || []);
  }
  if (path === '/api/v2/invitations/accept') {
    server.workspaces.push({ id: 'w-new', name: 'مساحة مدعو إليها', role: 'member', created_at: '2026-11-13T02:00:00Z' });
    server.members['w-new'] = [{ user_id: 'u-owner', email: 'owner@x.test', role: 'member' }];
    return json(200, { workspace_id: 'w-new', role: 'member' });
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

test('accounts not configured on the server: honest message, no sign-in form, nothing else called', async () => {
  server.config = { configured: false, email: false, providers: { google: false, github: false }, authorize_url: null };
  const ui = await render();
  try {
    assert.ok(ui.container.textContent.includes('غير مفعلة على هذا الخادم بعد'));
    assert.equal(ui.container.querySelector('#acc-email'), null);
    assert.deepEqual(calls.map((x) => x.path), ['/api/v2/auth/config']);
  } finally { await teardown(ui); }
});

test('sign-in form; provider buttons only for providers the server enables', async () => {
  let ui = await render();
  try {
    assert.ok(ui.container.querySelector('#acc-email'));
    assert.equal(button(ui.container, 'الدخول عبر Google'), undefined);
    assert.ok(ui.container.textContent.includes('غير مفعل على هذا الخادم بعد'));
  } finally { await teardown(ui); }
  server.config.providers.google = true;
  sessionMod._resetForTests();
  ui = await render();
  try {
    assert.ok(button(ui.container, 'الدخول عبر Google'));
    assert.equal(button(ui.container, 'الدخول عبر GitHub'), undefined);
  } finally { await teardown(ui); }
});

test('wrong password: clear message, still signed out', async () => {
  const ui = await render();
  try {
    await signIn(ui.container, 'wrong');
    assert.ok(ui.container.textContent.includes('البريد أو كلمة المرور غير صحيحة'));
    assert.ok(ui.container.querySelector('#acc-email'));
  } finally { await teardown(ui); }
});

test('owner: switcher, members with role controls, invitation link shown once', async () => {
  const ui = await render();
  try {
    await signIn(ui.container);
    const c = ui.container;
    assert.ok(c.textContent.includes('owner@x.test'));
    const switcher = c.querySelector('select[aria-label="المساحة الحالية"]');
    assert.equal(switcher.value, 'w-own');
    assert.ok(c.querySelector('select[aria-label="دور two@x.test"]'), 'owner can change roles');
    assert.ok(button(c, 'حذف المساحة'));
    assert.equal(Boolean(button(c, 'مغادرة')), false, 'the only owner is not offered to leave (the server refuses it)');
    assert.ok(c.querySelector('form[aria-label="دعوة عضو"]'));
    const roleOptions = [...c.querySelectorAll('select[aria-label="دور المدعو"] option')].map((o) => o.value);
    assert.deepEqual(roleOptions, ['member', 'admin']);

    setValue(c.querySelector('form[aria-label="دعوة عضو"] input[type="email"]'), 'new@x.test');
    await submit(c.querySelector('form[aria-label="دعوة عضو"]'));
    const link = c.querySelector('input[aria-label="رابط الدعوة"]');
    assert.equal(link.value, 'https://app.test/#invite=tok-secret-once');
    assert.ok(c.textContent.includes('يظهر مرة واحدة فقط'));
    assert.ok(c.querySelector('[data-invitation]').textContent.includes('بانتظار القبول'));
    assert.equal(localStorage.getItem('agos_workspace'), 'w-own');
    assert.equal([...Object.values(localStorage)].join().includes('tok-secret'), false);

    // switch to the workspace where I am only a member, then back: no controls, then no stale link
    setValue(c.querySelector('select[aria-label="المساحة الحالية"]'), 'w-mem');
    await settle();
    assert.equal(c.querySelector('select[aria-label^="دور "]:not([aria-label="دور المدعو"])'), null, 'a member changes no roles');
    assert.equal(c.querySelector('form[aria-label="دعوة عضو"]'), null, 'a member sees no invitations');
    assert.equal(button(c, 'حذف المساحة'), undefined);
    assert.equal(button(c, 'إزالة'), undefined);
    assert.ok(button(c, 'مغادرة'));
    assert.equal(localStorage.getItem('agos_workspace'), 'w-mem');
    setValue(c.querySelector('select[aria-label="المساحة الحالية"]'), 'w-own');
    await settle();
    assert.equal(c.querySelector('input[aria-label="رابط الدعوة"]'), null, 'the one-time link is not shown again');
  } finally { await teardown(ui); }
});

test('admin: invites members only, cannot change roles or delete', async () => {
  server.workspaces = [{ id: 'w-adm', name: 'إدارة', role: 'admin', created_at: '2026-11-13T00:00:00Z' }];
  server.members['w-adm'] = [{ user_id: 'u-boss', email: 'boss@x.test', role: 'owner' },
    { user_id: 'u-owner', email: 'owner@x.test', role: 'admin' }, { user_id: 'u-3', email: 'three@x.test', role: 'member' }];
  server.invitations['w-adm'] = [];
  const ui = await render();
  try {
    await signIn(ui.container);
    const c = ui.container;
    assert.deepEqual([...c.querySelectorAll('select[aria-label="دور المدعو"] option')].map((o) => o.value), ['member']);
    assert.equal(c.querySelector('select[aria-label="دور three@x.test"]'), null);
    assert.equal(button(c, 'حذف المساحة'), undefined);
    const removable = [...c.querySelectorAll('[data-member]')].filter((li) => [...li.querySelectorAll('button')].some((b) => b.textContent === 'إزالة'));
    assert.deepEqual(removable.map((li) => li.dataset.member), ['u-3'], 'an admin removes members only, never the owner');
  } finally { await teardown(ui); }
});

test('invitation from the address bar: removed from the URL, accepted after sign-in, its workspace selected', async () => {
  history.replaceState(null, '', '/#invite=tok_from_link_0123456789');
  const ui = await render();
  try {
    assert.equal(location.hash, '', 'token removed from the address bar');
    assert.ok(ui.container.textContent.includes('سجّل الدخول بالبريد المدعو'));
    await signIn(ui.container);
    await click(button(ui.container, 'قبول الدعوة'));
    const accept = calls.find((x) => x.path === '/api/v2/invitations/accept');
    assert.deepEqual(JSON.parse(accept.init.body), { token: 'tok_from_link_0123456789' });
    assert.equal(ui.container.querySelector('select[aria-label="المساحة الحالية"]').value, 'w-new');
    assert.ok(ui.container.textContent.includes('انضممت إلى المساحة بدور عضو'));
    assert.equal(sessionStorage.getItem('agos_pending_invite'), null);
  } finally { await teardown(ui); }
});

test('invitation link opened in the already-open page (hash change, no reload) is picked up', async () => {
  const ui = await render();
  try {
    await signIn(ui.container);
    assert.equal(Boolean(button(ui.container, 'قبول الدعوة')), false);
    dom.window.location.hash = '#invite=tok_same_page_0123456789';
    await settle();
    assert.equal(location.hash, '', 'token removed from the address bar');
    assert.ok(button(ui.container, 'قبول الدعوة'), 'pending invitation offered without a reload');
    assert.equal(sessionStorage.getItem('agos_pending_invite'), 'tok_same_page_0123456789');
  } finally { await teardown(ui); }
});

test('same-page invitation link while signed out: told to sign in with the invited e-mail', async () => {
  const ui = await render();
  try {
    dom.window.location.hash = '#invite=tok_same_page_signed_out_01';
    await settle();
    assert.equal(location.hash, '');
    assert.ok(ui.container.textContent.includes('سجّل الدخول بالبريد المدعو'));
  } finally { await teardown(ui); }
});

test('reload with the refresh cookie restores the session without a password', async () => {
  server.cookie = true;
  const ui = await render();
  try {
    assert.ok(ui.container.textContent.includes('owner@x.test'));
    assert.equal(calls.some((x) => x.path === '/api/v2/auth/login'), false);
  } finally { await teardown(ui); }
});

test('sign-out ends the server session and returns to the sign-in form', async () => {
  const ui = await render();
  try {
    await signIn(ui.container);
    await click(button(ui.container, 'تسجيل الخروج'));
    const lo = calls.find((x) => x.path === '/api/v2/auth/logout');
    assert.equal(lo.init.headers.Authorization, 'Bearer acc-1');
    assert.ok(ui.container.querySelector('#acc-email'));
    assert.ok(ui.container.textContent.includes('خرجت من حسابك'));
    assert.equal(server.cookie, false);
  } finally { await teardown(ui); }
});

test('membership lost meanwhile (404): reported, list reloaded without it', async () => {
  const ui = await render();
  try {
    await signIn(ui.container);
    server.gone.add('w-mem');
    setValue(ui.container.querySelector('select[aria-label="المساحة الحالية"]'), 'w-mem');
    await settle();
    assert.ok(ui.container.textContent.includes('لم تعد عضوًا في تلك المساحة'));
    const opts = [...ui.container.querySelectorAll('select[aria-label="المساحة الحالية"] option')].map((o) => o.value);
    assert.deepEqual(opts, ['w-own']);
  } finally { await teardown(ui); }
});

test('cleanup: transpiled temp files removed', () => {
  rmSync(tmp, { recursive: true, force: true });
});
