import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Editor from '@monaco-editor/react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import { ide, langFor } from '../services/ide'
import { useAuth } from '../context/AuthContext'
import { useWorkspaceTask } from '../context/WorkspaceTaskContext'
import { useToast } from '../components/ui'
import './workspace/ide.css'

const FILE_ICON = (name) => {
  const ext = (name.split('.').pop() || '').toLowerCase()
  if (['js', 'jsx', 'mjs'].includes(ext)) return { i: 'JS', c: '#f0db4f' }
  if (['ts', 'tsx'].includes(ext)) return { i: 'TS', c: '#4f8cff' }
  if (ext === 'html') return { i: '<>', c: '#e34c26' }
  if (['css', 'scss'].includes(ext)) return { i: '#', c: '#42a5f5' }
  if (ext === 'json') return { i: '{}', c: '#cbcb41' }
  if (ext === 'py') return { i: 'py', c: '#4b8bbe' }
  if (['c', 'h'].includes(ext)) return { i: 'C', c: '#5c6bc0' }
  if (['cpp', 'hpp', 'cc'].includes(ext)) return { i: '++', c: '#004482' }
  if (ext === 'java') return { i: 'J', c: '#e76f00' }
  if (ext === 'md') return { i: 'M', c: '#9aa5b1' }
  if (ext === 'sql') return { i: 'DB', c: '#f29111' }
  if (['sh', 'bash'].includes(ext)) return { i: '$_', c: '#89e051' }
  if (ext === 'php') return { i: 'php', c: '#777bb3' }
  if (ext === 'rb') return { i: 'rb', c: '#cc342d' }
  return { i: '·', c: '#8b9ab3' }
}

function buildTree(files) {
  const root = { children: {}, path: '', name: '', is_dir: true }
  for (const f of files) {
    const parts = f.path.split('/')
    let node = root
    parts.forEach((part, i) => {
      const leaf = i === parts.length - 1
      node.children = node.children || {}
      if (!node.children[part]) {
        node.children[part] = {
          name: part, path: parts.slice(0, i + 1).join('/'),
          is_dir: leaf ? !!f.is_dir : true, children: {},
        }
      }
      if (leaf && f.is_dir) node.children[part].is_dir = true
      node = node.children[part]
    })
  }
  return root
}

