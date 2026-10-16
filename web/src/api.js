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
export const createTask = (specialization, prompt, data) =>
  api('/api/tasks', { method: 'POST', body: JSON.stringify({ specialization, prompt, data }) });
export const sendChat = (message) =>
  api('/api/chat', { method: 'POST', body: JSON.stringify({ message }) });
export const registerExternalAgent = (name, url, model, apiKey) =>
  api('/api/agents/external', { method: 'POST', body: JSON.stringify({ name, url, model, api_key: apiKey }) });
export const removeExternalAgent = (name) =>
  api(`/api/agents/external/${encodeURIComponent(name)}`, { method: 'DELETE' });

// SEC-02: /api/events/stream carries tenant events, so it needs X-API-Key.
// EventSource cannot send headers, so the stream is read with fetch and parsed
// here ("data: <json>" lines). Without a key nothing is opened. Reconnects
// after `retryMs` like EventSource does; close() stops it for good.
export function openEventStream(onEvent, { retryMs = 5000 } = {}) {
  let closed = false;
  let controller = null;
  let timer = null;

  const connect = async () => {
    const key = getApiKey();
    if (closed || !key) return;
    controller = new AbortController();
    try {
      const res = await fetch(`${API_BASE}/api/events/stream`, {
        headers: { 'X-API-Key': key, Accept: 'text/event-stream' },
        signal: controller.signal,
      });
      if (!res.ok || !res.body) {
        if (res.status === 401 || res.status === 403) return; // wrong key: wait for the user
        throw new Error(`HTTP ${res.status}`);
      }
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
          const data = block.split('\n').filter((l) => l.startsWith('data: ')).map((l) => l.slice(6)).join('\n');
          if (!data) continue;
          try { onEvent(JSON.parse(data)); } catch { /* ignore malformed event */ }
        }
      }
    } catch {
      /* network error or abort — retry below unless closed */
    }
    if (!closed) timer = setTimeout(connect, retryMs);
  };

  connect();
  return {
    close() {
      closed = true;
      if (timer) clearTimeout(timer);
      if (controller) controller.abort();
    },
  };
}
