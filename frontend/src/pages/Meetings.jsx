
import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../context/AuthContext'
import {
  Card,
  Empty,
  Modal,
  Spinner,
  Status,
  fmt,
  useToast
} from '../components/ui'

export default function Meetings() {
  const { user } = useAuth()
  const toast = useToast()

  const [meetings, setMeetings] = useState([])
  const [people, setPeople] = useState([])
  const [loading, setLoading] = useState(true)
  const [showNew, setShowNew] = useState(false)
  const [detail, setDetail] = useState(null)
  const [saving, setSaving] = useState(false)

  const [google, setGoogle] = useState({
    configured: false,
    linked: false
  })

  // Only Admin and On-Roll can create meetings.
  const canCreate =
    user?.role === 'admin' ||
    user?.role === 'developer_onroll'

  const [form, setForm] = useState({
    title: '',
    agenda: '',
    date: '',
    start: '10:00',
    end: '11:00',
    participant_ids: [],
    create_meet_link: true
  })

  // Load meetings
  const load = useCallback(async () => {
    setLoading(true)

    try {
      const { data } = await api.meetings()
      setMeetings(data || [])
    } catch (error) {
      toast(
        error?.response?.data?.detail || 'Could not load meetings',
        'error'
      )
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => {
    load()
  }, [load])

  // Load Google account status and meeting recipients
  useEffect(() => {
    api.googleStatus()
      .then(({ data }) => setGoogle(data))
      .catch(() => {})

    if (canCreate) {
      api.meetingRecipients()
        .then(({ data }) => setPeople(data || []))
        .catch((error) => {
          console.error('Recipient loading failed:', error)
          toast('Could not load meeting participants', 'error')
        })
    }
  }, [canCreate, toast])

  // Update form
  const updateForm = (key, value) => {
    setForm(previous => ({
      ...previous,
      [key]: value
    }))
  }

  // Select or remove participants
  const toggleParticipant = (id, checked) => {
    setForm(previous => ({
      ...previous,
      participant_ids: checked
        ? [...previous.participant_ids, id]
        : previous.participant_ids.filter(item => item !== id)
    }))
  }

  // Create meeting
  const create = async () => {
    if (!canCreate) {
      toast('You do not have permission to create meetings', 'error')
      return
    }

    if (!form.title.trim() || !form.date) {
      toast('Meeting title and date are required', 'error')
      return
    }

    const starts = new Date(`${form.date}T${form.start}:00`)
    const ends = new Date(`${form.date}T${form.end}:00`)

    if (Number.isNaN(starts.getTime()) || Number.isNaN(ends.getTime())) {
      toast('Please enter a valid meeting date and time', 'error')
      return
    }

    if (ends <= starts) {
      toast('End time must be after start time', 'error')
      return
    }

    setSaving(true)

    try {
      const { data } = await api.createMeeting({
        title: form.title.trim(),
        agenda: form.agenda.trim(),
        starts_at: starts.toISOString(),
        ends_at: ends.toISOString(),
        participant_ids: form.participant_ids,
        create_meet_link: form.create_meet_link
      })

      setShowNew(false)

      setForm({
        title: '',
        agenda: '',
        date: '',
        start: '10:00',
        end: '11:00',
        participant_ids: [],
        create_meet_link: true
      })

      await load()

      if (data.meet_url) {
        toast('Meeting created successfully. Google Meet link generated.')
      } else {
        toast(
          data.error_message ||
          'Meeting saved, but Google Meet link was not generated.',
          'error'
        )
      }
    } catch (error) {
      toast(
        error?.response?.data?.detail || 'Could not create meeting',
        'error'
      )
    } finally {
      setSaving(false)
    }
  }

  // Join meeting
  const join = async (id) => {
    try {
      const { data } = await api.joinMeeting(id)

      if (data.meet_url) {
        window.open(data.meet_url, '_blank', 'noopener,noreferrer')
      } else {
        toast('Google Meet link is not available', 'error')
      }
    } catch (error) {
      toast(
        error?.response?.data?.detail || 'You cannot join this meeting',
        'error'
      )
    }
  }

  // View meeting details
  const open = async (meeting) => {
    try {
      const { data } = await api.meeting(meeting.id)
      setDetail(data)
    } catch (error) {
      toast(
        error?.response?.data?.detail || 'Cannot open meeting',
        'error'
      )
    }
  }

  // Connect Google account
  const linkGoogle = async () => {
    try {
      const { data } = await api.googleAuthUrl()
      window.location.href = data.url
    } catch (error) {
      toast(
        error?.response?.data?.detail || 'Google connection failed',
        'error'
      )
    }
  }

  // Cancel meeting
  const cancel = async (id) => {
    const confirmed = window.confirm(
      'Are you sure you want to cancel this meeting?'
    )

    if (!confirmed) return

    try {
      await api.cancelMeeting(id)

      setDetail(null)
      await load()

      toast('Meeting cancelled successfully')
    } catch (error) {
      toast(
        error?.response?.data?.detail || 'Could not cancel meeting',
        'error'
      )
    }
  }

  return (
    <>
      {/* Page Header */}
      <div className="page-head">
        <div>
          <div className="eyebrow">Collaboration</div>
          <h1>Meetings</h1>

          <p className="muted small" style={{ marginTop: 4 }}>
            Schedule meetings, invite participants and join through Google Meet.
          </p>
        </div>

        <div className="row">
          {canCreate && !google.linked && google.configured && (
            <button className="btn" onClick={linkGoogle}>
              Connect Google
            </button>
          )}

          {canCreate && (
            <button
              className="btn primary"
              onClick={() => setShowNew(true)}
            >
              + Schedule Meeting
            </button>
          )}
        </div>
      </div>

      {/* Google Configuration */}
      {!google.configured && canCreate && (
        <Card style={{ marginBottom: 16 }}>
          <span className="pill amber">
            Google is not configured
          </span>

          <p className="small muted" style={{ marginTop: 8 }}>
            Configure Google OAuth in backend/.env to generate Google Meet links.
          </p>
        </Card>
      )}

      {canCreate && google.configured && !google.linked && (
        <Card style={{ marginBottom: 16 }}>
          <p className="small muted">
            Connect your Google account before scheduling meetings with Google Meet links.
          </p>
          <button className="btn" onClick={linkGoogle}>
            Connect Google Account
          </button>
        </Card>
      )}

      {/* Meeting List */}
      {loading ? (
        <Spinner />
      ) : (
        <div className="grid c2">
          {meetings.map(meeting => (
            <Card key={meeting.id} className="meet-card">
              <div className="between">
                <h3>{meeting.title}</h3>
                <Status value={meeting.status} />
              </div>

              <div
                className="small muted"
                style={{ margin: '6px 0 10px' }}
              >
                {fmt.dateTime(meeting.starts_at)}
                {' → '}
                {fmt.time(meeting.ends_at)}
                {' · '}
                {meeting.provider}
              </div>

              <div className="row wrap">
                <button
                  className="btn sm"
                  onClick={() => open(meeting)}
                >
                  Details
                </button>

                {meeting.meet_url &&
                  meeting.status !== 'cancelled' && (
                    <button
                      className="btn sm primary"
                      onClick={() => join(meeting.id)}
                    >
                      Join Meeting
                    </button>
                  )}

                {!meeting.meet_url && meeting.error_message && (
                  <span
                    className="small"
                    style={{ color: 'var(--warn)' }}
                  >
                    {meeting.error_message}
                  </span>
                )}
              </div>
            </Card>
          ))}

          {!meetings.length && (
            <Card>
              <Empty>No meetings scheduled.</Empty>
            </Card>
          )}
        </div>
      )}

      {/* Create Meeting Modal */}
      {showNew && canCreate && (
        <Modal
          title="Schedule Meeting"
          onClose={() => setShowNew(false)}
        >
          <div className="field">
            <label>Meeting Title</label>
            <input
              value={form.title}
              onChange={e => updateForm('title', e.target.value)}
              placeholder="Enter meeting title"
            />
          </div>

          <div className="field">
            <label>Agenda</label>
            <textarea
              value={form.agenda}
              onChange={e => updateForm('agenda', e.target.value)}
              placeholder="Enter meeting agenda"
            />
          </div>

          <div className="grid c3">
            <div className="field">
              <label>Date</label>
              <input
                type="date"
                value={form.date}
                onChange={e => updateForm('date', e.target.value)}
              />
            </div>

            <div className="field">
              <label>Start Time</label>
              <input
                type="time"
                value={form.start}
                onChange={e => updateForm('start', e.target.value)}
              />
            </div>

            <div className="field">
              <label>End Time</label>
              <input
                type="time"
                value={form.end}
                onChange={e => updateForm('end', e.target.value)}
              />
            </div>
          </div>

          {/* Participant Selection */}
          <div className="field">
            <label>
              Select Participants ({form.participant_ids.length} selected)
            </label>

            <div
              className="stack"
              style={{
                maxHeight: 220,
                overflowY: 'auto',
                padding: 10,
                border: '1px solid var(--border)',
                borderRadius: 8
              }}
            >
              {people.map(person => (
                <label
                  key={person.id}
                  className="row small"
                  style={{ cursor: 'pointer' }}
                >
                  <input
                    type="checkbox"
                    style={{ width: 16 }}
                    checked={form.participant_ids.includes(person.id)}
                    onChange={e =>
                      toggleParticipant(person.id, e.target.checked)
                    }
                  />

                  <span>{person.full_name}</span>

                  <span className="muted">
                    ({person.role})
                  </span>
                </label>
              ))}

              {!people.length && (
                <span className="small muted">
                  No participants available.
                </span>
              )}
            </div>
          </div>

          {/* Google Meet Option */}
          <label className="row small" style={{ marginBottom: 14 }}>
            <input
              type="checkbox"
              style={{ width: 16 }}
              checked={form.create_meet_link}
              onChange={e =>
                updateForm('create_meet_link', e.target.checked)
              }
            />

            Generate Google Meet Link
          </label>

          <button
            className="btn primary"
            onClick={create}
            disabled={saving}
          >
            {saving ? 'Creating...' : 'Create Meeting'}
          </button>
        </Modal>
      )}

      {/* Meeting Details */}
      {detail && (
        <Modal
          title={detail.title}
          onClose={() => setDetail(null)}
          wide
        >
          <div className="row wrap" style={{ marginBottom: 12 }}>
            <Status value={detail.status} />

            <span className="small muted">
              {fmt.dateTime(detail.starts_at)}
              {' → '}
              {fmt.time(detail.ends_at)}
            </span>

            {detail.meet_url && (
              <span className="pill green">
                Meet Link Ready
              </span>
            )}
          </div>

          <p className="small muted" style={{ marginBottom: 14 }}>
            {detail.agenda || 'No agenda.'}
          </p>

          <h3 style={{ marginBottom: 8 }}>
            Participants
          </h3>

          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Role</th>
                <th>Response</th>
              </tr>
            </thead>

            <tbody>
              {(detail.participants || []).map(person => (
                <tr key={person.user_id}>
                  <td>{person.full_name}</td>

                  <td className="small muted">
                    {person.role}
                  </td>

                  <td>
                    <span className="pill grey">
                      {person.response || 'pending'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="row" style={{ marginTop: 16 }}>
            {detail.can_join &&
              detail.meet_url &&
              detail.status !== 'cancelled' && (
                <button
                  className="btn primary"
                  onClick={() => join(detail.id)}
                >
                  Join Meeting
                </button>
              )}

            {detail.can_manage &&
              detail.status !== 'cancelled' && (
                <button
                  className="btn danger"
                  onClick={() => cancel(detail.id)}
                >
                  Cancel Meeting
                </button>
              )}
          </div>
        </Modal>
      )}
    </>
  )
}
