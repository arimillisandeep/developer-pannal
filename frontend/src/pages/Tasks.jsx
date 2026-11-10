import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../context/AuthContext'
import { Card, Empty, Modal, Progress, Spinner, Status, fmt, useToast } from '../components/ui'

function TaskDetail({ task, onClose, onChanged }) {
  const { user } = useAuth()
  const toast = useToast()
  const [comments, setComments] = useState([])
  const [body, setBody] = useState('')
  const [projects, setProjects] = useState([])
  const [teams, setTeams] = useState([])
  const [projectIdForTask, setProjectIdForTask] = useState(task.project_id || '')
  const [reviewTeamId, setReviewTeamId] = useState(task.team_id || '')
  const [projectId, setProjectId] = useState('')
  const [notes, setNotes] = useState('')
  const [submissions, setSubmissions] = useState([])
  const [submitting, setSubmitting] = useState(false)
  const isAssignee = task.assignee_id === user.id
  const canManage = user.role === 'admin' || task.assigned_by === user.id

  const load = useCallback(async () => {
    const [c, s] = await Promise.all([
      api.comments(task.id), api.submissions({ task_id: task.id }).catch(() => ({ data: [] }))
    ])
    setComments(c.data); setSubmissions(s.data)
  }, [task.id])

  useEffect(() => { load() }, [load])
  useEffect(() => { api.projects().then(({ data }) => setProjects(data)) }, [])
  useEffect(() => { if (user.role === 'admin') api.teams().then(({ data }) => setTeams(data)).catch(() => setTeams([])) }, [user.role])

  const send = async () => {
    if (!body.trim()) return
    await api.comment(task.id, body.trim())
    setBody(''); load()
  }

  const move = async (status, progress) => {
    try {
      await api.updateTask(task.id, { status, progress })
      toast('Task updated'); onChanged()
    } catch (e) { toast(e?.response?.data?.detail || 'Update failed', 'error') }
  }

  const saveTaskProject = async () => {
    try {
      await api.updateTask(task.id, { project_id: projectIdForTask || null })
      toast('Task project updated')
      onChanged()
    } catch (e) { toast(e?.response?.data?.detail || 'Could not update task project', 'error') }
  }

  const saveReviewTeam = async () => {
    try {
      await api.updateTask(task.id, { team_id: reviewTeamId || null })
      toast('Review team updated')
      onChanged()
    } catch (e) { toast(e?.response?.data?.detail || 'Could not update review team', 'error') }
  }

  const submit = async () => {
    if (submitting) return
    setSubmitting(true)
    try {
      await api.submit({ task_id: task.id, project_id: projectId || undefined, notes })
      toast('Code submitted for review')
      setNotes(''); load(); onChanged()
    } catch (e) { toast(e?.response?.data?.detail || 'Submission failed', 'error') }
    finally { setSubmitting(false) }
  }
  const awaitingReview = submissions.some((s) => ['submitted', 'approved', 'forwarded_to_admin'].includes(s.status))

  return (
    <Modal title={task.title} onClose={onClose} wide>
      <div className="row wrap" style={{ gap: 10, marginBottom: 14 }}>
        <span className="pill grey mono">{task.code}</span>
        <Status value={task.status} />
        <span className="pill blue">{task.priority}</span>
        <span className="small muted">Due {fmt.date(task.due_date)}</span>
      </div>
      <p className="muted small" style={{ whiteSpace: 'pre-wrap', marginBottom: 16 }}>{task.description || 'No description.'}</p>
      <div className="between small" style={{ marginBottom: 6 }}><span className="muted">Progress</span><strong>{task.progress}%</strong></div>
      <Progress value={task.progress} />

      {user.role === 'admin' && (
        <div className="field" style={{ marginTop: 14 }}>
          <label>On-Roll review team</label>
          <div className="row">
            <select value={reviewTeamId} onChange={(e) => setReviewTeamId(e.target.value)}>
              <option value="">— select a team —</option>
              {teams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}
            </select>
            <button className="btn sm" disabled={reviewTeamId === (task.team_id || '')} onClick={saveReviewTeam}>Save</button>
          </div>
          <span className="small muted">Team lead receives code submissions for review.</span>
          <label style={{ marginTop: 8 }}>Assignee project</label>
          <div className="row">
            <select value={projectIdForTask} onChange={(e) => setProjectIdForTask(e.target.value)}>
              <option value="">— no project linked —</option>
              {projects.filter((project) => project.owner_id === task.assignee_id).map((project) => (
                <option key={project.id} value={project.id}>{project.name}</option>
              ))}
            </select>
            <button className="btn sm" disabled={projectIdForTask === (task.project_id || '')} onClick={saveTaskProject}>Save project</button>
          </div>
        </div>
      )}

      {(isAssignee || canManage) && (
        <>
          <div className="divider" />
          <div className="row wrap">
            {isAssignee && task.status !== 'submitted' && (
              <>
                <button className="btn sm" onClick={() => move('in_progress', Math.max(task.progress, 10))}>Start work</button>
                <button className="btn sm" onClick={() => move('in_progress', Math.min(90, task.progress + 20))}>+20% progress</button>
              </>
            )}
            {canManage && !['completed', 'approved', 'final_approved'].includes(task.status) && (
              <button className="btn sm success" onClick={() => move('completed', 100)}>Mark complete</button>
            )}
          </div>
        </>
      )}

      {isAssignee && (
        <>
          <div className="divider" />
          <h3 style={{ marginBottom: 10 }}>Submit code</h3>
          <div className="grid c2">
            <div className="field">
              <label>Project</label>
              <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                <option value="">— choose project —</option>
                {projects.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.language})</option>)}
              </select>
            </div>
            <div className="field">
              <label>Notes for reviewer</label>
              <input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="What changed?" />
            </div>
          </div>
          <button className="btn primary" onClick={submit} disabled={!projectId || submitting || awaitingReview}>
            {submitting ? 'Submitting…' : awaitingReview ? 'Awaiting review' : 'Submit code for review'}
          </button>
        </>
      )}

      <div className="divider" />
      <h3 style={{ marginBottom: 10 }}>Submission history</h3>
      {submissions.length === 0 && <span className="small muted">No submissions yet.</span>}
      {submissions.map((s) => (
        <div key={s.id} className="between" style={{ padding: '6px 0', borderBottom: '1px solid var(--border)' }}>
          <span className="small mono">v{s.version_no} · {fmt.dateTime(s.created_at)}</span>
          <Status value={s.status} />
        </div>
      ))}

      <div className="divider" />
      <h3 style={{ marginBottom: 10 }}>Discussion</h3>
      <div className="stack" style={{ maxHeight: 240, overflowY: 'auto', marginBottom: 12 }}>
        {comments.map((c) => (
          <div key={c.id} style={{ background: 'var(--panel-2)', padding: '8px 10px', borderRadius: 10 }}>
            <div className="small" style={{ fontWeight: 600 }}>{c.sender_name}</div>
            <div className="small">{c.body}</div>
            <div className="small muted" style={{ fontSize: 10 }}>{fmt.dateTime(c.created_at)}</div>
          </div>
        ))}
        {!comments.length && <span className="small muted">No comments yet.</span>}
      </div>
      <div className="row">
        <input value={body} onChange={(e) => setBody(e.target.value)} placeholder="Write a comment…"
          onKeyDown={(e) => e.key === 'Enter' && send()} />
        <button className="btn primary" onClick={send}>Send</button>
      </div>
    </Modal>
  )
}

