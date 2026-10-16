import { useState, useEffect } from 'react'
import { fetchEvents } from '../api.js'

export default function Dashboard({ status, liveEvent }) {
  const [events, setEvents] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let mounted = true
    async function load() {
      try {
        const data = await fetchEvents(20)
        if (mounted) setEvents(data.events || [])
      } catch (e) {
        console.error('events error', e)
      } finally {
        if (mounted) setLoading(false)
      }
    }
    load()
    const interval = setInterval(load, 15000)
    return () => { mounted = false; clearInterval(interval) }
  }, [liveEvent])

  if (!status) {
    return <div className="loading"><div className="spinner" /> جاري التحميل...</div>
  }

  const uptime = status.uptime_s || 0
  const hours = Math.floor(uptime / 3600)
  const mins = Math.floor((uptime % 3600) / 60)
  const secs = Math.floor(uptime % 60)

  return (
    <div>
      <div className="dashboard-grid">
        <div className="stat-card">
          <div className="stat-label">الوكلاء النشطون</div>
          <div className="stat-value accent">{status.total_agents}</div>
          <div className="stat-sub">{status.specializations} تخصص · {status.external_agents} خارجي</div>
        </div>

        <div className="stat-card">
          <div className="stat-label">المهام المكتملة</div>
          <div className="stat-value success">{status.tasks_completed}</div>
          <div className="stat-sub">{status.tasks_failed} فشلت</div>
        </div>

        <div className="stat-card">
          <div className="stat-label">نموذج LLM</div>
          <div className="stat-value" style={{ fontSize: '14px' }}>
            {/* SEC-06 (R-145): the provider is disclosed honestly — a public
                keyless provider is shown as what it is, never as a keyed one. */}
            {status.llm_provider === 'openrouter'
              ? (status.llm_model || 'OpenRouter')
              : status.llm_provider === 'keyless' ? 'مزود عام (بلا مفتاح)' : 'غير مُهيأ'}
          </div>
          <div className="stat-sub">
            {status.llm_provider === 'openrouter' ? (
              <span className="badge badge-success"><span className="badge-dot pulse" /> متصل (OpenRouter)</span>
            ) : status.llm_provider === 'keyless' ? (
              <span className="badge badge-warning"><span className="badge-dot" /> عام · يتطلب موافقتك لكل طلب</span>
            ) : (
              <span className="badge badge-warning"><span className="badge-dot" /> لا مزود — يحتاج OPENROUTER_API_KEY أو تفعيل المشغّل للمزود العام</span>
            )}
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-label">قاعدة البيانات</div>
          <div className="stat-value" style={{ fontSize: '16px' }}>
            {status.supabase_configured ? 'Supabase' : 'في الذاكرة'}
          </div>
          <div className="stat-sub">
            {status.tasks_in_db !== null
              ? `${status.tasks_in_db} مهمة محفوظة`
              : `${status.tasks_in_memory} في الذاكرة`}
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-label">مدة التشغيل</div>
          <div className="stat-value" style={{ fontSize: '20px' }}>
            {hours}س {mins}د {secs}ث
          </div>
          <div className="stat-sub">
            <span className="badge badge-success"><span className="badge-dot pulse" /> يعمل</span>
          </div>
        </div>

        <div className="stat-card">
          <div className="stat-label">آخر حدث</div>
          <div className="stat-value" style={{ fontSize: '14px' }}>
            {liveEvent ? liveEvent.type : 'في انتظار...'}
          </div>
          <div className="stat-sub">
            {liveEvent && liveEvent.agent ? `الوكيل: ${liveEvent.agent}` : '\u00A0'}
          </div>
        </div>
      </div>

      <div className="card" style={{ marginTop: '24px' }}>
        <div className="card-title">سجل الأحداث المباشر</div>
        {loading ? (
          <div className="loading"><div className="spinner" /> جاري التحميل...</div>
        ) : events.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">◌</div>
            <div>لا توجد أحداث بعد</div>
          </div>
        ) : (
          <div className="task-list">
            {events.map((ev) => (
              <div key={ev.id || ev.created_at} className="task-item">
                <div className={`task-status-icon ${ev.event_type === 'task_completed' ? 'completed' : 'queued'}`}>
                  {ev.event_type === 'task_completed' ? '✓' : '→'}
                </div>
                <div className="task-content">
                  <div className="task-header">
                    <span className="task-spec">{ev.event_type}</span>
                  </div>
                  <div className="task-prompt">
                    {ev.event_data ? JSON.stringify(ev.event_data).slice(0, 100) : ''}
                  </div>
                  <div className="task-meta">
                    {new Date(ev.created_at).toLocaleString('ar')}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
