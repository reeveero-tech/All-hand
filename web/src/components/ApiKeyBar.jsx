import { useState, useEffect } from 'react'
import { getApiKey, setApiKey, clearApiKey, AUTH_EVENT } from '../api.js'

// R-101: shown only when the server reports security.authentication === 'enabled'
// (or after a 401/403), so open-mode deployments look exactly as before.
export default function ApiKeyBar({ authEnabled }) {
  const [saved, setSaved] = useState(Boolean(getApiKey()))
  const [draft, setDraft] = useState('')
  const [rejected, setRejected] = useState(null)

  useEffect(() => {
    const onAuth = (e) => {
      setRejected(e.detail?.status || 401)
      setSaved(Boolean(getApiKey()))
    }
    window.addEventListener(AUTH_EVENT, onAuth)
    return () => window.removeEventListener(AUTH_EVENT, onAuth)
  }, [])

  if (!authEnabled && !rejected) return null

  const save = (e) => {
    e.preventDefault()
    if (!draft.trim()) return
    if (!setApiKey(draft)) {
      setRejected('storage')
      return
    }
    setDraft('')
    setSaved(true)
    setRejected(null)
  }

  const forget = () => {
    clearApiKey()
    setSaved(false)
  }

  let note = saved ? 'المفتاح محفوظ لهذه الجلسة فقط' : 'الخادم يعمل بوضع المصادقة — أدخل مفتاح API لإنشاء المهام'
  if (rejected === 403) note = 'المفتاح مرفوض — أدخل المفتاح الصحيح'
  else if (rejected === 401 && !saved) note = 'هذه العملية تتطلب مفتاح API'
  else if (rejected === 'storage') note = 'تخزين الجلسة معطل في المتصفح — لا يمكن حفظ المفتاح'

  return (
    <form className="api-key-bar" onSubmit={save} aria-label="مفتاح API">
      <span className={`badge ${saved && rejected !== 403 ? 'badge-success' : 'badge-warning'}`}>
        {saved ? 'مفتاح مضبوط' : 'بلا مفتاح'}
      </span>
      <input
        className="form-input api-key-input"
        type="password"
        autoComplete="off"
        placeholder="X-API-Key"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
      />
      <button className="btn btn-primary" type="submit" disabled={!draft.trim()}>حفظ</button>
      {saved && <button className="btn" type="button" onClick={forget}>مسح</button>}
      <span className="api-key-note">{note}</span>
    </form>
  )
}
