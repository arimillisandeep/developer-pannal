import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../context/AuthContext'
import { Card, Empty, Spinner, useToast } from '../components/ui'

const dateText = (value) => value ? new Date(value).toLocaleString() : '—'
const duration = (seconds = 0) => `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`

function rangeFor(period) {
  const end = new Date()
  const start = new Date(end)
  if (period === 'week') start.setDate(end.getDate() - ((end.getDay() + 6) % 7))
  if (period === 'month') start.setDate(1)
  const asDate = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
  return { from: asDate(start), to: asDate(end) }
}

export default function EmployeeReports() {
  const { user } = useAuth()
  const toast = useToast()
  const [range, setRange] = useState(() => rangeFor('week'))
  const [own, setOwn] = useState(null)
  const [team, setTeam] = useState([])
  const [employeeId, setEmployeeId] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    const params = { date_from: range.from, date_to: range.to }
    try {
      const ownRequest = api.myReport(params)
      const teamRequest = user.role === 'developer_onroll' ? api.teamReport(params) : Promise.resolve(null)
      const [ownResponse, teamResponse] = await Promise.all([ownRequest, teamRequest])
      setOwn(ownResponse.data)
      setTeam(teamResponse?.data?.employees || [])
    } catch (error) {
      toast(error?.response?.data?.detail || 'Could not load work reports', 'error')
      setOwn(null)
      setTeam([])
    } finally { setLoading(false) }
  }, [range, user.role, toast])

  useEffect(() => { load() }, [load])

  const selected = useMemo(() => team.find((item) => item.employee.id === employeeId) || own,
    [team, employeeId, own])
  const totals = useMemo(() => ({
    active: (selected?.sessions || []).reduce((sum, item) => sum + (item.active_seconds || 0), 0),
    paused: (selected?.sessions || []).reduce((sum, item) => sum + (item.paused_seconds || 0), 0),
    away: (selected?.sessions || []).reduce((sum, item) => sum + (item.away_seconds || 0), 0),
  }), [selected])

  const exportReport = async (format) => {
    if (!selected) return
    try {
      const params = { date_from: range.from, date_to: range.to }
      const result = employeeId
        ? await api.exportTeamReport(employeeId, format, params)
        : await api.exportMyReport(format, params)
      const url = URL.createObjectURL(result.data)
      const link = document.createElement('a')
      link.href = url
      link.download = `workcode-report-${selected.employee.id.slice(0, 8)}-${range.from}-${range.to}.${format}`
      link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (error) {
      toast(error?.response?.data?.detail || `Could not export ${format.toUpperCase()} report`, 'error')
    }
  }

  return (
    <>
      <div className="page-head employee-report-head">
        <div>
          <div className="eyebrow">Work reporting</div>
          <h1>{employeeId ? 'Team member report' : 'My work report'}</h1>
          <p className="muted small">Calculated from saved tasks, timer sessions, recordings, submissions, and audit events.</p>
        </div>
        <div className="employee-report-filters">
          {['day', 'week', 'month'].map((period) => <button key={period} className="btn sm"
            onClick={() => setRange(rangeFor(period))}>{period === 'day' ? 'Today' : `This ${period}`}</button>)}
          <input aria-label="From date" type="date" value={range.from}
            onChange={(event) => setRange((old) => ({ ...old, from: event.target.value }))} />
          <input aria-label="To date" type="date" value={range.to}
            onChange={(event) => setRange((old) => ({ ...old, to: event.target.value }))} />
          <button className="btn sm primary" onClick={load}>Apply</button>
        </div>
      </div>

      {loading ? <Spinner /> : !own ? <Empty>No report data is available.</Empty> : (
        <>
          {user.role === 'developer_onroll' && team.length > 0 && (
            <Card className="employee-report-team">
              <div className="card-head"><h3>Assigned review team</h3>
                <button className="btn sm" onClick={() => setEmployeeId('')}>My report</button></div>
              <div className="employee-report-team-list">
                {team.map((report) => <button key={report.employee.id}
                  className={`employee-report-person ${employeeId === report.employee.id ? 'active' : ''}`}
                  onClick={() => setEmployeeId(report.employee.id)}>
                  <span><b>{report.employee.name}</b><small>{report.employee.role.replaceAll('_', ' ')}</small></span>
                  <span>{report.tasks.length} tasks · {report.submissions.length} submissions</span>
                </button>)}
              </div>
            </Card>
          )}

          <div className="employee-report-summary">
            <Card><span className="label">Verified active time</span><strong>{duration(totals.active)}</strong></Card>
            <Card><span className="label">Paused time</span><strong>{duration(totals.paused)}</strong></Card>
            <Card><span className="label">Recorded away intervals</span><strong>{duration(totals.away)}</strong></Card>
            <Card><span className="label">Tasks in scope</span><strong>{selected?.tasks?.length || 0}</strong></Card>
          </div>

          <div className="employee-report-actions"><span className="muted small">{selected?.employee?.name} · {range.from} to {range.to}</span>
            <div className="row wrap" style={{ gap: 6 }}>
              <button className="btn sm" onClick={() => exportReport('csv')}>CSV</button>
              <button className="btn sm" onClick={() => exportReport('xlsx')}>Excel</button>
              <button className="btn sm" onClick={() => exportReport('pdf')}>PDF</button>
            </div>
          </div>

          <Card className="employee-report-section">
            <h3>Daily attendance</h3>
            {!selected?.attendance?.length ? <Empty>No attendance entries in this date range.</Empty> : (
              <div className="table-scroll"><table><thead><tr><th>Date</th><th>Status</th><th>Active</th><th>Paused</th><th>First start</th><th>Last stop</th></tr></thead>
                <tbody>{selected.attendance.map((item) => <tr key={item.date}><td>{item.date}</td><td>{item.status}</td>
                  <td>{duration(item.active_seconds)}</td><td>{duration(item.paused_seconds)}</td>
                  <td>{dateText(item.started_at)}</td><td>{dateText(item.stopped_at)}</td></tr>)}</tbody></table></div>
            )}
          </Card>

          <div className="grid c2 employee-report-grid">
            <Card className="employee-report-section"><h3>Assigned tasks</h3>
              {!selected?.tasks?.length ? <Empty>No tasks in scope.</Empty> : <div className="table-scroll"><table><thead><tr><th>Task</th><th>Status</th><th>Due</th></tr></thead>
                <tbody>{selected.tasks.map((task) => <tr key={task.id}><td>{task.code} · {task.title}</td><td>{task.status.replaceAll('_', ' ')}</td><td>{task.due_date || '—'}</td></tr>)}</tbody></table></div>}
            </Card>
            <Card className="employee-report-section"><h3>Submission and review history</h3>
              {!selected?.submissions?.length ? <Empty>No submissions in scope.</Empty> : <div className="table-scroll"><table><thead><tr><th>Created</th><th>Version</th><th>Status</th><th>Review comments</th></tr></thead>
                <tbody>{selected.submissions.map((item) => <tr key={item.id}><td>{dateText(item.created_at)}</td><td>{item.version_no}</td><td>{item.status.replaceAll('_', ' ')}</td><td>{item.review_notes || '—'}</td></tr>)}</tbody></table></div>}
            </Card>
          </div>

          <Card className="employee-report-section"><h3>Work sessions and recordings</h3>
            {!selected?.sessions?.length && !selected?.recordings?.length ? <Empty>No sessions or recordings in this date range.</Empty> : <div className="table-scroll"><table><thead><tr><th>Type</th><th>Started</th><th>Status</th><th>Duration / active</th><th>Details</th></tr></thead><tbody>
              {(selected.sessions || []).map((item) => <tr key={`s-${item.id}`}><td>Timer session</td><td>{dateText(item.started_at)}</td><td>{item.status}</td><td>{duration(item.active_seconds)}</td><td>paused {duration(item.paused_seconds)} · away {duration(item.away_seconds)}</td></tr>)}
              {(selected.recordings || []).map((item) => <tr key={`r-${item.id}`}><td>Screen recording</td><td>{dateText(item.started_at)}</td><td>{item.status}</td><td>{duration(item.duration_seconds)}</td><td>{item.size_bytes} bytes</td></tr>)}
            </tbody></table></div>}
          </Card>

          <Card className="employee-report-section"><h3>Audit activity</h3>
            {!selected?.activity?.length ? <Empty>No matching audit events in this date range.</Empty> : <div className="table-scroll"><table><thead><tr><th>Time</th><th>Action</th><th>Record</th></tr></thead><tbody>
              {selected.activity.map((item, index) => <tr key={`${item.action}-${item.entity_id}-${index}`}><td>{dateText(item.created_at)}</td><td>{item.action}</td><td>{item.entity_type} · {item.entity_id}</td></tr>)}
            </tbody></table></div>}
          </Card>
        </>
      )}
    </>
  )
}
