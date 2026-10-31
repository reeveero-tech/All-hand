// R-101: the server may run with AGOS_API_KEY (authenticated mode — since
// R-100/D-044 the recommended way to enable test_runner). Write routes then
// require the X-API-Key header. SEC-02 (R-138): the server is multi-tenant —
// every route with tenant data (tasks, events, memory, the live stream) needs
// the key, which also decides whose data is shown. The key is typed by the user in the UI and kept
// only in sessionStorage for this tab: never bundled into the build, never in
// the repository, gone when the tab closes.
const API_BASE = (import.meta.env && import.meta.env.VITE_API_BASE) || '';
export const API_KEY_STORAGE = 'agos_api_key';
export const AUTH_EVENT = 'agos-auth-required';

function storage() {
  try {
    return globalThis.sessionStorage || null;
  } catch {
    return null; // storage disabled (privacy mode / sandboxed iframe)
  }
}

export function getApiKey() {
  const s = storage();
  return (s && s.getItem(API_KEY_STORAGE)) || '';
}

export function setApiKey(key) {
  const s = storage();
  if (!s) return false;
  const v = (key || '').trim();
  if (v) s.setItem(API_KEY_STORAGE, v);
  else s.removeItem(API_KEY_STORAGE);
  return true;
}

export function clearApiKey() {
  setApiKey('');
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

function authMessage(status, hasKey, detail) {
  if (status === 401 && String(detail || '').startsWith('authentication is not configured')) {
    return 'المصادقة غير مهيأة على هذا الخادم — لا تُعرض البيانات ولا تُنشأ المهام حتى يضبط المشغّل المفاتيح';
  }
  // SEC-02: a wrong key is 401 too (no valid identity); 403 = valid key, role not allowed.
  if (status === 401) {
    return hasKey
      ? 'مفتاح API غير صحيح — صحّحه في حقل المفتاح أعلى الصفحة'
      : 'هذا الخادم يتطلب مفتاح API — أدخله في حقل المفتاح أعلى الصفحة';
  }
  return hasKey
    ? 'هذه العملية تتطلب صلاحية مدير — مفتاحك لا يملكها'
    : 'الوصول مرفوض — أدخل مفتاح API في حقل المفتاح أعلى الصفحة';
}

export async function api(path, options = {}) {
  const key = getApiKey();
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (key) headers['X-API-Key'] = key;
  // headers last so a caller's options.headers cannot drop the merged set.
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    if (res.status === 401 || res.status === 403) {
      if (typeof globalThis.dispatchEvent === 'function' && typeof globalThis.CustomEvent === 'function') {
        globalThis.dispatchEvent(new CustomEvent(AUTH_EVENT, { detail: { status: res.status } }));
      }
      throw new ApiError(authMessage(res.status, Boolean(key), err.detail), res.status);
    }
    throw new ApiError(err.detail || `HTTP ${res.status}`, res.status);
  }
  return res.json();
}

export const fetchSystemStatus = () => api('/api/system/status');
export const fetchSpecializations = () => api('/api/specializations');
export const fetchAgents = (spec) => api(`/api/agents${spec ? `?specialization=${spec}` : ''}`);
export const fetchTasks = (limit = 50) => api(`/api/tasks?limit=${limit}`);
export const fetchEvents = (limit = 50) => api(`/api/events?limit=${limit}`);
// SEC-06 (R-145): allow_public_llm is the explicit per-request consent to the
// public (keyless) LLM provider. Default false (absent = refusal); the UI only
// sends it after the user confirms the external-data warning, and never
// persists it — it applies to this one request only.
export const createTask = (specialization, prompt, data, allowPublicLlm = false) =>
  api('/api/tasks', { method: 'POST', body: JSON.stringify({ specialization, prompt, data, allow_public_llm: allowPublicLlm }) });
export const sendChat = (message, allowPublicLlm = false) =>
  api('/api/chat', { method: 'POST', body: JSON.stringify({ message, allow_public_llm: allowPublicLlm }) });
