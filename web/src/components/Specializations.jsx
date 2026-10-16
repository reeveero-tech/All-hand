import { useState, useEffect } from 'react'
import { fetchSpecializations, createTask } from '../api.js'

const ICONS = {
  'chart-line': '📊', 'plug': '🔌', 'building': '🏗️', 'hammer': '🔨',
  'code': '💻', 'comments': '💬', 'calculator': '🧮', 'database': '🗄️',
  'palette': '🎨', 'graduation-cap': '🎓', 'edit': '✏️', 'monitor-heart': '📈',
  'network-wired': '🌐', 'search': '🔍', 'check-double': '✔️', 'seedling': '🌱',
  'chess': '♟️', 'syringe': '💉', 'vial': '🧪', 'shield-check': '🛡️',
  'robot': '🤖',
}

export default function Specializations({ status }) {
  const [specs, setSpecs] = useState([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState(null)
  const [prompt, setPrompt] = useState('')
  const [taskData, setTaskData] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState(null)
  const [publicLlmConsent, setPublicLlmConsent] = useState(false)

  // SEC-06 (R-145): same per-request consent gate as the chat — the public
  // (keyless) LLM provider is operator-enabled: warn and require an explicit,
  // unpersisted, per-task consent before anything is sent.
  const publicLlmActive = status?.llm_provider === 'keyless'

  // Stale consent is never sent (see Chat.jsx): the checkbox is cleared
  // whenever the active provider changes, so allow_public_llm is only sent
  // while the public provider is actually serving this request.
  useEffect(() => {
    setPublicLlmConsent(false)
  }, [status?.llm_provider])

  useEffect(() => {
    async function load() {
      try {
        const data = await fetchSpecializations()
        setSpecs(data.specializations || [])
      } catch (e) {
        console.error('specs error', e)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  const handleSubmit = async () => {
    if (!selected || !prompt.trim()) return
    if (publicLlmActive && !publicLlmConsent) {
      // Refusal: nothing is sent; the reason is shown, not hidden behind
      // a spinner or a fake success.
      setResult({ ok: false, error: 'لم يُرسل الطلب: مزود LLM عام خارجي مفعّل على هذا الخادم ويحتاج موافقتك الصريحة (أسفل النموذج) قبل إرسال نص المهمة إليه' })
      return
    }
    setSubmitting(true)
    setResult(null)
    try {
      let data = null
      if (taskData.trim()) {
        try { data = JSON.parse(taskData) } catch { data = taskData }
      }
      const res = await createTask(selected, prompt, data, publicLlmActive && publicLlmConsent)
      setResult({ ok: true, task_id: res.task_id, status: res.status })
    } catch (e) {
      setResult({ ok: false, error: e.message })
    } finally {
      setSubmitting(false)
      // per-request consent: never carries over to the next task
      setPublicLlmConsent(false)
    }
  }

  if (loading) {
    return <div className="loading"><div className="spinner" /> جاري تحميل التخصصات...</div>
  }

  if (selected) {
    const spec = specs.find((s) => s.name === selected)
    return (
      <div>
        <div className="card" style={{ marginBottom: '16px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
            <div className="spec-icon" style={{ marginBottom: 0 }}>
              {ICONS[spec?.icon] || '🤖'}
            </div>
            <div>
              <div className="spec-name">{spec?.name}</div>
              <div className="spec-desc" style={{ margin: 0 }}>{spec?.description}</div>
            </div>
            <button className="btn btn-ghost" style={{ marginRight: 'auto' }} onClick={() => { setSelected(null); setResult(null) }}>
              رجوع
            </button>
          </div>

          {spec?.tool && (
            <div className="badge badge-info" style={{ marginBottom: '12px' }}>
              أداة: {spec.tool}
            </div>
          )}

          <div className="form-group">
            <label className="form-label">المهمة / الطلب</label>
            <textarea
              className="form-input"
              placeholder="اكتب ما تريد من الوكيل القيام به..."
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={3}
            />
          </div>

          {(selected === 'data_processor' || selected === 'validator' || selected === 'test_runner') && (
            <div className="form-group">
              <label className="form-label">
                {selected === 'test_runner' ? 'كود Python لتنفيذه' : 'البيانات (JSON أو أرقام)'}
              </label>
              <textarea
                className="form-input"
                placeholder={selected === 'test_runner' ? 'print("Hello")' : '{"key": "value"} أو [1, 2, 3]'}
                value={taskData}
                onChange={(e) => setTaskData(e.target.value)}
                rows={4}
              />
            </div>
          )}

          {publicLlmActive && (
            <div style={{
              padding: '10px 14px', background: 'var(--bg-elevated)',
              borderRadius: 'var(--radius-sm)', border: '1px solid var(--warning, #e6a700)',
              fontSize: '13px', marginBottom: '12px',
            }}>
              <div style={{ fontWeight: 600, marginBottom: '6px' }}>
                تنبيه: مزود LLM عام خارجي (بلا مفتاح) مفعّل على هذا الخادم
              </div>
              <div style={{ color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                بموافقتك قد يُرسل نص هذه المهمة والتعليمات ذات الصلة إلى مزود خارجي (Pollinations
                ومزودو النماذج خلفه). لا ترسل كلمات مرور أو مفاتيح أو بيانات سرية أو شخصية
                حساسة إلا إذا كان لديك تفويض وسياسة بيانات مناسبة. موافقتك تخص هذه المهمة وحدها.
              </div>
              <label style={{ display: 'flex', gap: '8px', alignItems: 'center', marginTop: '8px', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={publicLlmConsent}
                  onChange={(e) => setPublicLlmConsent(e.target.checked)}
                  disabled={submitting}
                />
                <span>أوافق على إرسال نص هذه المهمة إلى المزود العام الخارجي</span>
              </label>
            </div>
          )}

          <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
            <button className="btn btn-primary" onClick={handleSubmit} disabled={submitting || !prompt.trim()}>
              {submitting ? 'جاري الإرسال...' : 'إرسال المهمة'}
            </button>
            {result && (
              <div>
                {result.ok ? (
                  <span className="badge badge-success">تم الإرسال: {result.task_id}</span>
                ) : (
                  <span className="badge badge-error">خطأ: {result.error}</span>
                )}
              </div>
            )}
          </div>
          <div style={{ marginTop: '12px', fontSize: '13px', color: 'var(--text-muted)' }}>
            يمكنك تتبع المهمة في تبويب "المهام"
          </div>
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className="card" style={{ marginBottom: '24px' }}>
        <div className="card-title">20 تخصص · {specs.length} وكيل متاح</div>
        <div style={{ fontSize: '14px', color: 'var(--text-secondary)' }}>
          كل تخصص يحتوي على وكلاء حقيقيين يعملون بنماذج LLM وأدوات فعلية. اختر تخصصاً لإرسال مهمة.
        </div>
      </div>

      <div className="spec-grid">
        {specs.map((spec) => (
          <div key={spec.name} className="spec-card" onClick={() => setSelected(spec.name)}>
            <div className="spec-icon">{ICONS[spec.icon] || '🤖'}</div>
            <div className="spec-name">{spec.name}</div>
            <div className="spec-desc">{spec.description}</div>
            <div className="spec-meta">
              <span>{spec.agents} وكيل</span>
              {spec.tool && <span className="spec-tool">⚙ {spec.tool}</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
