import { useState, useRef, useEffect } from 'react'
import { sendChat } from '../api.js'

const QUICK_PROMPTS = [
  'حلل أداء النظام الحالي',
  'ابحث عن أحدث تقنيات الذكاء الاصطناعي',
  'اكتب كود Python لحساب متوسط قائمة أرقام',
  'تحقق من صحة هذا JSON: {"name":"test","value":42}',
  'صمم استراتيجية لتطوير الحضارة',
]

export default function Chat({ status }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [publicLlmConsent, setPublicLlmConsent] = useState(false)
  const messagesEnd = useRef(null)

  // SEC-06 (R-145): the public (keyless) LLM provider is operator-enabled —
  // warn before any send and require explicit per-message consent. The
  // checkbox starts unchecked, is not persisted, and is cleared after every
  // send: consent covers exactly one request, never the session.
  const publicLlmActive = status?.llm_provider === 'keyless'

  // Stale consent is never sent: if the provider changes (or the status is
  // still unknown), the checkbox disappears and its value is cleared — the
  // request body only carries allow_public_llm when the public provider is
  // actually active and the user re-checked the box for this request.
  useEffect(() => {
    setPublicLlmConsent(false)
  }, [status?.llm_provider])

  useEffect(() => {
    messagesEnd.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSend = async (text) => {
    const prompt = (text || input).trim()
    if (!prompt || loading) return

    if (publicLlmActive && !publicLlmConsent) {
      // Refusal: nothing is sent — no request leaves the browser, and the
      // reason is shown instead of a loading state or a fake success.
      setMessages((prev) => [...prev, {
        role: 'agent',
        content: 'لم يُرسل الطلب: مزود LLM عام خارجي مفعّل على هذا الخادم ويحتاج موافقتك الصريحة قبل إرسال نص رسالتك إليه. أقرأ التنبيه أسفل الحقل وفعّل خيار الموافقة ثم أعد الإرسال. يمكنك أيضًا مراسلة المشغّل لضبط OPENROUTER_API_KEY.',
        agent: 'system',
        specialization: 'consent',
      }])
      return
    }

    setInput('')
    setMessages((prev) => [...prev, { role: 'user', content: prompt }])
    setLoading(true)

    try {
      const res = await sendChat(prompt, publicLlmActive && publicLlmConsent)
      const answer = res.outcome?.result?.answer || res.outcome?.result?.error || 'لا يوجد رد'
      setMessages((prev) => [...prev, {
        role: 'agent',
        content: answer,
        agent: res.agent,
        specialization: res.routed_to,
        elapsed: res.outcome?.elapsed_s,
      }])
    } catch (e) {
      setMessages((prev) => [...prev, {
        role: 'agent',
        content: `خطأ: ${e.message}`,
        agent: 'system',
        specialization: 'error',
      }])
    } finally {
      setLoading(false)
      // per-request consent: it never carries over to the next message
      setPublicLlmConsent(false)
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="chat-container">
      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="empty-state">
            <div className="empty-state-icon">✦</div>
            <div>ابدأ محادثة مع حضارة الوكلاء</div>
            <div style={{ marginTop: '20px', display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'center' }}>
              {QUICK_PROMPTS.map((p) => (
                <button key={p} className="btn btn-ghost" style={{ fontSize: '13px' }} onClick={() => handleSend(p)}>
                  {p}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`chat-message ${msg.role}`}>
            <div className={`chat-avatar ${msg.role}`}>
              {msg.role === 'user' ? 'أنت' : 'A'}
            </div>
            <div>
              <div className="chat-bubble">{msg.content}</div>
              <div className="chat-meta">
                {msg.role === 'agent' && msg.agent && `${msg.specialization} · ${msg.agent}`}
                {msg.elapsed && ` · ${msg.elapsed}s`}
              </div>
            </div>
          </div>
        ))}

        {loading && (
          <div className="chat-message agent">
            <div className="chat-avatar agent">A</div>
            <div className="chat-bubble">
              <div className="spinner" style={{ width: '16px', height: '16px', borderWidth: '2px' }} />
              <span style={{ marginRight: '8px' }}>الوكيل يفكر...</span>
            </div>
          </div>
        )}

        <div ref={messagesEnd} />
      </div>

      <div className="chat-input-area">
        {publicLlmActive && (
          <div style={{
            width: '100%', marginBottom: '10px', padding: '10px 14px',
            background: 'var(--bg-elevated)', borderRadius: 'var(--radius-sm)',
            border: '1px solid var(--warning, #e6a700)', fontSize: '13px',
          }}>
            <div style={{ fontWeight: 600, marginBottom: '6px' }}>
              تنبيه: مزود LLM عام خارجي (بلا مفتاح) مفعّل على هذا الخادم
            </div>
            <div style={{ color: 'var(--text-secondary)', lineHeight: 1.6 }}>
              بموافقتك قد يُرسل نص رسالتك والتعليمات ذات الصلة إلى مزود خارجي (Pollinations
              ومزودو النماذج خلفه) لتوليد الرد. لا ترسل كلمات مرور أو مفاتيح أو بيانات سرية أو
              شخصية حساسة إلا إذا كان لديك تفويض وسياسة بيانات مناسبة. موافقتك تخص هذا الطلب
              وحده ولا تُحفظ.
            </div>
            <label style={{ display: 'flex', gap: '8px', alignItems: 'center', marginTop: '8px', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={publicLlmConsent}
                onChange={(e) => setPublicLlmConsent(e.target.checked)}
                disabled={loading}
              />
              <span>أوافق على إرسال نص هذا الطلب إلى المزود العام الخارجي</span>
            </label>
          </div>
        )}
        <textarea
          className="chat-input"
          placeholder="اكتب رسالتك هنا... (Enter للإرسال، Shift+Enter لسطر جديد)"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={1}
          disabled={loading}
        />
        <button className="btn btn-primary" onClick={() => handleSend()} disabled={loading || !input.trim()}>
          إرسال
        </button>
      </div>
    </div>
  )
}
