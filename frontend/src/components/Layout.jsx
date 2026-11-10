import React, { useCallback, useEffect, useState } from 'react'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { api, tokens } from '../api'
import { ROLE_LABEL, useAuth } from '../context/AuthContext'
import { WorkspaceTaskProvider } from '../context/WorkspaceTaskContext'
import { fmt, ToastProvider, useToast } from './ui'
import WorkspaceRecorder from './WorkspaceRecorder'

const NAV = {
  admin: [
    ['/admin', '⌂', 'Overview'],
    ['/users', '👥', 'People'],
    ['/tasks', '☑', 'Tasks'],
    ['/reviews', '⌥', 'Code reviews'],
    ['/monitoring', '◎', 'Monitoring'],
    ['/recordings', '⏺', 'Recordings'],
    ['/reports', '▤', 'Reports'],
    ['/meetings', '◷', 'Meetings'],
    ['/chat', '✉', 'Chat']
  ],
  worker: [
    ['/tasks', '☑', 'My tasks'],
    ['/workspace', '⌨', 'Coding workspace'],
    ['/reviews', '⌥', 'Reviews'],
    ['/meetings', '◷', 'Meetings'],
    ['/recordings', '⏺', 'My recordings'],
    ['/reports', '▤', 'My reports'],
    ['/chat', '✉', 'Chat']
  ]
}

function TimerWidget() {
  const { user } = useAuth()
  const [state, setState] = useState({ active: false, session: null })
  const [seconds, setSeconds] = useState(0)
  const [busy, setBusy] = useState(false)
  const toast = useToast()

  const load = useCallback(async () => {
    try {
      const { data } = await api.timerCurrent()
      setState(data)
      setSeconds(data.seconds_today || data.session?.accumulated_seconds || 0)
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    if (!user || user.role === 'admin') return
    load()
    const tick = setInterval(() => {
      setSeconds((s) => (state.session?.status === 'running' ? s + 1 : s))
    }, 1000)
    return () => clearInterval(tick)
  }, [user, load, state.session?.status])

  // Keep server heartbeats running across browser-tab switches.
  useEffect(() => {
    if (!user || user.role === 'admin' || !state.active) return
    const id = setInterval(async () => {
      try {
        const { data } = await api.heartbeat('heartbeat')
        if (data?.should_pause) {
          toast('Session paused after inactivity — press Resume', 'error')
          load()
        }
      } catch { /* offline */ }
    }, 30000)
    return () => clearInterval(id)
  }, [user, state.active, state.session?.status, load, toast])

  if (!user || user.role === 'admin') return null

  const act = async (fn, msg) => {
    setBusy(true)
    try { await fn(); toast(msg); await load() } catch (e) { toast(e?.response?.data?.detail || 'Action failed', 'error') }
    finally { setBusy(false) }
  }

  const status = state.session?.status
  return (
    <div className={`timer ${status || ''}`}>
      <span className="state" />
      <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.1 }}>
        <span className="clock">{fmt.clock(seconds)}</span>
        <span className="small muted">{(status || 'not working')}</span>
      </div>
      {!state.active && <button className="btn sm primary" disabled={busy} onClick={() => act(api.timerStart, 'Work session started')}>Start</button>}
      {status === 'running' && <button className="btn sm" disabled={busy} onClick={() => act(api.timerPause, 'Paused')}>Pause</button>}
      {status === 'paused' && <button className="btn sm success" disabled={busy} onClick={() => act(api.timerResume, 'Resumed')}>Resume</button>}
      {state.active && <button className="btn sm danger" disabled={busy} onClick={() => act(api.timerStop, 'Session stopped')}>Stop</button>}
    </div>
  )
}

