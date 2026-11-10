import http from '../api'

/* ---------------------------------------------------------------- workspace */
export const ide = {
  tree: (id, params) => http.get(`/workspace/${id}/tree`, { params }),
  read: (id, path) => http.get(`/workspace/${id}/file`, { params: { path } }),
  create: (id, body) => http.post(`/workspace/${id}/file`, body),
  move: (id, body) => http.patch(`/workspace/${id}/file`, body),
  remove: (id, path) => http.delete(`/workspace/${id}/file`, { params: { path } }),
  upload: (id, file, dir = '') => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('dir', dir)
    return http.post(`/workspace/${id}/upload`, fd)
  },
  downloadFile: (id, path) => http.get(`/workspace/${id}/download`, { params: { path }, responseType: 'blob' }),
  downloadZip: (id) => http.get(`/workspace/${id}/download`, { responseType: 'blob' }),
  search: (id, q) => http.get(`/workspace/${id}/search`, { params: { q } }),
  sync: (id) => http.post(`/workspace/${id}/sync`),
  run: (id, body) => http.post('/workspace/run', { project_id: id, ...body }),
  build: (id) => http.post(`/workspace/${id}/build`),

  // terminals
  newTerminal: (id) => http.post(`/workspace/${id}/terminals`),
  terminals: (id) => http.get(`/workspace/${id}/terminals`),
  killTerminal: (sid) => http.delete(`/workspace/terminals/${sid}`),

  // dev servers
  servers: (id) => http.get(`/workspace/${id}/servers`),
  startServer: (id, body) => http.post(`/workspace/${id}/servers`, body),
  runDev: (id) => http.post(`/workspace/${id}/dev`),
  stopServer: (id, sid) => http.delete(`/workspace/${id}/servers/${sid}`),

  // git
  gitStatus: (id) => http.get(`/workspace/${id}/git/status`),
  gitLog: (id) => http.get(`/workspace/${id}/git/log`),
  gitDiff: (id, params) => http.get(`/workspace/${id}/git/diff`, { params }),
  gitBranches: (id) => http.get(`/workspace/${id}/git/branches`),
  git: (id, body) => http.post(`/workspace/${id}/git`, body),

  // preview urls (token in query so an iframe can authenticate)
  previewUrl: (id, path = '') => {
    const token = localStorage.getItem('wc_access') || ''
    return `/api/workspace/preview/${id}/${path}?token=${encodeURIComponent(token)}`
  },

  // live dev server (Vite / Express) through the same-origin proxy
  devProxyUrl: (id, serverId, path = '') => {
    const token = localStorage.getItem('wc_access') || ''
    return `/api/workspace/${id}/devproxy/${serverId}/${encodeURIComponent(token)}/${path}`
  }
}

export const LANG_BY_EXT = {
  js: 'javascript', jsx: 'javascript', ts: 'typescript', tsx: 'typescript',
  html: 'html', htm: 'html', css: 'css', scss: 'scss', json: 'json', md: 'markdown',
  py: 'python', java: 'java', c: 'c', h: 'c', cpp: 'cpp', cc: 'cpp', hpp: 'cpp',
  cs: 'csharp', php: 'php', go: 'go', rb: 'ruby', rs: 'rust', sql: 'sql',
  sh: 'shell', bash: 'shell', ps1: 'powershell', vue: 'html', xml: 'xml',
  yaml: 'yaml', yml: 'yaml', txt: 'plaintext', env: 'ini', toml: 'ini'
}

export const langFor = (path = '') => LANG_BY_EXT[path.split('.').pop().toLowerCase()] || 'plaintext'
