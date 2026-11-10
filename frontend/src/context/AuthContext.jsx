import React, { createContext, useContext, useEffect, useMemo, useState } from 'react'
import { api, tokens } from '../api'

const AuthCtx = createContext(null)

export const ROLE_HOME = {
  admin: '/admin',
  developer_onroll: '/onroll',
  developer_offroll: '/offroll',
  intern: '/intern'
}

export const ROLE_LABEL = {
  admin: 'Administrator',
  developer_onroll: 'Developer · On-Roll',
  developer_offroll: 'Developer · Off-Roll',
  intern: 'Intern'
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => {
    const raw = localStorage.getItem('wc_user')
    return raw ? JSON.parse(raw) : null
  })
  const [loading, setLoading] = useState(Boolean(tokens.access))

  useEffect(() => {
    if (!tokens.access) { setLoading(false); return }
    api.me()
      .then(({ data }) => {
        setUser(data)
        localStorage.setItem('wc_user', JSON.stringify(data))
      })
      .catch(() => { tokens.clear(); setUser(null) })
      .finally(() => setLoading(false))
  }, [])

  const value = useMemo(() => ({
    user,
    loading,
    isAdmin: user?.role === 'admin',
    isDeveloper: user?.role === 'developer_onroll' || user?.role === 'developer_offroll',
    isIntern: user?.role === 'intern',
    home: ROLE_HOME[user?.role] || '/login',
    async login(identifier, password) {
      const { data } = await api.login(identifier, password)
      tokens.set(data.access_token, data.refresh_token)
      localStorage.setItem('wc_user', JSON.stringify(data.user))
      setUser(data.user)
      return data
    },
    async logout() {
      if (user && user.role !== 'admin') {
        try { await api.timerStop() } catch { /* a stopped or expired session is already safe */ }
      }
      try { await api.logout() } catch { /* token may already be dead */ }
      tokens.clear()
      setUser(null)
    }
  }), [user, loading])

  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>
}

export const useAuth = () => useContext(AuthCtx)
