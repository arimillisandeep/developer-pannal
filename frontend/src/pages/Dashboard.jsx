import React, { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { ROLE_LABEL, useAuth } from '../context/AuthContext'
import { Card, Empty, Progress, Spinner, Stat, Status, fmt } from '../components/ui'
import MyTimeCard from '../components/MyTimeCard'

function AdminDashboard() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api.dashboard().then(({ data }) => setData(data)).catch((e) => setError(e?.response?.data?.detail || 'Failed to load'))
  }, [])

  if (error) return <Card>{error}</Card>
  if (!data) return <Spinner />

  const t = data.totals
  const today = data.today
  const tasks = data.tasks
  const act = data.activity

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">Administration</div>
          <h1>Platform overview</h1>
          <p className="muted small" style={{ marginTop: 4 }}>Live state of every employee, task and coding session.</p>
        </div>
        <div className="row">
          <Link className="btn" to="/monitoring">Monitoring</Link>
          <Link className="btn primary" to="/reports">Reports</Link>
        </div>
      </div>

      <div className="grid c5" style={{ marginBottom: 16 }}>
        <Stat label="Total people" value={t.users} delta={`${t.active_users} active · ${t.inactive_users} inactive`} icon="👥" />
        <Stat label="On-roll" value={t.onroll} delta={`${t.offroll} off-roll · ${t.interns} interns`} icon="◈" />
        <Stat label="Hours today" value={`${today.working_hours}h`} delta={`${today.attendance_present} checked in`} icon="◷" />
        <Stat label="Open tasks" value={tasks.pending} delta={`${tasks.completed} completed`} icon="☑" />
        <Stat label="Overdue" value={tasks.overdue} delta={`${tasks.under_review} awaiting review`} icon="⚠" />
      </div>

      <div className="grid c3" style={{ marginBottom: 16 }}>
        <Card>
          <div className="card-head"><h3>Task distribution</h3><span className="pill blue">{tasks.total} total</span></div>
          <div className="stack">
            {Object.entries(tasks.by_status || {}).map(([k, v]) => (
              <div key={k}>
                <div className="between small" style={{ marginBottom: 4 }}>
                  <span style={{ textTransform: 'capitalize' }}>{k.replace(/_/g, ' ')}</span>
                  <span className="muted">{v}</span>
                </div>
                <Progress value={(v / Math.max(1, tasks.total)) * 100} />
              </div>
            ))}
            {!Object.keys(tasks.by_status || {}).length && <Empty>No tasks yet</Empty>}
          </div>
        </Card>

        <Card>
          <div className="card-head"><h3>Platform activity</h3></div>
          <div className="stack">
            {[
              ['Code submissions', act.submissions, '/reviews'],
              ['Code reviews', act.reviews, '/reviews'],
              ['Projects', act.projects, '/users'],
              ['Screen recordings', act.recordings, '/recordings'],
              ['Messages today', act.messages_today, '/chat'],
              ['Scheduled meetings', act.meetings, '/meetings'],
              ['Teams', act.teams, '/users']
            ].map(([label, value, to]) => (
              <Link key={label} to={to} className="between" style={{ padding: '6px 0', borderBottom: '1px solid var(--border)' }}>
                <span className="small muted">{label}</span>
                <strong>{value}</strong>
              </Link>
            ))}
          </div>
        </Card>

        <Card>
          <div className="card-head"><h3>Live now</h3><span className="pill green">{today.online_now} online</span></div>
          <div className="grid" style={{ gap: 10 }}>
            <div className="between"><span className="small muted">Active work sessions</span><strong>{today.active_sessions}</strong></div>
            <div className="between"><span className="small muted">Checked in today</span><strong>{today.attendance_present}</strong></div>
            <div className="between"><span className="small muted">Working hours</span><strong>{today.working_hours}h</strong></div>
            <div className="divider" style={{ margin: '6px 0' }} />
            <div className="eyebrow">Recent submissions</div>
            {data.recent_submissions.length === 0 && <span className="small muted">None yet</span>}
            {data.recent_submissions.map((s) => (
              <div key={s.id} className="between small">
                <span className="mono">#{s.id.slice(0, 6)}</span>
                <Status value={s.status} />
                <span className="muted">{fmt.date(s.created_at)}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Card>
        <div className="card-head"><h3>Recent audit events</h3><Link className="btn sm" to="/monitoring">Open audit log</Link></div>
        <table>
          <thead><tr><th>Action</th><th>Entity</th><th>When</th></tr></thead>
          <tbody>
            {data.recent_audit.map((a) => (
              <tr key={a.id}>
                <td className="mono small">{a.action}</td>
                <td className="small muted">{a.entity_type}</td>
                <td className="small muted">{fmt.dateTime(a.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!data.recent_audit.length && <Empty>No audit events yet</Empty>}
      </Card>
    </>
  )
}

function WorkerDashboard() {
  const { user } = useAuth()
  const [profile, setProfile] = useState(null)
  const [tasks, setTasks] = useState([])
  const [attendance, setAttendance] = useState(null)
  const [submissions, setSubmissions] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      api.myProfile(), api.tasks({ mine: true }), api.attendance(),
      api.submissions().catch(() => ({ data: [] }))
    ]).then(([p, t, a, s]) => {
      setProfile(p.data); setTasks(t.data); setAttendance(a.data); setSubmissions(s.data)
    }).finally(() => setLoading(false))
  }, [])

  const stats = useMemo(() => {
    const done = tasks.filter((t) => ['completed', 'approved', 'final_approved'].includes(t.status)).length
    const overdue = tasks.filter((t) => t.status === 'overdue').length
    const open = tasks.filter((t) => !['completed', 'approved', 'final_approved'].includes(t.status)).length
    return { done, overdue, open }
  }, [tasks])

  if (loading) return <Spinner />

  const p = profile?.profile
  const daysLeft = profile?.days_remaining

  return (
    <>
      <div style={{ marginBottom: 16 }}><MyTimeCard /></div>
      <div className="page-head">
        <div>
          <div className="eyebrow">{ROLE_LABEL[user.role]}</div>
          <h1>Hi {user.full_name.split(' ')[0]} 👋</h1>
          <p className="muted small" style={{ marginTop: 4 }}>
            {p ? `${p.employee_code} · joined ${fmt.date(p.joining_date)}` : 'Profile incomplete — contact an administrator.'}
          </p>
        </div>
        <div className="row">
          <Link className="btn" to="/tasks">My tasks</Link>
          <Link className="btn primary" to="/workspace">Open workspace</Link>
        </div>
      </div>

      <div className="grid c4" style={{ marginBottom: 16 }}>
        <Stat label="Open tasks" value={stats.open} icon="☑" />
        <Stat label="Completed" value={stats.done} delta={`${stats.overdue} overdue`} icon="✓" />
        <Stat label="Hours (30d)" value={`${attendance?.total_hours ?? 0}h`} delta={`avg ${attendance?.avg_hours_per_day ?? 0}h/day`} icon="◷" />
        <Stat label={user.role === 'developer_onroll' ? 'Employment' : 'Period ends in'}
          value={user.role === 'developer_onroll' ? 'On-roll' : (daysLeft != null ? `${daysLeft}d` : '—')}
          delta={p?.expected_end_date ? fmt.date(p.expected_end_date) : 'Permanent'} icon="◈" />
      </div>

      <div className="grid c2" style={{ marginBottom: 16 }}>
        <Card>
          <div className="card-head"><h3>My tasks</h3><Link className="btn sm" to="/tasks">View all</Link></div>
          {tasks.slice(0, 6).map((t) => (
            <div key={t.id} style={{ padding: '10px 0', borderBottom: '1px solid var(--border)' }}>
              <div className="between">
                <div className="grow" style={{ minWidth: 0 }}>
                  <div className="small truncate" style={{ fontWeight: 600 }}>{t.title}</div>
                  <div className="small muted mono">{t.code} · due {fmt.date(t.due_date)}</div>
                </div>
                <Status value={t.status} />
              </div>
              <div style={{ marginTop: 6 }}><Progress value={t.progress} /></div>
            </div>
          ))}
          {!tasks.length && <Empty>No tasks assigned yet</Empty>}
        </Card>

        <Card>
          <div className="card-head"><h3>Attendance</h3><span className="pill grey">last 30 days</span></div>
          <div className="grid c2" style={{ gap: 12 }}>
            <div><div className="eyebrow">Total hours</div><div style={{ fontSize: 22, fontWeight: 700 }}>{attendance?.total_hours ?? 0}h</div></div>
            <div><div className="eyebrow">Days logged</div><div style={{ fontSize: 22, fontWeight: 700 }}>{attendance?.days?.length ?? 0}</div></div>
          </div>
          <div className="divider" />
          <div className="stack">
            {(attendance?.days || []).slice(0, 6).map((d) => (
              <div key={d.date} className="between small">
                <span className="muted">{fmt.date(d.date)}</span>
                <Status value={d.status === 'present' ? 'completed' : d.status} />
                <strong>{fmt.hours(d.active_seconds)}</strong>
              </div>
            ))}
            {!(attendance?.days || []).length && <Empty>Start the timer to log your first day</Empty>}
          </div>
        </Card>
      </div>

      <Card>
        <div className="card-head"><h3>My submissions</h3><Link className="btn sm" to="/reviews">Review centre</Link></div>
        <table>
          <thead><tr><th>Task</th><th>Version</th><th>Status</th><th>Submitted</th></tr></thead>
          <tbody>
            {submissions.slice(0, 6).map((s) => (
              <tr key={s.id}>
                <td className="mono small">{s.task_id.slice(0, 8)}</td>
                <td>v{s.version_no}</td>
                <td><Status value={s.status} /></td>
                <td className="small muted">{fmt.dateTime(s.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!submissions.length && <Empty>Nothing submitted yet</Empty>}
      </Card>
    </>
  )
}

export default function Dashboard() {
  const { user } = useAuth()
  return user?.role === 'admin' ? <AdminDashboard /> : <WorkerDashboard />
}
