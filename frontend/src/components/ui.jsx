import React, { createContext, useCallback, useContext, useEffect, useState } from 'react'

export function Card({ children, className = '', ...rest }) {
  return <div className={`card ${className}`} {...rest}>{children}</div>
}

export function Stat({ label, value, delta, icon }) {
  return (
    <Card className="stat-card">
      <div className="stat">
        <div className="between">
          <span className="label">{label}</span>
          {icon && <span className="stat-icon">{icon}</span>}
        </div>
        <span className="value">{value ?? '—'}</span>
        {delta && <span className="delta">{delta}</span>}
      </div>
    </Card>
  )
}

const STATUS_MAP = {
  not_started: ['grey', 'Not started'],
  in_progress: ['blue', 'In progress'],
  submitted: ['purple', 'Submitted'],
  under_review: ['purple', 'Under review'],
  rework_required: ['amber', 'Rework required'],
  approved: ['green', 'Approved'],
  completed: ['green', 'Completed'],
  final_approved: ['green', 'Final approved'],
  overdue: ['red', 'Overdue'],
  rejected: ['red', 'Rejected'],
  running: ['green', 'Running'],
  paused: ['amber', 'Paused'],
  stopped: ['grey', 'Stopped'],
  recording: ['red', 'Recording'],
  uploaded: ['blue', 'Uploaded'],
  forwarded_to_admin: ['purple', 'With admin'],
  draft: ['amber', 'Draft'],
  scheduled: ['blue', 'Scheduled'],
  cancelled: ['red', 'Cancelled']
}

export function Status({ value }) {
  const [tone, label] = STATUS_MAP[value] || ['grey', (value || '—').replace(/_/g, ' ')]
  return <span className={`pill ${tone}`}>{label}</span>
}

export function Avatar({ name = '?', color = '#4f8cff', size = 32 }) {
  const initials = name.split(' ').filter(Boolean).slice(0, 2).map((n) => n[0]).join('').toUpperCase()
  return <span className="avatar" style={{ background: color, width: size, height: size, fontSize: size * 0.38 }}>{initials}</span>
}

export function Progress({ value = 0 }) {
  return <div className="progress"><div style={{ width: `${Math.min(100, Math.max(0, value))}%` }} /></div>
}

export function Empty({ children = 'Nothing here yet.' }) {
  return <div className="empty">{children}</div>
}

export function Spinner() {
  return <div className="center" style={{ padding: 40 }}><div className="spinner" /></div>
}

export function Modal({ title, onClose, children, wide = false }) {
  useEffect(() => {
    const fn = (e) => e.key === 'Escape' && onClose?.()
    window.addEventListener('keydown', fn)
    return () => window.removeEventListener('keydown', fn)
  }, [onClose])
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose?.()}>
      <div className={`modal ${wide ? 'wide' : ''}`}>
        <div className="between" style={{ marginBottom: 16 }}>
          <h2>{title}</h2>
          <button className="btn sm ghost" onClick={onClose}>✕</button>
        </div>
        {children}
      </div>
    </div>
  )
}

/* --------------------------------------------------------------------- toasts */
const ToastCtx = createContext(() => {})
export const useToast = () => useContext(ToastCtx)

export function ToastProvider({ children }) {
  const [items, setItems] = useState([])
  const push = useCallback((message, kind = 'ok') => {
    const id = Math.random().toString(36).slice(2)
    setItems((prev) => [...prev, { id, message, kind }])
    setTimeout(() => setItems((prev) => prev.filter((i) => i.id !== id)), 4000)
  }, [])
  return (
    <ToastCtx.Provider value={push}>
      {children}
      {items.map((i) => <div key={i.id} className={`toast ${i.kind}`}>{i.message}</div>)}
    </ToastCtx.Provider>
  )
}

export const fmt = {
  date(d) {
    if (!d) return '—'
    return new Date(d).toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' })
  },
  dateTime(d) {
    if (!d) return '—'
    return new Date(d).toLocaleString(undefined, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
  },
  time(d) {
    if (!d) return '—'
    return new Date(d).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  },
  hours(seconds) {
    const h = Math.floor((seconds || 0) / 3600)
    const m = Math.floor(((seconds || 0) % 3600) / 60)
    return `${h}h ${String(m).padStart(2, '0')}m`
  },
  clock(seconds) {
    const s = Math.max(0, Math.floor(seconds || 0))
    const h = String(Math.floor(s / 3600)).padStart(2, '0')
    const m = String(Math.floor((s % 3600) / 60)).padStart(2, '0')
    const sec = String(s % 60).padStart(2, '0')
    return `${h}:${m}:${sec}`
  },
  bytes(n) {
    if (!n) return '0 B'
    const u = ['B', 'KB', 'MB', 'GB']
    const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)))
    return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${u[i]}`
  }
}
