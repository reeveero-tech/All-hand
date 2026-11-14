import { useState, useEffect, useCallback } from 'react'
import {
  authConfig, signIn, signUp, signOut, oauthUrl, completeFromUrl, refreshSession,
  getSession, onSessionChange, pendingInvite, clearPendingInvite, acceptInvitation,
  savedWorkspace, saveWorkspace, listWorkspaces, createWorkspace, renameWorkspace,
  deleteWorkspace, listMembers, setMemberRole, removeMember, listInvitations,
  createInvitation, revokeInvitation, listKeyProviders, listProviderKeys, addProviderKey,
  editProviderKey, revokeProviderKey, deleteProviderKey, testProviderKey,
} from '../session.js'

// R-204 (D-153): sign-up / sign-in (e-mail + Google/GitHub when enabled on the
// server), the workspace switcher, members and invitations. Everything the
// user is allowed to do is decided by the server (RLS); the buttons shown here
// only follow the role the server reported, they are not the protection.

const ROLE_LABEL = { owner: 'مالك', admin: 'مدير', member: 'عضو' }
const STATE_LABEL = { pending: 'بانتظار القبول', accepted: 'مقبولة', expired: 'منتهية', revoked: 'ملغاة' }
const PROVIDER_LABEL = { google: 'Google', github: 'GitHub' }

function Notice({ notice }) {
  if (!notice) return null
  return <div className={`badge ${notice.kind === 'error' ? 'badge-error' : 'badge-success'}`} role="status">{notice.text}</div>
}

function SignIn({ config, setNotice }) {
  const [mode, setMode] = useState('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const signupAllowed = config.signup_enabled !== false
  const providers = Object.keys(PROVIDER_LABEL).filter((p) => (config.providers || {})[p])

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setNotice(null)
    try {
      if (mode === 'signup') {
        const r = await signUp(email.trim(), password)
        if (r.confirmationRequired) {
          setNotice({ kind: 'ok', text: 'إن كان البريد صحيحًا فقد أُرسل إليه رابط تأكيد — افتحه ثم سجّل الدخول' })
          setMode('login')
        }
      } else {
        await signIn(email.trim(), password)
      }
      setPassword('')
    } catch (err) {
      setNotice({ kind: 'error', text: err.message })
    } finally {
      setBusy(false)
    }
  }

  const withProvider = async (p) => {
    try {
      const url = await oauthUrl(p, config, `${window.location.origin}/`)
      window.location.assign(url)
    } catch (err) {
      setNotice({ kind: 'error', text: err.message })
    }
  }

  return (
    <div className="card account-card">
      <div className="card-title">{mode === 'signup' ? 'إنشاء حساب' : 'تسجيل الدخول'}</div>
      <form onSubmit={submit} aria-label={mode === 'signup' ? 'إنشاء حساب' : 'تسجيل الدخول'}>
        <div className="form-group">
          <label className="form-label" htmlFor="acc-email">البريد الإلكتروني</label>
          <input id="acc-email" className="form-input" type="email" autoComplete="email" required
                 value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="form-group">
          <label className="form-label" htmlFor="acc-password">كلمة المرور</label>
          <input id="acc-password" className="form-input" type="password" required
                 autoComplete={mode === 'signup' ? 'new-password' : 'current-password'}
                 minLength={mode === 'signup' ? 8 : 1}
                 value={password} onChange={(e) => setPassword(e.target.value)} />
          {mode === 'signup' && <div className="account-hint">8 أحرف على الأقل</div>}
        </div>
        <div className="account-actions">
          <button className="btn btn-primary" type="submit" disabled={busy || !email.trim() || !password}>
            {mode === 'signup' ? 'إنشاء الحساب' : 'دخول'}
          </button>
          {signupAllowed && (
            <button className="btn btn-ghost" type="button" onClick={() => setMode(mode === 'signup' ? 'login' : 'signup')}>
              {mode === 'signup' ? 'لدي حساب' : 'حساب جديد'}
            </button>
          )}
        </div>
      </form>
      {providers.length > 0 ? (
        <div className="account-actions">
          {providers.map((p) => (
            <button key={p} className="btn btn-ghost" type="button" onClick={() => withProvider(p)}>
              الدخول عبر {PROVIDER_LABEL[p]}
            </button>
          ))}
        </div>
      ) : (
        <div className="account-hint">الدخول عبر Google أو GitHub غير مفعل على هذا الخادم بعد</div>
      )}
    </div>
  )
}

