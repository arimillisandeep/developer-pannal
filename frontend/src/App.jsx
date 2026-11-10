import React from 'react'
import { Navigate, Outlet, Route, Routes } from 'react-router-dom'
import { useAuth } from './context/AuthContext'
import Layout from './components/Layout'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Tasks from './pages/Tasks'
import Workspace from './pages/Workspace'
import Chat from './pages/Chat'
import Meetings from './pages/Meetings'
import Recordings from './pages/Recordings'
import Users from './pages/Users'
import Monitoring from './pages/Monitoring'
import Reports from './pages/Reports'
import Profile from './pages/Profile'
import Reviews from './pages/Reviews'

function Loading() {
  return <div className="center" style={{ height: '100vh' }}><div className="spinner" /></div>
}

function Protected({ children, roles }) {
  const { user, loading } = useAuth()
  if (loading) return <Loading />
  if (!user) return <Navigate to="/login" replace />
  if (roles && !roles.includes(user.role)) return <Navigate to="/" replace />
  return children
}

function AuthenticatedLayout() {
  const { user, loading } = useAuth()
  if (loading) return <Loading />
  if (!user) return <Navigate to="/login" replace />
  return <Layout><Outlet /></Layout>
}

function Home() {
  const { home, user, loading } = useAuth()
  if (loading) return <Loading />
  if (!user) return <Navigate to="/login" replace />
  return <Navigate to={home} replace />
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<Home />} />
      <Route element={<AuthenticatedLayout />}>
        <Route path="/admin" element={<Protected roles={['admin']}><Dashboard /></Protected>} />
        <Route path="/onroll" element={<Protected roles={['developer_onroll']}><Dashboard /></Protected>} />
        <Route path="/offroll" element={<Protected roles={['developer_offroll']}><Dashboard /></Protected>} />
        <Route path="/intern" element={<Protected roles={['intern']}><Dashboard /></Protected>} />
        <Route path="/tasks" element={<Protected><Tasks /></Protected>} />
        <Route path="/reviews" element={<Protected roles={['admin', 'developer_onroll', 'developer_offroll']}><Reviews /></Protected>} />
        <Route path="/workspace" element={<Protected roles={['developer_onroll', 'developer_offroll', 'intern']}><Workspace /></Protected>} />
        <Route path="/workspace/:projectId" element={<Protected roles={['developer_onroll', 'developer_offroll', 'intern']}><Workspace /></Protected>} />
        <Route path="/chat" element={<Protected><Chat /></Protected>} />
        <Route path="/meetings" element={<Protected><Meetings /></Protected>} />
        <Route path="/recordings" element={<Protected><Recordings /></Protected>} />
        <Route path="/users" element={<Protected roles={['admin']}><Users /></Protected>} />
        <Route path="/monitoring" element={<Protected roles={['admin']}><Monitoring /></Protected>} />
        <Route path="/reports" element={<Protected><Reports /></Protected>} />
        <Route path="/profile" element={<Protected><Profile /></Protected>} />
      </Route>
      <Route path="*" element={<Home />} />
    </Routes>
  )
}
