import { useState, useEffect } from 'react'
import { fetchAgents, fetchSpecializations, registerExternalAgent, removeExternalAgent } from '../api.js'

export default function Agents() {
  const [agents, setAgents] = useState([])
  const [specs, setSpecs] = useState([])
  const [filter, setFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)
  const [formData, setFormData] = useState({ name: '', url: '', model: '', api_key: '' })

  const load = async () => {
    try {
      const [agentsData, specsData] = await Promise.all([
        fetchAgents(),
        fetchSpecializations(),
      ])
      setAgents(agentsData.agents || [])
      setSpecs(specsData.specializations || [])
    } catch (e) {
      console.error('agents error', e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    const interval = setInterval(load, 20000)
    return () => clearInterval(interval)
  }, [])

  const handleRegister = async () => {
    if (!formData.name.trim() || !formData.url.trim()) return
    try {
      await registerExternalAgent(formData.name, formData.url, formData.model, formData.api_key)
      setShowModal(false)
      setFormData({ name: '', url: '', model: '', api_key: '' })
      load()
    } catch (e) {
      alert('خطأ: ' + e.message)
    }
  }

  const handleRemove = async (name) => {
    if (!confirm(`حذف الوكيل "${name}"?`)) return
    try {
      await removeExternalAgent(name)
      load()
    } catch (e) {
      alert('خطأ: ' + e.message)
    }
  }

  // R-201: the local "agents" are identical generated copies of ONE real
  // handler per specialization (R-200). Show one card per handler with its
  // copy count and live status; external agents are real and listed one by one.
  const local = agents.filter((a) => a.specialization !== 'external')
  const external = agents.filter((a) => a.specialization === 'external')
  const workingNow = agents.filter((a) => a.status === 'working').length
  const handlers = {}
  local.forEach((a) => {
    const h = handlers[a.specialization] || (handlers[a.specialization] = {
      name: a.specialization, copies: 0, working: 0, completed: 0, failed: 0, time: 0,
    })
    h.copies += 1
    if (a.status === 'working') h.working += 1
    h.completed += a.stats?.tasks_completed || 0
    h.failed += a.stats?.tasks_failed || 0
    h.time += a.stats?.total_time_s || 0
  })
  const handlerList = Object.values(handlers).filter((h) => !filter || h.name === filter)
  const externalList = !filter || filter === 'external' ? external : []

  if (loading) {
    return <div className="loading"><div className="spinner" /> جاري تحميل الوكلاء...</div>
  }

  return (
    <div>
      <div className="card" style={{ marginBottom: '24px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '12px' }}>
          <div>
            <div className="card-title" style={{ marginBottom: '4px' }}>
              {Object.keys(handlers).length} معالجات حقيقية · {local.length} نسخة مسجلة · {workingNow} تعمل الآن
            </div>
            <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
              النسخ في كل تخصص نسخ متطابقة من معالج واحد · {external.length} وكلاء خارجيون
            </div>
          </div>
          <button className="btn btn-primary" onClick={() => setShowModal(true)}>
            + إضافة وكيل خارجي
          </button>
        </div>

        <div style={{ display: 'flex', gap: '8px', marginTop: '16px', flexWrap: 'wrap' }}>
          <button
            className={`btn ${filter === '' ? 'btn-primary' : 'btn-ghost'}`}
            style={{ fontSize: '13px' }}
            onClick={() => setFilter('')}
          >
            الكل
          </button>
          {specs.map((s) => (
            <button
              key={s.name}
              className={`btn ${filter === s.name ? 'btn-primary' : 'btn-ghost'}`}
              style={{ fontSize: '13px' }}
              onClick={() => setFilter(s.name)}
            >
              {s.name}
            </button>
          ))}
        </div>
      </div>

      <div className="agent-list" style={{ marginBottom: '24px' }}>
        {handlerList.map((h) => (
          <div key={h.name} className="agent-card">
            <div className="agent-name">{h.name}</div>
            <div className="agent-spec">
              {h.copies} نسخ متطابقة · {h.working > 0 ? `يعمل الآن (${h.working})` : 'خامل'}
            </div>
            <div className="agent-stats">
              <div className="agent-stat">
                <span className="agent-stat-label">مكتملة</span>
                <span className="agent-stat-value success">{h.completed}</span>
              </div>
              <div className="agent-stat">
                <span className="agent-stat-label">فشلت</span>
                <span className="agent-stat-value error">{h.failed}</span>
              </div>
              <div className="agent-stat">
                <span className="agent-stat-label">الوقت</span>
                <span className="agent-stat-value">{h.time.toFixed(1)}s</span>
              </div>
            </div>
          </div>
        ))}
        {externalList.map((agent) => (
          <div key={agent.name} className="agent-card">
            <div className="agent-name">{agent.name}</div>
            <div className="agent-spec">
              <span className="badge badge-warning" style={{ marginRight: '6px' }}>خارجي</span>
              {agent.status === 'idle' ? 'خامل' : agent.status === 'working' ? 'يعمل' : agent.status}
            </div>
            <div className="agent-stats">
              <div className="agent-stat">
                <span className="agent-stat-label">مكتملة</span>
                <span className="agent-stat-value success">{agent.stats?.tasks_completed || 0}</span>
              </div>
              <div className="agent-stat">
                <span className="agent-stat-label">فشلت</span>
                <span className="agent-stat-value error">{agent.stats?.tasks_failed || 0}</span>
              </div>
              <div className="agent-stat">
                <span className="agent-stat-label">الوقت</span>
                <span className="agent-stat-value">{(agent.stats?.total_time_s || 0).toFixed(1)}s</span>
              </div>
            </div>
            <button
              className="btn btn-danger"
              style={{ marginTop: '12px', fontSize: '12px', padding: '4px 10px' }}
              onClick={() => handleRemove(agent.name)}
            >
              حذف
            </button>
          </div>
        ))}
      </div>

      {showModal && (
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-title">إضافة وكيل خارجي</div>
            <div style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '16px' }}>
              أي وكيل يدعم OpenAI-compatible API (Ollama, LM Studio, vLLM...)
            </div>
            <div className="form-group">
              <label className="form-label">الاسم</label>
              <input
                className="form-input"
                placeholder="my-ollama"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              />
            </div>
            <div className="form-group">
              <label className="form-label">URL (endpoint)</label>
              <input
                className="form-input"
                placeholder="http://localhost:11434/v1/chat/completions"
                value={formData.url}
                onChange={(e) => setFormData({ ...formData, url: e.target.value })}
              />
            </div>
            <div className="form-group">
              <label className="form-label">النموذج (اختياري)</label>
              <input
                className="form-input"
                placeholder="llama3"
                value={formData.model}
                onChange={(e) => setFormData({ ...formData, model: e.target.value })}
              />
            </div>
            <div className="form-group">
              <label className="form-label">API Key (اختياري)</label>
              <input
                className="form-input"
                type="password"
                placeholder="sk-..."
                value={formData.api_key}
                onChange={(e) => setFormData({ ...formData, api_key: e.target.value })}
              />
            </div>
            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setShowModal(false)}>إلغاء</button>
              <button className="btn btn-primary" onClick={handleRegister}>إضافة</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
