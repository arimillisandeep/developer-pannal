import React, { useEffect, useRef, useState } from 'react'
import { Card, Spinner } from './ui'
import { tokens } from '../api'

const hm = (s) => {
  s = Math.max(0, Math.floor(s || 0))
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60
  return h ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}m ${String(sec).padStart(2, '0')}s`
}
const clock = (s) => {
  s = Math.max(0, Math.floor(s || 0))
  return `${String(Math.floor(s / 3600)).padStart(2, '0')}:${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}
const BADGE = {
  active: { label: 'Active', bg: 'rgba(34,197,94,.15)', color: '#22c55e' },
  away: { label: 'Away', bg: 'rgba(234,179,8,.18)', color: '#eab308' },
  logged_out: { label: 'Logged out', bg: 'rgba(148,163,184,.18)', color: '#94a3b8' },
  interrupted: { label: 'Interrupted', bg: 'rgba(239,68,68,.18)', color: '#ef4444' },
  paused: { label: 'Paused', bg: 'rgba(234,179,8,.18)', color: '#eab308' }
}

export default function MyTimeCard() {
  const [data, setData] = useState(null)
  const [tick, setTick] = useState(0)
  const timer = useRef(null)

  useEffect(() => {
    let alive = true
    const load = () => fetch('/api/monitoring/me', { headers: { Authorization: `Bearer ${tokens.access}` } })
      .then((r) => (r.ok ? r.json() : null)).then((d) => { if (alive) setData(d) }).catch(() => {})
    load()
    const poll = setInterval(load, 20000)
    timer.current = setInterval(() => setTick((t) => t + 1), 1000)
    return () => { alive = false; clearInterval(poll); clearInterval(timer.current) }
  }, [])

  if (!data) return <Card><Spinner /></Card>
  const s = data.summary || {}
  const b = BADGE[s.status] || BADGE.logged_out
  const away = data.away_intervals?.[0]
  const openAway = away && !away.returned_at ? Math.max(0, (Date.now() - new Date(away.left_at).getTime()) / 1000) : 0
  const active = s.status === 'active' ? (s.active_seconds || 0) + tick : (s.active_seconds || 0)
  const awayTotal = (s.away_seconds || 0) + (openAway ? 1 : 0)

  return (
    <Card>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <h3 style={{ margin: 0, fontSize: 15 }}>My working time</h3>
        <span style={{ padding: '2px 9px', borderRadius: 10, fontSize: 11.5, fontWeight: 700, background: b.bg, color: b.color }}>{b.label}</span>
        <span style={{ marginLeft: 'auto', fontSize: 11.5, opacity: 0.6 }}>Asia/Kolkata · server timestamps</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(130px,1fr))', gap: 10 }}>
        {[['Active', clock(active)], ['Away', hm(awayTotal)], ['Session', hm(s.session_seconds)],
          ['Login', s.login_at || '—'], ['Logout', s.logout_at || '—'], ['Last return', away?.returned_at || '—']].map(([k, v]) => (
          <div key={k} style={{ border: '1px solid rgba(148,163,184,.22)', borderRadius: 10, padding: '9px 11px' }}>
            <div style={{ fontSize: 10.5, textTransform: 'uppercase', letterSpacing: '.05em', opacity: 0.6 }}>{k}</div>
            <div style={{ fontSize: 14, fontWeight: 650, marginTop: 2 }}>{v}</div>
          </div>
        ))}
      </div>
      {(
        <div style={{ marginTop: 14 }}>
          <div style={{ fontSize: 11.5, textTransform: 'uppercase', letterSpacing: '.05em', opacity: 0.6, marginBottom: 6 }}>Daily attendance</div>
          <table style={{ width: '100%', fontSize: 12.5, borderCollapse: 'collapse' }}>
            <thead><tr style={{ opacity: 0.6, textAlign: 'left' }}>
              <th style={{ padding: '4px 6px' }}>Date</th><th>Active</th><th>Away</th><th>Status</th>
            </tr></thead>
            <tbody>
              {(data.attendance || []).slice(0, 7).map((a) => (
                <tr key={a.date} style={{ borderTop: '1px solid rgba(148,163,184,.16)' }}>
                  <td style={{ padding: '4px 6px' }}>{a.date}</td><td>{hm(a.active_seconds)}</td>
                  <td>{hm(a.away_seconds)}</td><td>{a.status}</td>
                </tr>
              ))}
              {!(data.attendance || []).length && (
                <tr><td colSpan={4} style={{ padding: '6px 6px', opacity: 0.6 }}>No attendance recorded yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
      <div style={{ fontSize: 11, opacity: 0.55, marginTop: 10 }}>
        The timer starts automatically on login and pauses when this tab is not visible.
      </div>
    </Card>
  )
}