export const registerExternalAgent = (name, url, model, apiKey) =>
  api('/api/agents/external', { method: 'POST', body: JSON.stringify({ name, url, model, api_key: apiKey }) });
export const removeExternalAgent = (name) =>
  api(`/api/agents/external/${encodeURIComponent(name)}`, { method: 'DELETE' });

// SEC-02: /api/events/stream carries tenant events, so it needs X-API-Key.
// EventSource cannot send headers, so the stream is read with fetch and parsed
// here ("data: <json>" lines). Without a key nothing is opened. close() stops
// it for good.
// SEC-04 (R-144) reconnection:
//  - 401/403: stop (wrong key / no role) — wait for the user, never loop;
//  - 429: wait Retry-After (seconds, capped at 1 h) — at least the backoff;
//  - other HTTP errors / network errors: exponential backoff from `retryMs`
//    (x2 per consecutive failure, capped at `maxRetryMs`);
//  - a stream that was accepted (200) and then ended — e.g. the server's max
//    lifetime — reconnects after `retryMs` and resets the backoff.
// The server may send {"type": "stream_gap", "missed": n} when this listener
// fell behind: events were dropped; re-read state from /api/tasks, /api/events.
// G-160-5 (D-109): bus events carry an SSE `id:`; the last one seen is sent
// back as `Last-Event-ID` on every reconnect (fetch does not do it for us,
// EventSource would), so the server replays what was missed. When the server
// cannot place that id it sends {"type": "stream_reset"}: re-read state.
const MAX_RETRY_AFTER_MS = 3600 * 1000;

function retryAfterMs(res) {
  const raw = res.headers && typeof res.headers.get === 'function' ? res.headers.get('Retry-After') : null;
  if (!raw) return 0;
  const secs = Number(raw);
  let ms = Number.isFinite(secs) ? secs * 1000 : Date.parse(raw) - Date.now();
  if (!Number.isFinite(ms) || ms < 0) ms = 0;
  return Math.min(ms, MAX_RETRY_AFTER_MS);
}

export function openEventStream(onEvent, {
  retryMs = 5000, maxRetryMs = 5 * 60 * 1000, schedule = setTimeout, cancel = clearTimeout,
} = {}) {
  let closed = false;
  let controller = null;
  let timer = null;
  let failures = 0; // consecutive attempts that were not accepted
  let lastEventId = null; // G-160-5: the last bus event id seen on any connection

  const backoff = () => Math.min(retryMs * 2 ** failures, maxRetryMs);

  const connect = async () => {
    const key = getApiKey();
    if (closed || !key) return;
    controller = new AbortController();
    let delay;
    try {
      const headers = { 'X-API-Key': key, Accept: 'text/event-stream' };
      if (lastEventId) headers['Last-Event-ID'] = lastEventId;
      const res = await fetch(`${API_BASE}/api/events/stream`, {
        headers,
        signal: controller.signal,
      });
      if (!res.ok || !res.body) {
        if (res.status === 401 || res.status === 403) return; // wrong key: wait for the user
        delay = res.status === 429 ? Math.max(retryAfterMs(res), backoff()) : backoff();
        failures += 1;
        throw new Error(`HTTP ${res.status}`);
      }
      failures = 0; // accepted
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const block = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const lines = block.split('\n');
          const data = lines.filter((l) => l.startsWith('data: ')).map((l) => l.slice(6)).join('\n');
          const idLine = lines.find((l) => l.startsWith('id: '));
          if (idLine) lastEventId = idLine.slice(4);
          if (!data) continue;
          try { onEvent(JSON.parse(data)); } catch { /* ignore malformed event */ }
        }
      }
      delay = retryMs; // stream ended after being accepted (e.g. max lifetime)
    } catch {
      /* network error or abort — retry below unless closed */
      if (delay === undefined) {
        delay = backoff();
        failures += 1;
      }
    }
    if (!closed) timer = schedule(connect, delay);
  };

  connect();
  return {
    close() {
      closed = true;
      if (timer) cancel(timer);
      if (controller) controller.abort();
    },
  };
}
