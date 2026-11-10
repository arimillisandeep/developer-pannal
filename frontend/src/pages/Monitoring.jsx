import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Card, Modal, Spinner, useToast } from '../components/ui'
import { tokens } from '../api'

const CATS = [['all', 'All employees'], ['developer_onroll', 'On-Roll'], ['developer_offroll', 'Off-Roll'], ['intern', 'Interns']]
const ROLE = { developer_onroll: 'On-Roll', developer_offroll: 'Off-Roll', intern: 'Intern', admin: 'Admin' }
const BADGE = {
  active: ['Active', '#22c55e'], away: ['Away', '#eab308'], logged_out: ['Logged out', '#94a3b8'],
  interrupted: ['Interrupted', '#ef4444'], paused: ['Paused', '#eab308']
}
const hm = (s) => {
  s = Math.max(0, Math.floor(s || 0))
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
  return h ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}m`
}

async function authedDownload(path, filename) {
  const res = await fetch(path, { headers: { Authorization: `Bearer ${tokens.access}` } })
  if (!res.ok) throw new Error(`Export failed (${res.status})`)
  const blob = await res.blob()
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  a.click()
  URL.revokeObjectURL(a.href)
}

export default function Monitoring() {
  const toast = useToast()
  const [tab, setTab] = useState('all')
  const [status, setStatus] = useState('all')
  const [q, setQ] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [detail, setDetail] = useState(null)
  const [report, setReport] = useState(null)
  const [period, setPeriod] = useState('daily')
  const wsRef = useRef(null)

  const load = useCallback(async () => {
    const params = new URLSearchParams({ category: tab === 'reports' ? 'all' : tab, status })
    const r = await fetch(`/api/monitoring/live?${params}`, { headers: { Authorization: `Bearer ${tokens.access}` } })
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    const data = await r.json()
    setRows(data.items || [])
    setLoading(false)
  }, [tab, status])

  useEffect(() => { load().catch(() => setLoading(false)) }, [load])

  useEffect(() => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/api/monitoring/ws/monitoring?token=${encodeURIComponent(tokens.access)}`)
    wsRef.current = ws
    ws.onmessage = () => { load().catch(() => {}) }
    return () => ws.close()
  }, [load])

  const openDetail = async (id) => {
    setDetail({ loading: true })
    try {
      const r = await fetch(`/api/monitoring/employees/${id}`, { headers: { Authorization: `Bearer ${tokens.access}` } })
      setDetail(await r.json())
    } catch (e) { toast('Could not load employee history', 'error'); setDetail(null) }
  }

  const loadReport = async () => {
    try {
      const p = new URLSearchParams({ period, date_from: dateFrom, date_to: dateTo, category: tab === 'reports' ? 'all' : tab })
      const r = await fetch(`/api/monitoring/reports?${p}`, { headers: { Authorization: `Bearer ${tokens.access}` } })
      setReport(await r.json())
    } catch (e) { toast('Report failed', 'error') }
  }

  useEffect(() => { if (tab === 'reports') loadReport() }, [tab, period]) // eslint-disable-line react-hooks/exhaustive-deps

  const shown = useMemo(() => rows.filter((r) => {
    const t = q.trim().toLowerCase()
    if (!t) return true
    return (r.full_name || '').toLowerCase().includes(t) || (r.employee_id || '').toLowerCase().includes(t)
      || (r.email || '').toLowerCase().includes(t)
  }), [rows, q])

  const stock = useMemo(() => {
    const c = {}
    rows.forEach((r) => { c[r.status] = (c[r.status] || 0) + 1 })
    return c
  }, [rows])

  const exportQuery = () => {
    const p = new URLSearchParams({ period, date_from: dateFrom, date_to: dateTo, category: tab === 'reports' ? 'all' : tab })
    return p.toString()
  }

  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <div className="page-head">
        <div>
          <div className="eyebrow">Administration</div>
          <h1>Employee monitoring</h1>
          <p className="muted small">Live attendance and working time for On-Roll, Off-Roll and Intern employees.</p>
        </div>
        <div className="row">
          <button className="btn" onClick={() => authedDownload(`/api/monitoring/report.csv?category=${tab === 'reports' ? 'all' : tab}&status=${status}`, 'workcode-attendance.csv').catch((e) => toast(e.message, 'error'))}>CSV</button>
          <button className="btn" onClick={() => authedDownload(`/api/monitoring/export.xlsx?${exportQuery()}`, 'workcode-time-report.xlsx').catch((e) => toast(e.message, 'error'))}>Excel</button>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {[...CATS, ['reports', 'Reports']].map(([k, label]) => (
          <button key={k} className={`btn ${tab === k ? 'primary' : ''}`} onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>

      {tab !== 'reports' && (
        <>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <input className="input" placeholder="Search name or employee ID" value={q} onChange={(e) => setQ(e.target.value)} style={{ minWidth: 220 }} />
            <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="all">All statuses</option><option value="active">Active</option><option value="away">Away</option>
              <option value="logged_out">Logged out</option><option value="interrupted">Interrupted</option>
            </select>
            <span style={{ marginLeft: 'auto', fontSize: 12, opacity: 0.7 }}>
              {rows.length} employees · active {stock.active || 0} · away {stock.away || 0} · logged out {stock.logged_out || 0} · interrupted {stock.interrupted || 0}
            </span>
          </div>

          <Card style={{ padding: 0, overflow: 'hidden' }}>
            {loading ? <div style={{ padding: 20 }}><Spinner /></div> : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', fontSize: 12.5, borderCollapse: 'collapse', minWidth: 980 }}>
                  <thead>
                    <tr style={{ textAlign: 'left', opacity: 0.6, fontSize: 11, textTransform: 'uppercase', letterSpacing: '.04em' }}>
                      <th style={{ padding: '10px 12px' }}>Employee</th><th>ID</th><th>Category</th><th>Login</th><th>Logout</th>
                      <th>Status</th><th>Active</th><th>Away</th><th>Session</th><th>Last activity</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((r) => {
                      const [label, color] = BADGE[r.status] || BADGE.logged_out
                      return (
                        <tr key={r.user_id} onClick={() => openDetail(r.user_id)} style={{ cursor: 'pointer', borderTop: '1px solid rgba(148,163,184,.14)' }}>
                          <td style={{ padding: '9px 12px', fontWeight: 600 }}>{r.full_name}<div style={{ fontSize: 11, opacity: 0.6 }}>{r.email}</div></td>
                          <td>{r.employee_id}</td>
                          <td>{ROLE[r.category] || r.category}</td>
                          <td>{r.login_at || '—'}</td>
                          <td>{r.logout_at || '—'}</td>
                          <td><span style={{ padding: '2px 8px', borderRadius: 10, fontSize: 11, fontWeight: 700, color, background: `${color}22` }}>{label}</span></td>
                          <td>{hm(r.active_seconds)}</td>
                          <td>{hm(r.away_seconds)}</td>
                          <td>{hm(r.session_seconds)}</td>
                          <td style={{ fontSize: 11.5, opacity: 0.75 }}>{r.last_activity || '—'}</td>
                        </tr>
                      )
                    })}
                    {!shown.length && <tr><td colSpan={10} style={{ padding: 18, opacity: 0.6 }}>No employees match these filters.</td></tr>}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}

      {tab === 'reports' && (
        <>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <select className="input" value={period} onChange={(e) => setPeriod(e.target.value)}>
              <option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option>
            </select>
            <input className="input" type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
            <input className="input" type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
            <button className="btn primary" onClick={loadReport}>Run report</button>
            {report && <span style={{ fontSize: 12, opacity: 0.7 }}>{report.date_from} → {report.date_to}</span>}
          </div>
          <Card style={{ padding: 0, overflow: 'hidden' }}>
            {!report ? <div style={{ padding: 20 }}><Spinner /></div> : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', fontSize: 12.5, borderCollapse: 'collapse', minWidth: 860 }}>
                  <thead><tr style={{ textAlign: 'left', opacity: 0.6, fontSize: 11, textTransform: 'uppercase' }}>
                    <th style={{ padding: '10px 12px' }}>Employee</th><th>Category</th><th>Sessions</th><th>Days</th>
                    <th>Active</th><th>Away</th><th>Session</th><th>Away %</th><th>Avg active/session</th>
                  </tr></thead>
                  <tbody>
                    {report.rows.map((r) => (
                      <tr key={r.user_id} style={{ borderTop: '1px solid rgba(148,163,184,.14)', background: r.away_ratio >= 0.3 ? 'rgba(234,179,8,.08)' : undefined }}>
                        <td style={{ padding: '9px 12px', fontWeight: 600 }}>{r.full_name}<div style={{ fontSize: 11, opacity: 0.6 }}>{r.employee_id}</div></td>
                        <td>{ROLE[r.category] || r.category}</td><td>{r.sessions}</td><td>{r.days}</td>
                        <td>{hm(r.active_seconds)}</td><td>{hm(r.away_seconds)}</td><td>{hm(r.session_seconds)}</td>
                        <td>{Math.round(r.away_ratio * 100)}%</td><td>{hm(r.avg_active_seconds)}</td>
                      </tr>
                    ))}
                    {!report.rows.length && <tr><td colSpan={9} style={{ padding: 18, opacity: 0.6 }}>No sessions in this range.</td></tr>}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}

      {detail && <Modal onClose={() => setDetail(null)} wide
        title={detail?.profile ? `${detail.profile.full_name} — activity` : 'Employee activity'}>
        {!detail || detail.loading ? <Spinner /> : (
          <div style={{ display: 'grid', gap: 14, maxHeight: '68vh', overflow: 'auto' }}>
            <div style={{ fontSize: 12.5, opacity: 0.8 }}>
              {detail.profile.email} · {ROLE[detail.profile.category] || detail.profile.category} · ID {detail.profile.employee_code || detail.profile.id.slice(0, 8)}
            </div>
            {detail.summary && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(120px,1fr))', gap: 8 }}>
                {[['Status', (BADGE[detail.summary.status] || ['—'])[0]], ['Login', detail.summary.login_at || '—'],
                  ['Logout', detail.summary.logout_at || '—'], ['Active', hm(detail.summary.active_seconds)],
                  ['Away', hm(detail.summary.away_seconds)], ['Session', hm(detail.summary.session_seconds)]].map(([k, v]) => (
                  <div key={k} style={{ border: '1px solid rgba(148,163,184,.2)', borderRadius: 9, padding: '8px 10px' }}>
                    <div style={{ fontSize: 10.5, textTransform: 'uppercase', opacity: 0.6 }}>{k}</div>
                    <div style={{ fontWeight: 650 }}>{v}</div>
                  </div>
                ))}
              </div>
            )}
            <div>
              <div style={{ fontSize: 11.5, textTransform: 'uppercase', opacity: 0.6, marginBottom: 6 }}>Away intervals ({detail.away_intervals?.length || 0})</div>
              <div style={{ maxHeight: 150, overflow: 'auto', fontSize: 12.5 }}>
                {(detail.away_intervals || []).map((a, i) => (
                  <div key={i} style={{ display: 'flex', gap: 10, padding: '4px 0', borderBottom: '1px solid rgba(148,163,184,.12)' }}>
                    <span style={{ minWidth: 150 }}>{a.left_at}</span><span style={{ opacity: 0.6 }}>→</span>
                    <span style={{ minWidth: 150 }}>{a.returned_at || 'still away'}</span>
                    <span style={{ marginLeft: 'auto', opacity: 0.75 }}>{hm(a.seconds)} · {a.reason}</span>
                  </div>
                ))}
                {!(detail.away_intervals || []).length && <div style={{ opacity: 0.6 }}>No away intervals recorded.</div>}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11.5, textTransform: 'uppercase', opacity: 0.6, marginBottom: 6 }}>Timeline</div>
              <div style={{ maxHeight: 160, overflow: 'auto', fontSize: 12.5 }}>
                {(detail.timeline || []).slice().reverse().map((t, i) => (
                  <div key={i} style={{ display: 'flex', gap: 10, padding: '3px 0' }}>
                    <span style={{ minWidth: 150, opacity: 0.75 }}>{t.at}</span><span style={{ fontWeight: 600 }}>{t.type}</span>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11.5, textTransform: 'uppercase', opacity: 0.6, marginBottom: 6 }}>Sessions</div>
              <table style={{ width: '100%', fontSize: 12.5, borderCollapse: 'collapse' }}>
                <thead><tr style={{ opacity: 0.6, textAlign: 'left' }}><th>Start</th><th>End</th><th>Status</th><th>Active</th><th>Away</th></tr></thead>
                <tbody>
                  {(detail.sessions || []).slice(0, 12).map((s) => (
                    <tr key={s.id} style={{ borderTop: '1px solid rgba(148,163,184,.14)' }}>
                      <td style={{ padding: '4px 0' }}>{s.started_at}</td><td>{s.ended_at || 'open'}</td><td>{s.status}</td>
                      <td>{hm(s.active_seconds)}</td><td>{hm(s.away_seconds)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div>
              <div style={{ fontSize: 11.5, textTransform: 'uppercase', opacity: 0.6, marginBottom: 6 }}>Daily attendance</div>
              <div style={{ maxHeight: 130, overflow: 'auto', fontSize: 12.5 }}>
                {(detail.attendance || []).map((a) => (
                  <div key={a.date} style={{ display: 'flex', gap: 10, padding: '3px 0' }}>
                    <span style={{ minWidth: 110 }}>{a.date}</span><span>active {hm(a.active_seconds)}</span>
                    <span>away {hm(a.away_seconds)}</span><span style={{ opacity: 0.65 }}>{a.status}</span>
                  </div>
                ))}
                {!(detail.attendance || []).length && <div style={{ opacity: 0.6 }}>No attendance rows yet.</div>}
              </div>
            </div>
          </div>
        )}
      </Modal>}
    </div>
  )
}
