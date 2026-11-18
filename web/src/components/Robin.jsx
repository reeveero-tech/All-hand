import { useState, useEffect } from 'react'

// R-206e (D-165): Robin (dark-web OSINT, apurvsinghgautam/robin, MIT) inside
// AGOS. Robin runs as its own service built from OUR copy of its code; this
// page talks to it through /api/v2/robin/* (signed-in users only). The report
// is shown as plain text (never as HTML) and .onion addresses are shown as
// text, not links. The personal-identity preset is not offered (D-165).
export const PRESETS = {
  threat_intel: 'استخبارات التهديدات العامة',
  ransomware_malware: 'برامج الفدية والبرمجيات الخبيثة',
  corporate_espionage: 'تسريبات بيانات الشركات',
}

export const STATUS_TEXT = {
  ok: 'اكتمل البحث',
  no_results: 'لم تُرجع محركات البحث أي نتيجة',
  engines_unreachable: 'تعذر الوصول إلى محركات البحث عبر Tor (لم يُعرف شيء، لا يعني عدم الوجود)',
  nothing_relevant: 'وُجدت نتائج لكن لا شيء منها متعلق بالسؤال',
  nothing_readable: 'تعذرت قراءة أي صفحة من النتائج',
}

export function serviceLine(st) {
  if (!st) return ['badge-info', 'جارٍ فحص الخدمة…']
  if (!st.configured) return ['badge-info', 'خدمة Robin غير مهيأة على هذا الخادم بعد']
  if (!st.reachable) return ['badge-error', 'خدمة Robin لا تستجيب الآن (قد تكون نائمة؛ أعد المحاولة بعد دقيقة)']
  if (!st.service_configured) return ['badge-error', 'خدمة Robin تعمل لكن إعدادها ناقص']
  if (!st.tor_bootstrapped) return ['badge-info', 'خدمة Robin تعمل وTor لم يكتمل اتصاله بعد']
  return ['badge-success', st.busy ? 'خدمة Robin تعمل (مشغولة ببحث آخر)' : 'خدمة Robin تعمل وTor متصل']
}

function Sources({ list }) {
  if (!list || list.length === 0) return null
  return (
    <ul className="robin-sources" data-testid="robin-sources">
      {list.map((s, i) => (
        <li key={i}><span>{s.title || '—'}</span> <code dir="ltr">{s.link}</code></li>
      ))}
    </ul>
  )
}

export default function Robin({ api }) {
  const [signed, setSigned] = useState(() => !!api.getSession())
  const [st, setSt] = useState(null)
  const [models, setModels] = useState(null)
  const [form, setForm] = useState({ query: '', model: '', preset: 'threat_intel' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  useEffect(() => api.onSessionChange((s) => setSigned(!!s)), [api])
  useEffect(() => {
    let alive = true
    api.status().then((s) => alive && setSt(s)).catch(() => alive && setSt({ configured: true, reachable: false }))
    return () => { alive = false }
  }, [api])
  useEffect(() => {
    if (!signed || !st || !st.reachable) return undefined
    let alive = true
    api.models().then((m) => {
      if (!alive) return
      setModels(m)
      setForm((f) => ({ ...f, model: f.model || (m.models && m.models[0]) || '' }))
    }).catch((e) => alive && setError(e.message || String(e)))
    return () => { alive = false }
  }, [api, signed, st])

  async function submit(e) {
    e.preventDefault()
    setBusy(true); setError(null); setResult(null)
    try {
      setResult(await api.investigate({ query: form.query.trim(), model: form.model, preset: form.preset }))
    } catch (err) {
      setError(err.message || String(err))
    } finally {
      setBusy(false)
    }
  }

  const [cls, line] = serviceLine(st)
  const noModels = models && (!models.models || models.models.length === 0)
  const rec = result && result.ok ? result.record : null
  return (
    <div className="account-page robin-page">
      <div className="card">
        <h2>Robin — بحث استخباراتي في الويب المظلم</h2>
        <p className="account-hint">
          يحسّن سؤالك بنموذج ذكاء اصطناعي، ويبحث في محركات بحث الويب المظلم عبر Tor، ويقرأ الصفحات،
          ثم يكتب تقريرًا. للاستخدام المشروع فقط: أبحاث أمنية وتهديدات وتسريبات شركات.
          البحث عن أشخاص بعينهم غير متاح هنا، ونتائج مواد إساءة الأطفال تُحذف دائمًا.
        </p>
        <span className={`badge ${cls}`} data-testid="robin-service">{line}</span>
      </div>

      {!signed && (
        <div className="card"><p className="account-hint" data-testid="robin-signin">
          سجّل الدخول من تبويب «الحساب» لاستخدام Robin.
        </p></div>
      )}

      {signed && st && st.reachable && (
        <form className="card robin-form" onSubmit={submit} data-testid="robin-form">
          <label>السؤال
            <input value={form.query} maxLength={300} required minLength={3} dir="auto"
              onChange={(e) => setForm({ ...form, query: e.target.value })}
              placeholder="مثال: ransomware group leak site" />
          </label>
          <label>نوع التقرير
            <select value={form.preset} onChange={(e) => setForm({ ...form, preset: e.target.value })}>
              {Object.entries(PRESETS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
          <label>النموذج
            <select value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} disabled={!models || noModels}>
              {(models && models.models || []).map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
          {noModels && <p className="badge badge-info" data-testid="robin-no-models">
            لا يوجد نموذج متاح: يحتاج المالك إلى وضع مفتاح نموذج (مثل OPENROUTER_API_KEY) في إعدادات خدمة Robin.
          </p>}
          <div className="account-actions">
            <button className="btn" type="submit" disabled={busy || !form.model || form.query.trim().length < 3}>
              {busy ? 'جارٍ البحث… (قد يستغرق عدة دقائق)' : 'ابدأ البحث'}
            </button>
          </div>
        </form>
      )}

      {error && <div className="card"><p role="alert" className="badge badge-error">{error}</p></div>}
      {result && !result.ok && (
        <div className="card"><p role="alert" className="badge badge-error" data-testid="robin-failed">
          فشلت مرحلة «{result.stage}»: {result.error}
        </p></div>
      )}
      {rec && (
        <div className="card" data-testid="robin-result">
          <h3>{STATUS_TEXT[rec.status] || rec.status}</h3>
          {rec.refined_query && <p className="account-hint">السؤال بعد التحسين: <bdi>{rec.refined_query}</bdi></p>}
          <p className="account-hint">
            نتائج البحث: {rec.results_count ?? '—'} · صفحات مقروءة: {rec.scraped_count ?? '—'}
            {rec.sources_dropped > 0 && ` · حُذف ${rec.sources_dropped} مصدر بفلتر الحماية`}
            {result.seconds != null && ` · ${result.seconds} ث`}
          </p>
          {rec.summary && <pre className="robin-summary" dir="auto" data-testid="robin-summary">{rec.summary}</pre>}
          <Sources list={rec.sources} />
          {rec.pivots && rec.pivots.length > 0 && (
            <p className="account-hint">أسئلة للمتابعة: {rec.pivots.join(' · ')}</p>
          )}
        </div>
      )}
    </div>
  )
}
