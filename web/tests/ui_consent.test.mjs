// SEC-06 (R-145): behavioural tests of the real consent UI — the actual
// Chat.jsx / Specializations.jsx components rendered in jsdom (no bundler:
// vite's public transformWithEsbuild transpiles the JSX in memory; jsdom is a
// dev-only dependency). What is proven here is the contract the user sees:
//   - the warning + unchecked, unpersisted consent checkbox appear ONLY when
//     the public (keyless) provider is the one serving (no false claims for
//     none/openrouter);
//   - a send (button, Enter, quick prompt) without consent leaves the browser
//     — no fetch at all — and shows the refusal reason;
//   - with consent the request body carries allow_public_llm=true, and after
//     the response the checkbox is cleared again (consent is per request);
//   - stale consent is never sent when the provider is not keyless.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import { transformWithEsbuild } from 'vite';

// ---------------------------------------------------------------- jsdom setup
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
for (const [k, v] of Object.entries({
  window: dom.window, document: dom.window.document, navigator: dom.window.navigator,
  HTMLElement: dom.window.HTMLElement, Element: dom.window.Element, Node: dom.window.Node,
  Event: dom.window.Event, CustomEvent: dom.window.CustomEvent,
  getComputedStyle: dom.window.getComputedStyle, sessionStorage: dom.window.sessionStorage,
  requestAnimationFrame: (cb) => setTimeout(cb, 0),
})) globalThis[k] = v;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
// jsdom lacks scrollIntoView (used by Chat's messagesEnd ref)
dom.window.HTMLElement.prototype.scrollIntoView = function () {};

const React = (await import('react')).default ?? await import('react');
const { act } = await import('react');
const { createRoot } = await import('react-dom/client');

// ------------------------------------------------------------- fetch capture
const calls = [];
const chatResponse = { outcome: { result: { answer: 'رد الاختبار' }, ok: true }, agent: 'a', routed_to: 'r' };
const specsResponse = { specializations: [
  { name: 'analyst', description: 'تحليل البيانات', icon: 'chart-line', agents: 3 },
] };
globalThis.fetch = async (url, init) => {
  calls.push({ url: String(url), init });
  const body = String(url).includes('/api/specializations') ? specsResponse : chatResponse;
  return { ok: true, status: 200, statusText: 'ok', json: async () => body };
};

// --------------------------------------------------------- component loading
// temp files live inside web/tests/ so the transpiled code can resolve
// 'react' and the rewritten api.js from web/node_modules
const tmp = mkdtempSync(join(fileURLToPath(new URL('.', import.meta.url)), '.ui-tmp-'));
let seq = 0;
async function loadComponent(relPath) {
  const src = readFileSync(new URL(relPath, import.meta.url), 'utf8');
  const out = await transformWithEsbuild(src, 'component.jsx', { loader: 'jsx', jsx: 'automatic' });
  // point the component's '../api.js' import at the real module (the temp
  // file lives outside src/, so rewrite the specifier to an absolute URL)
  const apiAbs = pathToFileURL(new URL('../src/api.js', import.meta.url).pathname).href;
  const code = out.code
    .replaceAll("'../api.js'", JSON.stringify(apiAbs))
    .replaceAll('"../api.js"', JSON.stringify(apiAbs));
  const file = join(tmp, `c${++seq}.mjs`);
  writeFileSync(file, code);
  return import(pathToFileURL(file).href);
}
const { default: Chat } = await loadComponent('../src/components/Chat.jsx');
const { default: Specializations } = await loadComponent('../src/components/Specializations.jsx');

const KEYLESS = { llm_provider: 'keyless', public_llm_enabled: true, public_llm_requires_consent: true };
const OPENROUTER = { llm_provider: 'openrouter', public_llm_enabled: false, public_llm_requires_consent: false };
const NONE = { llm_provider: 'none', public_llm_enabled: false, public_llm_requires_consent: false };

async function render(Component, props) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(React.createElement(Component, props)); });
  return { root, container };
}
const teardown = async ({ root, container }) => {
  await act(async () => { root.unmount(); });
  container.remove();
};

const setNativeValue = (el, value) => {
  const proto = el.tagName === 'TEXTAREA'
    ? dom.window.HTMLTextAreaElement.prototype : dom.window.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
  el.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
};
const sendButton = (c) => [...c.querySelectorAll('button')].find((b) => b.textContent.trim() === 'إرسال');
const refusalShown = (c) => c.textContent.includes('لم يُرسل الطلب');

beforeEach(() => { calls.length = 0; });

test('SEC-06: keyless provider — warning + unchecked checkbox near the input; send without consent leaves the browser (no fetch) and shows the reason', async () => {
  const ui = await render(Chat, { status: KEYLESS });
  try {
    assert.ok(ui.container.textContent.includes('تنبيه: مزود LLM عام خارجي'), 'warning missing');
    const box = ui.container.querySelector('.chat-input-area input[type="checkbox"]');
    assert.ok(box, 'consent checkbox missing');
    assert.equal(box.checked, false, 'checkbox must start unchecked');
    const area = ui.container.querySelector('.chat-input-area');
    assert.ok(area.contains(box), 'checkbox must sit next to the input, not far from it');

    setNativeValue(ui.container.querySelector('textarea.chat-input'), 'رسالة اختبار');
    await act(async () => { sendButton(ui.container).click(); });
    assert.equal(calls.length, 0, 'nothing may be sent without consent');
    assert.ok(refusalShown(ui.container), 'refusal reason must be shown');
  } finally { await teardown(ui); }
});

