// R-204 (D-153): behaviour of the real web/src/session.js (no bundler) — where
// the tokens live, refresh/retry, sign-out, PKCE, and what is read from and
// removed from the address bar. Run: cd web && npm test
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

class MemoryStorage {
  constructor() { this.m = new Map(); }
  getItem(k) { return this.m.has(k) ? this.m.get(k) : null; }
  setItem(k, v) { this.m.set(k, String(v)); }
  removeItem(k) { this.m.delete(k); }
  dump() { return [...this.m.values()].join('\n'); }
}

const calls = [];
let responder = () => ({ status: 200, body: {} });
globalThis.fetch = async (url, init = {}) => {
  calls.push({ url: String(url), init });
  const { status, body } = responder(String(url), init, calls.length);
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: `status ${status}`,
    json: async () => (body === undefined ? Promise.reject(new Error('no body')) : body),
  };
};

const s = await import('../src/session.js');

const SESSION = (n = 1, ttl = 3600) => ({
  access_token: `access-${n}`, token_type: 'bearer', expires_in: ttl,
  expires_at: Math.floor(Date.now() / 1000) + ttl, user: { id: 'u1', email: 'a@x.test' },
});
const auth = (c) => (c.init.headers || {}).Authorization;
const paths = () => calls.map((c) => `${c.init.method || 'GET'} ${c.url}`);

beforeEach(() => {
  calls.length = 0;
  responder = () => ({ status: 200, body: {} });
  globalThis.sessionStorage = new MemoryStorage();
  globalThis.localStorage = new MemoryStorage();
  s._resetForTests();
});

async function signedIn(n = 1, ttl = 3600) {
  responder = () => ({ status: 200, body: SESSION(n, ttl) });
  await s.signIn('a@x.test', 'password1');
  calls.length = 0;
}

test('sign-in keeps the access token in memory only — never in storage, never exposed to the UI', async () => {
  await signedIn();
  assert.deepEqual(s.getSession().user, { id: 'u1', email: 'a@x.test' });
  assert.equal('access_token' in s.getSession(), false, 'the UI must not see the token');
  assert.equal(sessionStorage.dump().includes('access-1'), false);
  assert.equal(localStorage.dump().includes('access-1'), false);
  responder = () => ({ status: 200, body: [] });
  await s.listWorkspaces();
  assert.equal(auth(calls[0]), 'Bearer access-1');
  assert.equal(calls[0].url.includes('access-1'), false, 'token never in the URL');
  assert.equal(calls[0].init.credentials, 'same-origin');
  assert.equal(calls[0].init.cache, 'no-store');
});

test('sign-in errors become clear Arabic messages; no session is kept', async () => {
  responder = () => ({ status: 401, body: { detail: 'invalid email or password' } });
  await assert.rejects(s.signIn('a@x.test', 'bad'), (e) => e.status === 401 && /غير صحيحة/.test(e.message));
  responder = () => ({ status: 403, body: { detail: 'email not confirmed' } });
  await assert.rejects(s.signIn('a@x.test', 'pw'), (e) => e.status === 403 && /لم يُؤكَّد/.test(e.message));
  assert.equal(s.getSession(), null);
});

test('sign-up: confirmation pending keeps no session; an immediate session is adopted', async () => {
  responder = () => ({ status: 200, body: { confirmation_required: true, session: null } });
  assert.deepEqual(await s.signUp('n@x.test', 'password1'), { confirmationRequired: true });
  assert.equal(s.getSession(), null);
  assert.equal(JSON.parse(calls[0].init.body).password, 'password1');
  assert.equal(auth(calls[0]), undefined);
  responder = () => ({ status: 200, body: { confirmation_required: false, session: SESSION(7) } });
  await s.signUp('n@x.test', 'password1');
  assert.equal(s.getSession().user.id, 'u1');
});

test('a 401 triggers exactly one refresh and one retry with the new token', async () => {
  await signedIn(1);
  responder = (url, init, n) => {
    if (url.endsWith('/auth/refresh')) return { status: 200, body: SESSION(2) };
    return auth({ init }) === 'Bearer access-1' ? { status: 401, body: { detail: 'session expired' } } : { status: 200, body: [] };
  };
  assert.deepEqual(await s.listWorkspaces(), []);
  assert.deepEqual(paths(), ['GET /api/v2/workspaces', 'POST /api/v2/auth/refresh', 'GET /api/v2/workspaces']);
  assert.equal(auth(calls[2]), 'Bearer access-2');
  assert.equal(auth(calls[1]), undefined, 'refresh uses the HttpOnly cookie, not a bearer');
});

