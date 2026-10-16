// R-101: behaviour of the real web/src/api.js (no bundler): X-API-Key handling,
// header merging and 401/403 messages. Run: cd web && npm test
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

class MemoryStorage {
  constructor() { this.m = new Map(); }
  getItem(k) { return this.m.has(k) ? this.m.get(k) : null; }
  setItem(k, v) { this.m.set(k, String(v)); }
  removeItem(k) { this.m.delete(k); }
}

const calls = [];
let nextResponse = { status: 200, body: { ok: true } };
const events = [];

globalThis.sessionStorage = new MemoryStorage();
globalThis.fetch = async (url, init) => {
  calls.push({ url, init });
  const { status, body } = nextResponse;
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: `status ${status}`,
    json: async () => body,
  };
};
// Browser `window` is an EventTarget; Node's globalThis is not, so give it one.
const target = new EventTarget();
globalThis.addEventListener = target.addEventListener.bind(target);
globalThis.dispatchEvent = target.dispatchEvent.bind(target);
globalThis.addEventListener('agos-auth-required', (e) => events.push(e.detail));

const api = await import('../src/api.js');

beforeEach(() => {
  calls.length = 0;
  events.length = 0;
  nextResponse = { status: 200, body: { ok: true } };
  globalThis.sessionStorage = new MemoryStorage();
});

test('open mode: no key stored -> no X-API-Key header (behaviour unchanged)', async () => {
  await api.fetchSystemStatus();
  assert.equal(calls[0].url, '/api/system/status');
  assert.equal(calls[0].init.headers['X-API-Key'], undefined);
  assert.equal(calls[0].init.headers['Content-Type'], 'application/json');
});

test('stored key is sent on every call, write and read', async () => {
  assert.equal(api.setApiKey('  secret-123  '), true);
  assert.equal(api.getApiKey(), 'secret-123');
  await api.createTask('analyst', 'hello');
  await api.sendChat('hi');
  await api.fetchTasks();
  for (const c of calls) assert.equal(c.init.headers['X-API-Key'], 'secret-123');
  assert.equal(calls[0].init.method, 'POST');
  assert.deepEqual(JSON.parse(calls[0].init.body), { specialization: 'analyst', prompt: 'hello' });
});

test('key never appears in the URL or body', async () => {
  api.setApiKey('secret-xyz');
  await api.registerExternalAgent('a', 'https://agent.example.org/x', 'm', 'agent-own-key');
  assert.ok(!calls[0].url.includes('secret-xyz'));
  assert.ok(!calls[0].init.body.includes('secret-xyz'));
  // the external agent's own key is a body field, distinct from the server key
  assert.equal(JSON.parse(calls[0].init.body).api_key, 'agent-own-key');
});

test('caller headers are merged, not allowed to drop Content-Type or the key', async () => {
  api.setApiKey('k1');
  await api.api('/api/tasks', { method: 'POST', headers: { 'X-Trace': 't' }, body: '{}' });
  const h = calls[0].init.headers;
  assert.equal(h['X-Trace'], 't');
  assert.equal(h['Content-Type'], 'application/json');
  assert.equal(h['X-API-Key'], 'k1');
});

test('clearApiKey / empty value removes the key', async () => {
  api.setApiKey('k2');
  api.clearApiKey();
  assert.equal(api.getApiKey(), '');
  api.setApiKey('k3');
  api.setApiKey('   ');
  assert.equal(api.getApiKey(), '');
  await api.fetchEvents();
  assert.equal(calls[0].init.headers['X-API-Key'], undefined);
});

test('401 -> clear Arabic message, status kept, auth event dispatched', async () => {
  nextResponse = { status: 401, body: { detail: 'API key required' } };
  await assert.rejects(api.createTask('analyst', 'x'), (e) => {
    assert.equal(e.name, 'ApiError');
    assert.equal(e.status, 401);
    assert.match(e.message, /يتطلب مفتاح API/);
    return true;
  });
  assert.deepEqual(events, [{ status: 401 }]);
});

test('SEC-02: 401 with a stored key -> "key is wrong" message (server answers 401 for an invalid key)', async () => {
  api.setApiKey('wrong');
  nextResponse = { status: 401, body: { detail: 'Invalid API key' } };
  await assert.rejects(api.sendChat('x'), (e) => e.status === 401 && /غير صحيح/.test(e.message));
  assert.deepEqual(events, [{ status: 401 }]);
});

test('SEC-02: 403 with a valid key -> admin role message', async () => {
  api.setApiKey('user-key');
  nextResponse = { status: 403, body: { detail: 'admin role required' } };
  await assert.rejects(api.registerExternalAgent('n', 'https://x.example', '', ''),
    (e) => e.status === 403 && /صلاحية مدير/.test(e.message));
  assert.deepEqual(events, [{ status: 403 }]);
});

test('SEC-02: server without credentials -> "authentication not configured" message', async () => {
  nextResponse = { status: 401, body: { detail: 'authentication is not configured on this server: x' } };
  await assert.rejects(api.fetchTasks(), (e) => e.status === 401 && /غير مهيأة/.test(e.message));
});

test('SEC-02: the event stream is not opened without a key', async () => {
  const s = api.openEventStream(() => {});
  await new Promise((r) => setTimeout(r, 10));
  s.close();
  assert.equal(calls.length, 0);
});

test('SEC-02: the event stream sends X-API-Key and parses data events', async () => {
  api.setApiKey('tenant-a-key');
  const enc = new TextEncoder();
  const chunks = ['data: {"type":"task_created","task_id":"t1"}\n\nda', 'ta: {"type":"heartbeat"}\n\n'];
  const prev = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    let i = 0;
    return {
      ok: true, status: 200,
      body: { getReader: () => ({ read: async () => (i < chunks.length
        ? { value: enc.encode(chunks[i++]), done: false } : { value: undefined, done: true }) }) },
    };
  };
  const got = [];
  const s = api.openEventStream((e) => got.push(e), { retryMs: 60000 });
  await new Promise((r) => setTimeout(r, 20));
  s.close();
  globalThis.fetch = prev;
  assert.equal(calls[0].url, '/api/events/stream');
  assert.equal(calls[0].init.headers['X-API-Key'], 'tenant-a-key');
  assert.deepEqual(got, [{ type: 'task_created', task_id: 't1' }, { type: 'heartbeat' }]);
});

test('other errors keep the server detail', async () => {
  nextResponse = { status: 400, body: { detail: 'Unknown specialization: nope' } };
  await assert.rejects(api.createTask('nope', 'x'), (e) => e.status === 400 && e.message === 'Unknown specialization: nope');
  assert.equal(events.length, 0);
});

test('external agent names are URL-encoded on delete', async () => {
  await api.removeExternalAgent('a b/c');
  assert.equal(calls[0].url, '/api/agents/external/a%20b%2Fc');
  assert.equal(calls[0].init.method, 'DELETE');
});

test('no sessionStorage (blocked) -> calls still work without a key', async () => {
  globalThis.sessionStorage = undefined;
  assert.equal(api.setApiKey('k'), false);
  assert.equal(api.getApiKey(), '');
  await api.fetchSpecializations();
  assert.equal(calls[0].init.headers['X-API-Key'], undefined);
});
