import { useState, useEffect } from 'react'

// R-206d (D-165): download page for Mister Download Manager. Every link points
// to OUR copy of the release (mirror-mdm-<tag> in this repository's releases),
// and each file shows its SHA256 and whether it matched the upstream
// SHA256SUMS.txt (manifest.json written by the tools-mirror workflow). The
// program is GPL-3.0 and stays separate from AGOS (its own licence and code).
const API_BASE = (import.meta.env && import.meta.env.VITE_API_BASE) || ''

export async function fetchDownloads(tool = 'mdm', fetchImpl = globalThis.fetch) {
  const r = await fetchImpl(`${API_BASE}/api/v2/downloads/${encodeURIComponent(tool)}`)
  let body = null
  try { body = await r.json() } catch { /* not JSON */ }
  if (!r.ok) {
    const why = (body && (body.error || body.detail)) || ''
    throw new Error(`تعذر تحميل صفحة التنزيل (${r.status})${why ? ': ' + why : ''}`)
  }
  return body
}

export function fmtSize(n) {
  if (n == null) return '—'
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`
  if (n >= 1024) return `${Math.round(n / 1024)} KB`
  return `${n} B`
}

// the visitor's platform, to put its group first (best effort; never hides others)
function fmtTime(ts) {
  if (!ts) return '—'
  try { return new Date(ts).toLocaleString('ar') } catch { return ts }
}

export function guessPlatform(ua = (globalThis.navigator && globalThis.navigator.userAgent) || '') {
  if (/Windows/i.test(ua)) return 'windows'
  if (/Mac OS X|Macintosh/i.test(ua)) return 'macos'
  if (/Linux|X11/i.test(ua) && !/Android/i.test(ua)) return 'linux'
  return null
}

export function orderGroups(groups, platform) {
  if (!platform) return groups
  const i = groups.findIndex((g) => g.id === platform)
  if (i <= 0) return groups
  return [groups[i], ...groups.slice(0, i), ...groups.slice(i + 1)]
}

const VERIFY_LABEL = {
  verified: ['badge-success', 'كل الملفات طابقت مجاميع المصدر (SHA256)'],
  failed: ['badge-error', 'فشل التحقق: ملف أو أكثر لا يطابق مجاميع المصدر'],
  not_verified_yet: ['badge-info', 'لم يُتحقق من هذه النسخة بعد (يُتحقق في تشغيل النسخ القادم)'],
}

function FileRow({ f }) {
  const mark = f.verified === true ? ['badge-success', 'مطابق'] : f.verified === false
    ? ['badge-error', 'لا يطابق'] : ['badge-info', 'غير مدرج في المجاميع']
  return (
    <li className="dl-file" data-testid="dl-file">
      <div className="dl-file-head">
        {f.verified === false
          ? <span className="dl-name">{f.name}</span>
          : <a className="dl-name" href={f.url}>{f.name}</a>}
        <span className="account-hint">{fmtSize(f.size)}</span>
        <span className={`badge ${mark[0]}`}>{mark[1]}</span>
      </div>
      <code className="dl-sha" title="SHA256 لنسختنا">{f.sha256}</code>
    </li>
  )
}

export default function Downloads({ tool = 'mdm', load = fetchDownloads, platform = guessPlatform() }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let alive = true
    load(tool).then((d) => { if (alive) { setData(d); setError(null) } })
      .catch((e) => { if (alive) setError(e.message || String(e)) })
    return () => { alive = false }
  }, [tool, load])

  if (error) return <div className="account-page downloads-page"><div className="card"><p role="alert" className="badge badge-error">{error}</p></div></div>
  if (!data) return <div className="account-page downloads-page"><div className="card"><p className="account-hint">جارٍ التحميل…</p></div></div>

  const v = data.verification || {}
  const [cls, label] = VERIFY_LABEL[v.state] || ['badge-info', v.state]
  const groups = orderGroups(data.groups || [], platform)
  return (
    <div className="account-page downloads-page">
      <div className="card">
        <h2>{data.name} <span className="account-hint">{data.release && data.release.tag}</span></h2>
        <p className="account-hint">
          مدير تحميل مفتوح المصدر. الملفات تُنزَّل من نسختنا المحفوظة في هذا المستودع، لا من المصدر،
          فتبقى متاحة حتى لو أرشف المالك مشروعه أو حذف الإصدار.
        </p>
        <div className="account-actions">
          <span className={`badge ${cls}`} data-testid="verify-state">{label}</span>
          {data.upstream_state && data.upstream_state !== 'active' &&
            <span className="badge badge-info">المصدر: {data.upstream_state === 'archived' ? 'مؤرشف لدى مالكه' : 'محذوف لدى مالكه'}</span>}
        </div>
        {v.mismatched && v.mismatched.length > 0 &&
          <p role="alert" className="badge badge-error">ملفات لا تطابق: {v.mismatched.join('، ')}</p>}
        {v.error && <p role="alert" className="badge badge-error">تعذر قراءة ملف التحقق: {v.error}</p>}
        {v.sums_source && <p className="account-hint">مصدر المجاميع: <code>{v.sums_source}</code> · وقت التحقق: <bdi>{fmtTime(v.verified_at)}</bdi></p>}
      </div>

      {groups.map((g) => (
        <div className="card" key={g.id} data-testid={`group-${g.id}`}>
          <h3>{g.label}</h3>
          <ul className="dl-list">{g.files.map((f) => <FileRow key={f.name} f={f} />)}</ul>
        </div>
      ))}
      {groups.length === 0 && (
        <div className="card"><p className="account-hint">
          لا قائمة ملفات متحققة بعد. صفحة النسخة: <a href={data.release && data.release.mirror_page} target="_blank" rel="noreferrer">{data.release && data.release.tag}</a>
        </p></div>
      )}

      <div className="card">
        <h3>الكود والرخصة</h3>
        <p className="account-hint">
          الرخصة: <a href={data.license_url} target="_blank" rel="noreferrer">{data.license}</a> — {data.license_note}
        </p>
        <div className="account-actions">
          {data.source && <a className="btn btn-ghost" href={data.source.source_url}>تنزيل الكود</a>}
          {data.source && <a className="btn btn-ghost" href={data.source.bundle_url}>نسخة git كاملة (bundle)</a>}
          <a className="btn btn-ghost" href={data.upstream_url} target="_blank" rel="noreferrer">المشروع الأصلي</a>
        </div>
        {data.releases && data.releases.length > 1 && (
          <p className="account-hint">إصدارات أخرى محفوظة: {data.releases.slice(1).map((r) =>
            <a key={r.tag} href={r.mirror_page} target="_blank" rel="noreferrer">{r.tag} </a>)}</p>
        )}
      </div>
    </div>
  )
}