test('SEC-06: quick prompts are blocked the same way (no send without consent)', async () => {
  const ui = await render(Chat, { status: KEYLESS });
  try {
    const quick = ui.container.querySelector('.empty-state .btn.btn-ghost');
    assert.ok(quick, 'quick prompt button missing');
    await act(async () => { quick.click(); });
    assert.equal(calls.length, 0, 'quick prompt must not send without consent');
    assert.ok(refusalShown(ui.container));
  } finally { await teardown(ui); }
});

test('SEC-06: Enter key is blocked the same way (no send without consent)', async () => {
  const ui = await render(Chat, { status: KEYLESS });
  try {
    const ta = ui.container.querySelector('textarea.chat-input');
    setNativeValue(ta, 'رسالة عبر Enter');
    await act(async () => {
      ta.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    });
    assert.equal(calls.length, 0, 'Enter must not send without consent');
    assert.ok(refusalShown(ui.container));
  } finally { await teardown(ui); }
});

test('SEC-06: with consent the request carries allow_public_llm=true — and the checkbox is cleared again after the send', async () => {
  const ui = await render(Chat, { status: KEYLESS });
  try {
    const box = ui.container.querySelector('input[type="checkbox"]');
    await act(async () => { box.click(); }); // explicit, per-request consent
    assert.equal(box.checked, true);
    setNativeValue(ui.container.querySelector('textarea.chat-input'), 'رسالة موافقة');
    await act(async () => { sendButton(ui.container).click(); });
    assert.equal(calls.length, 1, 'exactly one request');
    const body = JSON.parse(calls[0].init.body);
    assert.equal(body.allow_public_llm, true, 'consent must reach the request body');
    assert.equal(body.message, 'رسالة موافقة');
    await act(async () => {}); // let the response settle
    const boxAfter = ui.container.querySelector('input[type="checkbox"]');
    assert.equal(boxAfter.checked, false, 'consent must not persist after the send');
  } finally { await teardown(ui); }
});

test('SEC-06: no warning, no checkbox and no consent flag for the keyed provider (openrouter) — stale consent is never sent', async () => {
  const ui = await render(Chat, { status: OPENROUTER });
  try {
    assert.ok(!ui.container.textContent.includes('مزود LLM عام خارجي'), 'no false public-provider claim');
    assert.equal(ui.container.querySelectorAll('input[type="checkbox"]').length, 0);
    setNativeValue(ui.container.querySelector('textarea.chat-input'), 'رسالة عادية');
    await act(async () => { sendButton(ui.container).click(); });
    assert.equal(calls.length, 1);
    assert.equal(JSON.parse(calls[0].init.body).allow_public_llm, false, 'stale consent must not be sent');
  } finally { await teardown(ui); }
});

test('SEC-06: provider none — no warning, no consent flag in the request', async () => {
  const ui = await render(Chat, { status: NONE });
  try {
    assert.ok(!ui.container.textContent.includes('مزود LLM عام خارجي'));
    setNativeValue(ui.container.querySelector('textarea.chat-input'), 'رسالة');
    await act(async () => { sendButton(ui.container).click(); });
    assert.equal(calls.length, 1);
    assert.equal(JSON.parse(calls[0].init.body).allow_public_llm, false);
  } finally { await teardown(ui); }
});

test('SEC-06: Specializations — task form blocks submission without consent, carries consent when given, clears it after', async () => {
  const ui = await render(Specializations, { status: KEYLESS });
  try {
    // wait for the async specializations load to settle (the fetch stub
    // resolves across a few microtask turns)
    let card = null;
    for (let i = 0; i < 20 && !card; i++) {
      await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
      card = ui.container.querySelector('.spec-card');
    }
    assert.ok(card, 'specialization cards missing');
    await act(async () => { card.click(); });
    const ta = ui.container.querySelector('textarea.form-input');
    assert.ok(ta, 'task prompt textarea missing');
    const submit = [...ui.container.querySelectorAll('button')].find((b) => b.textContent.trim() === 'إرسال المهمة');
    assert.ok(submit, 'submit button missing');
    assert.ok(ui.container.textContent.includes('تنبيه: مزود LLM عام خارجي'), 'warning missing');
    const box = ui.container.querySelector('input[type="checkbox"]');
    assert.ok(box && box.checked === false);

    // the mount-time /api/specializations load is not a task submission —
    // count only /api/tasks calls from here on
    calls.length = 0;
    setNativeValue(ta, 'مهمة اختبار');
    await act(async () => { submit.click(); });
    assert.equal(calls.length, 0, 'task must not be submitted without consent');
    assert.ok(refusalShown(ui.container), 'refusal reason must be shown');

    await act(async () => { box.click(); });
    setNativeValue(ta, 'مهمة موافقة');
    await act(async () => { submit.click(); });
    assert.equal(calls.filter((c) => c.url.includes('/api/tasks')).length, 1);
    const taskCall = calls.find((c) => c.url.includes('/api/tasks'));
    const body = JSON.parse(taskCall.init.body);
    assert.equal(body.allow_public_llm, true);
    assert.equal(body.prompt, 'مهمة موافقة');
    await act(async () => {});
    const boxAfter = ui.container.querySelector('input[type="checkbox"]');
    assert.equal(boxAfter.checked, false, 'consent must not persist after the task');
  } finally { await teardown(ui); }
});

// cleanup the transpiled temp files (evidence hygiene: nothing is left behind)
test('cleanup: transpiled temp files removed', () => {
  rmSync(tmp, { recursive: true, force: true });
  assert.ok(true);
});