/* ------------------------------------------------------------------ terminal */
function TerminalView({ sessionId, active, token, onExit, onSelection, pasteRequest, clearRequest }) {
  const host = useRef(null)
  const termRef = useRef(null)
  const fitRef = useRef(null)
  const wsRef = useRef(null)
  const handledPasteRef = useRef(null)
  const handledClearRef = useRef(null)
  const [status, setStatus] = useState('connecting')

  useEffect(() => {
    if (!host.current) return
    const xterm = new Terminal({
      fontSize: 13,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
      cursorBlink: true,
      convertEol: false,
      scrollback: 6000,
      allowProposedApi: true,
      theme: {
        background: '#05070b', foreground: '#cfd8e6', cursor: '#4f8cff',
        selectionBackground: '#26406b', black: '#0b0e14', red: '#ff6b6b',
        green: '#7ee787', yellow: '#e3b341', blue: '#79c0ff', magenta: '#d2a8ff',
        cyan: '#56d4dd', white: '#cfd8e6',
      },
    })
    const fit = new FitAddon()
    xterm.loadAddon(fit)
    xterm.open(host.current)
    try { fit.fit() } catch { /* not laid out yet */ }
    termRef.current = xterm
    fitRef.current = fit

    let disposed = false
    let sock = null
    let pending = ''
    let retryTimer = null
    let retryDelay = 500

    const open = () => {
      if (disposed) return
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      sock = new WebSocket(`${proto}://${location.host}/ws/terminal/${encodeURIComponent(sessionId)}?token=${encodeURIComponent(token)}`)
      wsRef.current = sock
      sock.onopen = () => {
        retryDelay = 500
        setStatus('open')
        try { sock.send(JSON.stringify({ type: 'resize', cols: xterm.cols, rows: xterm.rows })) } catch { /* noop */ }
        if (pending) { sock.send(JSON.stringify({ type: 'input', data: pending })); pending = '' }
        xterm.focus()
      }
      sock.onmessage = (ev) => {
        let msg
        try { msg = JSON.parse(ev.data) } catch { return }
        if (msg.type === 'output') {
          if (msg.replay) xterm.clear()
          xterm.write(msg.data)
        }
        else if (msg.type === 'exit') {
          xterm.write('\r\n\x1b[90m[process exited]\x1b[0m\r\n'); setStatus('closed'); onExit?.(sessionId)
        }
      }
      sock.onerror = () => setStatus('retrying')
      sock.onclose = (ev) => {
        if (disposed) return
        if (ev.code === 4401 || ev.code === 4403) {
          setStatus('denied')
          xterm.write('\r\n\x1b[31msession unavailable — open a new terminal\x1b[0m\r\n')
          return
        }
        setStatus('retrying')
        retryTimer = setTimeout(() => {
          retryDelay = Math.min(retryDelay * 2, 5000)
          xterm.clear()
          open()
        }, retryDelay)
      }
    }
    open()

    // Every keystroke, Enter ("\r"), Backspace ("\x7f"), Ctrl+C ("\x03") and paste
    // arrive here as raw PTY input and are forwarded verbatim to the shell.
    const sub = xterm.onData((data) => {
      if (sock && sock.readyState === WebSocket.OPEN) sock.send(JSON.stringify({ type: 'input', data }))
      else pending += data
    })
    const selectionSub = xterm.onSelectionChange(() => onSelection?.(xterm.getSelection()))
    const preventCopy = (event) => { event.preventDefault(); event.stopPropagation() }
    const preventNativeMenu = (event) => event.preventDefault()
    host.current.addEventListener('copy', preventCopy, true)
    host.current.addEventListener('cut', preventCopy, true)
    host.current.addEventListener('contextmenu', preventNativeMenu)

    const onResize = () => {
      try {
        fit.fit()
        if (sock && sock.readyState === WebSocket.OPEN) {
          sock.send(JSON.stringify({ type: 'resize', cols: xterm.cols, rows: xterm.rows }))
        }
      } catch { /* noop */ }
    }
    const ro = new ResizeObserver(onResize)
    ro.observe(host.current)
    window.addEventListener('resize', onResize)

    return () => {
      disposed = true
      clearTimeout(retryTimer)
      ro.disconnect()
      window.removeEventListener('resize', onResize)
      try { sub.dispose() } catch { /* noop */ }
      try { selectionSub.dispose() } catch { /* noop */ }
      host.current?.removeEventListener('copy', preventCopy, true)
      host.current?.removeEventListener('cut', preventCopy, true)
      host.current?.removeEventListener('contextmenu', preventNativeMenu)
      if (sock) sock.close()
      xterm.dispose()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, onSelection])

  useEffect(() => {
    if (active) setTimeout(() => {
      try { fitRef.current?.fit() } catch { /* noop */ }
      termRef.current?.focus()
    }, 30)
  }, [active])

  useEffect(() => {
    if (!active || !pasteRequest || pasteRequest.sessionId !== sessionId ||
        handledPasteRef.current === pasteRequest.id || !termRef.current) return
    handledPasteRef.current = pasteRequest.id
    termRef.current.paste(pasteRequest.text)
  }, [active, pasteRequest, sessionId])

  useEffect(() => {
    if (!active || !clearRequest || clearRequest.sessionId !== sessionId ||
        handledClearRef.current === clearRequest.id || !termRef.current) return
    handledClearRef.current = clearRequest.id
    termRef.current.clear()
  }, [active, clearRequest, sessionId])

  const label = { connecting: 'connecting…', retrying: 'reconnecting…', closed: 'disconnected', error: 'connection error', denied: 'session ended' }[status]
  return (
    <div className="term-wrap" style={{ height: '100%', display: active ? 'block' : 'none' }}>
      <div className="term-host" ref={host} onMouseDown={() => termRef.current?.focus()} />
      {label && <div className="term-status">{label}</div>}
    </div>
  )
}

/* ------------------------------------------------------------ command palette */
function Palette({ commands, onClose, files, onOpen }) {
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(0)
  const inputRef = useRef(null)

  useEffect(() => { inputRef.current?.focus() }, [])

  const items = useMemo(() => {
    const needle = q.toLowerCase().trim()
    const base = needle.startsWith('>') ? commands : [...commands, ...files.map((f) => ({
      label: `Go to ${f.path}`, hint: 'file', icon: '↦', run: () => onOpen(f.path),
    }))]
    const term = needle.replace(/^>/, '').trim()
    if (!term) return base.slice(0, 40)
    return base.filter((c) => (c.label + ' ' + (c.hint || '')).toLowerCase().includes(term)).slice(0, 40)
  }, [q, commands, files, onOpen])

  useEffect(() => { setSel(0) }, [q])

  return (
    <div className="palette-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="palette">
        <input ref={inputRef} value={q} placeholder="Type a command or file name…  (Esc to close)"
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') onClose()
            if (e.key === 'ArrowDown') { e.preventDefault(); setSel((s) => Math.min(s + 1, items.length - 1)) }
            if (e.key === 'ArrowUp') { e.preventDefault(); setSel((s) => Math.max(0, s - 1)) }
            if (e.key === 'Enter' && items[sel]) { items[sel].run(); onClose() }
          }} />
        <div className="palette-list">
          {items.map((item, i) => (
            <div key={item.label + i} className={`palette-item ${i === sel ? 'sel' : ''}`}
              onMouseEnter={() => setSel(i)} onClick={() => { item.run(); onClose() }}>
              <span style={{ width: 18, textAlign: 'center', opacity: .7 }}>{item.icon || '›'}</span>
              <span>{item.label}</span>
              {item.hint && <span className="hint">{item.hint}</span>}
            </div>
          ))}
          {!items.length && <div className="tree-empty">No matching commands</div>}
        </div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------- IDE main */
export default function Workspace() {
  const params = useParams()
  const nav = useNavigate()
  const toast = useToast()
  const setWorkspaceTaskId = useWorkspaceTask()
  const { user } = useAuth()
  const token = localStorage.getItem('wc_access') || ''

  const [projects, setProjects] = useState([])
  const [projectTasks, setProjectTasks] = useState([])
  const [selectedTaskId, setSelectedTaskId] = useState('')
  const [submittingTask, setSubmittingTask] = useState(false)
  const [checkingSubmission, setCheckingSubmission] = useState(false)
  const [hasOpenSubmission, setHasOpenSubmission] = useState(false)
  const [pid, setPid] = useState(params.projectId || null)
  const [project, setProject] = useState(null)
  const [files, setFiles] = useState([])
  const [docs, setDocs] = useState({})
  const [open, setOpen] = useState([])
  const [active, setActive] = useState(null)
  const [split, setSplit] = useState(null)
  const [dirty, setDirty] = useState({})
  const [sidebar, setSidebar] = useState('explorer')
  const [sideW, setSideW] = useState(250)
  const [panel, setPanel] = useState('terminal')
  const [panelH, setPanelH] = useState(220)
  const [panelOpen, setPanelOpen] = useState(true)
  const [terms, setTerms] = useState([])
  const [activeTerm, setActiveTerm] = useState(null)
  const [terminalError, setTerminalError] = useState('')
  const [pasteRequest, setPasteRequest] = useState(null)
  const [clearRequest, setClearRequest] = useState(null)
  const [output, setOutput] = useState([])
  const [running, setRunning] = useState(false)
  const [searchQ, setSearchQ] = useState('')
  const [results, setResults] = useState([])
  const [git, setGit] = useState(null)
  const [commitMsg, setCommitMsg] = useState('')
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [cursor, setCursor] = useState({ line: 1, col: 1 })
  const [langs, setLangs] = useState(null)
  const [servers, setServers] = useState([])
  const runningServer = servers.find((s) => s.status === 'running' || s.status === 'starting')
  const selectedTask = projectTasks.find((task) => task.id === selectedTaskId) || null
  const canSubmitTask = ['intern', 'developer_offroll', 'developer_onroll'].includes(user?.role)

  useEffect(() => {
    setWorkspaceTaskId(selectedTask?.id || null)
    return () => setWorkspaceTaskId(null)
  }, [selectedTask?.id, setWorkspaceTaskId])

  useEffect(() => {
    if (!pid) { setProjectTasks([]); setSelectedTaskId(''); return undefined }
    let cancelled = false
    api.tasks({ mine: true, size: 200 }).then(({ data }) => {
      if (cancelled) return
      const mine = Array.isArray(data) ? data : []
      // Show tasks linked to this project and unlinked tasks owned by the same assignee.
      // Never show tasks assigned to a different employee.
      const matching = mine.filter((task) => task.assignee_id === user?.id && (!task.project_id || task.project_id === pid))
      const inOtherProjects = mine.filter((task) => task.assignee_id === user?.id && task.project_id && task.project_id !== pid)
      setProjectTasks(matching)
      setSelectedTaskId((current) => matching.some((task) => task.id === current) ? current : matching[0]?.id || '')
      if (!matching.length && inOtherProjects.length) {
        toast('Assigned tasks exist but are linked to a different project. Open the correct project or ask an Admin to update the task project.', 'info')
      }
    }).catch((e) => {
      if (!cancelled) {
        setProjectTasks([])
        toast(e?.response?.data?.detail || 'Could not load assigned tasks', 'error')
      }
    })
    return () => { cancelled = true }
  }, [pid, toast, user?.id])

  useEffect(() => {
    let cancelled = false
    if (!selectedTask || !canSubmitTask) {
      setHasOpenSubmission(false)
      setCheckingSubmission(false)
      return undefined
    }
    setCheckingSubmission(true)
    api.submissions({ task_id: selectedTask.id })
      .then(({ data }) => {
        if (cancelled) return
        setHasOpenSubmission(data.some((submission) => submission.user_id === user.id &&
          ['submitted', 'pending_permanent_review', 'approved', 'forwarded_to_admin', 'pending_admin_review']
            .includes(submission.status)))
      })
      .catch(() => { if (!cancelled) setHasOpenSubmission(false) })
      .finally(() => { if (!cancelled) setCheckingSubmission(false) })
    return () => { cancelled = true }
  }, [selectedTask?.id, canSubmitTask, user?.id])

  // a dev server can appear at any moment (started from the terminal), so poll
  useEffect(() => {
    if (!pid) return undefined
    let stop = false
    const tick = () => ide.servers(pid)
      .then(({ data }) => { if (!stop) setServers(data.servers || []) })
      .catch(() => {})
    tick()
    const timer = setInterval(tick, 4000)
    return () => { stop = true; clearInterval(timer) }
  }, [pid])
  const [nonce, setNonce] = useState(0)
  const [ctx, setCtx] = useState(null)
  const [modal, setModal] = useState(null)
  const [collapsed, setCollapsed] = useState({})
  const [fullscreen, setFullscreen] = useState(true)  // editor-first by default
  useEffect(() => {
    // Escape exits focus mode; the platform chrome (timer, nav) stays mounted underneath
    const onKey = (e) => { if (e.key === 'Escape') setFullscreen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  const [creating, setCreating] = useState({ name: '', language: 'python' })
  const [showCreate, setShowCreate] = useState(false)
  const [syncState, setSyncState] = useState('idle')
  const [saveState, setSaveState] = useState('saved')
  const [saveInFlight, setSaveInFlight] = useState(false)
  const [plusOpen, setPlusOpen] = useState(false)
  const [testModal, setTestModal] = useState(false)
  const [testCasesText, setTestCasesText] = useState('[\n  { "stdin": "", "expected": "" }\n]')
  const [testResults, setTestResults] = useState(null)
  const [testing, setTesting] = useState(false)
  const [editorContext, setEditorContext] = useState(null)
  const [terminalSelection, setTerminalSelection] = useState('')

  const editorRef = useRef(null)
  const dirtyRef = useRef({})
  const docsRef = useRef({})
  const saveTimer = useRef(null)
  const uploadRef = useRef(null)
  const uploadDir = useRef('')

  dirtyRef.current = dirty
  docsRef.current = docs

  const activeDoc = active ? (docs[active] ?? '') : ''
  const tree = useMemo(() => buildTree(files), [files])

  /* ------------------------------------------------------------------ loading */
  useEffect(() => {
    api.projects().then(({ data }) => {
      setProjects(data)
      if (!pid && data.length) nav(`/workspace/${data[0].id}`, { replace: true })
      if (!data.length) setShowCreate(true)
    }).catch((e) => toast(e?.response?.data?.detail || 'Could not load projects', 'error'))
    api.languages().then(({ data }) => setLangs(data)).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loadTree = useCallback(async (projectId) => {
    const { data } = await ide.tree(projectId)
    setFiles(data.files)
    setProject({ ...data.project, entry: data.entry })
    return data
  }, [])

  useEffect(() => {
    if (!pid) return
    loadTree(pid).then((data) => {
      const first = data.entry || (data.files.find((f) => !f.is_dir) || {}).path
      if (first) openFile(first)
    }).catch(() => toast('Project not found', 'error'))
    ide.terminals(pid).then(({ data }) => {
      const alive = data.terminals.filter((t) => t.alive)
      setTerms(alive)
      if (alive.length) { setActiveTerm(alive[0].id); setTerminalError(''); return }
      ide.newTerminal(pid).then(({ data: t }) => { setTerms([t]); setActiveTerm(t.id); setTerminalError('') })
        .catch((error) => setTerminalError(error?.response?.data?.detail || 'Could not start an isolated terminal.'))
    }).catch((error) => setTerminalError(error?.response?.data?.detail || 'Could not check terminal availability.'))
    ide.servers(pid).then(({ data }) => setServers(data.servers)).catch(() => {})
    setDocs({}); setOpen([]); setActive(null); setSplit(null); setDirty({})
  }, [pid, loadTree, toast])

  /* -------------------------------------------------------------------- files */
  const openFile = useCallback(async (path, line) => {
    if (!path) return
    if (docsRef.current[path] === undefined) {
      try {
        const { data } = await ide.read(pid, path)
        setDocs((d) => ({ ...d, [path]: data.content }))
      } catch (e) {
        toast(e?.response?.data?.detail || 'Cannot open file', 'error')
        return
      }
    }
    setOpen((o) => (o.includes(path) ? o : [...o, path]))
    setActive(path)
    if (line && editorRef.current) {
      setTimeout(() => {
        editorRef.current.revealLineInCenter(line)
        editorRef.current.setPosition({ lineNumber: line, column: 1 })
        editorRef.current.focus()
      }, 120)
    }
  }, [pid, toast])

  const closeTab = (path, e) => {
    e?.stopPropagation()
    setOpen((o) => o.filter((p) => p !== path))
    if (active === path) setActive(open.filter((p) => p !== path).slice(-1)[0] || null)
    if (split === path) setSplit(null)
  }

  const savePromiseRef = useRef(null)

  const save = useCallback(async (silent = false) => {
    if (!pid) return false
    if (savePromiseRef.current) return savePromiseRef.current

    const run = async () => {
      const dirtyPaths = Object.keys(dirtyRef.current)
      if (!dirtyPaths.length) {
        setSaveState('saved')
        return true
      }
      const snapshot = Object.fromEntries(dirtyPaths.map((path) => [path, docsRef.current[path] ?? '']))
      setSaveState('saving')
      setSaveInFlight(true)
      try {
        for (const path of dirtyPaths) await ide.create(pid, { path, content: snapshot[path] })
        // Sync disk/index so version snapshot reflects terminal-created/renamed files too.
        try { await ide.sync(pid) } catch { /* snapshot can still proceed from known files */ }
        const { data: treeData } = await ide.tree(pid)
        const nextFiles = treeData.files || []
        const payload = nextFiles
          .filter((f) => !f.is_dir)
          .map((f) => ({
            path: f.path,
            content: snapshot[f.path] ?? docsRef.current[f.path] ?? '',
          }))
        // Ensure content for files not yet loaded into docs is read from backend.
        for (const item of payload) {
          if (item.content === '' && docsRef.current[item.path] === undefined && snapshot[item.path] === undefined) {
            try {
              const { data } = await ide.read(pid, item.path)
              item.content = data.content ?? ''
            } catch { /* keep empty string rather than dropping file */ }
          }
        }
        await api.saveProject(pid, payload, silent ? 'Auto save' : 'IDE save')
        setFiles(nextFiles)
        setProject((prev) => ({ ...(prev || {}), ...treeData.project, entry: treeData.entry }))
        setDirty((current) => {
          const next = { ...current }
          for (const path of dirtyPaths) {
            if ((docsRef.current[path] ?? '') === snapshot[path]) delete next[path]
          }
          return next
        })
        setSaveState('saved')
        if (!silent) toast('Saved')
        return true
      } catch (e) {
        setSaveState('failed')
        if (!silent) toast(e?.response?.data?.detail || 'Save failed', 'error')
        return false
      } finally {
        setSaveInFlight(false)
        savePromiseRef.current = null
      }
    }

    const promise = run()
    savePromiseRef.current = promise
    return promise
  }, [pid, toast])

  useEffect(() => {
    if (!Object.keys(dirty).length) return
    clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => save(true), 1200)
    return () => clearTimeout(saveTimer.current)
  }, [dirty, save])

  /* ----------------------------------------------------------------- terminals */
  const newTerminal = async () => {
    if (!pid) return
    setTerminalError('')
    try {
      const { data } = await ide.newTerminal(pid)
      setTerms((t) => [...t, data])
      setActiveTerm(data.id)
      setTerminalError('')
      setPanel('terminal'); setPanelOpen(true)
    } catch (e) {
      const message = e?.response?.data?.detail || 'Could not start terminal.'
      setTerminalError(message)
      setPanel('terminal'); setPanelOpen(true)
      toast(message, 'error')
    }
  }

  const restartTerminal = async (id, event) => {
    event?.stopPropagation()
    if (!pid) return
    try {
      const { data } = await ide.newTerminal(pid)
      setTerms((items) => [...items.filter((item) => item.id !== id), data])
      setActiveTerm(data.id)
      try { await ide.killTerminal(id) } catch { /* old session may have exited */ }
    } catch (e) {
      toast(e?.response?.data?.detail || 'Could not restart terminal', 'error')
    }
  }

  const killTerminal = async (id, e) => {
    e?.stopPropagation()
    try {
      await ide.killTerminal(id)
      setTerms((t) => {
        const next = t.filter((x) => x.id !== id)
        if (activeTerm === id) setActiveTerm(next[0]?.id || null)
        return next
      })
    } catch { /* already gone */ }
  }

  const pasteClipboard = async () => {
    if (!activeTerm) { toast('Open a terminal before pasting', 'error'); return }
    try {
      const text = await navigator.clipboard.readText()
      if (!text) return
      if (text.length > 200000) { toast('Clipboard text is too large to paste into the terminal', 'error'); return }
      setPasteRequest({ id: `${Date.now()}-${Math.random()}`, sessionId: activeTerm, text })
    } catch {
      toast('Clipboard access was blocked. Use Ctrl+V while the terminal is focused.', 'error')
    }
  }

  const clearTerminal = () => {
    if (activeTerm) setClearRequest({ id: `${Date.now()}-${Math.random()}`, sessionId: activeTerm })
  }

  /* ---------------------------------------------------------------- run / build */
  const pushOut = (lines) => setOutput((o) => [...o, ...lines])

  const run = async (currentFile = false) => {
    if (!pid || running || testing) return
    setPanel('output'); setPanelOpen(true); setRunning(true)
    setOutput([])
    pushOut([{ kind: 'dim', text: `▶ ${currentFile ? 'running ' + active : 'running project entry'}` }])
    try {
      const body = currentFile
        ? { language: project?.language, filename: active, code: docs[active] ?? '' }
        : { language: project?.language }
      const { data } = await ide.run(pid, body)
      pushOut([
        { kind: 'dim', text: `runner=${data.runner} exit=${data.exit_code} ${data.duration_ms}ms` },
        ...(data.stdout ? data.stdout.split('\n').map((t) => ({ kind: 'ok', text: t })) : []),
        ...(data.stderr ? data.stderr.split('\n').map((t) => ({ kind: 'err', text: t })) : []),
      ])
      if (data.runner === 'preview') { setPanel('preview'); setNonce((n) => n + 1) }
      loadTree(pid)
    } catch (e) {
      pushOut([{ kind: 'err', text: e?.response?.data?.detail || 'Execution failed' }])
    } finally { setRunning(false) }
  }

  const submitTask = async () => {
    if (!selectedTask || submittingTask || checkingSubmission) return
    if (!pid) { toast('Select a project before submitting', 'error'); return }
    if (selectedTask.project_id && selectedTask.project_id !== pid) {
      toast('Selected task is linked to a different project. Open the correct project to submit it.', 'error')
      return
    }
    setSubmittingTask(true)
    try {
      const saved = await save(true)
      if (!saved) {
        toast('Save your changes before submitting', 'error')
        return
      }
      const { status } = await api.submit({
        task_id: selectedTask.id,
        project_id: pid,
        test_results: testResults || [],
      })
      setHasOpenSubmission(true)
      toast(status === 200 ? 'This task is already awaiting review' : 'Saved snapshot submitted for review')
      api.tasks({ mine: true, size: 200 }).then(({ data }) => {
        const mine = Array.isArray(data) ? data : []
        const matching = mine.filter((task) => task.assignee_id === user?.id && (!task.project_id || task.project_id === pid))
        setProjectTasks(matching)
      }).catch(() => {})
    } catch (error) {
      toast(error?.response?.data?.detail || 'Could not submit this task', 'error')
    } finally {
      setSubmittingTask(false)
    }
  }

  const testCode = async () => {
    let cases
    try { cases = JSON.parse(testCasesText) } catch { toast('Test cases must be valid JSON', 'error'); return }
    if (Array.isArray(cases) && !cases.length) { toast('Add test cases before running tests', 'info'); return }
    if (!Array.isArray(cases) || cases.length > 50 ||
        cases.some((item) => !item || typeof item !== 'object' || typeof item.expected !== 'string' ||
          (item.stdin !== undefined && typeof item.stdin !== 'string'))) {
      toast('Add 1–50 test cases with string stdin and expected fields', 'error'); return
    }
    if (!active) { toast('Open a source file before running tests', 'error'); return }
    setTesting(true); setTestResults(null)
    const resultsForCases = []
    try {
      for (const [index, item] of cases.entries()) {
        const { data } = await ide.run(pid, {
          language: project?.language, filename: active, code: docs[active] ?? '',
          stdin: item.stdin ?? '',
        })
        const actual = (data.stdout || '').replace(/\r\n/g, '\n').trimEnd()
        const expected = item.expected.replace(/\r\n/g, '\n').trimEnd()
        resultsForCases.push({ index: index + 1, expected, actual, error: data.stderr || '',
          passed: data.ok && actual === expected })
      }
      setTestResults(resultsForCases)
    } catch (e) {
      setTestResults([{ index: 1, expected: '', actual: '', error: e?.response?.data?.detail || 'Test execution failed', passed: false }])
    } finally { setTesting(false) }
  }

  const build = async () => {
    if (!pid) return
    setPanel('output'); setPanelOpen(true)
    pushOut([{ kind: 'dim', text: '▶ building project…' }])
    try {
      const { data } = await ide.build(pid)
      pushOut([{ kind: data.ok ? 'ok' : 'err', text: `${data.command || 'build'} → exit ${data.exit_code ?? '—'}` },
        ...(data.output || '').split('\n').slice(-120).map((t) => ({ kind: data.ok ? 'ok' : 'err', text: t }))])
      if (data.ok && data.dist) { setPanel('preview'); setNonce((n) => n + 1); loadTree(pid) }
    } catch (e) { pushOut([{ kind: 'err', text: e?.response?.data?.detail || 'Build failed' }]) }
  }

  const sync = async () => {
    setSyncState('syncing')
    try {
      const { data } = await ide.sync(pid)
      setFiles(data.files)
      setSyncState('idle')
      toast(`Synced ${data.files.length} files from disk`)
    } catch { setSyncState('idle') }
  }

  /* --------------------------------------------------------------- file actions */
  const ask = (type, target = null) => {
    setModal({ type, target, value: '' })
    setCtx(null)
  }

  const submitModal = async () => {
    if (!modal) return
    const value = (modal.value || '').trim()
    try {
      if (modal.type === 'newfile' && value) {
        await ide.create(pid, { path: (modal.target ? modal.target + '/' : '') + value, content: '' })
        await loadTree(pid); openFile((modal.target ? modal.target + '/' : '') + value)
      } else if (modal.type === 'newfolder' && value) {
        await ide.create(pid, { path: (modal.target ? modal.target + '/' : '') + value, is_dir: true })
        await loadTree(pid)
      } else if (modal.type === 'rename' && value) {
        const parent = modal.target.includes('/') ? modal.target.slice(0, modal.target.lastIndexOf('/')) : ''
        await ide.move(pid, { from_path: modal.target, to_path: (parent ? parent + '/' : '') + value, action: 'rename' })
        await loadTree(pid)
      }
      setModal(null)
    } catch (e) { toast(e?.response?.data?.detail || 'Operation failed', 'error') }
  }

  const doAction = async (action, path) => {
    setCtx(null)
    try {
      if (action === 'duplicate') { await ide.move(pid, { from_path: path, action: 'duplicate' }); await loadTree(pid) }
      if (action === 'delete') {
        if (!window.confirm(`Delete ${path}? This cannot be undone.`)) return
        await ide.remove(pid, path); await loadTree(pid)
        setOpen((o) => o.filter((p) => p !== path)); if (active === path) setActive(null)
      }
      if (action === 'download') {
        const { data } = await ide.downloadFile(pid, path)
        const url = URL.createObjectURL(data); const a = document.createElement('a')
        a.href = url; a.download = path.split('/').pop(); a.click(); URL.revokeObjectURL(url)
      }
      if (action === 'copyPath') { navigator.clipboard?.writeText(path); toast('Path copied') }
    } catch (e) { toast(e?.response?.data?.detail || 'Action failed', 'error') }
  }

  const uploadFiles = async (fileList, dir) => {
    for (const file of fileList) {
      try { await ide.upload(pid, file, dir) } catch (e) { toast(e?.response?.data?.detail || 'Upload failed', 'error') }
    }
    loadTree(pid)
  }

  const onDrop = async (e, targetDir) => {
    e.preventDefault(); e.stopPropagation()
    const dragged = e.dataTransfer.getData('text/plain')
    if (dragged) {
      const name = dragged.split('/').pop()
      const to = (targetDir ? targetDir + '/' : '') + name
      if (to !== dragged) {
        try { await ide.move(pid, { from_path: dragged, to_path: to, action: 'rename' }); await loadTree(pid) }
        catch (err) { toast(err?.response?.data?.detail || 'Move failed', 'error') }
      }
      return
    }
    if (e.dataTransfer.files?.length) uploadFiles(e.dataTransfer.files, targetDir)
  }

  /* ---------------------------------------------------------------------- git */
  const loadGit = useCallback(async () => {
    if (!pid) return
    try {
      const [{ data: status }, { data: log }] = await Promise.all([ide.gitStatus(pid), ide.gitLog(pid)])
      setGit({ ...status, commits: log.commits })
    } catch { setGit(null) }
  }, [pid])

  useEffect(() => { if (sidebar === 'scm') loadGit() }, [sidebar, loadGit])

  const gitAction = async (body) => {
    try {
      const { data } = await ide.git(pid, body)
      if (!data.ok) toast((data.stderr || data.stdout || 'git failed').slice(0, 160), 'error')
      await loadGit(); await loadTree(pid)
      if (body.action === 'commit' && data.ok) { setCommitMsg(''); toast('Committed') }
    } catch (e) { toast(e?.response?.data?.detail || 'git failed', 'error') }
  }

  /* ------------------------------------------------------------------ commands */
  const commands = useMemo(() => [
    { label: 'Save all files', hint: 'Ctrl+S', icon: '💾', run: () => save() },
    { label: 'Run project', icon: '▶', run: () => run(false) },
    { label: 'Run current file', hint: 'F5', icon: '▶', run: () => run(true) },
    { label: 'Build & preview', icon: '▣', run: build },
    { label: 'New terminal', hint: 'Ctrl+`', icon: '❯', run: newTerminal },
    { label: 'Toggle panel', hint: 'Ctrl+J', icon: '▤', run: () => setPanelOpen((p) => !p) },
    { label: 'Toggle sidebar', hint: 'Ctrl+B', icon: '▥', run: () => setSidebar((s) => (s ? '' : 'explorer')) },
    { label: 'New file', icon: '＋', run: () => ask('newfile') },
    { label: 'New folder', icon: '🗀', run: () => ask('newfolder') },
    { label: 'Sync files from terminal', icon: '⟳', run: sync },
    { label: 'Show explorer', icon: '▤', run: () => setSidebar('explorer') },
    { label: 'Show search', hint: 'Ctrl+Shift+F', icon: '⌕', run: () => setSidebar('search') },
    { label: 'Show source control', icon: '⑂', run: () => setSidebar('scm') },
    { label: 'Toggle word wrap', icon: '↔', run: () => editorRef.current?.updateOptions({ wordWrap: editorRef.current?.getOption(editorRef.current?.getModel() ? 93 : 93) === 'on' ? 'off' : 'on' }) },
    { label: 'Toggle minimap', icon: '▦', run: () => editorRef.current?.updateOptions({ minimap: { enabled: !editorRef.current?.getOption(93) } }) },
    { label: 'Format document', hint: 'Shift+Alt+F', icon: '≡', run: () => editorRef.current?.getAction('editor.action.formatDocument')?.run() },
    { label: 'Open project ZIP', icon: '⬇', run: async () => {
      const { data } = await ide.downloadZip(pid)
      const url = URL.createObjectURL(data); const a = document.createElement('a')
      a.href = url; a.download = `${project?.name || 'project'}.zip`; a.click(); URL.revokeObjectURL(url)
    } },
    { label: 'Toggle full screen IDE', icon: '⛶', run: () => setFullscreen((f) => !f) },
    { label: 'Switch project…', run: () => { setSidebar('explorer'); toast('Use the project selector in the title bar') } },
  ], [save, run, build, newTerminal, sync, pid, project])

  /* ------------------------------------------------------------------ shortcuts */
  useEffect(() => {
    const onKey = (e) => {
      const mod = e.ctrlKey || e.metaKey
      if (mod && e.key.toLowerCase() === 's') { e.preventDefault(); save() }
      else if (e.key === 'F5' && !e.shiftKey) { e.preventDefault(); run(true) }
      else if (mod && e.key === '`') { e.preventDefault(); setPanelOpen((p) => !p) }
      else if (mod && e.key.toLowerCase() === 'b') { e.preventDefault(); setSidebar((s) => (s ? '' : 'explorer')) }
      else if (mod && e.key.toLowerCase() === 'p') { e.preventDefault(); setPaletteOpen(true) }
      else if (mod && e.shiftKey && e.key.toLowerCase() === 'p') { e.preventDefault(); setPaletteOpen(true) }
      else if (mod && e.key.toLowerCase() === 'j') { e.preventDefault(); setPanelOpen((p) => !p) }
      else if (e.key === 'Escape') { setPaletteOpen(false); setCtx(null) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [save, run])

  useEffect(() => {
    const insideEditor = (event) => event.target instanceof Element && event.target.closest('.monaco-editor')
    const onCopy = (event) => {
      if (!insideEditor(event)) return
      event.preventDefault()
      event.stopImmediatePropagation()
    }
    const onEditorShortcut = (event) => {
      if (!insideEditor(event) || !(event.ctrlKey || event.metaKey)) return
      if (!['c', 'x'].includes(event.key.toLowerCase())) return
      event.preventDefault()
      event.stopImmediatePropagation()
    }
    window.addEventListener('copy', onCopy, true)
    window.addEventListener('cut', onCopy, true)
    window.addEventListener('keydown', onEditorShortcut, true)
    return () => {
      window.removeEventListener('copy', onCopy, true)
      window.removeEventListener('cut', onCopy, true)
      window.removeEventListener('keydown', onEditorShortcut, true)
    }
  }, [])

  /* ------------------------------------------------------------------ resizing */
  const startResize = (axis, startEvent) => {
    startEvent.preventDefault()
    const startX = startEvent.clientX
    const startY = startEvent.clientY
    const startSide = sideW
    const startPanel = panelH

    const onMove = (e) => {
      if (axis === 'x') setSideW(Math.max(170, Math.min(620, startSide + (e.clientX - startX))))
      else setPanelH(Math.max(90, Math.min(window.innerHeight - 260, startPanel - (e.clientY - startY))))
    }
    const onUp = () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp) }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  /* --------------------------------------------------------------------- create */
  const createProject = async () => {
    try {
      const { data } = await api.createProject(creating)
      setShowCreate(false); setProjects((p) => [data, ...p]); nav(`/workspace/${data.id}`)
      toast('Project created')
    } catch (e) { toast(e?.response?.data?.detail || 'Could not create project', 'error') }
  }

  /* ------------------------------------------------------------------- renderers */
  const renderNode = (node, depth = 0) => {
    const entries = Object.values(node.children || {}).sort((a, b) => {
      if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1
      return a.name.localeCompare(b.name)
    })
    return entries.map((child) => {
      const icon = child.is_dir ? null : FILE_ICON(child.name)
      const isOpen = !collapsed[child.path]
      return (
        <div key={child.path}>
          <div
            className={`tree-row ${active === child.path ? 'active' : ''}`}
            style={{ paddingLeft: 6 + depth * 13 }}
            draggable
            onDragStart={(e) => e.dataTransfer.setData('text/plain', child.path)}
            onDragOver={(e) => { if (child.is_dir) { e.preventDefault(); e.currentTarget.classList.add('drop-target') } }}
            onDragLeave={(e) => e.currentTarget.classList.remove('drop-target')}
            onDrop={(e) => { e.currentTarget.classList.remove('drop-target'); onDrop(e, child.is_dir ? child.path : (child.path.includes('/') ? child.path.slice(0, child.path.lastIndexOf('/')) : '')) }}
            onClick={() => child.is_dir
              ? setCollapsed((c) => ({ ...c, [child.path]: !c[child.path] }))
              : openFile(child.path)}
            onContextMenu={(e) => { e.preventDefault(); setCtx({ x: e.clientX, y: e.clientY, path: child.path, is_dir: child.is_dir }) }}
            title={child.path}
          >
            <span className="twisty">{child.is_dir ? (isOpen ? '▾' : '▸') : ''}</span>
            {child.is_dir
              ? <span style={{ width: 14, textAlign: 'center', color: '#d9b25c' }}>{isOpen ? '🗀' : '🗀'}</span>
              : <span style={{ width: 14, textAlign: 'center', color: icon.c, fontSize: 9, fontWeight: 700 }}>{icon.i}</span>}
            <span className="fname">{child.name}</span>
            {dirty[child.path] && <span style={{ width: 6, height: 6, borderRadius: 3, background: '#4f8cff' }} />}
            <span className="ext-badge">{(child.name.split('.').pop() || '').toUpperCase()}</span>
          </div>
          {child.is_dir && isOpen && renderNode(child, depth + 1)}
        </div>
      )
    })
  }

  const SideContent = () => {
    if (sidebar === 'explorer') {
      return (
        <>
          <div className="side-head">
            <span>Explorer</span>
            <div className="side-actions">
              <button className="icon-btn" title="New file" onClick={() => ask('newfile')}>＋</button>
              <button className="icon-btn" title="New folder" onClick={() => ask('newfolder')}>🗀</button>
              <button className="icon-btn" title="Upload" onClick={() => { uploadDir.current = ''; uploadRef.current?.click() }}>⭱</button>
              <button className="icon-btn" title="Sync from disk" onClick={sync}>{syncState === 'syncing' ? '…' : '⟳'}</button>
            </div>
          </div>
          <div className="side-scroll" onDragOver={(e) => e.preventDefault()} onDrop={(e) => onDrop(e, '')}>
            {files.length ? renderNode(tree) : <div className="tree-empty">No files yet — create one with ＋</div>}
          </div>
        </>
      )
    }

    if (sidebar === 'search') {
      return (
        <>
          <div className="side-head"><span>Search</span></div>
          <div className="search-box">
            <input value={searchQ} placeholder="Search files and content…" autoFocus
              onChange={(e) => setSearchQ(e.target.value)}
              onKeyDown={async (e) => {
                if (e.key === 'Enter' && searchQ.trim()) {
                  try {
                    const { data } = await ide.search(pid, searchQ.trim())
                    setResults(data.results)
                  } catch { setResults([]) }
                }
              }} />
          </div>
          <div className="side-scroll">
            {results.map((r, i) => (
              <div key={i} className="sr-item" onClick={() => openFile(r.path, r.line)}>
                <div className="sr-file"><span style={{ color: '#79c0ff' }}>{r.path.split('/').pop()}</span>
                  <span className="sr-count">{r.kind === 'filename' ? 'name' : `line ${r.line}`}</span></div>
                <div className="sr-line">{r.text}</div>
                <div className="sr-count">{r.path}</div>
              </div>
            ))}
            {!results.length && <div className="tree-empty">Search the project (Enter)</div>}
          </div>
        </>
      )
    }

    if (sidebar === 'scm') {
      return (
        <>
          <div className="side-head">
            <span>Source control</span>
            <div className="side-actions">
              <button className="icon-btn" title="Refresh" onClick={loadGit}>⟳</button>
            </div>
          </div>
          <div className="side-scroll">
            {!git?.initialized && (
              <div className="scm-block">
                <p className="small" style={{ color: '#8b9ab3', marginBottom: 8 }}>No repository in this workspace.</p>
                <button className="btn sm primary" style={{ width: '100%' }}
                  onClick={() => gitAction({ action: 'init' })}>Initialize repository</button>
              </div>
            )}
            {git?.initialized && (
              <>
                <div className="scm-block">
                  <div className="small" style={{ color: '#8b9ab3', marginBottom: 6 }}>
                    branch <b style={{ color: '#dbe5f5' }}>{git.branch}</b> {git.ahead ? `· ${git.ahead} ahead` : ''}
                  </div>
                  <textarea className="scm-input" rows={2} placeholder="Commit message (Ctrl+Enter)"
                    value={commitMsg} onChange={(e) => setCommitMsg(e.target.value)}
                    onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && commitMsg.trim()) gitAction({ action: 'commit', message: commitMsg }) }} />
                  <button className="btn sm primary" style={{ width: '100%' }}
                    onClick={() => { gitAction({ action: 'add_all' }).then(() => commitMsg.trim() && gitAction({ action: 'commit', message: commitMsg })) }}>
                    Stage all &amp; commit
                  </button>
                </div>
                <div className="section-label"><span>Changes</span><span>{git.changes.length}</span></div>
                {git.changes.map((c) => (
                  <div className="scm-row" key={c.path} title={c.path}
                    onClick={() => ide.gitDiff(pid, { path: c.path }).then(({ data }) => {
                      setPanel('output'); setPanelOpen(true)
                      setOutput([{ kind: 'dim', text: `diff ${c.path}` },
                        ...(data.diff || '(no diff — file is untracked or unchanged)').split('\n').slice(0, 200).map((t) => ({ kind: 'dim', text: t }))])
                    })}>
                    <span className="scm-code">{c.code}</span>
                    <span className="scm-path">{c.path.split('/').pop()}</span>
                    <span className="scm-actions">
                      <button className="icon-btn" title="Stage" onClick={(e) => { e.stopPropagation(); gitAction({ action: 'add', path: c.path }) }}>＋</button>
                    </span>
                  </div>
                ))}
                {!git.changes.length && <div className="tree-empty">Working tree clean</div>}
                <div className="section-label"><span>Commits</span></div>
                {(git.commits || []).map((c) => (
                  <div className="scm-row" key={c.hash} title={c.subject}>
                    <span className="scm-code">•</span>
                    <span className="scm-path">{c.subject}</span>
                  </div>
                ))}
              </>
            )}
          </div>
        </>
      )
    }

    if (sidebar === 'extensions') {
      const list = (langs?.languages || []).filter((l) => l.runnable || l.preview)
      return (
        <>
          <div className="side-head"><span>Runtime &amp; tools</span></div>
          <div className="side-scroll">
            {list.map((l) => (
              <div className="ext-row" key={l.id}>
                <div className="ext-ico">{l.id.slice(0, 2).toUpperCase()}</div>
                <div style={{ minWidth: 0 }}>
                  <div className="ext-name">
                    {l.id}
                    <span className={`ide-chip ${l.installed || l.preview ? 'ok' : 'warn'}`}>
                      {l.preview ? 'preview' : l.installed ? 'installed' : 'docker only'}
                    </span>
                  </div>
                  <div className="ext-desc">
                    {l.preview ? 'Renders in the preview panel' : l.installed ? `${l.extension} · run with ▶` : `needs image ${l.docker_image}`}
                  </div>
                </div>
              </div>
            ))}
            <div className="ext-row">
              <div className="ext-ico">git</div>
              <div><div className="ext-name">Git {langs?.shells?.git ? <span className="ide-chip ok">installed</span> : null}</div>
                <div className="ext-desc">Source control panel · commit, branch, diff</div></div>
            </div>
          </div>
        </>
      )
    }

    if (sidebar === 'outline') {
      const symbols = activeDoc.split('\n').map((line, i) => ({ line: i + 1, text: line }))
        .filter((l) => /^(class |def |function |const |let |var |export |# |public |private )/.test(l.text.trim()))
      return (
        <>
          <div className="side-head"><span>Outline</span></div>
          <div className="side-scroll">
            {symbols.map((s) => (
              <div className="scm-row" key={s.line} onClick={() => openFile(active, s.line)}>
                <span className="scm-code">{s.line}</span>
                <span className="scm-path" style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11.5 }}>
                  {s.text.trim().slice(0, 46)}
                </span>
              </div>
            ))}
            {!symbols.length && <div className="tree-empty">No symbols detected</div>}
          </div>
        </>
      )
    }

    return <div className="tree-empty">Select a view from the activity bar</div>
  }

  /* --------------------------------------------------------------------- boot */
  if (showCreate && !projects.length) {
    return (
      <div className="ide-root" style={{ padding: 40, display: 'grid', placeItems: 'center' }}>
        <div className="ide-modal" style={{ width: 420 }}>
          <h3>Create your first project</h3>
          <div className="field" style={{ marginBottom: 10 }}>
            <label>Project name</label>
            <input value={creating.name} onChange={(e) => setCreating({ ...creating, name: e.target.value })} placeholder="my-app" />
          </div>
          <div className="field" style={{ marginBottom: 14 }}>
            <label>Language</label>
            <select value={creating.language} onChange={(e) => setCreating({ ...creating, language: e.target.value })}>
              {(langs?.languages || [{ id: 'python' }, { id: 'javascript' }, { id: 'html' }]).map((l) => (
                <option key={l.id} value={l.id}>{l.id}</option>
              ))}
            </select>
          </div>
          <button className="btn primary" style={{ width: '100%' }} disabled={!creating.name} onClick={createProject}>Create project</button>
        </div>
      </div>
    )
  }

  return (
    <div className={`ide-root ${fullscreen ? 'fullscreen' : ''}`} onClick={() => setCtx(null)}>
      <div className="ide-titlebar">
        <select value={pid || ''} onChange={(e) => nav(`/workspace/${e.target.value}`)} title="Active project">
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <span className="ide-chip">{project?.language || '—'}</span>
        <span className="ide-chip">v{project?.current_version ?? 1}</span>
        {canSubmitTask && <select className="ide-task-select"
          title={projectTasks.length ? 'Assigned task for this project' : 'No matching assigned task; choose its project or ask an Admin to assign one'}
          value={selectedTaskId}
          onChange={(event) => setSelectedTaskId(event.target.value)} disabled={!projectTasks.length}>
          {!projectTasks.length && <option value="">No assigned task for this project</option>}
          {projectTasks.length > 0 && projectTasks.map((task) => <option key={task.id} value={task.id}>{task.code} · {task.title}</option>)}
        </select>}
        <div className="spacer" />
        <div id="workspace-recording-slot" className="ide-recording-slot" />
        <div className="ide-plus-wrap">
          <button className="icon-btn" onClick={(e) => { e.stopPropagation(); setPlusOpen((open) => !open) }} title="Add">＋</button>
          {plusOpen && <div className="ide-plus-menu" onClick={(e) => e.stopPropagation()}>
            <button onClick={() => { setShowCreate(true); setPlusOpen(false) }}>＋ New project</button>
          </div>}
        </div>
        <button className="icon-btn ide-chatgpt-btn" title="Open ChatGPT in a new tab" onClick={() => window.open('https://chatgpt.com/', '_blank', 'noopener,noreferrer')}>✦ ChatGPT</button>
        <button className="icon-btn" onClick={() => setPaletteOpen(true)} title="Command palette (Ctrl+Shift+P)">⌘</button>
        <button className="icon-btn" onClick={() => setFullscreen((f) => !f)} title="Toggle full screen">⛶</button>
      </div>

      <div className="ide-body">
        <div className="activity-bar">
          {[
            ['explorer', '▤', 'Explorer (Ctrl+B)'],
            ['search', '⌕', 'Search (Ctrl+Shift+F)'],
            ['scm', '⑂', 'Source control'],
            ['extensions', '▩', 'Runtimes & tools'],
            ['outline', '☰', 'Outline'],
          ].map(([key, icon, title]) => (
            <button key={key} className={`act ${sidebar === key ? 'active' : ''}`} title={title}
              onClick={() => setSidebar((s) => (s === key ? '' : key))}>{icon}</button>
          ))}
          <div style={{ flex: 1 }} />
          <button className="act" title="Run current file (F5)" disabled={running || testing || !active} onClick={() => run(true)}>▶</button>
          <button className="act" title="New terminal (Ctrl+`)" onClick={newTerminal}>❯</button>
          <button className="act" title="Settings" onClick={() => setPaletteOpen(true)}>⚙</button>
        </div>

        {sidebar && (
          <>
            <div className="sidebar" style={{ width: sideW }}>
              <SideContent />
            </div>
            <div className="side-resizer" onMouseDown={(e) => startResize('x', e)} />
          </>
        )}

        <div className="editor-area">
          <div className="tabstrip">
            {open.map((path) => (
              <div key={path} className={`vtab ${active === path ? 'active' : ''}`} onClick={() => setActive(path)}
                onAuxClick={(e) => e.button === 1 && closeTab(path, e)} title={path}>
                <span style={{ fontSize: 9, color: FILE_ICON(path.split('/').pop()).c, fontWeight: 700 }}>
                  {FILE_ICON(path.split('/').pop()).i}
                </span>
                <span>{path.split('/').pop()}</span>
                {dirty[path] ? <span className="dot" /> : <span className="x" onClick={(e) => closeTab(path, e)}>✕</span>}
              </div>
            ))}
            <div className="tabs-actions">
              <button className="icon-btn" title="Split editor" onClick={() => setSplit(split ? null : active)}>▥</button>
              <span className={`save-state ${saveState}`} role="status">{saveState === 'saving' ? 'Saving…' : saveState === 'failed' ? 'Save failed' : Object.keys(dirty).length ? 'Unsaved changes' : 'Saved'}</span>
              <button className="icon-btn" title="Save (Ctrl+S)" disabled={saveInFlight || saveState === 'saving'} onClick={() => save(false)}>{saveState === 'saving' ? 'Saving…' : '💾 Save'}</button>
              <button className="icon-btn" title="Run current file (F5)" disabled={running || testing || !active} onClick={() => run(true)}>{running ? '… Running' : '▶ Run'}</button>
              <button className="icon-btn" title="Run test cases" disabled={testing || running || !active} onClick={() => { setTestResults(null); setTestModal(true) }}>✓ Test</button>
              {canSubmitTask && <button className="btn sm primary workspace-submit-btn"
                title={selectedTask ? `Submit work for ${selectedTask.code}` : 'No matching assigned task for this project'}
                aria-label={submittingTask ? 'Submitting task' : 'Submit task'}
                disabled={!selectedTask || !files.some((file) => !file.is_dir) || submittingTask || checkingSubmission || hasOpenSubmission || running || testing || saveState === 'saving' || saveInFlight}
                onClick={submitTask}>
                {submittingTask ? 'Submitting…' : checkingSubmission ? 'Checking…' : hasOpenSubmission ? 'Submitted' : '⇧ Submit'}
              </button>}
            </div>
          </div>

          {active && (
            <div className="breadcrumbs">
              {(active || '').split('/').map((seg, i, arr) => (
                <React.Fragment key={i}>
                  <span>{i === arr.length - 1 ? <b>{seg}</b> : seg}</span>
                  {i < arr.length - 1 && <span className="sep">›</span>}
                </React.Fragment>
              ))}
              <span className="sep" style={{ marginLeft: 'auto' }} />
              <span>{saveState === 'saving' ? 'saving' : saveState === 'failed' ? 'save failed' : dirty[active] ? 'unsaved' : 'saved'}</span>
            </div>
          )}

          <div className="split" onCopy={(e) => e.preventDefault()} onCut={(e) => e.preventDefault()}
            onKeyDownCapture={(e) => {
              if ((e.ctrlKey || e.metaKey) && ['c', 'x'].includes(e.key.toLowerCase())) {
                e.preventDefault(); e.stopPropagation()
              }
            }}
            onContextMenu={(e) => { e.preventDefault(); setEditorContext({ x: e.clientX, y: e.clientY }) }}>
            <div className="pane">
              {active ? (
                <Editor
                  height="100%"
                  theme="vs-dark"
                  path={active}
                  language={langFor(active)}
                  value={activeDoc}
                  onChange={(value) => {
                    setDocs((d) => ({ ...d, [active]: value ?? '' }))
                    setDirty((prev) => (prev[active] ? prev : { ...prev, [active]: true }))
                    setTestResults(null)
                  }}
                  onMount={(editor, monaco) => {
                    editorRef.current = editor
                    editor.onDidChangeCursorPosition((e) => setCursor({ line: e.position.lineNumber, col: e.position.column }))
                    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => save())
                    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyC, () => {})
                    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyX, () => {})
                    editor.addCommand(monaco.KeyCode.F5, () => run(true))
                  }}
                  options={{
                    fontSize: 13,
                    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                    minimap: { enabled: true, renderCharacters: false, maxColumn: 90 },
                    smoothScrolling: true,
                    cursorBlinking: 'smooth',
                    cursorSmoothCaretAnimation: 'on',
                    padding: { top: 10 },
                    automaticLayout: true,
                    scrollBeyondLastLine: false,
                    renderLineHighlight: 'all',
                    bracketPairColorization: { enabled: true },
                    guides: { bracketPairs: true, indentation: true },
                    tabSize: 2,
                    wordWrap: 'off',
                    formatOnPaste: true,
                    suggestOnTriggerCharacters: true,
                    quickSuggestions: true,
                    multiCursorModifier: 'alt',
                    stickyScroll: { enabled: true },
                    contextmenu: false,
                  }}
                />
              ) : (
                <div className="welcome">
                  <div style={{ textAlign: 'center' }}>
                    <h2>WORKCODE IDE</h2>
                    <p className="small" style={{ marginTop: 8, color: '#5d6b82' }}>
                      Open a file from the explorer · <kbd>Ctrl</kbd>+<kbd>P</kbd> to quick-open ·
                      <kbd> F5</kbd> to run the current file · <kbd>Ctrl</kbd>+<kbd>`</kbd> for the terminal
                    </p>
                    <div style={{ marginTop: 18, display: 'flex', gap: 8, justifyContent: 'center' }}>
                      <button className="btn sm" onClick={() => setPaletteOpen(true)}>Command palette</button>
                      <button className="btn sm" onClick={newTerminal}>Open terminal</button>
                      <button className="btn sm" onClick={() => setPanel('preview')}>Preview</button>
                    </div>
                  </div>
                </div>
              )}
            </div>
            {split && split !== active && (
              <div className="pane">
                <div className="pane-label">
                  <span>{split}</span>
                  <button className="icon-btn" style={{ marginLeft: 'auto' }} onClick={() => setSplit(null)}>✕</button>
                </div>
                <Editor height="100%" theme="vs-dark" path={`split:${split}`} language={langFor(split)}
                  value={docs[split] ?? ''} options={{ fontSize: 13, minimap: { enabled: false }, automaticLayout: true, readOnly: false }}
                  onChange={(value) => {
                    setDocs((d) => ({ ...d, [split]: value ?? '' }))
                    setDirty((prev) => (prev[split] ? prev : { ...prev, [split]: true }))
                  }} />
              </div>
            )}

          </div>
        </div>
      </div>

      {panelOpen && (
        <>
          <div className="panel-resizer" onMouseDown={(e) => startResize('y', e)} />
          <div className="bottom-panel" style={{ height: panelH }}>
            <div className="panel-tabs">
              {['terminal', 'output', 'problems', 'debug', 'ports', 'preview'].map((key) => (
                <button key={key} className={`ptab ${panel === key ? 'active' : ''}`} onClick={() => setPanel(key)}>
                  {key}{key === 'problems' && output.filter((o) => o.kind === 'err').length ? ` (${output.filter((o) => o.kind === 'err').length})` : ''}
                </button>
              ))}
              <div className="spacer" />
              {panel === 'terminal' && (
                <div className="term-tabs">
                  {terms.map((t, i) => (
                    <div key={t.id} className={`term-tab ${activeTerm === t.id ? 'active' : ''}`} onClick={() => setActiveTerm(t.id)}>
                      {(t.shell || 'bash').split(/[\\/]/).pop().replace(/\.exe$/i, '')} {i + 1}
                      <span className="x" title="Restart terminal" onClick={(e) => restartTerminal(t.id, e)}>⟳</span>
                      <span className="x" onClick={(e) => killTerminal(t.id, e)}>✕</span>
                    </div>
                  ))}
                  <button className="terminal-action" title="Paste clipboard text into the active terminal" onClick={pasteClipboard}>Paste</button>
                  <button className="terminal-action" title="Clear active terminal output" onClick={clearTerminal}>Clear</button>
                  <button className="icon-btn" title="New terminal" onClick={newTerminal}>＋</button>
                </div>
              )}
              {panel === 'preview' && (
                <div className="row" style={{ gap: 6 }}>
                  <button className="btn sm" onClick={() => setNonce((n) => n + 1)}>⟳ Reload</button>
                  <button className="btn sm" onClick={build}>Build</button>
                  <button className="btn sm primary" title="Start npm run dev and preview it" onClick={async () => {
                    try {
                      const { data } = await ide.runDev(pid)
                      toast(`dev server starting on :${data.port}`)
                      setTimeout(async () => {
                        const r = await ide.servers(pid); setServers(r.data.servers || [])
                        setPanel('preview'); setNonce((n) => n + 1)
                      }, 3000)
                    } catch (e) { toast(e?.response?.data?.detail || 'Could not start dev server', 'error') }
                  }}>▶ Run</button>
                  <button className="btn sm" onClick={async () => {
                    const { data } = await ide.servers(pid); setServers(data.servers || [])
                    setPanel('ports')
                  }}>Servers{servers.length ? ` (${servers.length})` : ''}</button>
                  {runningServer && <span className="ide-chip ok">:{runningServer.port} live</span>}
                </div>
              )}
              <button className="icon-btn" style={{ marginLeft: 8 }} title="Toggle panel" onClick={() => setPanelOpen(false)}>✕</button>
            </div>

            <div className="panel-body">
              {panel === 'terminal' && (
                <div style={{ height: '100%' }}>
                  {!terms.length && (
                    <div className="empty" style={{ padding: 24 }}>
                      {terminalError ? (
                        <div className="terminal-notice" role="alert">
                          <strong>Isolated terminal unavailable</strong>
                          <p>{terminalError}</p>
                          <p className="small">
                            {terminalError.toLowerCase().includes('not available locally')
                              ? 'Build the terminal image using the command shown above, then retry.'
                              : 'Check Docker Desktop and the configured workspace storage, then retry.'}
                            {' '}WORKCODE never runs submitted code in the web server process.
                          </p>
                          <button className="btn sm primary" onClick={newTerminal}>↻ Retry terminal</button>
                        </div>
                      ) : (
                        <>
                          <p style={{ marginBottom: 10 }}>No shell yet — start an isolated terminal.</p>
                          <button className="btn sm primary" onClick={newTerminal}>＋ New terminal</button>
                        </>
                      )}
                    </div>
                  )}
                  {terms.map((t) => (
                    <TerminalView key={t.id} sessionId={t.id} token={token} active={activeTerm === t.id}
                      onExit={() => { }} onSelection={setTerminalSelection}
                      pasteRequest={pasteRequest} clearRequest={clearRequest} />
                  ))}
                </div>
              )}

              {panel === 'output' && (
                <div style={{ padding: '6px 0' }}>
                  {output.length > 0 && <button className="btn sm" style={{ margin: '0 10px 6px' }} onClick={() => setOutput([])}>Clear output</button>}
                  {output.map((line, i) => (
                    <div key={i} className={`out-line ${line.kind || ''}`}>{line.text}</div>
                  ))}
                  {!output.length && <div className="out-line dim">Run the project (F5) or build it to see output here.</div>}
                </div>
              )}

              {panel === 'problems' && (
                <div style={{ padding: '6px 0' }}>
                  {output.filter((o) => o.kind === 'err').length === 0
                    ? <div className="out-line dim">No problems have been detected in the workspace.</div>
                    : output.filter((o) => o.kind === 'err').map((line, i) => (
                      <div key={i} className="out-line err">⚠ {line.text}</div>
                    ))}
                </div>
              )}

              {panel === 'debug' && (
                <div style={{ padding: 12 }}>
                  <div className="out-line dim">Debug console</div>
                  <div className="out-line dim">Breakpoint-level debugging requires a language server, which is not wired into this build — the console shows real execution output instead.</div>
                  {output.slice(-20).map((line, i) => <div key={i} className={`out-line ${line.kind || ''}`}>{line.text}</div>)}
                </div>
              )}

              {panel === 'ports' && (
                <div style={{ padding: '6px 10px', fontSize: 12 }}>
                  {!servers.length && (
                    <div className="out-line dim">No forwarded ports. Start a dev server from the terminal — <code>npm run dev</code> — it appears here with its port.</div>
                  )}
                  {servers.map((s) => (
                    <div key={s.id} className="port-row">
                      <span className="ide-chip ok">:{s.port}</span>
                      <span className="port-cmd">{s.command}</span>
                      <span className="out-line dim">{s.status}</span>
                      <button className="btn sm" onClick={() => { setPanel('preview'); setNonce((n) => n + 1) }}>Open</button>
                      <button className="btn sm" onClick={async () => { await ide.stopServer(pid, s.id); const { data } = await ide.servers(pid); setServers(data.servers) }}>Stop</button>
                    </div>
                  ))}
                </div>
              )}

              {panel === 'preview' && (
                <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                  <div className="row" style={{ padding: '4px 10px', gap: 8, borderBottom: '1px solid #141b26', fontSize: 11, color: '#7d8aa0' }}>
                    <span>/{project?.name || ''}{project?.language === 'html' || project?.language === 'react' ? '' : ''}</span>
                    <span style={{ marginLeft: 'auto' }}>live preview · same-origin</span>
                  </div>
                  <iframe key={nonce} className="peek-frame" title="preview"
                    src={runningServer ? ide.devProxyUrl(pid, runningServer.id) : ide.previewUrl(pid)} sandbox="allow-scripts allow-same-origin allow-forms allow-modals" />
                </div>
              )}
            </div>
          </div>
        </>
      )}

      <div className="statusbar">
        <span className="sb brand">⑂ {git?.branch || 'no repo'}</span>
        <span className="sb click" onClick={() => setSidebar('scm')}>changes {git?.changes?.length ?? 0}</span>
        <span className="sb click" onClick={sync}>{syncState === 'syncing' ? 'syncing…' : '⟳ sync'}</span>
        <span className="spacer" />
        {servers.length > 0 && <span className="sb" style={{ color: '#86efac' }}>● {servers.length} server</span>}
        <span className="sb">Ln {cursor.line}, Col {cursor.col}</span>
        <span className="sb">Spaces: 2</span>
        <span className="sb">UTF-8</span>
        <span className="sb">{langFor(active || '')}</span>
        <span className="sb">WORKCODE IDE</span>
      </div>

      {paletteOpen && (
        <Palette commands={commands} files={files.filter((f) => !f.is_dir)} onOpen={openFile}
          onClose={() => setPaletteOpen(false)} />
      )}

      {editorContext && (
        <div className="ctx-menu editor-paste-menu" style={{ left: editorContext.x, top: editorContext.y }}
          onMouseDown={(e) => e.stopPropagation()} onClick={(e) => e.stopPropagation()}>
          <button className="ctx-item" onClick={async () => {
            setEditorContext(null)
            try {
              const text = await navigator.clipboard.readText()
              const editor = editorRef.current
              if (editor && text) {
                const range = editor.getSelection()
                editor.executeEdits('paste', [{ range, text }])
                editor.focus()
              }
            } catch { toast('Clipboard access was denied by the browser', 'error') }
          }}>Paste</button>
        </div>
      )}

      {testModal && (
        <div className="ide-modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && setTestModal(false)}>
          <div className="ide-modal test-modal">
            <div className="row" style={{ justifyContent: 'space-between' }}><h3>Test current file</h3><button className="icon-btn" onClick={() => setTestModal(false)}>✕</button></div>
            <p className="small muted">Enter JSON test cases. Each case runs the current file with stdin and compares stdout to expected text. No cases run until you add them and click Run tests.</p>
            <textarea className="test-cases-input" value={testCasesText} onChange={(e) => setTestCasesText(e.target.value)} spellCheck="false" />
            <div className="row" style={{ justifyContent: 'space-between', marginTop: 10 }}>
              {testResults && <span className={testResults.every((item) => item.passed) ? 'test-pass' : 'test-fail'}>
                {testResults.filter((item) => item.passed).length} passed · {testResults.filter((item) => !item.passed).length} failed
              </span>}
              <span className="spacer" />
              {testResults && <button className="btn sm" onClick={() => setTestResults(null)}>Clear results</button>}
              <button className="btn sm primary" disabled={testing || running} onClick={testCode}>{testing ? 'Testing…' : 'Run tests'}</button>
            </div>
            {testResults && <div className="test-results">
              {testResults.map((item) => <div className={`test-result ${item.passed ? 'pass' : 'fail'}`} key={item.index}>
                <b>{item.passed ? 'PASS' : 'FAIL'} · Case {item.index}</b>
                {item.error && <pre>Error: {item.error}</pre>}
                <pre>Expected: {item.expected || '(empty)'}</pre><pre>Actual: {item.actual || '(empty)'}</pre>
              </div>)}
            </div>}
          </div>
        </div>
      )}

      {ctx && (
        <div className="ctx-menu" style={{ left: ctx.x, top: ctx.y }} onClick={(e) => e.stopPropagation()}>
          {ctx.is_dir && <>
            <div className="ctx-item" onClick={() => ask('newfile', ctx.path)}>＋ New file</div>
            <div className="ctx-item" onClick={() => ask('newfolder', ctx.path)}>🗀 New folder</div>
            <div className="ctx-item" onClick={() => { uploadDir.current = ctx.path; uploadRef.current?.click(); setCtx(null) }}>⭱ Upload here</div>
            <div className="ctx-sep" />
          </>}
          {!ctx.is_dir && <div className="ctx-item" onClick={() => { openFile(ctx.path); setCtx(null) }}>↗ Open</div>}
          <div className="ctx-item" onClick={() => ask('rename', ctx.path)}>✎ Rename</div>
          <div className="ctx-item" onClick={() => doAction('duplicate', ctx.path)}>⧉ Duplicate</div>
          <div className="ctx-item" onClick={() => doAction('download', ctx.path)}>⬇ Download</div>
          <div className="ctx-item" onClick={() => doAction('copyPath', ctx.path)}>⌘ Copy path</div>
          <div className="ctx-sep" />
          <div className="ctx-item danger" onClick={() => doAction('delete', ctx.path)}>🗑 Delete</div>
        </div>
      )}

      {modal && (
        <div className="ide-modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && setModal(null)}>
          <div className="ide-modal">
            <h3>{modal.type === 'newfile' ? 'New file' : modal.type === 'newfolder' ? 'New folder' : 'Rename'}</h3>
            <div className="field" style={{ marginBottom: 12 }}>
              <label>{modal.type === 'rename' ? `Renaming ${modal.target}` : modal.target ? `Inside ${modal.target}` : 'Project root'}</label>
              <input autoFocus defaultValue={modal.type === 'rename' ? (modal.target || '').split('/').pop() : ''}
                onChange={(e) => setModal({ ...modal, value: e.target.value })}
                onKeyDown={(e) => e.key === 'Enter' && submitModal()}
                placeholder={modal.type === 'newfolder' ? 'components' : 'App.jsx'} />
            </div>
            <div className="row" style={{ justifyContent: 'flex-end' }}>
              <button className="btn sm" onClick={() => setModal(null)}>Cancel</button>
              <button className="btn sm primary" onClick={submitModal}>OK</button>
            </div>
          </div>
        </div>
      )}

      {showCreate && projects.length > 0 && (
        <div className="ide-modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && setShowCreate(false)}>
          <div className="ide-modal">
            <h3>New project</h3>
            <div className="field" style={{ marginBottom: 10 }}>
              <label>Name</label>
              <input autoFocus value={creating.name} onChange={(e) => setCreating({ ...creating, name: e.target.value })} />
            </div>
            <div className="field" style={{ marginBottom: 14 }}>
              <label>Language</label>
              <select value={creating.language} onChange={(e) => setCreating({ ...creating, language: e.target.value })}>
                {(langs?.languages || []).map((l) => <option key={l.id} value={l.id}>{l.id}</option>)}
              </select>
            </div>
            <div className="row" style={{ justifyContent: 'flex-end' }}>
              <button className="btn sm" onClick={() => setShowCreate(false)}>Cancel</button>
              <button className="btn sm primary" disabled={!creating.name} onClick={createProject}>Create</button>
            </div>
          </div>
        </div>
      )}

      <input ref={uploadRef} type="file" multiple hidden
        onChange={(e) => { uploadFiles(e.target.files, uploadDir.current); e.target.value = '' }} />
    </div>
  )
}
