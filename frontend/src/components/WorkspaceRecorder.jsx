import React, { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api'
import { useAuth } from '../context/AuthContext'
import { LocalRecordingStorage } from '../services/localRecordingStorage'
import './workspace-recorder.css'

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function sendWithRetry(id, seq, blob) {
  let error
  for (let attempt = 0; attempt < 4; attempt += 1) {
    try {
      await api.recChunk(id, seq, blob)
      return
    } catch (caught) {
      error = caught
      if (attempt < 3) await delay(250 * (2 ** attempt))
    }
  }
  throw error
}

function formatClock(totalSeconds) {
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

export default function WorkspaceRecorder({ projectId, taskId = null, toast }) {
  const { user } = useAuth()
  const [recording, setRecording] = useState(null)
  const [seconds, setSeconds] = useState(0)
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [uploadProblem, setUploadProblem] = useState(false)
  const [localItems, setLocalItems] = useState([])
  const [showLocal, setShowLocal] = useState(false)
  const [showCapturePrompt, setShowCapturePrompt] = useState(false)
  const [portalTarget, setPortalTarget] = useState(null)
  const [localVideo, setLocalVideo] = useState(null)
  const streamRef = useRef(null)
  const recorderRef = useRef(null)
  const recordingRef = useRef(null)
  const startedAtRef = useRef(0)
  const sequenceRef = useRef(0)
  const queueRef = useRef(Promise.resolve())
  const failedRef = useRef([])
  const localFailureRef = useRef(null)
  const busyRef = useRef(false)

  const refreshLocal = useCallback(async () => {
    try {
      let items = await LocalRecordingStorage.list(user.id)
      const interruptedAt = new Date().toISOString()
      for (const item of items) {
        if (item.status === 'recording' && item.id !== recordingRef.current?.localId) {
          const duration = Math.max(0, Math.floor((Date.now() - Date.parse(item.started_at)) / 1000))
          await LocalRecordingStorage.update(item.id, {
            status: 'interrupted', ended_at: interruptedAt, duration_seconds: duration,
            sync_status: item.remote_id ? 'pending' : 'local_only',
          })
        }
      }
      items = await LocalRecordingStorage.list(user.id)
      setLocalItems(items)
    } catch (error) {
      toast(error?.message || 'Could not read local recordings', 'error')
    }
  }, [toast, user.id])

  useEffect(() => { refreshLocal() }, [refreshLocal])
  useEffect(() => {
    const findTarget = () => setPortalTarget(document.getElementById('workspace-recording-slot'))
    findTarget()
    const observer = new MutationObserver(findTarget)
    observer.observe(document.body, { childList: true, subtree: true })
    return () => observer.disconnect()
  }, [])
  useEffect(() => {
    if (projectId) setShowCapturePrompt(true)
  }, [projectId])
  useEffect(() => {
    if (!recording) return undefined
    const tick = setInterval(() => setSeconds(Math.floor((Date.now() - startedAtRef.current) / 1000)), 1000)
    return () => clearInterval(tick)
  }, [recording])
  useEffect(() => () => { if (localVideo) URL.revokeObjectURL(localVideo.url) }, [localVideo])

  const syncLocalRecording = async (item) => {
    const stored = await LocalRecordingStorage.get(item.id)
    if (!stored) throw new Error('Local recording was not found')

    let remoteId = stored.remote_id
    if (remoteId) {
      try {
        const { data } = await api.recording(remoteId)
        if (data.status === 'completed') {
          await LocalRecordingStorage.update(stored.id, { sync_status: 'synced' })
          return
        }
      } catch (error) {
        if (error?.response?.status === 404) remoteId = null
        else throw error
      }
    }

    if (!remoteId) {
      const { data } = await api.recStart({
        consent: true,
        project_id: stored.project_id,
        task_id: stored.task_id,
        session_id: stored.session_id,
        mime_type: stored.mime_type,
      })
      remoteId = data.id
      await LocalRecordingStorage.update(stored.id, { remote_id: remoteId })
    }

    const chunks = await LocalRecordingStorage.chunks(stored.id)
    for (const chunk of chunks) await sendWithRetry(remoteId, chunk.sequence, chunk.blob)
    await api.recComplete(remoteId, stored.duration_seconds || 0)
    await LocalRecordingStorage.update(stored.id, { sync_status: 'synced', remote_id: remoteId })
  }

  const stopRecording = async () => {
    const current = recordingRef.current
    if (!current || busyRef.current) return
    busyRef.current = true
    setStopping(true)
    try {
      const mediaRecorder = recorderRef.current
      if (mediaRecorder?.state === 'recording') {
        const stopped = new Promise((resolve) => mediaRecorder.addEventListener('stop', resolve, { once: true }))
        mediaRecorder.stop()
        await stopped
      }
      streamRef.current?.getTracks().forEach((track) => track.stop())
      await queueRef.current

      const durationSeconds = Math.max(0, Math.floor((Date.now() - startedAtRef.current) / 1000))
      const endedAt = new Date().toISOString()
      let syncStatus = current.remoteId ? 'pending' : 'local_only'

      if (current.remoteId && !localFailureRef.current) {
        for (const sequence of failedRef.current.splice(0)) {
          try {
            const chunks = await LocalRecordingStorage.chunks(current.localId)
            const chunk = chunks.find((item) => item.sequence === sequence)
            if (chunk) await sendWithRetry(current.remoteId, sequence, chunk.blob)
          } catch {
            failedRef.current.push(sequence)
          }
        }
        if (!failedRef.current.length) {
          try {
            await api.recComplete(current.remoteId, durationSeconds)
            syncStatus = 'synced'
          } catch {
            syncStatus = 'pending'
          }
        }
      }

      await LocalRecordingStorage.update(current.localId, {
        status: 'completed', ended_at: endedAt, duration_seconds: durationSeconds,
        sync_status: syncStatus,
      })
      if (localFailureRef.current) {
        toast('Recording stopped, but some local chunks could not be saved', 'error')
      } else {
        toast(syncStatus === 'synced' ? 'Recording saved and synced' : 'Recording saved on this device', 'ok')
      }
      recordingRef.current = null
      recorderRef.current = null
      streamRef.current = null
      setRecording(null)
      setSeconds(0)
      setUploadProblem(syncStatus === 'pending')
      await refreshLocal()
    } catch (error) {
      toast(error?.response?.data?.detail || error?.message || 'Could not finish recording', 'error')
    } finally {
      busyRef.current = false
      setStopping(false)
    }
  }

  useEffect(() => () => {
    if (recordingRef.current) void stopRecording()
  }, [])

  const startRecording = async () => {
    if (starting || recordingRef.current) return
    if (!window.isSecureContext) {
      toast(`Screen sharing requires HTTPS or localhost. This page is using ${location.protocol}//${location.host}.`, 'error')
      return
    }
    if (!navigator.mediaDevices?.getDisplayMedia || typeof MediaRecorder === 'undefined') {
      toast('Screen capture is unavailable in this browser or browsing context.', 'error')
      return
    }

    setStarting(true)
    let stream
    let remote = null
    let local = null
    try {
      // Keep the screen picker directly in the click flow so browser consent remains explicit.
      stream = await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: 5 }, audio: false })
      const mimeType = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm']
        .find((type) => MediaRecorder.isTypeSupported(type))
      if (!mimeType) throw new Error('This browser cannot encode a supported recording format')

      let sessionId = null
      try {
        let timer = await api.timerCurrent()
        if (!timer.data.active) timer = await api.timerStart()
        sessionId = timer.data.session?.id || timer.data.id || null
      } catch {
        // A local recording can continue during a backend outage.
      }

      try {
        const { data } = await api.recStart({ consent: true, project_id: projectId, task_id: taskId, session_id: sessionId, mime_type: mimeType })
        remote = data
      } catch {
        // Persist locally first; a later retry can upload these same chunks.
      }

      local = await LocalRecordingStorage.create({
        user_id: user.id, task_id: taskId, session_id: sessionId, project_id: projectId,
        started_at: new Date().toISOString(), ended_at: null, duration_seconds: 0,
        mime_type: mimeType, remote_id: remote?.id || null,
        consent_given: true, sync_status: remote ? 'pending' : 'local_only',
      })
      const mediaRecorder = new MediaRecorder(stream, { mimeType })
      streamRef.current = stream
      recorderRef.current = mediaRecorder
      recordingRef.current = { localId: local.id, remoteId: remote?.id || null }
      sequenceRef.current = 0
      failedRef.current = []
      localFailureRef.current = null
      queueRef.current = Promise.resolve()
      startedAtRef.current = Date.now()
      setUploadProblem(false)
      setSeconds(0)

      mediaRecorder.ondataavailable = (event) => {
        if (!event.data?.size) return
        const sequence = sequenceRef.current++
        queueRef.current = queueRef.current.then(async () => {
          try {
            await LocalRecordingStorage.appendChunk(local.id, sequence, event.data)
          } catch (error) {
            localFailureRef.current = error
            setUploadProblem(true)
          }
          if (remote?.id) {
            try {
              await sendWithRetry(remote.id, sequence, event.data)
            } catch {
              failedRef.current.push(sequence)
              setUploadProblem(true)
            }
          }
        })
      }
      stream.getVideoTracks()[0]?.addEventListener('ended', stopRecording, { once: true })
      mediaRecorder.start(5000)
      setRecording(local)
      setShowCapturePrompt(false)
      toast(remote ? 'Recording started; chunks are saved locally and synced.' : 'Recording started; saved to this device until sync is available.', 'ok')
    } catch (error) {
      if (recorderRef.current?.state === 'recording') {
        try { recorderRef.current.stop() } catch { /* already stopped */ }
      }
      stream?.getTracks().forEach((track) => track.stop())
      recordingRef.current = null
      recorderRef.current = null
      streamRef.current = null
      setRecording(null)
      if (remote?.id) api.recDelete(remote.id).catch(() => {})
      if (local?.id) {
        await LocalRecordingStorage.update(local.id, {
          status: 'interrupted', ended_at: new Date().toISOString(),
          remote_id: null, sync_status: 'local_only',
        }).catch(() => {})
        await refreshLocal()
      }
      toast(error?.response?.data?.detail || error?.message || 'Screen recording permission was denied', 'error')
    } finally {
      setStarting(false)
    }
  }

  const openLocalVideo = async (item) => {
    try {
      if (localVideo) URL.revokeObjectURL(localVideo.url)
      const blob = await LocalRecordingStorage.makeBlob(item.id)
      setLocalVideo({ id: item.id, url: URL.createObjectURL(blob) })
    } catch (error) {
      toast(error?.message || 'Could not open local recording', 'error')
    }
  }

  const downloadLocal = async (item) => {
    try {
      const blob = await LocalRecordingStorage.makeBlob(item.id)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `workcode-recording-${item.id}.webm`
      link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (error) {
      toast(error?.message || 'Could not download local recording', 'error')
    }
  }

  const removeLocal = async (item) => {
    if (!window.confirm('Delete this recording from this browser profile?')) return
    if (localVideo?.id === item.id) setLocalVideo(null)
    try {
      await LocalRecordingStorage.remove(item.id)
      await refreshLocal()
      toast('Local recording deleted', 'ok')
    } catch (error) {
      toast(error?.message || 'Could not delete local recording', 'error')
    }
  }

  const retryUpload = async (item) => {
    try {
      await syncLocalRecording(item)
      setUploadProblem(false)
      await refreshLocal()
      toast('Recording synced to the server', 'ok')
    } catch (error) {
      toast(error?.response?.data?.detail || 'Upload is still unavailable; the local copy is safe', 'error')
    }
  }

  const controls = (
    <div className="ide-recording-control">
      {recording ? (
        <>
          <span className="ide-recording-status" role="status">● REC {formatClock(seconds)}{uploadProblem ? ' · saved locally' : ''}</span>
          <button className="icon-btn" disabled={stopping} title="Stop and save recording" onClick={stopRecording}>
            {stopping ? 'Saving…' : 'Stop recording'}
          </button>
        </>
      ) : (
        <button className="icon-btn" disabled={starting} title="Start screen recording; browser will ask which screen to share"
          onClick={startRecording}>{starting ? 'Requesting screen…' : '⏺ Record'}</button>
      )}
      <button className="icon-btn" title="View recordings saved in this browser" onClick={() => { setShowLocal((open) => !open); refreshLocal() }}>
        Local ({localItems.length})
      </button>
      {showCapturePrompt && projectId && !recording && (
        <div className="ide-recording-prompt" role="dialog" aria-label="Start screen recording">
          <strong>Start a screen recording?</strong>
          <p>Choose <b>Entire Screen</b> in the browser prompt if you want the recording to include other tabs, such as ChatGPT. Browser permission is required.</p>
          {!window.isSecureContext && <p className="secure-warning">This page uses HTTP on a network address. Screen capture requires HTTPS or localhost.</p>}
          <div className="ide-recording-prompt-actions">
            <button className="btn sm" onClick={() => setShowCapturePrompt(false)}>Later</button>
            <button className="btn sm primary" disabled={!window.isSecureContext || !navigator.mediaDevices?.getDisplayMedia}
              onClick={() => { setShowCapturePrompt(false); startRecording() }}>Start recording</button>
          </div>
        </div>
      )}
      {showLocal && (
        <div className="ide-local-recordings" role="dialog" aria-label="Local recordings">
          <div className="between"><strong>Local recordings</strong><button className="icon-btn" onClick={() => setShowLocal(false)}>✕</button></div>
          {!localItems.length && <p className="small muted">No recordings saved in this browser profile.</p>}
          {localItems.map((item) => (
            <div className="ide-local-recording" key={item.id}>
              <span className="small">{new Date(item.started_at).toLocaleString()} · {formatClock(item.duration_seconds || 0)}</span>
              <span className="small muted">{item.status === 'interrupted' ? 'Recovered after interruption' : item.sync_status === 'synced' ? 'Synced' : 'Stored on this device'} · {item.size_bytes} bytes</span>
              <div className="row wrap">
                <button className="btn sm" disabled={!item.chunk_count} onClick={() => openLocalVideo(item)}>Play</button>
                <button className="btn sm" disabled={!item.chunk_count} onClick={() => downloadLocal(item)}>Download</button>
                {item.sync_status !== 'synced' && ['completed', 'interrupted'].includes(item.status) && item.chunk_count > 0 &&
                  <button className="btn sm" onClick={() => retryUpload(item)}>Sync</button>}
                <button className="btn sm danger" disabled={item.status === 'recording'} onClick={() => removeLocal(item)}>Delete</button>
              </div>
            </div>
          ))}
          {localVideo && <video controls autoPlay src={localVideo.url} style={{ width: '100%', marginTop: 10, background: '#000' }} />}
          <p className="small muted">Local copies stay in this browser profile. Admins cannot see them until they are synced.</p>
        </div>
      )}
    </div>
  )
  return portalTarget ? createPortal(controls, portalTarget) : controls
}
