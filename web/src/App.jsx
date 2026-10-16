import { useState, useEffect, useCallback } from 'react'
import Dashboard from './components/Dashboard.jsx'
import Chat from './components/Chat.jsx'
import Specializations from './components/Specializations.jsx'
import Agents from './components/Agents.jsx'
import Tasks from './components/Tasks.jsx'
import ApiKeyBar from './components/ApiKeyBar.jsx'
import { fetchSystemStatus, openEventStream } from './api.js'

export default function App() {
  const [tab, setTab] = useState('dashboard')
  const [status, setStatus] = useState(null)
  const [liveEvent, setLiveEvent] = useState(null)

  const refreshStatus = useCallback(async () => {
    try {
      const s = await fetchSystemStatus()
      setStatus(s)
    } catch (e) {
      console.error('status error', e)
    }
  }, [])

  useEffect(() => {
    refreshStatus()
    const interval = setInterval(refreshStatus, 10000)

    // SEC-02: the live stream is per tenant and needs the key (fetch, not EventSource)
    let es
    try {
      es = openEventStream((data) => {
        if (data.type === 'stream_gap') {
          // SEC-04: events were dropped for this slow listener — re-read state
          refreshStatus()
        } else if (data.type !== 'heartbeat') {
          setLiveEvent(data)
          if (data.type === 'task_completed' || data.type === 'task_created') {
            refreshStatus()
          }
        }
      })
    } catch {}

    return () => {
      clearInterval(interval)
      if (es) es.close()
    }
  }, [refreshStatus])

  const tabs = [
    { id: 'dashboard', label: 'لوحة التحكم', icon: '◈' },
    { id: 'chat', label: 'المحادثة', icon: '✦' },
    { id: 'specializations', label: 'التخصصات', icon: '◆' },
    { id: 'agents', label: 'الوكلاء', icon: '◇' },
    { id: 'tasks', label: 'المهام', icon: '▢' },
  ]

  return (
    <div className="app">
      {/* R-101: header and key bar stick together, so the key field the error
          messages point to ("أعلى الصفحة") is always visible. */}
      <div className="app-top">
      <header className="app-header">
        <div className="app-header-brand">
          <div className="app-logo">A</div>
          <div>
            <div className="app-title">AGOS Civilization</div>
            <div className="app-subtitle">v3.0 · حضارة الوكلاء</div>
          </div>
        </div>
        <nav className="nav-tabs">
          {tabs.map((t) => (
            <button
              key={t.id}
              className={`nav-tab ${tab === t.id ? 'active' : ''}`}
              onClick={() => setTab(t.id)}
            >
              <span style={{ marginLeft: '6px' }}>{t.icon}</span>
              {t.label}
            </button>
          ))}
        </nav>
      </header>
      <ApiKeyBar authEnabled={status?.security?.authentication === 'enabled'} />
      </div>

      <main className="app-body">
        {tab === 'dashboard' && <Dashboard status={status} liveEvent={liveEvent} />}
        {tab === 'chat' && <Chat status={status} />}
        {tab === 'specializations' && <Specializations status={status} />}
        {tab === 'agents' && <Agents />}
        {tab === 'tasks' && <Tasks liveEvent={liveEvent} />}
      </main>
    </div>
  )
}
