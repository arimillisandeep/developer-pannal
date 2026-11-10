import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { ROLE_LABEL, useAuth } from '../context/AuthContext'
import { Card, Spinner, Stat, Status, fmt, useToast } from '../components/ui'

export default function Profile() {
  const { user } = useAuth()
  const toast = useToast()
  const [data, setData] = useState(null)
  const [form, setForm] = useState({ full_name: '', phone: '', skills: '' })
  const [pwd, setPwd] = useState({ current_password: '', new_password: '' })
  const [attendance, setAttendance] = useState(null)

  useEffect(() => {
    api.myProfile().then(({ data }) => {
      setData(data)
      setForm({ full_name: data.user.full_name, phone: data.user.phone || '', skills: data.profile?.skills || '' })
    })
    if (user.role !== 'admin') api.attendance().then(({ data }) => setAttendance(data)).catch(() => {})
  }, [user.role])

  const saveProfile = async () => {
    try {
      await api.updateMe(form)
      toast('Profile updated')
    } catch (e) { toast(e?.response?.data?.detail || 'Update failed', 'error') }
  }

  const changePassword = async () => {
    try {
      await api.changePassword(pwd.current_password, pwd.new_password)
      setPwd({ current_password: '', new_password: '' })
      toast('Password changed')
    } catch (e) { toast(e?.response?.data?.detail || 'Could not change password', 'error') }
  }

  if (!data) return <Spinner />
  const p = data.profile

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">Account</div>
          <h1>{data.user.full_name}</h1>
          <p className="muted small" style={{ marginTop: 4 }}>{ROLE_LABEL[data.user.role]} · {data.user.email}</p>
        </div>
      </div>

      {data.user.role !== 'admin' && (
        <div className="grid c4" style={{ marginBottom: 16 }}>
          <Stat label="Employee code" value={p?.employee_code || '—'} />
          <Stat label="Joined" value={fmt.date(p?.joining_date)} />
          <Stat label="Employment" value={p?.employment_status || '—'} delta={p?.expected_end_date ? `ends ${fmt.date(p.expected_end_date)}` : 'permanent'} />
          <Stat label="Hours (30d)" value={`${attendance?.total_hours ?? 0}h`} delta={`${attendance?.days?.length ?? 0} days logged`} />
        </div>
      )}

      <div className="grid c2">
        <Card>
          <div className="card-head"><h3>Profile details</h3></div>
          <div className="field"><label>Full name</label><input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></div>
          <div className="field"><label>Phone</label><input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></div>
          <div className="field"><label>Skills</label><input value={form.skills} onChange={(e) => setForm({ ...form, skills: e.target.value })} placeholder="Python, React, PostgreSQL" /></div>
          <div className="field"><label>Email (read only)</label><input value={data.user.email} disabled /></div>
          <button className="btn primary" onClick={saveProfile}>Save changes</button>
        </Card>

        <div className="stack">
          <Card>
            <div className="card-head"><h3>Security</h3></div>
            <div className="field"><label>Current password</label><input type="password" value={pwd.current_password} onChange={(e) => setPwd({ ...pwd, current_password: e.target.value })} /></div>
            <div className="field"><label>New password</label><input type="password" value={pwd.new_password} onChange={(e) => setPwd({ ...pwd, new_password: e.target.value })} placeholder="min 8 chars, letters + numbers" /></div>
            <button className="btn" onClick={changePassword} disabled={!pwd.current_password || pwd.new_password.length < 8}>Change password</button>
          </Card>

          <Card>
            <div className="card-head"><h3>Employment</h3></div>
            <dl className="kv">
              <dt>Team</dt><dd>{data.team?.name || '—'}</dd>
              <dt>Joining date</dt><dd>{fmt.date(p?.joining_date)}</dd>
              <dt>Status</dt><dd><Status value={p?.employment_status === 'active' ? 'approved' : 'in_progress'} /> {p?.employment_status}</dd>
              <dt>Period ends</dt><dd>{p?.expected_end_date ? `${fmt.date(p.expected_end_date)} (${data.days_remaining} days)` : 'Permanent role'}</dd>
            </dl>
            {data.user.role !== 'developer_onroll' && data.user.role !== 'admin' && (
              <p className="small muted" style={{ marginTop: 12 }}>
                Conversion to On-Roll is decided by an administrator — never automatic.
              </p>
            )}
          </Card>
        </div>
      </div>
    </>
  )
}