export default function Tasks() {
  const { user } = useAuth()
  const toast = useToast()
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('')
  const [q, setQ] = useState('')
  const [selected, setSelected] = useState(null)
  const [creating, setCreating] = useState(false)
  const [people, setPeople] = useState([])
  const [teams, setTeams] = useState([])
  const [projects, setProjects] = useState([])
  const [form, setForm] = useState({ title: '', description: '', assignee_id: '', team_id: '', project_id: '', priority: 'medium', due_date: '' })
  const canAssign = user.role === 'admin' || user.role === 'developer_onroll' || user.role === 'developer_offroll'

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await api.tasks({ status: filter || undefined, q: q || undefined })
      setTasks(data)
    } finally { setLoading(false) }
  }, [filter, q])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    if (!canAssign) return
    api.users({ size: 200 }).then(({ data }) => setPeople(data.filter((u) => u.role !== 'admin')))
      .catch(() => api.contacts().then(({ data }) => setPeople(data)))
    api.teams().then(({ data }) => setTeams(data)).catch(() => setTeams([]))
    api.projects().then(({ data }) => setProjects(data)).catch(() => setProjects([]))
  }, [canAssign])

  const create = async () => {
    try {
      await api.createTask({
        title: form.title, description: form.description, assignee_id: form.assignee_id,
        team_id: form.team_id || undefined, project_id: form.project_id || undefined,
        priority: form.priority, due_date: form.due_date || undefined
      })
      toast('Task created'); setCreating(false)
      setForm({ title: '', description: '', assignee_id: '', team_id: '', project_id: '', priority: 'medium', due_date: '' })
      load()
    } catch (e) { toast(e?.response?.data?.detail || 'Could not create task', 'error') }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">Work</div>
          <h1>Tasks</h1>
          <p className="muted small" style={{ marginTop: 4 }}>
            {canAssign ? 'Assign work, track progress and review submissions.' : 'Everything assigned to you.'}
          </p>
        </div>
        {canAssign && <button className="btn primary" onClick={() => setCreating(true)}>+ New task</button>}
      </div>

      <Card style={{ marginBottom: 16 }}>
        <div className="row wrap">
          <div className="search grow" style={{ minWidth: 220 }}>
            <input placeholder="Search title or task code…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <select style={{ width: 190 }} value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">All statuses</option>
            {['not_started', 'in_progress', 'submitted', 'under_review', 'rework_required', 'approved', 'completed', 'final_approved', 'overdue'].map((s) => (
              <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>
            ))}
          </select>
          <button className="btn sm" onClick={load}>Refresh</button>
        </div>
      </Card>

      {loading ? <Spinner /> : (
        <Card>
          <table>
            <thead>
              <tr><th>Code</th><th>Task</th><th>Priority</th><th>Status</th><th>Progress</th><th>Due</th></tr>
            </thead>
            <tbody>
              {tasks.map((t) => (
                <tr key={t.id} style={{ cursor: 'pointer' }} onClick={() => setSelected(t)}>
                  <td className="mono small">{t.code}</td>
                  <td><div style={{ fontWeight: 600 }}>{t.title}</div></td>
                  <td><span className={`pill ${t.priority === 'high' || t.priority === 'critical' ? 'red' : t.priority === 'low' ? 'grey' : 'amber'}`}>{t.priority}</span></td>
                  <td><Status value={t.status} /></td>
                  <td style={{ width: 140 }}>
                    <div className="row"><div className="grow"><Progress value={t.progress} /></div><span className="small muted">{t.progress}%</span></div>
                  </td>
                  <td className="small muted">{fmt.date(t.due_date)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!tasks.length && <Empty>No tasks match this filter.</Empty>}
        </Card>
      )}

      {selected && <TaskDetail task={selected} onClose={() => setSelected(null)} onChanged={load} />}

      {creating && (
        <Modal title="New task" onClose={() => setCreating(false)}>
          <div className="field"><label>Title</label><input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></div>
          <div className="field"><label>Description</label><textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
          <div className="grid c2">
            <div className="field">
              <label>Assign to</label>
              <select value={form.assignee_id} onChange={(e) => setForm({ ...form, assignee_id: e.target.value })}>
                <option value="">— select person —</option>
                {people.map((p) => <option key={p.id} value={p.id}>{p.full_name} · {p.role.replace('developer_', '')}</option>)}
              </select>
            </div>
            <div className="field">
              <label>On-Roll review team</label>
              <select value={form.team_id} onChange={(e) => setForm({ ...form, team_id: e.target.value })}>
                <option value="">{user.role === 'admin' || user.role === 'developer_offroll' ? '— select a team —' : 'Use me as reviewer'}</option>
                {teams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}
              </select>
              <span className="small muted">Team lead receives the submission for review.</span>
            </div>
            <div className="field">
              <label>Priority</label>
              <select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>
                {['low', 'medium', 'high', 'critical'].map((p) => <option key={p}>{p}</option>)}
              </select>
            </div>
          </div>
          <div className="field">
            <label>Project for the assignee (optional)</label>
            <select value={form.project_id} onChange={(e) => setForm({ ...form, project_id: e.target.value })}>
              <option value="">— no project linked —</option>
              {projects.filter((project) => project.owner_id === form.assignee_id).map((project) => (
                <option key={project.id} value={project.id}>{project.name}</option>
              ))}
            </select>
          </div>
          <div className="field"><label>Deadline</label><input type="date" value={form.due_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} /></div>
          <button className="btn primary" onClick={create}
            disabled={!form.title || !form.assignee_id || ((user.role === 'admin' || user.role === 'developer_offroll') && !form.team_id)}>
            Create task
          </button>
        </Modal>
      )}
    </>
  )
}
