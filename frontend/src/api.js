import axios from 'axios'

export const API = '/api'

const http = axios.create({ baseURL: API, timeout: 30000 })

export const tokens = {
  get access() { return localStorage.getItem('wc_access') },
  get refresh() { return localStorage.getItem('wc_refresh') },
  set(access, refresh) {
    if (access) localStorage.setItem('wc_access', access)
    if (refresh) localStorage.setItem('wc_refresh', refresh)
  },
  clear() {
    localStorage.removeItem('wc_access')
    localStorage.removeItem('wc_refresh')
    localStorage.removeItem('wc_user')
  }
}

http.interceptors.request.use((cfg) => {
  const t = tokens.access
  if (t) cfg.headers.Authorization = `Bearer ${t}`
  return cfg
})

let refreshing = null

http.interceptors.response.use(
  (r) => r,
  async (error) => {
    const original = error.config
    if (error.response?.status === 401 && !original._retry && tokens.refresh) {
      original._retry = true
      try {
        refreshing = refreshing || axios.post(`${API}/auth/refresh`, { refresh_token: tokens.refresh })
        const { data } = await refreshing
        refreshing = null
        tokens.set(data.access_token, data.refresh_token)
        original.headers.Authorization = `Bearer ${data.access_token}`
        return http(original)
      } catch (e) {
        refreshing = null
        tokens.clear()
        if (!location.pathname.startsWith('/login')) location.href = '/login'
      }
    }
    return Promise.reject(error)
  }
)