test('a failed refresh ends the local session — no loop', async () => {
  await signedIn(1);
  const seen = [];
  s.onSessionChange((v) => seen.push(v));
  responder = (url) => (url.endsWith('/auth/refresh')
    ? { status: 401, body: { detail: 'session ended' } } : { status: 401, body: { detail: 'session ended' } });
  await assert.rejects(s.listWorkspaces(), (e) => e.status === 401);
  assert.equal(calls.length, 2, 'one request + one refresh, nothing more');
  assert.equal(s.getSession(), null);
  assert.deepEqual(seen, [null]);
});

test('still 401 after a successful refresh -> session ended, no second refresh', async () => {
  await signedIn(1);
  responder = (url) => (url.endsWith('/auth/refresh') ? { status: 200, body: SESSION(2) } : { status: 401, body: {} });
  await assert.rejects(s.fetchMe(), (e) => e.status === 401);
  assert.equal(calls.length, 3);
  assert.equal(s.getSession(), null);
});

test('concurrent 401s share one refresh', async () => {
  await signedIn(1);
  responder = (url, init) => {
    if (url.endsWith('/auth/refresh')) return { status: 200, body: SESSION(2) };
    return auth({ init }) === 'Bearer access-1' ? { status: 401, body: {} } : { status: 200, body: [] };
  };
  await Promise.all([s.listWorkspaces(), s.fetchMe(), s.listMembers('w1')]);
  assert.equal(calls.filter((c) => c.url.endsWith('/auth/refresh')).length, 1);
});

test('a token about to expire is refreshed before the call', async () => {
  await signedIn(1, 10); // expires in 10 s (< 30 s margin)
  responder = (url) => (url.endsWith('/auth/refresh') ? { status: 200, body: SESSION(2) } : { status: 200, body: [] });
  await s.listWorkspaces();
  assert.deepEqual(paths(), ['POST /api/v2/auth/refresh', 'GET /api/v2/workspaces']);
  assert.equal(auth(calls[1]), 'Bearer access-2');
});

test('sign-out ends the server session with the token, then nothing is sent with it', async () => {
  await signedIn(1);
  responder = () => ({ status: 204 });
  await s.signOut();
  assert.deepEqual(paths(), ['POST /api/v2/auth/logout']);
  assert.equal(auth(calls[0]), 'Bearer access-1');
  assert.equal(s.getSession(), null);
  calls.length = 0;
  responder = () => ({ status: 401, body: {} });
  await assert.rejects(s.listWorkspaces());
  assert.equal(auth(calls[0]), undefined, 'no token after sign-out');
});

test('sign-out: the local session ends even when the server cannot confirm', async () => {
  await signedIn(1);
  responder = () => ({ status: 503, body: { detail: 'auth service unavailable; sign-out not confirmed' } });
  await assert.rejects(s.signOut(), (e) => e.status === 503);
  assert.equal(s.getSession(), null);
});

test('PKCE: S256 challenge of a 43-char verifier; URL carries the challenge, never the verifier', async () => {
  const { verifier, challenge } = await s.pkcePair();
  assert.match(verifier, /^[A-Za-z0-9_-]{43}$/);
  assert.equal(challenge, createHash('sha256').update(verifier).digest('base64url'));
  const cfg = { authorize_url: 'https://p.supabase.co/auth/v1/authorize', providers: { google: true, github: false } };
  const url = new URL(await s.oauthUrl('google', cfg, 'https://app.test/'));
  const stored = sessionStorage.getItem(s.OAUTH_VERIFIER_KEY);
  assert.match(stored, /^[A-Za-z0-9_-]{43}$/);
  assert.equal(url.origin + url.pathname, 'https://p.supabase.co/auth/v1/authorize');
  assert.equal(url.searchParams.get('provider'), 'google');
  assert.equal(url.searchParams.get('code_challenge_method'), 's256');
  assert.equal(url.searchParams.get('code_challenge'), createHash('sha256').update(stored).digest('base64url'));
  assert.equal(url.searchParams.get('redirect_to'), 'https://app.test/');
  assert.equal(url.href.includes(stored), false);
  await assert.rejects(s.oauthUrl('github', cfg, 'https://app.test/'), /غير مفعل/);
});

function fakeLocation(href) {
  const u = new URL(href);
  return { search: u.search, hash: u.hash, pathname: u.pathname };
}
function fakeHistory() {
  const h = { replaced: [] };
  h.replaceState = (_s, _t, url) => h.replaced.push(url);
  return h;
}

