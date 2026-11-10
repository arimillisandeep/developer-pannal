import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api, tokens } from '../api'
import { useAuth } from '../context/AuthContext'
import { Avatar, Modal, Spinner, useToast } from '../components/ui'

const ROLE_LABEL = { admin: 'Admin', developer_onroll: 'On-Roll', developer_offroll: 'Off-Roll', intern: 'Intern' }
const clock = (d) => (d ? new Date(d).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '')

export default function Chat() {
  const { user } = useAuth()
  const toast = useToast()
  const [rooms, setRooms] = useState([])
  const [active, setActive] = useState(null)
  const [messages, setMessages] = useState([])
  const [body, setBody] = useState('')
  const [loading, setLoading] = useState(true)
  const [showNew, setShowNew] = useState(false)
  const [contacts, setContacts] = useState([])
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState([])
  const [groupName, setGroupName] = useState('')
  const wsRef = useRef(null)
  const listRef = useRef(null)

  const loadRooms = useCallback(async () => {
    try {
      const { data } = await api.rooms()
      setRooms(data)
      setActive((cur) => (cur ? data.find((r) => r.id === cur.id) || cur : data[0] || null))
    } catch { /* ignore */ } finally { setLoading(false) }
  }, [])

  useEffect(() => { loadRooms() }, [loadRooms])

  useEffect(() => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(tokens.access)}`)
    wsRef.current = ws
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data)
        if (msg.channel !== 'message') return
        if (msg.data?.room_id === active?.id) {
          setMessages((m) => (m.some((x) => x.id === msg.data.id) ? m : [...m, msg.data]))
        }
        loadRooms()
      } catch { /* ignore */ }
    }
    return () => ws.close()
  }, [active?.id, loadRooms])

  useEffect(() => {
    if (!active) { setMessages([]); return }
    api.messages(active.id).then(({ data }) => {
      setMessages(data)
      api.markRead(active.id).then(loadRooms).catch(() => {})
    }).catch(() => setMessages([]))
  }, [active?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight
  }, [messages.length, active?.id])

  const send = async () => {
    const text = body.trim()
    if (!text || !active) return
    setBody('')
    try {
      const { data } = await api.send(active.id, text)
      setMessages((m) => (m.some((x) => x.id === data.id) ? m : [...m, data]))
      loadRooms()
    } catch (e) { toast(e?.response?.data?.detail || 'Message could not be sent', 'error'); setBody(text) }
  }

  const openNew = async () => {
    setShowNew(true); setSelected([]); setGroupName(''); setSearch('')
    try { const { data } = await api.contacts(); setContacts(data) } catch { setContacts([]) }
  }

  const createRoom = async () => {
    if (!selected.length) return
    try {
      const { data } = await api.createRoom({
        type: selected.length > 1 ? 'group' : 'direct',
        name: selected.length > 1 ? groupName : '',
        participant_ids: selected
      })
      setShowNew(false)
      await loadRooms()
      setActive(data)
    } catch (e) { toast(e?.response?.data?.detail || 'Could not start the conversation', 'error') }
  }

  const shown = rooms.filter((r) => {
    const t = search.trim().toLowerCase()
    if (!t) return true
    return (r.name || '').toLowerCase().includes(t) || (r.last_message || '').toLowerCase().includes(t)
  })
  const title = active?.type === 'direct'
    ? (active.participant_ids || []).length ? active.name : active.name
    : active?.name

  return (
    <div className="wchat-pane" style={{ display: 'grid', gridTemplateColumns: '290px 1fr', gap: 16, height: 'calc(100vh - 120px)', minHeight: 480 }}>
      <style>{`
        .wchat-pane .wchat-bubble{max-width:min(560px,72%);min-width:88px;padding:8px 12px;border-radius:12px;font-size:13.5px;line-height:1.45;word-break:break-word}
        .wchat-pane .wchat-bubble.in{background:#e8eef8;color:#0b1220;border:1px solid #cfdaea}
        .wchat-pane .wchat-bubble.out{background:#4338ca;color:#ffffff;border:1px solid #4338ca}
        .wchat-pane .wchat-bubble.in a{color:#1d4ed8}
        .wchat-pane .wchat-bubble.out a{color:#ffffff}
        .wchat-pane .wchat-time{font-size:10.5px;margin-top:4px;text-align:right;white-space:nowrap;letter-spacing:.02em}
        .wchat-pane .wchat-bubble.in .wchat-time{color:#475569}
        .wchat-pane .wchat-bubble.out .wchat-time{color:rgba(255,255,255,.88)}
        .wchat-pane .wchat-name{font-size:11.5px;font-weight:650;color:#a9c1e6;margin-bottom:3px}
        .wchat-pane .wchat-empty{text-align:center;font-size:13px;color:#93a3bb;margin-top:30px}
        .wchat-pane .wchat-meta{font-size:11.5px;color:#9fb0c7}
        .wchat-pane .wchat-pick{display:grid;grid-template-columns:16px 28px minmax(0,1fr);gap:10px;align-items:center;padding:8px 10px;border-radius:8px;cursor:pointer}
        .wchat-pane .wchat-pick:hover{background:rgba(79,140,255,.10)}
        .wchat-pane .wchat-pick input{margin:0;justify-self:center}
        .wchat-pane .wchat-pick .pick-name{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      `}</style>

      <div className="card" style={{ display: 'flex', flexDirection: 'column', padding: 0, overflow: 'hidden' }}>
        <div style={{ display: 'flex', gap: 6, padding: 10, borderBottom: '1px solid var(--line,#e5eaf2)' }}>
          <input className="input" placeholder="Search" value={search} onChange={(e) => setSearch(e.target.value)} style={{ flex: 1 }} />
          <button className="btn primary" onClick={openNew} title="New conversation">＋</button>
        </div>
        <div style={{ overflow: 'auto', flex: 1 }}>
          {loading && <div style={{ padding: 14 }}><Spinner /></div>}
          {!loading && shown.map((r) => (
            <div key={r.id} onClick={() => setActive(r)}
              style={{
                display: 'flex', gap: 10, padding: '10px 12px', cursor: 'pointer', alignItems: 'center',
                borderBottom: '1px solid var(--line,#f1f4f9)',
                background: active?.id === r.id ? 'rgba(79,140,255,.10)' : 'transparent'
              }}>
              <Avatar name={r.name} size={34} />
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: 13.5, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {r.name} {r.type !== 'direct' && <span style={{ fontSize: 10.5, opacity: 0.6 }}>· group</span>}
                </div>
                <div style={{ fontSize: 12, opacity: 0.6, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {r.last_message || 'No messages yet'}
                </div>
              </div>
              {r.unread > 0 && (
                <span style={{ background: '#ef4444', color: '#fff', borderRadius: 10, fontSize: 10.5, padding: '1px 6px', fontWeight: 700 }}>{r.unread}</span>
              )}
            </div>
          ))}
          {!loading && !shown.length && (
            <div style={{ padding: 16, fontSize: 13, opacity: 0.6 }}>No conversations yet. Press ＋ to start one.</div>
          )}
        </div>
      </div>

      <div className="card" style={{ display: 'flex', flexDirection: 'column', padding: 0, overflow: 'hidden' }}>
        {!active ? (
          <div style={{ margin: 'auto', fontSize: 13.5, opacity: 0.6 }}>Select a conversation to start chatting</div>
        ) : (
          <>
            <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--line,#e5eaf2)', display: 'flex', alignItems: 'center', gap: 10 }}>
              <Avatar name={title} size={32} />
              <div>
                <div style={{ fontWeight: 650, fontSize: 14 }}>{title}</div>
                <div style={{ fontSize: 11.5, opacity: 0.6 }}>
                  {active.type === 'direct' ? 'Direct message' : `${active.participant_count} members`}
                  {' · '}{ROLE_LABEL[user?.role] || user?.role}
                </div>
              </div>
            </div>

            <div ref={listRef} style={{ flex: 1, overflow: 'auto', padding: 16, background: 'rgba(79,140,255,.02)' }}>
              {messages.map((m) => {
                const mine = m.sender_id === user?.id
                const name = m.sender?.full_name || m.sender_name || 'Unknown'
                return (
                  <div key={m.id} style={{ display: 'flex', justifyContent: mine ? 'flex-end' : 'flex-start', marginBottom: 10 }}>
                    <div style={{ maxWidth: '72%' }}>
                      {!mine && <div className="wchat-name">{name}</div>}
                      <div className={`wchat-bubble ${mine ? 'out' : 'in'}`}>
                        {m.body}
                        {(m.attachments || []).map((a) => (
                          <div key={a.id} style={{ marginTop: 4 }}>
                            <a href={a.url} target="_blank" rel="noreferrer" style={{ textDecoration: 'underline' }}>📎 {a.file_name}</a>
                          </div>
                        ))}
                        <div className="wchat-time">{clock(m.created_at)}</div>
                      </div>
                    </div>
                  </div>
                )
              })}
              {!messages.length && (
                <div className="wchat-empty">No messages yet.</div>
              )}
            </div>

            <div style={{ padding: 10, borderTop: '1px solid var(--line,#e5eaf2)', display: 'flex', gap: 8 }}>
              <input className="input" value={body} placeholder="Type a message…"
                onChange={(e) => setBody(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); send() } }}
                style={{ flex: 1 }} />
              <button className="btn primary" onClick={send} disabled={!body.trim()}>Send</button>
            </div>
          </>
        )}
      </div>

      {showNew && <Modal open onClose={() => setShowNew(false)} title="New conversation">
        <div style={{ display: 'grid', gap: 12, minWidth: 400, paddingBottom: 4 }}>
          {selected.length > 1 && (
            <input className="input" placeholder="Group name" value={groupName} onChange={(e) => setGroupName(e.target.value)} />
          )}
          <div style={{ maxHeight: 300, overflowY: 'auto', overflowX: 'hidden', border: '1px solid rgba(148,163,184,.28)', borderRadius: 10, padding: '6px 8px 10px' }}>
            {contacts.map((c) => (
              <label key={c.id} className="wchat-pick">
                <input type="checkbox" checked={selected.includes(c.id)}
                  onChange={(e) => setSelected(e.target.checked ? [...selected, c.id] : selected.filter((x) => x !== c.id))} />
                <Avatar name={c.full_name} color={c.avatar_color} size={26} />
                <span className="pick-name">
                  {c.full_name}
                  <span style={{ fontSize: 11, opacity: 0.6 }}> · {ROLE_LABEL[c.role] || c.role}</span>
                </span>
              </label>
            ))}
            {!contacts.length && <div style={{ padding: 10, fontSize: 12.5, opacity: 0.6 }}>No employees found.</div>}
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: 12, opacity: 0.6 }}>{selected.length} selected</span>
            <button className="btn primary" onClick={createRoom} disabled={!selected.length}>
              {selected.length > 1 ? 'Create group' : 'Start chat'}
            </button>
          </div>
        </div>
      </Modal>}
    </div>
  )
}
