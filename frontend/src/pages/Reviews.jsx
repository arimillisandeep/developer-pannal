import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import http from '../api'
import { useAuth } from '../context/AuthContext'
import { Card, Empty, Modal, Spinner, Status, fmt, useToast } from '../components/ui'

export default function Reviews() {
  const { user } = useAuth()
  const toast = useToast()
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [status, setStatus] = useState('')
  const [detail, setDetail] = useState(null)
  const [activeFile, setActiveFile] = useState(null)
  const [recordingUrls, setRecordingUrls] = useState({})
  const recordingUrlsRef = useRef({})
  const [savingDecision, setSavingDecision] = useState(false)
  const [decision, setDecision] = useState({ decision: 'approved', comments: '' })

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await api.submissions({ status: status || undefined })
      setItems(data)
    } finally { setLoading(false) }
  }, [status])

  useEffect(() => { load() }, [load])
  useEffect(() => () => {
    for (const url of Object.values(recordingUrlsRef.current)) URL.revokeObjectURL(url)
  }, [])

  const open = async (s) => {
    for (const url of Object.values(recordingUrlsRef.current)) URL.revokeObjectURL(url)
    recordingUrlsRef.current = {}
    setRecordingUrls({})
    const { data } = await api.submission(s.id)
    setDetail(data)
    setDecision({ decision: 'approved', comments: '' })
    setActiveFile(data.files?.[0]?.path || null)
    const urls = {}
    for (const recording of data.recordings || []) {
      if (recording.status !== 'completed') continue
      try {
        const { data: blob } = await http.get(api.recordingUrl(recording.id), { responseType: 'blob' })
        urls[recording.id] = URL.createObjectURL(blob)
      } catch { /* The reviewer may not have recording access. */ }
    }
    recordingUrlsRef.current = urls
    setRecordingUrls(urls)
  }

  const act = async (kind) => {
    if (savingDecision) return
    if ((decision.decision === 'rejected' || decision.decision === 'rework') && !decision.comments.trim()) {
      toast('Add a reason before rejecting or requesting rework', 'error')
      return
    }
    setSavingDecision(true)
    try {
      if (kind === 'final') await api.finalDecision(detail.id, decision)
      else await api.review(detail.id, decision)
      toast('Review recorded')
      setDetail(null); load()
    } catch (e) { toast(e?.response?.data?.detail || 'Action failed', 'error') }
    finally { setSavingDecision(false) }
  }

  const isMine = detail && detail.user_id === user.id
  const canReview = user.role === 'developer_onroll' && !isMine &&
    detail && ['submitted', 'pending_permanent_review', 'rework_required'].includes(detail.status)
  const canFinalize = user.role === 'admin' && ['forwarded_to_admin', 'pending_admin_review'].includes(detail?.status)

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">Quality</div>
          <h1>Code reviews</h1>
          <p className="muted small" style={{ marginTop: 4 }}>Every submission keeps an immutable snapshot — nothing is deleted.</p>
        </div>
        <select style={{ width: 210 }} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All submissions</option>
          {['pending_permanent_review', 'pending_admin_review', 'submitted', 'under_review', 'approved', 'rework_required', 'forwarded_to_admin', 'rejected', 'final_approved'].map((s) => (
            <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>
          ))}
        </select>
      </div>

      {loading ? <Spinner /> : (
        <Card>
          <table>
            <thead><tr><th>Task</th><th>Version</th><th>Status</th><th>Submitted</th><th>Reviewed</th><th /></tr></thead>
            <tbody>
              {items.map((s) => (
                <tr key={s.id}>
                  <td className="mono small">{s.task_id.slice(0, 8)}…</td>
                  <td>v{s.version_no}</td>
                  <td><Status value={s.status} /></td>
                  <td className="small muted">{fmt.dateTime(s.created_at)}</td>
                  <td className="small muted">{s.reviewed_at ? fmt.dateTime(s.reviewed_at) : '—'}</td>
                  <td><button className="btn sm" onClick={() => open(s)}>Open</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!items.length && <Empty>No submissions yet.</Empty>}
        </Card>
      )}

      {detail && (
        <Modal title={`Submission v${detail.version_no} · ${detail.submitter_name || 'Employee'}`} onClose={() => setDetail(null)} wide>
          <div className="row wrap" style={{ marginBottom: 12 }}>
            <Status value={detail.status} />
            <span className="small muted">submitted {fmt.dateTime(detail.created_at)}</span>
            {detail.final_decision && <span className="pill green">final: {detail.final_decision}</span>}
          </div>
          {detail.notes && <p className="small muted" style={{ marginBottom: 12 }}>“{detail.notes}”</p>}
          {(detail.test_results || []).length > 0 && (
            <div className="stack" style={{ marginBottom: 14 }}>
              <h3>Submitted test results</h3>
              {detail.test_results.map((result) => (
                <div className={`test-result ${result.passed ? 'pass' : 'fail'}`} key={result.index}>
                  <strong>Test {result.index}: {result.passed ? 'passed' : 'failed'}</strong>
                  <div className="small">Expected: <code>{result.expected}</code></div>
                  <div className="small">Actual: <code>{result.actual}</code></div>
                  {result.error && <pre>{result.error}</pre>}
                </div>
              ))}
            </div>
          )}
          {(detail.recordings || []).length > 0 && (
            <div className="stack" style={{ marginBottom: 14 }}>
              <h3>Session recordings</h3>
              {detail.recordings.map((recording) => (
                <div key={recording.id} className="stack">
                  <span className="small muted">{fmt.dateTime(recording.started_at)} · {fmt.hours(recording.duration_seconds)}</span>
                  {recordingUrls[recording.id]
                    ? <video controls preload="metadata" src={recordingUrls[recording.id]} style={{ width: '100%', maxHeight: 280, background: '#000' }} />
                    : <span className="small muted">{recording.status === 'completed' ? 'Recording unavailable to this reviewer.' : 'Recording is not complete yet.'}</span>}
                </div>
              ))}
            </div>
          )}

          <div className="grid" style={{ gridTemplateColumns: '210px 1fr', gap: 12 }}>
            <div className="file-tree" style={{ borderRadius: 10, border: '1px solid var(--border)', maxHeight: 320 }}>
              {detail.files.map((f) => (
                <div key={f.path} className={`file-row ${activeFile === f.path ? 'active' : ''}`} onClick={() => setActiveFile(f.path)}>
                  <span className="truncate">{f.path}</span>
                </div>
              ))}
            </div>
            <pre className="console-body" style={{ background: '#070c15', border: '1px solid var(--border)', borderRadius: 10, maxHeight: 320, margin: 0 }}>
              {detail.files.find((f) => f.path === activeFile)?.content || 'Select a file'}
            </pre>
          </div>

          {detail.review_notes && (
            <p className="small" style={{ marginTop: 12, color: 'var(--warn)' }}>Reviewer note: {detail.review_notes}</p>
          )}

          <div className="divider" />
          <h3 style={{ marginBottom: 8 }}>Review history</h3>
          <div className="stack">
            {detail.reviews.map((r) => (
              <div key={r.id} className="small">
                <strong>{r.reviewer_name || 'Reviewer'}</strong> · {r.decision} · <span className="muted">{fmt.dateTime(r.created_at)}</span>
                {r.comments && <div className="muted">“{r.comments}”</div>}
              </div>
            ))}
            {!detail.reviews.length && <span className="small muted">No reviews yet.</span>}
          </div>

          {(canReview || canFinalize) && (
            <>
              <div className="divider" />
              <div className="row wrap">
                {(canReview || canFinalize) && <>
                  <select style={{ width: 180 }} value={decision.decision} onChange={(e) => setDecision({ ...decision, decision: e.target.value })}>
                    <option value="approved">{canFinalize ? 'Final approve' : 'Approve'}</option>
                    <option value="rework">Request rework</option>
                    <option value="rejected">Reject</option>
                  </select>
                  <input className="grow" placeholder="Comments (required for rejection/rework)" value={decision.comments}
                    onChange={(e) => setDecision({ ...decision, comments: e.target.value })} />
                  <button className={`btn ${canFinalize ? 'success' : 'primary'}`} disabled={savingDecision} onClick={() => act(canFinalize ? 'final' : 'review')}>
                    {savingDecision ? 'Saving…' : canFinalize ? 'Record final decision' : 'Submit review'}
                  </button>
                </>}
              </div>
            </>
          )}
        </Modal>
      )}
    </>
  )
}