export const api = {
  // auth
  login: (identifier, password) => http.post('/auth/login', { identifier, password }),
  me: () => http.get('/auth/me'),
  logout: () => http.post('/auth/logout', { refresh_token: tokens.refresh }),
  forgot: (email) => http.post('/auth/forgot-password', { email }),
  reset: (token, new_password) => http.post('/auth/reset-password', { token, new_password }),
  changePassword: (current_password, new_password) =>
    http.post('/auth/change-password', { current_password, new_password }),

  // profile / people
  myProfile: () => http.get('/users/me/profile'),
  updateMe: (body) => http.patch('/users/me/profile', body),
  users: (params) => http.get('/users', { params }),
  createUser: (body) => http.post('/users', body),
  updateUser: (id, body) => http.patch(`/users/${id}`, body),
  setStatus: (id, is_active) => http.post(`/users/${id}/status?is_active=${is_active}`),
  convert: (id, body) => http.post(`/users/${id}/convert`, body),
  userHistory: (id) => http.get(`/users/${id}/history`),
  teams: () => http.get('/teams'),
  createTeam: (body) => http.post('/teams', body),
  teamMembers: (id) => http.get(`/teams/${id}/members`),

  // admin
  dashboard: () => http.get('/admin/dashboard'),
  activity: (id, days = 14) => http.get(`/admin/users/${id}/activity`, { params: { days } }),
  progress: () => http.get('/admin/progress'),
  audit: (params) => http.get('/admin/audit-logs', { params }),
  admins: () => http.get('/admin/admins'),
  settings: () => http.get('/admin/settings'),

  // tasks
  tasks: (params) => http.get('/tasks', { params }),
  task: (id) => http.get(`/tasks/${id}`),
  createTask: (body) => http.post('/tasks', body),
  updateTask: (id, body) => http.patch(`/tasks/${id}`, body),
  comments: (id) => http.get(`/tasks/${id}/comments`),
  comment: (id, body) => http.post(`/tasks/${id}/comments`, { body }),

  // projects + workspace
  projects: () => http.get('/projects'),
  createProject: (body) => http.post('/projects', body),
  projectFiles: (id) => http.get(`/projects/${id}/files`),
  writeFile: (id, body) => http.put(`/projects/${id}/files`, body),
  deleteFile: (id, path) => http.delete(`/projects/${id}/files`, { params: { path } }),
  saveProject: (id, files, label = '') => http.post(`/projects/${id}/save`, { files, label }),
  versions: (id) => http.get(`/projects/${id}/versions`),
  restore: (id, v) => http.post(`/projects/${id}/versions/${v}/restore`),
  download: (id) => http.get(`/projects/${id}/download`, { responseType: 'blob' }),
  languages: () => http.get('/workspace/languages'),
  run: (body) => http.post('/workspace/run', body),
  terminal: (body) => http.post('/workspace/terminal', body),
  preview: (id) => http.get(`/workspace/preview/${id}`),

  // submissions
  submit: (body) => http.post('/submissions', body),
  submissions: (params) => http.get('/submissions', { params }),
  submission: (id) => http.get(`/submissions/${id}`),
  review: (id, body) => http.post(`/submissions/${id}/review`, body),
  forward: (id) => http.post(`/submissions/${id}/forward`),
  finalDecision: (id, body) => http.post(`/submissions/${id}/final`, body),

  // timer
  timerCurrent: () => http.get('/timer/current'),
  timerStart: () => http.post('/timer/start'),
  timerPause: () => http.post('/timer/pause', {}),
  timerResume: () => http.post('/timer/resume'),
  timerAway: (body) => http.post('/timer/away', body),
  timerHeartbeat: (body) => http.post('/timer/heartbeat', body),
  monitoringLive: (params) => http.get('/monitoring/live', { params }),
  monitoringEmployee: (id) => http.get(`/monitoring/employees/${id}`),
  monitoringCsv: (params) => http.get('/monitoring/report.csv', { params, responseType: 'blob' }),
  timerStop: () => http.post('/timer/stop'),
  heartbeat: (event, meta = {}) => http.post('/timer/heartbeat', { event, meta }),
  attendance: () => http.get('/timer/attendance'),
  timerSessions: () => http.get('/timer/sessions'),

  // recordings
  recStart: (body) => http.post('/recordings/start', body),
  recChunk: (id, seq, blob) => {
    const fd = new FormData()
    fd.append('seq', seq)
    fd.append('chunk', blob, `chunk-${seq}.webm`)
    return http.post(`/recordings/${id}/chunks`, fd)
  },
  recComplete: (id, seconds) => http.post(`/recordings/${id}/complete?duration_seconds=${seconds}`),
  recordings: (params) => http.get('/recordings', { params }),
  recording: (id) => http.get(`/recordings/${id}`),
  recordingPlayback: (id) => http.get(`/recordings/${id}/playback`),
  recDelete: (id) => http.delete(`/recordings/${id}`),
  recordingUrl: (id) => `${API}/recordings/${id}/video`,

  // chat
  rooms: () => http.get('/chat/rooms'),
  createRoom: (body) => http.post('/chat/rooms', body),
  messages: (id, params) => http.get(`/chat/rooms/${id}/messages`, { params }),
  send: (id, body) => http.post(`/chat/rooms/${id}/messages`, { body }),
  markRead: (id) => http.post(`/chat/rooms/${id}/read`),
  uploadChat: (id, file) => {
    const fd = new FormData()
    fd.append('file', file)
    return http.post(`/chat/rooms/${id}/attachments`, fd)
  },
  contacts: () => http.get('/chat/contacts'),

  // notifications
  notifications: (params) => http.get('/notifications', { params }),
  unread: () => http.get('/notifications/unread-count'),
  readNotif: (id) => http.post(`/notifications/${id}/read`),
  readAll: () => http.post('/notifications/read-all'),

  
  // meetings
  meetings: (params) => http.get('/meetings', { params }),
  meeting: (id) => http.get(`/meetings/${id}`),
  meetingRecipients: () => http.get('/meetings/recipients'),
  createMeeting: (body) => http.post('/meetings', body),
  cancelMeeting: (id) => http.post(`/meetings/${id}/cancel`),
  joinMeeting: (id) => http.post(`/meetings/${id}/join`),
  respondMeeting: (id, r) =>
    http.post(`/meetings/${id}/respond?response=${r}`),
  googleStatus: () => http.get('/meetings/google/status'),
  googleAuthUrl: () => http.get('/meetings/google/auth-url'),
  googleCallback: (params) => http.get('/meetings/google/callback', { params }),


  // reports
  report: (params) => http.get('/reports/summary', { params }),
  myReport: (params) => http.get('/reports/me', { params }),
  teamReport: (params) => http.get('/reports/team', { params }),
  exportMyReport: (format, params) => http.get('/reports/me/export', { params: { ...params, format }, responseType: 'blob' }),
  exportTeamReport: (employeeId, format, params) => http.get(`/reports/team/${employeeId}/export`, { params: { ...params, format }, responseType: 'blob' }),
  exportReport: (kind, params) => http.get(`/reports/export/${kind}`, { params, responseType: 'blob' })
}

export default http
