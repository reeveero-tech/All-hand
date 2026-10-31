// R-101: behaviour of the real web/src/api.js (no bundler): X-API-Key handling,
// header merging and 401/403 messages. Run: cd web && npm test
import { test, beforeEach, afterEach } from 'node:test';
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
  // SEC-06 (R-145): allow_public_llm defaults to false — an absent consent is refusal
  assert.deepEqual(JSON.parse(calls[0].init.body), { specialization: 'analyst', prompt: 'hello', allow_public_llm: false });
  assert.deepEqual(JSON.parse(calls[1].init.body), { message: 'hi', allow_public_llm: false });
});

test('SEC-06: explicit consent is sent only when the caller passes it (per request)', async () => {
  // the UI passes true only after the user checks the external-provider warning;
  // the value is never persisted and applies to exactly one request
  await api.createTask('analyst', 'hello', null, true);
  await api.sendChat('hi', true);
  assert.equal(JSON.parse(calls[0].init.body).allow_public_llm, true);
  assert.equal(JSON.parse(calls[1].init.body).allow_public_llm, true);
  // the next call without the flag is refusal again — consent never sticks
  await api.sendChat('again');
  assert.equal(JSON.parse(calls[2].init.body).allow_public_llm, false);
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

// ---------------------------------------------------------------- SEC-04 (R-144)
// openEventStream reconnection policy, with a recording scheduler: no real
// timers — each scheduled reconnect is run by the test.
// afterEach always restores fetch and closes the stream, even when an
// assertion fails mid-test (no leak into later tests).
let harnessCleanup = [];
afterEach(() => {
  for (const fn of harnessCleanup.splice(0)) fn();
});

function streamHarness(responses) {
  const delays = [];
  const pending = [];
  const prev = globalThis.fetch;
  let n = 0;
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    const r = responses[Math.min(n++, responses.length - 1)];
    if (r === 'network') throw new TypeError('network down');
    const headers = new Map(Object.entries(r.headers || {}));
    const enc = new TextEncoder();
    const chunks = r.chunks || [];
    let i = 0;
    return {
      ok: r.status >= 200 && r.status < 300, status: r.status,
      headers: { get: (k) => headers.get(k) ?? null },
      body: r.status === 200 ? { getReader: () => ({ read: async () => (i < chunks.length
        ? { value: enc.encode(chunks[i++]), done: false } : { value: undefined, done: true }) }) } : null,
    };
  };
  const schedule = (fn, ms) => { delays.push(ms); pending.push(fn); return pending.length; };
  const tick = () => new Promise((r) => setTimeout(r, 5));
  const runNext = async () => { const fn = pending.shift(); await fn(); await tick(); };
  const restore = () => { globalThis.fetch = prev; };
  harnessCleanup.push(restore);
  const track = (stream) => { harnessCleanup.unshift(() => stream.close()); return stream; };
  return { delays, pending, schedule, cancel: () => {}, tick, runNext, restore, track };
}

test('SEC-04: 429 waits Retry-After, never a fast retry', async () => {
  api.setApiKey('k');
  const h = streamHarness([{ status: 429, headers: { 'Retry-After': '120' } }]);
  const s = h.track(api.openEventStream(() => {}, { retryMs: 5000, schedule: h.schedule, cancel: h.cancel }));
  await h.tick();
  assert.equal(calls.length, 1);
  assert.deepEqual(h.delays, [120000]);           // Retry-After (s) honoured
  s.close(); h.restore();
});

test('SEC-04: repeated 429 without Retry-After backs off exponentially, capped', async () => {
  api.setApiKey('k');
  const h = streamHarness([{ status: 429 }]);
  const s = h.track(api.openEventStream(() => {}, { retryMs: 1000, maxRetryMs: 8000, schedule: h.schedule, cancel: h.cancel }));
  await h.tick();
  for (let i = 0; i < 5; i++) await h.runNext();
  assert.deepEqual(h.delays, [1000, 2000, 4000, 8000, 8000, 8000]);
  assert.equal(calls.length, 6);
  s.close(); h.restore();
});

test('SEC-04: short Retry-After never undercuts the backoff', async () => {
  api.setApiKey('k');
  const h = streamHarness([{ status: 429, headers: { 'Retry-After': '1' } }]);
  const s = h.track(api.openEventStream(() => {}, { retryMs: 5000, schedule: h.schedule, cancel: h.cancel }));
  await h.tick();
  await h.runNext();
  assert.deepEqual(h.delays, [5000, 10000]);
  s.close(); h.restore();
});

test('SEC-04: 401 / 403 stop reconnecting (no loop on a rejected key)', async () => {
  for (const status of [401, 403]) {
    calls.length = 0;
    api.setApiKey('k');
    const h = streamHarness([{ status }]);
    const s = h.track(api.openEventStream(() => {}, { schedule: h.schedule, cancel: h.cancel }));
    await h.tick();
    assert.equal(calls.length, 1);
    assert.deepEqual(h.delays, []);
    s.close(); h.restore();
  }
});

test('SEC-04: network errors back off; an accepted stream that ends (max lifetime) reconnects after retryMs and resets', async () => {
  api.setApiKey('k');
  const h = streamHarness(['network', 'network',
    { status: 200, chunks: ['data: {"type":"task_created","task_id":"t1"}\n\n'] },
    { status: 200, chunks: [] }]);
  const got = [];
  const s = h.track(api.openEventStream((e) => got.push(e), { retryMs: 1000, schedule: h.schedule, cancel: h.cancel }));
  await h.tick();
  await h.runNext();   // 2nd network error
  await h.runNext();   // accepted, one event, then the server ends the stream
  await h.runNext();   // accepted again, ends
  assert.deepEqual(h.delays, [1000, 2000, 1000, 1000]);
  assert.deepEqual(got, [{ type: 'task_created', task_id: 't1' }]);
  s.close(); h.restore();
});

test('SEC-04: stream_gap is delivered like any event; close() cancels the pending reconnect', async () => {
  api.setApiKey('k');
  const h = streamHarness([{ status: 200, chunks: ['data: {"type":"stream_gap","missed":3,"recover":["/api/tasks","/api/events"]}\n\n'] }]);
  const got = [];
  const s = h.track(api.openEventStream((e) => got.push(e), { retryMs: 1000, schedule: h.schedule, cancel: h.cancel }));
  await h.tick();
  assert.deepEqual(got, [{ type: 'stream_gap', missed: 3, recover: ['/api/tasks', '/api/events'] }]);
  s.close();
  await h.runNext();   // a reconnect that fires after close() does nothing
  assert.equal(calls.length, 1);
  h.restore();
});

test('G-160-5: the last SSE id is sent back as Last-Event-ID on reconnect', async () => {
  api.setApiKey('k');
  const h = streamHarness([
    { status: 200, chunks: ['id: 0a1b2c3d-1-1\ndata: {"type":"task_created","task_id":"t1"}\n\n',
      'data: {"type":"heartbeat"}\n\n',
      'id: 0a1b2c3d-1-2\ndata: {"type":"task_created","task_id":"t2"}\n\n'] },
    { status: 200, chunks: ['data: {"type":"stream_reset","reason":"unknown_last_event_id","recover":["/api/tasks","/api/events"]}\n\n'] },
    { status: 200, chunks: [] }]);
  const got = [];
  const s = h.track(api.openEventStream((e) => got.push(e), { retryMs: 1000, schedule: h.schedule, cancel: h.cancel }));
  await h.tick();
  assert.equal(calls[0].init.headers['Last-Event-ID'], undefined); // first connect: none
  await h.runNext();
  assert.equal(calls[1].init.headers['Last-Event-ID'], '0a1b2c3d-1-2'); // heartbeat did not move it
  await h.runNext();
  assert.equal(calls[2].init.headers['Last-Event-ID'], '0a1b2c3d-1-2'); // reset frame has no id
  assert.deepEqual(got.map((e) => e.type), ['task_created', 'heartbeat', 'task_created', 'stream_reset']);
  s.close(); h.restore();
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
