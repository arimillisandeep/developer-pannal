import { tokens } from '../api'

const BASE = '/api'

async function req(path, { method = 'GET', body, form } = {}) {
  const headers = {}
  if (tokens.access) headers.Authorization = `Bearer ${tokens.access}`
  let payload
  if (form) payload = form
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body) }
  const res = await fetch(BASE + path, { method, headers, body: payload })
  if (!res.ok) {
    let detail = `${res.status}`
    try { detail = (await res.json()).detail || detail } catch { /* ignore */ }
    const err = new Error(detail); err.status = res.status; throw err
  }
  return res.json()
}

const qs = (params = {}) => {
  const s = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => { if (v !== '' && v !== undefined && v !== null) s.set(k, v) })
  const t = s.toString()
  return t ? `?${t}` : ''
}

export const chatApi = {
  presence: () => req('/chat/presence'),
  contacts: (q = '') => req('/chat/contacts' + qs({ q })),
  rooms: () => req('/chat/rooms'),
  room: (id) => req(`/chat/rooms/${id}`),
  createRoom: (body) => req('/chat/rooms', { method: 'POST', body }),
  updateRoom: (id, body) => req(`/chat/rooms/${id}`, { method: 'PATCH', body }),
  addMembers: (id, user_ids) => req(`/chat/rooms/${id}/members`, { method: 'POST', body: { user_ids } }),
  removeMember: (id, uid) => req(`/chat/rooms/${id}/members/${uid}`, { method: 'DELETE' }),
  messages: (id, params) => req(`/chat/rooms/${id}/messages${qs(params)}`),
  send: (id, body, reply_to_id = null) => req(`/chat/rooms/${id}/messages`, { method: 'POST', body: { body, reply_to_id } }),
  read: (id) => req(`/chat/rooms/${id}/read`, { method: 'POST', body: {} }),
  deleteMessage: (id) => req(`/chat/messages/${id}`, { method: 'DELETE' }),
  upload: (id, file) => { const f = new FormData(); f.append('file', file); return req(`/chat/rooms/${id}/attachments`, { method: 'POST', form: f }) },
  unread: () => req('/chat/unread')
}
