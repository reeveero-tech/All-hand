// R-101: the server may run with AGOS_API_KEY (authenticated mode — since
// R-100/D-044 the recommended way to enable test_runner). Write routes then
// require the X-API-Key header. The key is typed by the user in the UI and kept
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

function authMessage(status, hasKey) {
  if (status === 401) return 'هذا الخادم يتطلب مفتاح API — أدخله في حقل المفتاح أعلى الصفحة';
  return hasKey
    ? 'مفتاح API غير صحيح — صحّحه في حقل المفتاح أعلى الصفحة'
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
      throw new ApiError(authMessage(res.status, Boolean(key)), res.status);
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

// EventSource cannot send headers; /api/events/stream is a read route that
// does not require the key (tests/test_r101_web_auth_contract.py).
export function createEventSource() {
  return new EventSource(`${API_BASE}/api/events/stream`);
}
