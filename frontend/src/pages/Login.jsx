import React, { useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../context/AuthContext'

export default function Login() {
  const { user, login, home } = useAuth()
  const nav = useNavigate()
  const [mode, setMode] = useState('login')
  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
  const [email, setEmail] = useState('')
  const [resetToken, setResetToken] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [busy, setBusy] = useState(false)

  if (user) return <Navigate to={home} replace />

  const submit = async (e) => {
    e.preventDefault()
    setError(''); setInfo(''); setBusy(true)
    try {
      const data = await login(identifier.trim(), password)
      nav(data.redirect_to || home, { replace: true })
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not sign in. Check your connection.')
    } finally {
      setBusy(false)
    }
  }

  const forgot = async (e) => {
    e.preventDefault(); setError(''); setInfo(''); setBusy(true)
    try {
      const { data } = await api.forgot(email.trim())
      setInfo(data.dev_token
        ? `Development mode: your reset token is ${data.dev_token} — paste it below.`
        : data.detail)
      if (data.dev_token) { setResetToken(data.dev_token); setMode('reset') }
    } catch (err) {
      setError(err?.response?.data?.detail || 'Request failed')
    } finally { setBusy(false) }
  }

  const reset = async (e) => {
    e.preventDefault(); setError(''); setInfo(''); setBusy(true)
    try {
      await api.reset(resetToken.trim(), newPassword)
      setInfo('Password updated. You can sign in now.')
      setMode('login'); setPassword('')
    } catch (err) {
      setError(err?.response?.data?.detail || 'Reset failed')
    } finally { setBusy(false) }
  }

  return (
    <div className="login-wrap">
      <div className="login-art">
        <div>
          <div className="row" style={{ marginBottom: 44 }}>
            <span className="brand-mark">WC</span>
            <div>
              <div className="brand-name">WORKCODE</div>
              <div className="brand-sub">DEV PLATFORM</div>
            </div>
          </div>
          <h1 style={{ fontSize: 40, lineHeight: 1.1, maxWidth: 520 }}>
            One platform for your<br />engineering team.
          </h1>
          <p className="muted" style={{ marginTop: 14, maxWidth: 440 }}>
            Tasks, code reviews, a browser IDE, working hours, screen recording and meetings — all inside WORKCODE.
          </p>
          <div style={{ marginTop: 40, maxWidth: 420 }}>
            {[
              ['⌨', 'Cloud IDE', 'Monaco editor, terminal, run code, version history.'],
              ['☑', 'Task workflow', 'Assign, submit, review, approve — with full audit trail.'],
              ['◎', 'Monitoring', 'Server-side timers, attendance, consent-based recordings.'],
              ['◷', 'Meetings', 'Real Google Meet links created through the Calendar API.']
            ].map(([ico, title, body]) => (
              <div className="feature" key={title}>
                <span className="dot">{ico}</span>
                <div>
                  <div style={{ fontWeight: 620, fontSize: 13.5 }}>{title}</div>
                  <div className="small muted">{body}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="small muted">Internal use only · Role-based access · Every action audited</div>
      </div>

      <div className="login-form">
        <div className="login-box">
          <h2 style={{ fontSize: 22 }}>{mode === 'login' ? 'Sign in' : mode === 'forgot' ? 'Reset your password' : 'Choose a new password'}</h2>
          <p className="muted small" style={{ marginTop: 6, marginBottom: 22 }}>
            {mode === 'login' ? 'Use your WORKCODE email or username.' : mode === 'forgot' ? 'We will send a reset token if the email exists.' : 'Paste the token you received and pick a new password.'}
          </p>

          {error && <div className="toast error" style={{ position: 'static', marginBottom: 14, maxWidth: '100%' }}>{error}</div>}
          {info && <div className="toast ok" style={{ position: 'static', marginBottom: 14, maxWidth: '100%', wordBreak: 'break-all' }}>{info}</div>}

          {mode === 'login' && (
            <form onSubmit={submit}>
              <div className="field">
                <label>Email or username</label>
                <input value={identifier} onChange={(e) => setIdentifier(e.target.value)} autoComplete="username" required placeholder="you@workcode.local" />
              </div>
              <div className="field">
                <label>Password</label>
                <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required placeholder="••••••••" />
              </div>
              <button className="btn primary" style={{ width: '100%' }} disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
              <div className="between" style={{ marginTop: 14 }}>
                <button type="button" className="btn sm ghost" onClick={() => setMode('forgot')}>Forgot password?</button>
                <span className="small muted">Protected by JWT + RBAC</span>
              </div>
            </form>
          )}

          {mode === 'forgot' && (
            <form onSubmit={forgot}>
              <div className="field">
                <label>Account email</label>
                <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required placeholder="you@workcode.local" />
              </div>
              <button className="btn primary" style={{ width: '100%' }} disabled={busy}>Send reset token</button>
              <button type="button" className="btn sm ghost" style={{ marginTop: 12 }} onClick={() => setMode('login')}>← Back to sign in</button>
            </form>
          )}

          {mode === 'reset' && (
            <form onSubmit={reset}>
              <div className="field">
                <label>Reset token</label>
                <input value={resetToken} onChange={(e) => setResetToken(e.target.value)} required />
              </div>
              <div className="field">
                <label>New password</label>
                <input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} required minLength={8} placeholder="min. 8 chars, letters + numbers" />
              </div>
              <button className="btn primary" style={{ width: '100%' }} disabled={busy}>Update password</button>
            </form>
          )}
        </div>
      </div>
    </div>
  )
}
