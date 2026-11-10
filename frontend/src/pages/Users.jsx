import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { Avatar, Card, Empty, Modal, Spinner, Status, fmt, useToast } from '../components/ui'

const ROLES = [
  ['developer_onroll', 'Developer · On-Roll'],
  ['developer_offroll', 'Developer · Off-Roll'],
  ['intern', 'Intern']
]

export default function Users() {
  const toast = useToast()
  const [users, setUsers] = useState([])
  const [teams, setTeams] = useState([])
  const [loading, setLoading] = useState(true)
  const [q, setQ] = useState('')
  const [role, setRole] = useState('')
  const [showNew, setShowNew] = useState(false)
  const [detail, setDetail] = useState(null)
  const [history, setHistory] = useState([])
  const [form, setForm] = useState({
    full_name: '', email: '', username: '', password: '', role: 'developer_onroll',
    designation: '', team_id: '', period_months: 6, skills: ''
  })

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [{ data: u }, { data: t }] = await Promise.all([
        api.users({ q: q || undefined, role: role || undefined, size: 200 }), api.teams()
      ])
      setUsers(u); setTeams(t)
    } finally { setLoading(false) }
  }, [q, role])

  useEffect(() => { load() }, [load])

  const create = async () => {
    try {
      await api.createUser({
        ...form,
        team_id: form.team_id || null,
        period_months: form.role === 'developer_onroll' ? null : Number(form.period_months)
      })
      toast('Employee account created')
      setShowNew(false)
      setForm({ full_name: '', email: '', username: '', password: '', role: 'developer_onroll', designation: '', team_id: '', period_months: 6, skills: '' })
      load()
    } catch (e) {
      const err = e?.response?.data
      toast(err?.detail || err?.errors?.[0]?.message || 'Could not create user', 'error')
    }
  }

  const toggle = async (u) => {
    try {
      await api.setStatus(u.id, !u.is_active)
      toast(u.is_active ? 'Account deactivated' : 'Account activated')
      load()
    } catch (e) { toast(e?.response?.data?.detail || 'Failed', 'error') }
  }

  const convert = async (u) => {
    if (!confirm(`Convert ${u.full_name} to Developer On-Roll?`)) return
    try {
      await api.convert(u.id, { to_role: 'developer_onroll', notes: 'Approved from admin panel' })
      toast('Employee converted to On-Roll')
      setDetail(null); load()
    } catch (e) { toast(e?.response?.data?.detail || 'Conversion failed', 'error') }
  }

  const openDetail = async (u) => {
    setDetail(u)
    const { data } = await api.userHistory(u.id)
    setHistory(data)
  }

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">Administration</div>
          <h1>People</h1>
          <p className="muted small" style={{ marginTop: 4 }}>Create accounts, assign teams, manage employment periods.</p>
        </div>
        <button className="btn primary" onClick={() => setShowNew(true)}>+ Add employee</button>
      </div>

      <Card style={{ marginBottom: 16 }}>
        <div className="row wrap">
          <div className="search grow" style={{ minWidth: 220 }}>
            <input placeholder="Search name, email or username…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <select style={{ width: 210 }} value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="">All roles</option>
            {ROLES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <span className="small muted">{users.length} accounts</span>
        </div>
      </Card>

      {loading ? <Spinner /> : (
        <Card>
          <table>
            <thead><tr><th>Person</th><th>Role</th><th>Team</th><th>Joined</th><th>Status</th><th /></tr></thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>
                    <div className="row">
                      <Avatar name={u.full_name} color={u.avatar_color} />
                      <div>
                        <div style={{ fontWeight: 600 }}>{u.full_name}</div>
                        <div className="small muted">{u.email}</div>
                      </div>
                    </div>
                  </td>
                  <td><span className="pill blue">{u.role.replace('developer_', '')}</span></td>
                  <td className="small muted">{teams.find((t) => t.id === u.profile?.team_id)?.name || '—'}</td>
                  <td className="small muted">{fmt.date(u.profile?.joining_date)}</td>
                  <td><span className={`pill ${u.is_active ? 'green' : 'grey'}`}>{u.is_active ? 'Active' : 'Inactive'}</span></td>
                  <td>
                    <div className="row" style={{ gap: 6 }}>
                      <button className="btn sm" onClick={() => openDetail(u)}>View</button>
                      <button className="btn sm ghost" onClick={() => toggle(u)}>{u.is_active ? 'Deactivate' : 'Activate'}</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!users.length && <Empty>No employees match this filter.</Empty>}
        </Card>
      )}

      {showNew && (
        <Modal title="Add employee" onClose={() => setShowNew(false)} wide>
          <div className="grid c2">
            <div className="field"><label>Full name</label><input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></div>
            <div className="field"><label>Email</label><input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="name@company.com" /></div>
            <div className="field"><label>Username</label><input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} /></div>
            <div className="field"><label>Temporary password</label><input value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} placeholder="min 8 chars, letters + numbers" /></div>
            <div className="field">
              <label>Role</label>
              <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
                {ROLES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </div>
            <div className="field">
              <label>Team</label>
              <select value={form.team_id} onChange={(e) => setForm({ ...form, team_id: e.target.value })}>
                <option value="">— no team —</option>
                {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </div>
            {form.role !== 'developer_onroll' && (
              <div className="field">
                <label>Employment period (months)</label>
                <input type="number" min="1" max="24" value={form.period_months} onChange={(e) => setForm({ ...form, period_months: e.target.value })} />
              </div>
            )}
            <div className="field"><label>Designation</label><input value={form.designation} onChange={(e) => setForm({ ...form, designation: e.target.value })} /></div>
          </div>
          <div className="field"><label>Skills</label><input value={form.skills} onChange={(e) => setForm({ ...form, skills: e.target.value })} placeholder="Python, React, PostgreSQL" /></div>
          <p className="small muted" style={{ marginBottom: 12 }}>Off-roll developers and interns automatically get a six-month review date. Conversion to On-Roll is always an explicit admin decision.</p>
          <button className="btn primary" onClick={create} disabled={!form.full_name || !form.email || !form.username || !form.password}>Create account</button>
        </Modal>
      )}

      {detail && (
        <Modal title={detail.full_name} onClose={() => setDetail(null)} wide>
          <div className="row" style={{ marginBottom: 14 }}>
            <Avatar name={detail.full_name} color={detail.avatar_color} size={44} />
            <div>
              <div style={{ fontWeight: 650 }}>{detail.email}</div>
              <div className="small muted">{detail.role.replace(/_/g, ' ')} · {detail.username}</div>
            </div>
          </div>
          <dl className="kv">
            <dt>Employee code</dt><dd>{detail.profile?.employee_code || '—'}</dd>
            <dt>Joined</dt><dd>{fmt.date(detail.profile?.joining_date)}</dd>
            <dt>Employment status</dt><dd>{detail.profile?.employment_status || '—'}</dd>
            <dt>Period ends</dt><dd>{fmt.date(detail.profile?.expected_end_date)}</dd>
            <dt>Skills</dt><dd>{detail.profile?.skills || '—'}</dd>
            <dt>Last login</dt><dd>{fmt.dateTime(detail.last_login_at)}</dd>
          </dl>
          <div className="divider" />
          <h3 style={{ marginBottom: 8 }}>Employment history</h3>
          {history.map((h) => (
            <div key={h.id} className="between" style={{ padding: '6px 0', borderBottom: '1px solid var(--border)' }}>
              <span className="small">{h.event} {h.from_role ? `(${h.from_role} → ${h.to_role})` : `→ ${h.to_role}`}</span>
              <span className="small muted">{fmt.date(h.effective_date)}</span>
            </div>
          ))}
          {!history.length && <span className="small muted">No history yet.</span>}
          {['developer_offroll', 'intern'].includes(detail.role) && (
            <button className="btn primary" style={{ marginTop: 16 }} onClick={() => convert(detail)}>
              Convert to Developer On-Roll
            </button>
          )}
        </Modal>
      )}
    </>
  )
}
