// R-204 (D-153): the signed-in user's session for /api/v2 (accounts,
// workspaces, members, invitations). Rules, each pinned by web/tests/session.test.mjs:
//  - the access token lives in this module's memory ONLY — never in
//    localStorage/sessionStorage, never in a URL. A page reload restores it
//    through POST /api/v2/auth/refresh, which reads the HttpOnly refresh cookie
//    the browser keeps (script code never sees the refresh token);
//  - a 401 triggers at most ONE refresh and one retry (no loop); a failed
//    refresh ends the local session;
//  - sign-in with Google/GitHub uses PKCE: the verifier waits in
//    sessionStorage for the redirect back and is removed when used;
//  - tokens Supabase puts in the URL after an e-mail confirmation are NOT
//    adopted: that session is ended on the server, the URL is cleaned, and the
//    user signs in normally;
//  - an invitation link (#invite=<token>) is kept for this tab only until the
//    user accepts it, and removed from the address bar at once.
// The v1 X-API-Key flow (api.js) is untouched: it is not a v2 identity.
const API_BASE = (import.meta.env && import.meta.env.VITE_API_BASE) || '';
export const OAUTH_VERIFIER_KEY = 'agos_oauth_verifier';
export const PENDING_INVITE_KEY = 'agos_pending_invite';
export const WORKSPACE_KEY = 'agos_workspace';
const REFRESH_MARGIN_MS = 30 * 1000;

function store(kind) {
  try {
    return globalThis[kind] || null;
  } catch {
    return null; // storage disabled (privacy mode / sandboxed iframe)
  }
}

export class AccountError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.name = 'AccountError';
    this.status = status;
    this.detail = detail;
  }
}

const STATUS_MESSAGES = {
  400: 'طلب غير صالح',
  401: 'انتهت الجلسة — سجّل الدخول مجددًا',
  403: 'لا تملك صلاحية هذه العملية',
  404: 'غير موجود، أو لست عضوًا في هذه المساحة',
  409: 'تعارض مع الحالة الحالية',
  410: 'الدعوة منتهية أو ملغاة',
  422: 'مدخل غير صالح',
  429: 'محاولات كثيرة — انتظر قليلًا ثم أعد المحاولة',
  502: 'خطأ من خدمة المصادقة',
  503: 'خدمة الحسابات غير متاحة الآن',
};

export function messageFor(status, detail) {
  return STATUS_MESSAGES[status] || (detail ? String(detail) : `HTTP ${status}`);
}

// ------------------------------------------------------------ session state
let session = null; // { access_token, expires_at, user: { id, email } }
let refreshing = null;
const listeners = new Set();

function setSession(data) {
  if (!session && !(data && data.access_token)) return; // already signed out: nothing changes
  session = data && data.access_token
    ? { access_token: data.access_token, expires_at: data.expires_at || null, user: data.user || {} }
    : null;
  for (const fn of listeners) {
    try { fn(getSession()); } catch { /* a listener error must not break sign-in */ }
  }
}

// what the UI may see: the user, never the token itself
export function getSession() {
  return session ? { user: { ...session.user }, expires_at: session.expires_at } : null;
}

export function onSessionChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

async function request(path, { method = 'GET', body, auth = true, token, retry = true } = {}) {
  if (auth && retry && session && session.expires_at &&
      session.expires_at * 1000 - Date.now() < REFRESH_MARGIN_MS) {
    await refreshSession();
  }
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const bearer = token || (auth && session ? session.access_token : null);
  if (bearer) headers.Authorization = `Bearer ${bearer}`;
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: 'same-origin',
    cache: 'no-store',
  });
  if (res.status === 401 && auth && !token && retry && session) {
    if (await refreshSession()) return request(path, { method, body, auth, retry: false });
  }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401 && auth && !token) setSession(null);
    throw new AccountError(messageFor(res.status, data.detail), res.status, data.detail);
  }
  return data;
}

// one refresh at a time; true = a new access token is in memory
export function refreshSession() {
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const data = await request('/api/v2/auth/refresh', { method: 'POST', auth: false });
        setSession(data);
        return true;
      } catch {
        setSession(null);
        return false;
      } finally {
        refreshing = null;
      }
    })();
  }
  return refreshing;
}