function Notifications() {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState([])
  const [count, setCount] = useState(0)
  const toast = useToast()

  const load = useCallback(async () => {
    try {
      const [{ data: list }, { data: unread }] = await Promise.all([api.notifications({ limit: 15 }), api.unread()])
      setItems(list); setCount(unread.unread)
    } catch { /* ignore */ }
  }, [])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (!tokens.access) return
    let ws
    let retry
    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(tokens.access)}`)
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data)
          if (msg.channel === 'notification') {
            setCount((c) => c + 1)
            setItems((prev) => [{ ...msg.data, created_at: new Date().toISOString() }, ...prev].slice(0, 15))
            toast(msg.data.title, 'ok')
          }
        } catch { /* ignore */ }
      }
      ws.onclose = () => { retry = setTimeout(connect, 4000) }
    }
    connect()
    return () => { clearTimeout(retry); ws?.close() }
  }, [toast])

  return (
    <div style={{ position: 'relative' }}>
      <button className="btn icon ghost" onClick={() => { setOpen((o) => !o); if (!open) load() }}>
        🔔{count > 0 && <span className="nav-badge" style={{ position: 'absolute', top: -4, right: -4 }}>{count}</span>}
      </button>
      {open && (
        <div className="card" style={{ position: 'absolute', right: 0, top: 44, width: 340, zIndex: 60, padding: 10 }}>
          <div className="between" style={{ padding: '4px 6px 10px' }}>
            <strong className="small">Notifications</strong>
            <button className="btn sm ghost" onClick={async () => { await api.readAll(); setCount(0); load() }}>Mark all read</button>
          </div>
          {items.length === 0 && <div className="empty small">No notifications</div>}
          {items.map((n) => (
            <div key={n.id} className="room" style={{ opacity: n.is_read ? 0.6 : 1 }}
              onClick={async () => { if (!n.is_read) { await api.readNotif(n.id); setCount((c) => Math.max(0, c - 1)) } }}>
              <div className="small" style={{ fontWeight: 600 }}>{n.title}</div>
              <div className="small muted truncate">{n.body}</div>
              <div className="small muted" style={{ fontSize: 10 }}>{fmt.dateTime(n.created_at)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function Shell({ children }) {
  const { user, logout } = useAuth()
  const nav = useNavigate()
  const loc = useLocation()
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [workspaceTaskId, setWorkspaceTaskId] = useState(null)
  const items = user?.role === 'admin' ? NAV.admin : NAV.worker
  const workspaceMatch = loc.pathname.match(/^\/workspace\/([^/]+)/)
  const workspaceProjectId = workspaceMatch?.[1] || null

  return (
    <WorkspaceTaskProvider value={setWorkspaceTaskId}>
    <div className="shell">
      <aside className={`sidebar ${open ? 'open' : ''}`}>
        <div className="brand">
          <span className="brand-mark">WC</span>
          <div>
            <div className="brand-name">WORKCODE</div>
            <div className="brand-sub">DEV PLATFORM</div>
          </div>
        </div>

        {user?.role !== 'admin' && (
          <NavLink to={user?.role === 'developer_onroll' ? '/onroll' : user?.role === 'developer_offroll' ? '/offroll' : '/intern'}
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            <span className="ico">◈</span> My dashboard
          </NavLink>
        )}

        <div className="nav-section eyebrow">{user?.role === 'admin' ? 'Administration' : 'Workspace'}</div>
        {items.map(([to, ico, label]) => (
          <NavLink key={to} to={to} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`} onClick={() => setOpen(false)}>
            <span className="ico">{ico}</span> {label}
          </NavLink>
        ))}

        <div className="nav-section eyebrow">Account</div>
        <NavLink to="/profile" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
          <span className="ico">☺</span> Profile
        </NavLink>

        <div style={{ marginTop: 'auto', paddingTop: 16 }}>
          <div className="card tight">
            <div className="row">
              <span className="avatar" style={{ background: user?.avatar_color || '#4f8cff' }}>
                {(user?.full_name || '?').split(' ').map((n) => n[0]).slice(0, 2).join('')}
              </span>
              <div className="grow" style={{ minWidth: 0 }}>
                <div className="small truncate" style={{ fontWeight: 600 }}>{user?.full_name}</div>
                <div className="small muted truncate" style={{ fontSize: 11 }}>{ROLE_LABEL[user?.role]}</div>
              </div>
            </div>
            <button className="btn sm ghost" style={{ width: '100%', marginTop: 10 }}
              onClick={async () => { await logout(); nav('/login') }}>Sign out</button>
          </div>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <button className="btn icon ghost" onClick={() => setOpen((o) => !o)} style={{ display: 'none' }}>☰</button>
          <div className="grow">
            <div className="eyebrow">{(ROLE_LABEL[user?.role] || '')}</div>
            <div style={{ fontWeight: 650, fontSize: 15 }}>{loc.pathname === '/' ? 'Dashboard' : loc.pathname.replace('/', '').split('/')[0].replace('-', ' ')}</div>
          </div>
          <TimerWidget />
          {user?.role !== 'admin' && <WorkspaceRecorder projectId={workspaceProjectId}
            taskId={workspaceProjectId ? workspaceTaskId : null} toast={toast} />}
          <Notifications />
        </header>
        <main className="page">{children}</main>
      </div>
    </div>
    </WorkspaceTaskProvider>
  )
}

export default function Layout({ children }) {
  const { user } = useAuth()
  return <ToastProvider><Shell>{children}</Shell></ToastProvider>
}
