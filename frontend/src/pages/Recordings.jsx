import React, { useCallback, useEffect, useState } from 'react'
import http, { api } from '../api'
import { useAuth } from '../context/AuthContext'
import { Card, Empty, Spinner, Status, fmt, useToast } from '../components/ui'

export default function Recordings() {
  const { user } = useAuth()
  const toast = useToast()
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState(null)
  const [detail, setDetail] = useState(null)
  const [videoUrl, setVideoUrl] = useState(null)
  const [filter, setFilter] = useState({ role: '', date_from: '', date_to: '' })

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = Object.fromEntries(Object.entries(filter).filter(([, value]) => value))
      const { data } = await api.recordings(params)
      setItems(data)
    } catch (error) {
      toast(error?.response?.data?.detail || 'Could not load recordings', 'error')
    } finally {
      setLoading(false)
    }
  }, [filter, toast])

  useEffect(() => { load() }, [load])
  useEffect(() => () => {
    if (videoUrl?.startsWith('blob:')) URL.revokeObjectURL(videoUrl)
  }, [videoUrl])

  const open = async (recording) => {
    setSelected(recording)
    setDetail(null)
    setVideoUrl(null)
    try {
      const { data } = await api.recording(recording.id)
      const video = await http.get(api.recordingUrl(recording.id), { responseType: 'blob' })
      setDetail(data)
      setVideoUrl(URL.createObjectURL(video.data))
    } catch (error) {
      setDetail(recording)
      toast(error?.response?.data?.detail || 'Could not open this recording', 'error')
    }
  }

  const close = () => {
    setSelected(null)
    setVideoUrl(null)
  }

  const isWorker = user.role !== 'admin'

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">Monitoring</div>
          <h1>{isWorker ? 'My screen recordings' : 'Screen recordings'}</h1>
          <p className="muted small" style={{ marginTop: 4 }}>
            Screen capture requires browser consent and shows the browser's sharing indicator. Use HTTPS or localhost; browsers block screen capture on ordinary HTTP network addresses.
          </p>
        </div>
      </div>

      {user.role === 'admin' && (
        <Card style={{ marginBottom: 16 }}>
          <div className="row wrap">
            <select style={{ width: 190 }} value={filter.role} onChange={(event) => setFilter({ ...filter, role: event.target.value })}>
              <option value="">All roles</option>
              <option value="developer_onroll">On-roll</option>
              <option value="developer_offroll">Off-roll</option>
              <option value="intern">Interns</option>
            </select>
            <input type="date" style={{ width: 170 }} value={filter.date_from} onChange={(event) => setFilter({ ...filter, date_from: event.target.value })} />
            <input type="date" style={{ width: 170 }} value={filter.date_to} onChange={(event) => setFilter({ ...filter, date_to: event.target.value })} />
            <button className="btn sm" onClick={load}>Apply filters</button>
          </div>
        </Card>
      )}

      {loading ? <Spinner /> : (
        <Card>
          <table>
            <thead>
              <tr><th>Started</th><th>Duration</th><th>Size</th><th>Chunks</th><th>Status</th><th>Retention</th><th /></tr>
            </thead>
            <tbody>
              {items.map((recording) => (
                <tr key={recording.id}>
                  <td className="small">{fmt.dateTime(recording.started_at)}</td>
                  <td className="small">{fmt.hours(recording.duration_seconds)}</td>
                  <td className="small muted">{fmt.bytes(recording.size_bytes)}</td>
                  <td className="small muted">{recording.chunk_count}</td>
                  <td><Status value={recording.status === 'completed' ? 'approved' : recording.status} /></td>
                  <td className="small muted">{fmt.date(recording.retention_until)}</td>
                  <td><button className="btn sm" onClick={() => open(recording)}>View</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!items.length && <Empty>No synced recordings yet. Local recordings stay in this browser profile until synced.</Empty>}
        </Card>
      )}

      {selected && (
        <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && close()}>
          <div className="modal wide">
            <div className="between" style={{ marginBottom: 12 }}>
              <h2>Recording · {fmt.dateTime(selected.started_at)}</h2>
              <button className="btn sm ghost" onClick={close}>✕</button>
            </div>
            <video controls preload="metadata" style={{ width: '100%', borderRadius: 10, background: '#000' }}
              src={videoUrl || undefined} />
            <dl className="kv" style={{ marginTop: 16 }}>
              <dt>Employee</dt><dd>{detail?.user_name || user.full_name}</dd>
              <dt>Role</dt><dd>{detail?.user_role || user.role}</dd>
              <dt>Duration</dt><dd>{fmt.hours(detail?.duration_seconds)}</dd>
              <dt>Chunks</dt><dd>{detail?.chunk_count}</dd>
              <dt>Consent</dt><dd>{detail?.consent_given ? 'Given at session start' : '—'}</dd>
              <dt>Retention until</dt><dd>{fmt.date(detail?.retention_until)}</dd>
            </dl>
          </div>
        </div>
      )}
    </>
  )
}
