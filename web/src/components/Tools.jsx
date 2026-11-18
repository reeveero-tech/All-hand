import { useState, useEffect } from 'react'

// R-206a (D-165): the tools hub. Lists the tools the owner asked for, with the
// state of their copies in this repository's releases (written by the
// tools-mirror workflow). Each tool's own page arrives with its item
// (R-206b flights, R-206c movies, R-206d Mister Download Manager, R-206e Robin);
// until then the card says so — it never pretends a tool works.
const API_BASE = (import.meta.env && import.meta.env.VITE_API_BASE) || ''

const UPSTREAM_LABEL = { active: 'نشط', archived: 'مؤرشف لدى مالكه', gone: 'محذوف لدى مالكه' }
const KIND_LABEL = { service: 'خدمة بيانات خارجية', built: 'مبني داخل المشروع', mirror: 'مشروع مفتوح المصدر منسوخ بالكامل' }

export async function fetchTools(fetchImpl = globalThis.fetch) {
  const r = await fetchImpl(`${API_BASE}/api/v2/tools`)
  if (!r.ok) throw new Error(`تعذر تحميل الأدوات (${r.status})`)
  return r.json()
}

function fmt(ts) {
  if (!ts) return '—'
  try { return new Date(ts).toLocaleString('ar') } catch { return ts }
}

function MirrorInfo({ m }) {
  if (!m) return null
  return (
    <div className="tool-mirror" data-testid="mirror">
      <div>
        <span className={`badge ${m.state === 'copied' ? 'badge-success' : 'badge-info'}`}>
          {m.state === 'copied' ? 'النسخة محفوظة لدينا' : 'لم تُنسخ بعد'}
        </span>{' '}
        {m.upstream_state && <span className="badge badge-info">المصدر: {UPSTREAM_LABEL[m.upstream_state] || m.upstream_state}</span>}
        {m.error && <span className="badge badge-error" role="alert">فشل آخر نسخ: {m.error}</span>}
      </div>
      <div className="account-hint">آخر نسخ: {fmt(m.snapshot_at)} · آخر فحص: {fmt(m.checked_at)}
        {m.snapshot_head && <> · الإيداع <code>{m.snapshot_head.slice(0, 8)}</code></>}</div>
      {m.state === 'copied' && (
        <div className="account-actions">
          <a className="btn btn-ghost" href={m.source_url}>تنزيل الكود</a>
          <a className="btn btn-ghost" href={m.bundle_url}>نسخة git كاملة (bundle)</a>
          <a className="btn btn-ghost" href={m.release_page} target="_blank" rel="noreferrer">صفحة النسخة</a>
        </div>
      )}
      {m.mirrored_releases?.length > 0 && (
        <div className="account-hint">الإصدارات المحفوظة:{' '}
          {m.mirrored_releases.map((r) => <a key={r.tag} href={r.page} target="_blank" rel="noreferrer">{r.tag} </a>)}
        </div>
      )}
    </div>
  )
}

export default function Tools({ load = fetchTools }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let alive = true
    load().then((d) => alive && setData(d)).catch((e) => alive && setError(e.message))
    return () => { alive = false }
  }, [load])

  if (error) return <div className="badge badge-error" role="alert">{error}</div>
  if (!data) return <div className="loading">جارٍ التحميل…</div>
  return (
    <div className="account-page tools-page">
      <div className="card account-card">
        <div className="card-title">الأدوات</div>
        <div className="account-hint">المشاريع مفتوحة المصدر محفوظة كاملة في إصدارات المستودع وتتحدث تلقائيًا يوميًا؛
          إن أرشفها مالكها أو حذفها تبقى آخر نسخة لدينا.</div>
        {data.status?.error && (
          <div className="badge badge-error" role="alert">حالة النسخ غير متاحة الآن: {data.status.error}</div>
        )}
      </div>
      {data.tools.map((t) => (
        <div className="card account-card" key={t.id} data-tool={t.id}>
          <div className="card-title">{t.name}</div>
          <div className="account-hint">{KIND_LABEL[t.kind] || t.kind}
            {t.license && <> · الرخصة {t.license}</>}
            {t.upstream_url && <> · <a href={t.upstream_url} target="_blank" rel="noreferrer">المصدر الأصلي</a></>}
            {t.source && !t.upstream_url && <> · <a href={t.source} target="_blank" rel="noreferrer">مصدر البيانات</a></>}
          </div>
          {t.data_terms && <div className="account-hint">شروط البيانات: {t.data_terms}</div>}
          <MirrorInfo m={t.mirror} />
          <div className="badge badge-info">الواجهة الكاملة لهذه الأداة قيد البناء ({t.item})</div>
        </div>
      ))}
    </div>
  )
}