function Members({ ws, me, setNotice, onLostAccess }) {
  const [members, setMembers] = useState(null)
  const load = useCallback(async () => {
    try {
      setMembers(await listMembers(ws.id))
    } catch (err) {
      if (err.status === 404) onLostAccess()
      else setNotice({ kind: 'error', text: err.message })
    }
  }, [ws.id, onLostAccess, setNotice])
  useEffect(() => { load() }, [load])

  const act = async (fn) => {
    try {
      await fn()
      await load()
    } catch (err) {
      setNotice({ kind: 'error', text: err.message })
    }
  }

  if (!members) return <div className="loading">جارٍ تحميل الأعضاء…</div>
  const owners = members.filter((m) => m.role === 'owner').length
  return (
    <div className="card account-card">
      <div className="card-title">الأعضاء ({members.length})</div>
      <ul className="account-list">
        {members.map((m) => {
          const self = m.user_id === me.id
          const canRemove = !self && (ws.role === 'owner' || (ws.role === 'admin' && m.role === 'member'))
          return (
            <li key={m.user_id} className="account-row" data-member={m.user_id}>
              <span>{m.email || m.user_id}{self ? ' (أنت)' : ''}</span>
              {ws.role === 'owner' && !self ? (
                <select className="form-input account-select" aria-label={`دور ${m.email || m.user_id}`}
                        value={m.role} onChange={(e) => act(() => setMemberRole(ws.id, m.user_id, e.target.value))}>
                  {Object.keys(ROLE_LABEL).map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
                </select>
              ) : (
                <span className="badge badge-info">{ROLE_LABEL[m.role] || m.role}</span>
              )}
              {canRemove && (
                <button className="btn btn-danger" type="button" onClick={() => act(() => removeMember(ws.id, m.user_id))}>إزالة</button>
              )}
              {self && !(m.role === 'owner' && owners === 1) && (
                <button className="btn btn-ghost" type="button" onClick={async () => {
                  if (!window.confirm('مغادرة هذه المساحة؟')) return
                  try {
                    await removeMember(ws.id, me.id)
                    onLostAccess()
                  } catch (err) {
                    setNotice({ kind: 'error', text: err.message })
                  }
                }}>مغادرة</button>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

// ------------------------------------------------------- R-205 (D-154): BYOK
// The user's own AI provider keys in this workspace. Visible to this user only
// (not to other members, owners or admins — the database enforces it). The key
// is typed into a password field, sent once, and the field is emptied; the
// server answers with the last 4 characters only.
const TEST_REASON = {
  ok: 'يعمل', invalid_key: 'المفتاح غير صحيح', forbidden: 'المفتاح لا يملك الصلاحية',
  endpoint_not_found: 'عنوان الاختبار غير موجود', rate_limited: 'المزود يحد الطلبات الآن',
  provider_unavailable: 'المزود غير متاح الآن', unexpected_status: 'رد غير متوقع من المزود',
  unexpected_redirect: 'المزود أعاد التوجيه (لم يُرسل المفتاح)', timeout: 'انتهت المهلة',
  unreachable: 'تعذر الوصول إلى المزود', blocked_address: 'العنوان محلي/خاص ولا يصل إليه الخادم',
  tls_error: 'خطأ في شهادة الاتصال', test_failed: 'فشل الاختبار',
}
const KEY_STATUS = { active: 'فعال', revoked: 'مُبطل' }

function keyError(err) {
  if (err.status === 503 && String(err.detail || '').includes('AGOS_BYOK_KEK')) return 'خزنة المفاتيح غير مفعلة على هذا الخادم بعد'
  if (err.status === 409 && String(err.detail || '').includes('limit')) return 'بلغت الحد الأقصى (20 مفتاحًا في المساحة)'
  if (err.status === 409) return 'تعذر فك المفتاح المحفوظ — أدخل المفتاح من جديد'
  if (err.status === 410) return 'المفتاح مُبطل'
  if (err.status === 429) return 'اختبارات كثيرة — انتظر دقيقة'
  return err.message
}

function ProviderKeys({ ws }) {
  const [providers, setProviders] = useState([])
  const [keys, setKeys] = useState(null)
  const [error, setError] = useState(null)
  const [provider, setProvider] = useState('openai')
  const [label, setLabel] = useState('')
  const [secret, setSecret] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [replacing, setReplacing] = useState(null)
  const [replacement, setReplacement] = useState('')
  const [results, setResults] = useState({})
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      setKeys(await listProviderKeys(ws.id))
      setError(null)
    } catch (err) {
      setKeys([])
      setError(keyError(err))
    }
  }, [ws.id])
  useEffect(() => {
    load()
    listKeyProviders().then(setProviders).catch(() => setProviders([]))
  }, [load])

  const current = providers.find((p) => p.id === provider)
  const needsUrl = Boolean(current && current.needs_url)
  const needsKey = !current || current.needs_key

  const add = async (e) => {
    e.preventDefault()
    setBusy(true)
    try {
      const body = { provider, label: label.trim() }
      if (secret) body.api_key = secret
      if (needsUrl) body.base_url = baseUrl.trim()
      await addProviderKey(ws.id, body)
      setLabel(''); setBaseUrl('')
      await load()
    } catch (err) {
      setError(keyError(err))
    } finally {
      setSecret('') // never kept, success or not
      setBusy(false)
    }
  }
  const run = async (fn) => {
    try { await fn(); await load() } catch (err) { setError(keyError(err)) }
  }
  const replace = async (e, k) => {
    e.preventDefault()
    const value = replacement
    setReplacement('')
    setReplacing(null)
    await run(() => editProviderKey(ws.id, k.id, { api_key: value }))
  }
  const test = async (k) => {
    try {
      const r = await testProviderKey(ws.id, k.id)
      setResults((m) => ({ ...m, [k.id]: r }))
      await load()
    } catch (err) {
      setError(keyError(err))
    }
  }

  return (
    <div className="card account-card" aria-label="مفاتيح الذكاء الاصطناعي">
      <div className="card-title">مفاتيح الذكاء الاصطناعي — مفاتيحك أنت فقط</div>
      <div className="account-hint">يُحفظ المفتاح مشفرًا على الخادم ولا يُعرض مرة أخرى؛ يظهر آخر 4 أحرف فقط. لا يراه أعضاء المساحة الآخرون. الإبطال أو الحذف يوقف استخدامه فورًا.</div>
      {error && <div className="badge badge-error" role="alert">{error}</div>}
      {!keys && <div className="loading">جارٍ التحميل…</div>}
      {keys && keys.length > 0 && (
        <ul className="account-list">
          {keys.map((k) => {
            const r = results[k.id]
            return (
              <li key={k.id} className="account-row" data-key={k.id}>
                <span>{(providers.find((p) => p.id === k.provider) || {}).name || k.provider} — {k.label}</span>
                <span className="badge badge-info" data-mask>{k.has_secret ? `••••${k.last4}` : (k.status === 'revoked' && k.last4 ? `••••${k.last4} (ممسوح)` : 'بلا مفتاح')}</span>
                {k.base_url && <span className="account-hint">{k.base_url}</span>}
                <span className={`badge ${k.status === 'active' ? 'badge-success' : 'badge-error'}`}>{KEY_STATUS[k.status] || k.status}</span>
                {k.last_test_at && (
                  <span className="account-hint" data-test-result>
                    آخر اختبار: {TEST_REASON[k.last_test_reason] || k.last_test_reason}{r && r.http_status ? ` (HTTP ${r.http_status})` : ''}
                  </span>
                )}
                <span className="account-hint" data-usage>استخدام: {k.use_count} — رموز {k.tokens_in}/{k.tokens_out} — تكلفة ${k.cost_usd}</span>
                {k.status === 'active' && (
                  <>
                    <button className="btn btn-ghost" type="button" onClick={() => test(k)}>اختبار المفتاح</button>
                    {k.provider !== 'ollama' && (
                      <button className="btn btn-ghost" type="button" onClick={() => { setReplacing(k.id); setReplacement('') }}>استبدال المفتاح</button>
                    )}
                    <button className="btn btn-ghost" type="button" onClick={() => {
                      if (window.confirm('إبطال هذا المفتاح؟ يتوقف استخدامه فورًا ويُمسح من الخادم.')) run(() => revokeProviderKey(ws.id, k.id))
                    }}>إبطال</button>
                  </>
                )}
                <button className="btn btn-danger" type="button" onClick={() => {
                  if (window.confirm('حذف هذا المفتاح نهائيًا؟')) run(() => deleteProviderKey(ws.id, k.id))
                }}>حذف</button>
                {replacing === k.id && (
                  <form className="account-actions" aria-label="استبدال المفتاح" onSubmit={(e) => replace(e, k)}>
                    <input className="form-input" type="password" autoComplete="off" spellCheck={false}
                           aria-label="المفتاح الجديد" maxLength={512} value={replacement}
                           onChange={(e) => setReplacement(e.target.value)} />
                    <button className="btn btn-primary" type="submit" disabled={replacement.length < 8}>حفظ</button>
                  </form>
                )}
              </li>
            )
          })}
        </ul>
      )}
      {keys && keys.length === 0 && !error && <div className="account-hint">لا مفاتيح بعد</div>}
      <form className="account-actions" aria-label="إضافة مفتاح" onSubmit={add}>
        <select className="form-input account-select" aria-label="المزود" value={provider} onChange={(e) => setProvider(e.target.value)}>
          {(providers.length ? providers : [{ id: 'openai', name: 'OpenAI' }]).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <input className="form-input" aria-label="اسم المفتاح" placeholder="اسم للتمييز" maxLength={80}
               value={label} onChange={(e) => setLabel(e.target.value)} />
        {needsUrl && (
          <input className="form-input" aria-label="عنوان Ollama" placeholder="http://host:11434" maxLength={300}
                 value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
        )}
        <input className="form-input" type="password" autoComplete="off" spellCheck={false} aria-label="المفتاح"
               placeholder={needsKey ? 'المفتاح' : 'مفتاح (اختياري)'} maxLength={512}
               value={secret} onChange={(e) => setSecret(e.target.value)} />
        <button className="btn btn-primary" type="submit"
                disabled={busy || !label.trim() || (needsKey && secret.length < 8) || (needsUrl && !baseUrl.trim())}>حفظ المفتاح</button>
      </form>
    </div>
  )
}

function Invitations({ ws, setNotice }) {
  const [items, setItems] = useState(null)
  const [email, setEmail] = useState('')
  const [role, setRole] = useState('member')
  const [created, setCreated] = useState(null)
  const load = useCallback(async () => {
    try {
      setItems(await listInvitations(ws.id))
    } catch (err) {
      setNotice({ kind: 'error', text: err.message })
    }
  }, [ws.id, setNotice])
  useEffect(() => { setCreated(null); load() }, [load])

  const invite = async (e) => {
    e.preventDefault()
    try {
      const inv = await createInvitation(ws.id, email.trim(), role)
      setCreated({ email: inv.email, url: inv.accept_url, expires_at: inv.expires_at })
      setEmail('')
      await load()
    } catch (err) {
      setNotice({ kind: 'error', text: err.message })
    }
  }

  const roles = ws.role === 'owner' ? ['member', 'admin'] : ['member']
  return (
    <div className="card account-card">
      <div className="card-title">الدعوات</div>
      <form onSubmit={invite} className="account-actions" aria-label="دعوة عضو">
        <input className="form-input" type="email" required placeholder="بريد المدعو"
               value={email} onChange={(e) => setEmail(e.target.value)} />
        <select className="form-input account-select" aria-label="دور المدعو" value={role} onChange={(e) => setRole(e.target.value)}>
          {roles.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
        </select>
        <button className="btn btn-primary" type="submit" disabled={!email.trim()}>إنشاء دعوة</button>
      </form>
      {created && (
        <div className="account-invite-link" role="status">
          <div>رابط دعوة {created.email} — يظهر مرة واحدة فقط ولا يُرسل تلقائيًا بالبريد. انسخه وأرسله إليه:</div>
          <input className="form-input" readOnly value={created.url} aria-label="رابط الدعوة" onFocus={(e) => e.target.select()} />
          <div className="account-hint">لا يقبلها إلا صاحب البريد نفسه بعد تأكيده، وتنتهي في {new Date(created.expires_at).toLocaleString('ar')}</div>
        </div>
      )}
      {items && items.length === 0 && <div className="account-hint">لا دعوات</div>}
      {items && items.length > 0 && (
        <ul className="account-list">
          {items.map((i) => (
            <li key={i.id} className="account-row" data-invitation={i.id}>
              <span>{i.email}</span>
              <span className="badge badge-info">{ROLE_LABEL[i.role] || i.role}</span>
              <span className={`badge ${i.state === 'pending' ? 'badge-warning' : 'badge-info'}`}>{STATE_LABEL[i.state] || i.state}</span>
              {i.state === 'pending' && (
                <button className="btn btn-ghost" type="button" onClick={async () => {
                  try {
                    await revokeInvitation(ws.id, i.id)
                    await load()
                  } catch (err) {
                    setNotice({ kind: 'error', text: err.message })
                  }
                }}>إلغاء</button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Workspaces({ user, setNotice, inviteTick }) {
  const [list, setList] = useState(null)
  const [current, setCurrent] = useState(savedWorkspace())
  const [newName, setNewName] = useState('')
  const [rename, setRename] = useState('')
  const [invite, setInvite] = useState(pendingInvite())
  useEffect(() => { if (inviteTick) setInvite(pendingInvite()) }, [inviteTick])

  const load = useCallback(async (prefer) => {
    try {
      const ws = await listWorkspaces()
      setList(ws)
      const want = prefer || savedWorkspace()
      const pick = ws.find((w) => w.id === want) || ws[0] || null
      setCurrent(pick ? pick.id : null)
      saveWorkspace(pick ? pick.id : null)
    } catch (err) {
      setNotice({ kind: 'error', text: err.message })
    }
  }, [setNotice])
  useEffect(() => { load() }, [load])

  const ws = list ? list.find((w) => w.id === current) : null
  useEffect(() => { setRename(ws ? ws.name : '') }, [ws && ws.id, ws && ws.name])

  const lostAccess = useCallback(() => {
    setNotice({ kind: 'error', text: 'لم تعد عضوًا في تلك المساحة' })
    saveWorkspace(null)
    load()
  }, [load, setNotice])

  const accept = async () => {
    try {
      const r = await acceptInvitation(invite)
      clearPendingInvite()
      setInvite(null)
      setNotice({ kind: 'ok', text: `انضممت إلى المساحة بدور ${ROLE_LABEL[r.role] || r.role}` })
      await load(r.workspace_id)
    } catch (err) {
      setNotice({ kind: 'error', text: err.status === 403 ? 'هذه الدعوة لبريد آخر، أو بريدك لم يُؤكَّد' : err.message })
      if ([403, 404, 409, 410].includes(err.status)) {
        clearPendingInvite()
        setInvite(null)
      }
    }
  }

  const create = async (e) => {
    e.preventDefault()
    try {
      const w = await createWorkspace(newName.trim())
      setNewName('')
      await load(w.id)
    } catch (err) {
      setNotice({ kind: 'error', text: err.message })
    }
  }

  const doRename = async (e) => {
    e.preventDefault()
    try {
      await renameWorkspace(ws.id, rename.trim())
      await load(ws.id)
    } catch (err) {
      setNotice({ kind: 'error', text: err.message })
    }
  }

  const doDelete = async () => {
    if (!window.confirm(`حذف المساحة «${ws.name}» وكل بياناتها نهائيًا؟`)) return
    try {
      await deleteWorkspace(ws.id)
      saveWorkspace(null)
      await load()
    } catch (err) {
      setNotice({ kind: 'error', text: err.message })
    }
  }

  return (
    <>
      {invite && (
        <div className="card account-card" role="region" aria-label="دعوة معلقة">
          <div className="card-title">لديك دعوة إلى مساحة عمل</div>
          <div className="account-actions">
            <button className="btn btn-primary" type="button" onClick={accept}>قبول الدعوة</button>
            <button className="btn btn-ghost" type="button" onClick={() => { clearPendingInvite(); setInvite(null) }}>تجاهل</button>
          </div>
        </div>
      )}
      <div className="card account-card">
        <div className="card-title">مساحات العمل</div>
        {!list && <div className="loading">جارٍ التحميل…</div>}
        {list && list.length === 0 && <div className="account-hint">لا مساحات بعد — أنشئ أول مساحة</div>}
        {list && list.length > 0 && (
          <select className="form-input" aria-label="المساحة الحالية" value={current || ''}
                  onChange={(e) => { setCurrent(e.target.value); saveWorkspace(e.target.value) }}>
            {list.map((w) => <option key={w.id} value={w.id}>{w.name} — {ROLE_LABEL[w.role] || w.role}</option>)}
          </select>
        )}
        <form onSubmit={create} className="account-actions" aria-label="مساحة جديدة">
          <input className="form-input" placeholder="اسم مساحة جديدة" maxLength={200}
                 value={newName} onChange={(e) => setNewName(e.target.value)} />
          <button className="btn btn-primary" type="submit" disabled={!newName.trim()}>إنشاء</button>
        </form>
      </div>
      {ws && (
        <>
          <div className="card account-card" data-workspace={ws.id}>
            <div className="card-title">{ws.name} <span className="badge badge-info">{ROLE_LABEL[ws.role] || ws.role}</span></div>
            {(ws.role === 'owner' || ws.role === 'admin') && (
              <form onSubmit={doRename} className="account-actions" aria-label="إعادة التسمية">
                <input className="form-input" maxLength={200} value={rename} onChange={(e) => setRename(e.target.value)} />
                <button className="btn btn-ghost" type="submit" disabled={!rename.trim() || rename.trim() === ws.name}>إعادة التسمية</button>
              </form>
            )}
            {ws.role === 'owner' && (
              <button className="btn btn-danger" type="button" onClick={doDelete}>حذف المساحة</button>
            )}
          </div>
          <Members key={`m-${ws.id}`} ws={ws} me={user} setNotice={setNotice} onLostAccess={lostAccess} />
          <ProviderKeys key={`k-${ws.id}`} ws={ws} />
          {(ws.role === 'owner' || ws.role === 'admin') && <Invitations key={`i-${ws.id}`} ws={ws} setNotice={setNotice} />}
        </>
      )}
    </>
  )
}

export default function Account() {
  const [config, setConfig] = useState(null)
  const [ready, setReady] = useState(false)
  const [sess, setSess] = useState(getSession())
  const [notice, setNotice] = useState(null)
  const [inviteTick, setInviteTick] = useState(0)

  useEffect(() => {
    const off = onSessionChange(setSess)
    let alive = true
    ;(async () => {
      let cfg
      try {
        cfg = await authConfig()
      } catch (err) {
        cfg = { configured: false, error: err.message }
      }
      if (!alive) return
      setConfig(cfg)
      if (cfg.configured) {
        const r = await completeFromUrl()
        if (r.error) setNotice({ kind: 'error', text: r.error })
        else if (r.confirmed) setNotice({ kind: 'ok', text: 'تم تأكيد بريدك — سجّل الدخول الآن' })
        else if (r.invite && !getSession()) setNotice({ kind: 'ok', text: 'لديك دعوة — سجّل الدخول بالبريد المدعو لقبولها' })
        if (!getSession()) await refreshSession()
      }
      if (alive) setReady(true)
    })()
    return () => { alive = false; off() }
  }, [])

  // an invitation link opened while the app is already loaded only changes
  // the hash (no reload) — read it then as well
  useEffect(() => {
    if (!config || !config.configured) return undefined
    const onHash = async () => {
      const r = await completeFromUrl()
      if (r.error) setNotice({ kind: 'error', text: r.error })
      else if (r.invite) {
        setInviteTick((t) => t + 1)
        if (!getSession()) setNotice({ kind: 'ok', text: 'لديك دعوة — سجّل الدخول بالبريد المدعو لقبولها' })
      }
    }
    const win = globalThis.window
    if (!win || typeof win.addEventListener !== 'function') return undefined
    win.addEventListener('hashchange', onHash)
    return () => win.removeEventListener('hashchange', onHash)
  }, [config])

  if (!config || !ready) return <div className="loading">جارٍ التحميل…</div>

  if (!config.configured) {
    return (
      <div className="card account-card">
        <div className="card-title">حسابات المستخدمين</div>
        <p>حسابات المستخدمين غير مفعلة على هذا الخادم بعد{config.error ? ` (${config.error})` : ''}.</p>
        <p className="account-hint">الوصول الحالي إلى المهام يبقى بمفتاح API في أعلى الصفحة.</p>
      </div>
    )
  }

  if (!sess) {
    return (
      <div className="account-page">
        <Notice notice={notice} />
        <SignIn config={config} setNotice={setNotice} />
      </div>
    )
  }

  const logout = async () => {
    try {
      await signOut()
      setNotice({ kind: 'ok', text: 'خرجت من حسابك' })
    } catch (err) {
      setNotice({ kind: 'error', text: `أُنهيت الجلسة في هذا المتصفح، لكن الخادم لم يؤكد إنهاءها: ${err.message}` })
    }
  }

  return (
    <div className="account-page">
      <div className="card account-card account-header">
        <span>{sess.user.email}</span>
        <button className="btn btn-ghost" type="button" onClick={logout}>تسجيل الخروج</button>
      </div>
      <Notice notice={notice} />
      <Workspaces user={sess.user} setNotice={setNotice} inviteTick={inviteTick} />
    </div>
  )
}