test('OAuth return: the code is exchanged with the stored verifier, which is then removed; URL cleaned', async () => {
  sessionStorage.setItem(s.OAUTH_VERIFIER_KEY, 'v'.repeat(43));
  responder = () => ({ status: 200, body: SESSION(3) });
  const hist = fakeHistory();
  const r = await s.completeFromUrl(fakeLocation('https://app.test/?code=abc123'), hist);
  assert.equal(r.oauth, 'ok');
  assert.deepEqual(JSON.parse(calls[0].init.body), { auth_code: 'abc123', code_verifier: 'v'.repeat(43) });
  assert.equal(sessionStorage.getItem(s.OAUTH_VERIFIER_KEY), null);
  assert.deepEqual(hist.replaced, ['/']);
  assert.equal(s.getSession().user.id, 'u1');
});

test('OAuth return without a verifier (other tab / replay) is refused locally — nothing sent', async () => {
  const r = await s.completeFromUrl(fakeLocation('https://app.test/?code=abc123'), fakeHistory());
  assert.equal(r.oauth, 'failed');
  assert.equal(calls.length, 0);
  assert.equal(s.getSession(), null);
});

test('e-mail confirmation tokens in the URL are NOT adopted: that session is ended and the URL cleaned', async () => {
  responder = () => ({ status: 204 });
  const hist = fakeHistory();
  const r = await s.completeFromUrl(
    fakeLocation('https://app.test/#access_token=implicit-tok&refresh_token=implicit-rt&type=signup&expires_in=3600'), hist);
  assert.equal(r.confirmed, true);
  assert.equal(s.getSession(), null, 'implicit tokens never become the app session');
  assert.deepEqual(paths(), ['POST /api/v2/auth/logout']);
  assert.equal(auth(calls[0]), 'Bearer implicit-tok');
  assert.equal(sessionStorage.dump().includes('implicit'), false);
  assert.equal(localStorage.dump().includes('implicit'), false);
  assert.deepEqual(hist.replaced, ['/']);
});

test('invitation link: token kept for this tab only, removed from the address bar; accept sends it in the body', async () => {
  const hist = fakeHistory();
  const r = await s.completeFromUrl(fakeLocation('https://app.test/#invite=tok_0123456789abcdefghij'), hist);
  assert.equal(r.invite, true);
  assert.equal(s.pendingInvite(), 'tok_0123456789abcdefghij');
  assert.equal(localStorage.dump().includes('tok_'), false);
  assert.deepEqual(hist.replaced, ['/']);
  assert.equal(calls.length, 0);
  await signedIn(1);
  responder = () => ({ status: 200, body: { workspace_id: 'w9', role: 'member' } });
  await s.acceptInvitation(s.pendingInvite());
  assert.equal(calls[0].url, '/api/v2/invitations/accept');
  assert.equal(calls[0].url.includes('tok_'), false);
  assert.deepEqual(JSON.parse(calls[0].init.body), { token: 'tok_0123456789abcdefghij' });
  s.clearPendingInvite();
  assert.equal(s.pendingInvite(), null);
});

test('error in the provider redirect is reported, URL cleaned, nothing exchanged', async () => {
  const hist = fakeHistory();
  const r = await s.completeFromUrl(fakeLocation('https://app.test/?error=access_denied&error_description=denied'), hist);
  assert.equal(r.error, 'denied');
  assert.equal(calls.length, 0);
  assert.deepEqual(hist.replaced, ['/']);
});

test('a plain URL is left alone', async () => {
  const hist = fakeHistory();
  const r = await s.completeFromUrl(fakeLocation('https://app.test/'), hist);
  assert.deepEqual(r, { oauth: null, confirmed: false, invite: false, error: null });
  assert.deepEqual(hist.replaced, []);
});

test('ids are URL-encoded; API errors map to Arabic messages and keep the server detail', async () => {
  await signedIn(1);
  responder = () => ({ status: 404, body: { detail: 'workspace not found' } });
  await assert.rejects(s.listMembers('../x?y'), (e) => e.status === 404 && /لست عضوًا/.test(e.message) && e.detail === 'workspace not found');
  assert.equal(calls[0].url, '/api/v2/workspaces/..%2Fx%3Fy/members');
  responder = () => ({ status: 410, body: { detail: 'invitation expired' } });
  await assert.rejects(s.acceptInvitation('t'.repeat(30)), (e) => e.status === 410 && /منتهية/.test(e.message));
});

test('no storage (blocked) -> sign-in still works; OAuth refuses clearly', async () => {
  globalThis.sessionStorage = undefined;
  globalThis.localStorage = undefined;
  await signedIn(1);
  assert.equal(s.getSession().user.id, 'u1');
  s.saveWorkspace('w1');
  assert.equal(s.savedWorkspace(), null);
  await assert.rejects(s.oauthUrl('google', { authorize_url: 'https://p/authorize', providers: { google: true } }, '/'), /تخزين الجلسة معطل/);
});
