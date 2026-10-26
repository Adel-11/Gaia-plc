import { FormEvent, useEffect, useState } from 'react'
import V2Project from './V2Project'

type Me = { csrf_token?: string; username?: string }

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  const response = await fetch(`/api${path}`, { ...options, headers, credentials: 'same-origin' })
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try { const data = await response.json(); message = typeof data.detail === 'string' ? data.detail : data.message || message } catch { /* response may not be JSON */ }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

export default function App() {
  const [csrf, setCsrf] = useState('')
  const [workspaceEpoch, setWorkspaceEpoch] = useState(0)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    api<Me>('/me').then(me => { setCsrf(me.csrf_token || ''); setUsername(me.username || '') }).catch(() => { /* signed-out state is shown below */ }).finally(() => setLoading(false))
  }, [])

  const login = async (event: FormEvent) => {
    event.preventDefault(); setSubmitting(true); setError('')
    try {
      const me = await api<Me>('/login', { method: 'POST', body: JSON.stringify({ username, password }) })
      const token = me.csrf_token || ''
      // Preserve the existing v1 session/state bootstrap contract before entering v2.
      await api('/state')
      setCsrf(token); setUsername(me.username || username); setPassword('')
    } catch (e) { setError(e instanceof Error ? e.message : 'Sign in failed') } finally { setSubmitting(false) }
  }

  const logout = async () => {
    try {
      await fetch('/api/logout', { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf } })
    } finally { setCsrf('') }
  }

  if (loading) return <div className="loading"><span className="brand-mark">G</span><span>Connecting to Gaia IO Generator…</span></div>
  if (!csrf) return <main className="login-shell"><section className="login-panel"><div className="brand"><span className="brand-mark">G</span><span>Gaia <b>IO Generator</b></span></div><div className="login-copy"><p className="eyebrow">CONTROL SYSTEMS · GAIA REFINERY</p><h1>Project data, in one place.</h1><p>Sign in to review PLC types, instances, source workbooks, and exports.</p></div><form onSubmit={login} className="login-form"><label>Username<input autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} required /></label><label>Password<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label><button className="button primary wide" disabled={submitting}>{submitting ? 'Signing in…' : 'Sign in'}</button>{error && <p className="inline-error" role="alert">{error}</p>}</form><p className="login-foot">Private workspace · Session protected</p></section><aside className="login-art"><div className="schematic-grid"/><div className="art-content"><div className="live-chip"><span/> PROJECT ACCESS</div><h2>Industrial signals,<br/>clearly in sync.</h2><p>Modbus exchange management for the Gaia carbon capture skid.</p><div className="art-lines"><span/><span/><span/><i>PLC · MODBUS TCP</i></div></div><div className="art-footer"><span>GAIA REFINERY</span><span>OTTAWA · CANADA</span></div></aside></main>

  return <div className="app-shell v2-shell v2-tabbed-shell">
    <main className="main-area"><header className="topbar"><div className="breadcrumbs"><span className="brand-mark">G</span><span>Gaia Refinery</span><span className="crumb-sep">/</span><b>PLC Generator</b></div><div className="top-actions"><span className="save-indicator"><span/> Signed in</span><span className="avatar small">{(username || 'A').slice(0, 1).toUpperCase()}</span><button className="button secondary" onClick={() => void logout()}>Déconnexion</button></div></header><V2Project key={workspaceEpoch} csrf={csrf} onWorkspaceChanged={() => setWorkspaceEpoch(old => old + 1)}/></main>
  </div>
}