// ---------------------------------------------------------------- sign-in
export const authConfig = () => request('/api/v2/auth/config', { auth: false });

export async function signUp(email, password) {
  const out = await request('/api/v2/auth/signup', { method: 'POST', body: { email, password }, auth: false });
  if (out && out.session) setSession(out.session);
  return { confirmationRequired: Boolean(out && out.confirmation_required) };
}

export async function signIn(email, password) {
  try {
    setSession(await request('/api/v2/auth/login', { method: 'POST', body: { email, password }, auth: false }));
  } catch (e) {
    if (e.status === 401) throw new AccountError('البريد أو كلمة المرور غير صحيحة', 401, e.detail);
    if (e.status === 403) throw new AccountError('البريد لم يُؤكَّد بعد — افتح رابط التأكيد المرسل إلى بريدك', 403, e.detail);
    throw e;
  }
  return getSession();
}

// The local session always ends; an error is re-thrown when the server could
// not confirm that the session was ended there.
export async function signOut() {
  const token = session ? session.access_token : null;
  setSession(null);
  if (!token) return;
  await request('/api/v2/auth/logout', { method: 'POST', token, auth: false });
}

function base64url(bytes) {
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export async function pkcePair() {
  const raw = new Uint8Array(32);
  globalThis.crypto.getRandomValues(raw);
  const verifier = base64url(raw); // 43 chars
  const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
  return { verifier, challenge: base64url(new Uint8Array(digest)) };
}

// returns the provider URL to navigate to; the verifier waits in sessionStorage
export async function oauthUrl(provider, config, redirectTo) {
  if (!config || !config.authorize_url || !(config.providers || {})[provider]) {
    throw new AccountError('الدخول عبر هذا المزود غير مفعل على الخادم', 400);
  }
  const s = store('sessionStorage');
  if (!s) throw new AccountError('تخزين الجلسة معطل في المتصفح — لا يمكن إكمال الدخول عبر المزود', 400);
  const { verifier, challenge } = await pkcePair();
  s.setItem(OAUTH_VERIFIER_KEY, verifier);
  const q = new URLSearchParams({
    provider, code_challenge: challenge, code_challenge_method: 's256', redirect_to: redirectTo,
  });
  return `${config.authorize_url}?${q}`;
}

// Reads what Supabase / an invitation link left in the address bar, acts on
// it, and removes it from the URL. Returns what happened, for the UI.
export async function completeFromUrl(loc = globalThis.location, hist = globalThis.history) {
  const out = { oauth: null, confirmed: false, invite: false, error: null };
  if (!loc) return out;
  const query = new URLSearchParams(loc.search || '');
  const hash = new URLSearchParams((loc.hash || '').replace(/^#/, ''));
  let touched = false;

  const invite = hash.get('invite');
  if (invite) {
    const s = store('sessionStorage');
    if (s) s.setItem(PENDING_INVITE_KEY, invite);
    out.invite = true;
    touched = true;
  }
  const err = query.get('error_description') || hash.get('error_description') || query.get('error') || hash.get('error');
  if (err) {
    out.error = err;
    touched = true;
  }
  // implicit-flow tokens (e-mail confirmation): end that session, never adopt it
  const implicit = hash.get('access_token');
  if (implicit) {
    out.confirmed = ['signup', 'email', 'email_change'].includes(hash.get('type') || 'signup');
    touched = true;
    try { await request('/api/v2/auth/logout', { method: 'POST', token: implicit, auth: false }); } catch { /* best effort */ }
  }
  const code = query.get('code');
  if (code) {
    touched = true;
    const s = store('sessionStorage');
    const verifier = s ? s.getItem(OAUTH_VERIFIER_KEY) : null;
    if (s) s.removeItem(OAUTH_VERIFIER_KEY);
    if (!verifier) {
      out.oauth = 'failed';
      out.error = out.error || 'انتهت محاولة الدخول عبر المزود — ابدأ من جديد';
    } else {
      try {
        setSession(await request('/api/v2/auth/oauth/exchange', {
          method: 'POST', body: { auth_code: code, code_verifier: verifier }, auth: false,
        }));
        out.oauth = 'ok';
      } catch (e) {
        out.oauth = 'failed';
        out.error = e.message;
      }
    }
  }
  if (touched && hist && typeof hist.replaceState === 'function') {
    hist.replaceState(null, '', loc.pathname || '/');
  }
  return out;
}

export function pendingInvite() {
  const s = store('sessionStorage');
  return (s && s.getItem(PENDING_INVITE_KEY)) || null;
}

export function clearPendingInvite() {
  const s = store('sessionStorage');
  if (s) s.removeItem(PENDING_INVITE_KEY);
}

// the chosen workspace id is a preference, not a secret
export function savedWorkspace() {
  const s = store('localStorage');
  return (s && s.getItem(WORKSPACE_KEY)) || null;
}

export function saveWorkspace(id) {
  const s = store('localStorage');
  if (!s) return;
  if (id) s.setItem(WORKSPACE_KEY, id);
  else s.removeItem(WORKSPACE_KEY);
}

// ------------------------------------------------------------- workspaces
const enc = encodeURIComponent;
export const fetchMe = () => request('/api/v2/me');
export const listWorkspaces = () => request('/api/v2/workspaces');
export const createWorkspace = (name) => request('/api/v2/workspaces', { method: 'POST', body: { name } });
export const renameWorkspace = (ws, name) => request(`/api/v2/workspaces/${enc(ws)}`, { method: 'PATCH', body: { name } });
export const deleteWorkspace = (ws) => request(`/api/v2/workspaces/${enc(ws)}`, { method: 'DELETE' });
export const listMembers = (ws) => request(`/api/v2/workspaces/${enc(ws)}/members`);
export const setMemberRole = (ws, user, role) =>
  request(`/api/v2/workspaces/${enc(ws)}/members/${enc(user)}`, { method: 'PATCH', body: { role } });
export const removeMember = (ws, user) =>
  request(`/api/v2/workspaces/${enc(ws)}/members/${enc(user)}`, { method: 'DELETE' });
export const listInvitations = (ws) => request(`/api/v2/workspaces/${enc(ws)}/invitations`);
export const createInvitation = (ws, email, role, ttlHours) =>
  request(`/api/v2/workspaces/${enc(ws)}/invitations`, {
    method: 'POST', body: ttlHours ? { email, role, ttl_hours: ttlHours } : { email, role },
  });
export const revokeInvitation = (ws, id) =>
  request(`/api/v2/workspaces/${enc(ws)}/invitations/${enc(id)}`, { method: 'DELETE' });
export const acceptInvitation = (token) => request('/api/v2/invitations/accept', { method: 'POST', body: { token } });

// ------------------------------------------------- R-205: provider keys (BYOK)
// The key travels ONCE, in the body of the add/replace request (HTTPS); it is
// never kept here, never put in storage or a URL, and the server never sends
// it back (only last4). Pinned by web/tests/provider_keys_ui.test.mjs.
export const listKeyProviders = () => request('/api/v2/provider-keys/providers');
export const listProviderKeys = (ws) => request(`/api/v2/workspaces/${enc(ws)}/provider-keys`);
export const addProviderKey = (ws, body) =>
  request(`/api/v2/workspaces/${enc(ws)}/provider-keys`, { method: 'POST', body });
export const editProviderKey = (ws, id, body) =>
  request(`/api/v2/workspaces/${enc(ws)}/provider-keys/${enc(id)}`, { method: 'PATCH', body });
export const revokeProviderKey = (ws, id) =>
  request(`/api/v2/workspaces/${enc(ws)}/provider-keys/${enc(id)}/revoke`, { method: 'POST' });
export const deleteProviderKey = (ws, id) =>
  request(`/api/v2/workspaces/${enc(ws)}/provider-keys/${enc(id)}`, { method: 'DELETE' });
export const testProviderKey = (ws, id) =>
  request(`/api/v2/workspaces/${enc(ws)}/provider-keys/${enc(id)}/test`, { method: 'POST' });

// tests only: forget the in-memory session without calling the server
export function _resetForTests() {
  session = null;
  refreshing = null;
  listeners.clear();
}
