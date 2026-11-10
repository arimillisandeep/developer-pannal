import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { Card, Empty, Spinner, Stat, fmt, useToast } from '../components/ui'
import { useAuth } from '../context/AuthContext'
import EmployeeReports from './EmployeeReports'

const EXPORTS = [
  ['attendance', 'Attendance'],
  ['tasks', 'Tasks'],
  ['employees', 'Employees'],
  ['timesheets', 'Timesheets']
]

function AdminReports() {
  const toast = useToast()
  const [data, setData] = useState(null)
  const [range, setRange] = useState({ from: '', to: '' })
  const [filters, setFilters] = useState({ employee_id: '', role: '', task_id: '' })
  const [employees, setEmployees] = useState([])
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = {}
      if (range.from) params.date_from = range.from
      if (range.to) params.date_to = range.to
      for (const [key, value] of Object.entries(filters)) if (value) params[key] = value
      const { data } = await api.report(params)
      setData(data)
    } finally { setLoading(false) }
  }, [range, filters])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    api.users({ size: 200 }).then(({ data: rows }) => setEmployees(rows.filter((item) => item.role !== 'admin'))).catch(() => {})
    api.tasks({ size: 200 }).then(({ data: rows }) => setTasks(rows)).catch(() => {})
  }, [])

  const download = async (kind, format) => {
    try {
      const params = {}
      if (range.from) params.date_from = range.from
      if (range.to) params.date_to = range.to
      for (const [key, value] of Object.entries(filters)) if (value) params[key] = value
      params.format = format
      const { data } = await api.exportReport(kind, params)
      const url = URL.createObjectURL(data)
      const a = document.createElement('a')
      a.href = url; a.download = `workcode-${kind}.${format}`; a.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
      toast(`${kind} ${format.toUpperCase()} report downloaded`)
    } catch (error) { toast(error?.response?.data?.detail || 'Export failed', 'error') }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">Administration</div>
          <h1>Reports &amp; analytics</h1>
          <p className="muted small" style={{ marginTop: 4 }}>Filter date, employee, role, or task. Task filters apply to task-linked activity; timer totals remain employee-wide because sessions are not task-linked.</p>
        </div>
        <div className="row wrap">
          <input type="date" style={{ width: 160 }} value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} />
          <input type="date" style={{ width: 160 }} value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} />
          <select aria-label="Filter by role" value={filters.role} onChange={(e) => setFilters({ ...filters, role: e.target.value, employee_id: '', task_id: '' })}>
            <option value="">All roles</option><option value="developer_onroll">On-Roll</option>
            <option value="developer_offroll">Off-Roll</option><option value="intern">Intern</option>
          </select>
          <select aria-label="Filter by employee" value={filters.employee_id} onChange={(e) => setFilters({ ...filters, employee_id: e.target.value, task_id: '' })}>
            <option value="">All employees</option>
            {employees.filter((item) => !filters.role || item.role === filters.role).map((item) =>
              <option key={item.id} value={item.id}>{item.full_name}</option>)}
          </select>
          <select aria-label="Filter by task" value={filters.task_id} onChange={(e) => setFilters({ ...filters, task_id: e.target.value })}>
            <option value="">All tasks</option>
            {tasks.filter((item) => !filters.employee_id || item.assignee_id === filters.employee_id)
              .map((item) => <option key={item.id} value={item.id}>{item.code} · {item.title}</option>)}
          </select>
          <button className="btn" onClick={load}>Apply</button>
        </div>
      </div>

      {loading ? <Spinner /> : !data ? <Empty>No data</Empty> : (
        <>
          <div className="grid c5" style={{ marginBottom: 16 }}>
            <Stat label="Headcount" value={data.headcount.total} delta={`${data.headcount.onroll} on-roll · ${data.headcount.offroll} off-roll · ${data.headcount.interns} interns`} />
            <Stat label="Hours logged" value={`${data.attendance.total_hours}h`} delta={`avg ${data.attendance.avg_hours}h per person`} />
            <Stat label="Tasks completed" value={data.tasks.completed} delta={`${data.tasks.completion_rate}% completion`} />
            <Stat label="Overdue" value={data.tasks.overdue} delta={`${data.tasks.pending} pending`} />
            <Stat label="Submissions" value={data.submissions.total} delta={`${data.submissions.pending_review} awaiting review`} />
          </div>

          <div className="grid c2" style={{ marginBottom: 16 }}>
            <Card>
              <div className="card-head"><h3>Export reports</h3><span className="small muted">{data.range.from} → {data.range.to}</span></div>
              <div className="report-export-list">
                {EXPORTS.map(([kind, label]) => (
                  <div key={kind} className="report-export-row">
                    <strong>{label}</strong>
                    <div className="row wrap" style={{ gap: 6 }}>
                      {['csv', 'xlsx', 'pdf'].map((format) => <button key={format} className="btn sm"
                        onClick={() => download(kind, format)}>{format.toUpperCase()}</button>)}
                    </div>
                  </div>
                ))}
              </div>
              <div className="divider" />
              <dl className="kv">
                <dt>Projects</dt><dd>{data.projects}</dd>
                <dt>Recordings</dt><dd>{data.recordings.count} ({data.recordings.total_minutes} min)</dd>
                <dt>Attendance records</dt><dd>{data.attendance.records}</dd>
                <dt>Days present</dt><dd>{data.attendance.days_present}</dd>
              </dl>
            </Card>

            <Card>
              <div className="card-head"><h3>Task breakdown</h3></div>
              {Object.entries(data.tasks.by_status || {}).map(([k, v]) => (
                <div key={k} className="between small" style={{ padding: '5px 0', borderBottom: '1px solid var(--border)' }}>
                  <span style={{ textTransform: 'capitalize' }}>{k.replace(/_/g, ' ')}</span>
                  <strong>{v}</strong>
                </div>
              ))}
              {!Object.keys(data.tasks.by_status || {}).length && <Empty>No tasks in range</Empty>}
            </Card>
          </div>

          <Card>
            <div className="card-head"><h3>Developer performance</h3></div>
            <table>
              <thead><tr><th>Employee</th><th>Role</th><th>Tasks</th><th>Completed</th><th>Rate</th><th>Hours</th><th>Submissions</th></tr></thead>
              <tbody>
                {data.people.map((p) => (
                  <tr key={p.user_id}>
                    <td style={{ fontWeight: 600 }}>{p.name}</td>
                    <td className="small muted">{p.role.replace('developer_', '')}</td>
                    <td>{p.tasks}</td>
                    <td>{p.completed}</td>
                    <td><span className={`pill ${p.completion_rate >= 70 ? 'green' : p.completion_rate >= 40 ? 'amber' : 'red'}`}>{p.completion_rate}%</span></td>
                    <td className="small">{p.hours}h</td>
                    <td className="small muted">{p.submissions}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!data.people.length && <Empty>No employees yet.</Empty>}
          </Card>
        </>
      )}
    </>
  )
}

export default function Reports() {
  const { user } = useAuth()
  return user?.role === 'admin' ? <AdminReports /> : <EmployeeReports />
}
