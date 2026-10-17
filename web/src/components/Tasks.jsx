import { useState, useEffect, useCallback } from 'react'
import { fetchTasks } from '../api.js'
import { taskView } from '../taskView.js'

function TaskResult({ result }) {
  if (!result) return null
  const r = result.result || {}
  const answer = r.answer || r.error || null
  const toolOutput = r.tool_output

  return (
    <div style={{ marginTop: '12px' }}>
      {toolOutput && (
        <div style={{ background: 'var(--bg-elevated)', borderRadius: 'var(--radius-sm)', padding: '10px 14px', marginBottom: '8px', fontSize: '13px' }}>
          <div style={{ color: 'var(--accent)', marginBottom: '4px', fontFamily: 'var(--font-mono)' }}>
            أداة: {toolOutput.tool}
          </div>
          <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: 'var(--text-secondary)', fontSize: '12px' }}>
            {JSON.stringify(toolOutput, null, 2).slice(0, 500)}
          </pre>
        </div>
      )}
      {answer && (
        <div style={{ background: 'var(--bg-elevated)', borderRadius: 'var(--radius-sm)', padding: '10px 14px', fontSize: '14px', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>
          {answer.slice(0, 800)}
          {answer.length > 800 && '...'}
        </div>
      )}
    </div>
  )
}

export default function Tasks({ liveEvent }) {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('all')
  const [expanded, setExpanded] = useState(null)

  const load = useCallback(async () => {
    try {
      const data = await fetchTasks(100)
      setTasks(data.tasks || [])
    } catch (e) {
      console.error('tasks error', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const interval = setInterval(load, 10000)
    return () => clearInterval(interval)
  }, [load])

  useEffect(() => {
    if (liveEvent && (liveEvent.type === 'task_completed' || liveEvent.type === 'task_created')) {
      load()
    }
  }, [liveEvent, load])

  const statusMap = { completed: '✓', failed: '✗', running: '⟳', queued: '○' }

  const filtered = filter === 'all' ? tasks : tasks.filter((t) => taskView(t).status === filter)

  const counts = tasks.reduce((acc, t) => {
    const s = taskView(t).status
    acc[s] = (acc[s] || 0) + 1
    return acc
  }, {})

  if (loading) {
    return <div className="loading"><div className="spinner" /> جاري تحميل المهام...</div>
  }

  return (
    <div>
      <div className="card" style={{ marginBottom: '24px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '12px' }}>
          <div>
            <div className="card-title" style={{ marginBottom: '4px' }}>{tasks.length} مهمة</div>
            <div style={{ display: 'flex', gap: '12px', fontSize: '13px', flexWrap: 'wrap' }}>
              <span className="badge badge-success">{counts.completed || 0} مكتملة</span>
              <span className="badge badge-error">{counts.failed || 0} فشلت</span>
              <span className="badge badge-info">{(counts.running || 0) + (counts.queued || 0)} قيد التشغيل</span>
            </div>
          </div>
          <button className="btn btn-ghost" onClick={load}>تحديث</button>
        </div>

        <div style={{ display: 'flex', gap: '8px', marginTop: '16px', flexWrap: 'wrap' }}>
          {['all', 'completed', 'failed', 'running', 'queued'].map((f) => (
            <button
              key={f}
              className={`btn ${filter === f ? 'btn-primary' : 'btn-ghost'}`}
              style={{ fontSize: '13px' }}
              onClick={() => setFilter(f)}
            >
              {f === 'all' ? 'الكل' : f === 'completed' ? 'مكتملة' : f === 'failed' ? 'فشلت' : f === 'running' ? 'تعمل' : 'في الانتظار'}
            </button>
          ))}
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">▢</div>
          <div>لا توجد مهام</div>
        </div>
      ) : (
        <div className="task-list">
          {filtered.map((task) => {
            const view = taskView(task)   // R-160: every record variant
            const taskId = view.id
            const status = view.status
            const isExpanded = expanded === taskId

            return (
              <div key={taskId} className="task-item" style={{ flexDirection: 'column', gap: '0', cursor: 'pointer' }}
                   onClick={() => setExpanded(isExpanded ? null : taskId)}>
                <div style={{ display: 'flex', gap: '12px', width: '100%' }}>
                  <div className={`task-status-icon ${status}`}>
                    {statusMap[status] || '?'}
                  </div>
                  <div className="task-content">
                    <div className="task-header">
                      <span className="task-spec">{view.specialization}</span>
                      {view.agent && (
                        <span className="badge badge-info" style={{ fontSize: '11px' }}>{view.agent}</span>
                      )}
                      <span className={`badge ${status === 'completed' ? 'badge-success' : status === 'failed' ? 'badge-error' : 'badge-info'}`}
                            style={{ marginRight: 'auto', fontSize: '11px' }}>
                        {status}
                      </span>
                    </div>
                    <div className="task-prompt">{view.prompt}</div>
                    <div className="task-meta">
                      <span>{taskId}</span>
                      {view.elapsedS !== null && <span>{view.elapsedS}s</span>}
                      {view.createdMs !== null && <span>{new Date(view.createdMs).toLocaleString('ar')}</span>}
                    </div>
                  </div>
                </div>

                {isExpanded && view.result && <TaskResult result={view.result} />}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
