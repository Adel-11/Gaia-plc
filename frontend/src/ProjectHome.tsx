import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import './project-home.css'
import WorkspacePanel from './WorkspacePanel'

type ProjectSummary = { id: number; name: string; version?: string; revision?: number; updated_at?: string }
type VersionEntry = { version?: string; date?: string; comment?: string; username?: string; kind?: string }
type ActivityItem = { at?: string; name?: string; filename?: string; username?: string; [key: string]: unknown }
type Workspace = { active_project_id?: number; projects: ProjectSummary[]; version?: string; history?: VersionEntry[]; activity?: Record<string, ActivityItem | ActivityItem[] | null>; is_admin?: boolean; users?: User[] }
type User = { id: number; username: string; is_admin?: boolean; created_at?: string }
type Review = { token?: string | null; errors?: unknown[]; warnings?: unknown[]; changes?: unknown[]; included?: unknown[] }
type Props = { csrf: string; project: any; onChanged: () => void; onSelectProject: (id: number) => void; beforeProjectChange?: () => boolean | Promise<boolean> }

const activityLabels: Record<string, string> = {
  types: 'Types modifiés', instances: 'Instances modifiées', program_export: 'Programme exporté', program_import: 'Programme importé',
  functions: 'Fonctions échange modifiées', generated_custom_code: 'Code généré modifié', tcp_clients: 'Clients TCP modifiés', tcp_points: 'Points TCP modifiés', rtu_lines: 'Lignes RTU modifiées', rtu_slaves: 'Esclaves RTU modifiés', rtu_channels: 'Canaux RTU modifiés', io_modules: 'Modules E/S modifiés', io_channels: 'Canaux E/S modifiés', virtual_io: 'E/S virtuelles modifiées', pa_values: 'Valeurs PA modifiées', exchange_functions: 'Fonctions échange modifiées', exchange_functions_detail: 'Détails de fonction échange modifiés', exchange_calls: 'Appels de fonctions échange modifiés', workbooks: 'Classeurs modifiés', catalog_options: 'Options de catalogue modifiées', excel_export: 'Excel exporté', backup: 'Sauvegarde créée', code: 'Code modifié', io: 'E/S modifiées', excel_import: 'Excel importé', backup_import: 'Sauvegarde restaurée', rename: 'Projet renommé', major: 'Version majeure créée', restore: 'Projet restauré',
}
const formatDate = (value?: string) => { if (!value) return 'Date inconnue'; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('fr-CA', { dateStyle: 'medium', timeStyle: 'short' }).format(date) }
function errorMessage(value: unknown): string { if (typeof value === 'string') return value; if (Array.isArray(value)) return value.map(errorMessage).filter(Boolean).join(' · '); if (value && typeof value === 'object') { const row = value as Record<string, unknown>; return errorMessage(row.message ?? row.detail ?? row.msg ?? row.error) }; return '' }

async function api<T>(path: string, options: RequestInit = {}, csrf = ''): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (csrf && options.method && options.method !== 'GET') headers.set('X-CSRF-Token', csrf)
  const response = await fetch(`/api/v2${path}`, { ...options, headers, credentials: 'same-origin' })
  if (!response.ok) { let message = `Erreur ${response.status}`; try { const body = await response.json(); message = errorMessage(body.detail ?? body.message ?? body.error) || message } catch { /* response may not contain JSON */ }; throw new Error(message) }
  return (response.headers.get('content-type') || '').includes('application/json') ? response.json() as Promise<T> : response.blob() as T
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob), anchor = document.createElement('a')
  anchor.href = url; anchor.download = filename; anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function responseFilename(response: Response, fallback: string): string {
  const disposition = response.headers.get('content-disposition') || ''
  const match = disposition.match(/(?:^|;)\s*filename\s*=\s*(?:"([^"]+)"|([^;\s]+))/i)
  const candidate = (match?.[1] || match?.[2] || '').replace(/[\\/]/g, '').replace(/[\u0000-\u001f\u007f]/g, '').trim()
  return candidate || fallback
}

async function downloadResponse(path: string, options: RequestInit = {}, csrf = '', fallback: string): Promise<{ blob: Blob; filename: string }> {
  const headers = new Headers(options.headers)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (csrf && options.method && options.method !== 'GET') headers.set('X-CSRF-Token', csrf)
  const response = await fetch(`/api/v2${path}`, { ...options, headers, credentials: 'same-origin' })
  if (!response.ok) {
    let message = `Erreur ${response.status}`
    try { const body = await response.json(); message = errorMessage(body.detail ?? body.message ?? body.error) || message } catch { /* response may not contain JSON */ }
    throw new Error(message)
  }
  return { blob: await response.blob(), filename: responseFilename(response, fallback) }
}

export default function ProjectHome({ csrf, project, onChanged, onSelectProject, beforeProjectChange }: Props) {
  const [workspace, setWorkspace] = useState<Workspace | null>(null)
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('')
  const [newName, setNewName] = useState(''), [copyCurrent, setCopyCurrent] = useState(false), [rename, setRename] = useState('')
  const [majorComment, setMajorComment] = useState(''), [majorOpen, setMajorOpen] = useState(false)
  const [review, setReview] = useState<Review | null>(null), [restoreReview, setRestoreReview] = useState<Review | null>(null)
  const [restoreFile, setRestoreFile] = useState<File | null>(null)
  const [adminOpen, setAdminOpen] = useState(false), [users, setUsers] = useState<User[]>([])
  const [username, setUsername] = useState(''), [password, setPassword] = useState(''), [newAdmin, setNewAdmin] = useState(false)
  const currentId = Number(workspace?.active_project_id ?? project?.id ?? 0)
  const currentProject = workspace?.projects?.find(item => item.id === currentId) || project || null
  const loadWorkspace = useCallback(async () => { try { const value = await api<Workspace>('/workspace'); setWorkspace(value) } catch (e) { setError(e instanceof Error ? e.message : 'Impossible de charger l’espace de travail') } }, [])
  useEffect(() => { void loadWorkspace() }, [loadWorkspace, project?.revision])
  useEffect(() => { setRename(workspace?.projects?.find(item => item.id === workspace.active_project_id)?.name || '') }, [workspace?.active_project_id])
  const counts = useMemo(() => {
    const supplied = project?.counts || {}
    return [
      ['Types', Number(supplied.types ?? project?.types?.length ?? 0)],
      ['Instances', Number(supplied.instances ?? project?.instances?.length ?? 0)],
      ['Membres DUT', Number(supplied.dut_members ?? 0)],
      ['Tags FUXA', Number(supplied.fuxa_signals ?? 0)],
      ['Fonctions échange', Number(supplied.exchange_functions ?? 0)],
      ['Classeurs', Number(supplied.workbooks ?? project?.workbooks?.length ?? 0)],
    ] as [string, number][]
  }, [project])
  const activityRows = useMemo(() => {
    const activity = workspace?.activity || {}
    return Object.entries(activity).flatMap(([key, value]) => {
      const items = Array.isArray(value) ? value : value ? [value] : []
      return items.map((item, index) => ({ key: `${key}-${index}`, label: activityLabels[key] || key.replaceAll('_', ' '), item }))
    }).sort((a, b) => (new Date(b.item.at || 0).getTime() || 0) - (new Date(a.item.at || 0).getTime() || 0))
  }, [workspace])
  const highlightedActivity = useMemo(() => {
    const activity = workspace?.activity || {}
    const keys = [['program_export', 'Dernier export du programme'], ['program_import', 'Dernier import du programme'], ['types', 'Dernière modification des types'], ['instances', 'Dernière modification des instances'], ['generated_custom_code', 'Dernière modification du code'], ['io_channels', 'Dernière modification des E/S'], ['pa_values', 'Dernière modification des valeurs PA']]
    return keys.map(([key, label]) => { const value = activity[key]; const entries = (Array.isArray(value) ? value : value ? [value] : []).filter((item): item is ActivityItem => Boolean(item)); const latest = entries.sort((a, b) => new Date(b.at || 0).getTime() - new Date(a.at || 0).getTime())[0]; return { key, label, latest } })
  }, [workspace])
  const approveProjectChange = async () => beforeProjectChange ? await beforeProjectChange() : true
  const refresh = async (message?: string) => { await loadWorkspace(); onChanged(); if (message) setNotice(message) }
  const post = async <T,>(path: string, body: unknown): Promise<T> => api<T>(path, { method: 'POST', body: JSON.stringify(body) }, csrf)
  const createProject = async (event: FormEvent) => { event.preventDefault(); if (!newName.trim() || !(await approveProjectChange())) return; setBusy(true); setError(''); try { const result = await post<{ id: number }>('/workspace/projects', { name: newName.trim(), copy_current: copyCurrent }); setNewName(''); setCopyCurrent(false); await refresh('Le projet a été créé et sélectionné.'); if (result.id) onSelectProject(result.id) } catch (e) { setError(e instanceof Error ? e.message : 'Impossible de créer le projet') } finally { setBusy(false) } }
  const selectProject = async (id: number) => { if (!id || id === currentId || !(await approveProjectChange())) return; setBusy(true); setError(''); try { await post('/workspace/select', { id }); await loadWorkspace(); onSelectProject(id) } catch (e) { setError(e instanceof Error ? e.message : 'Impossible de sélectionner le projet') } finally { setBusy(false) } }
  const renameProject = async (event: FormEvent) => { event.preventDefault(); if (!rename.trim()) return; setBusy(true); setError(''); try { await post('/workspace/rename', { name: rename.trim(), revision: Number(project?.revision ?? currentProject?.revision ?? 0) }); const savedName = rename.trim(); await refresh('Le nom du projet a été mis à jour.'); setRename(savedName) } catch (e) { setError(e instanceof Error ? e.message : 'Impossible de renommer le projet') } finally { setBusy(false) } }
  const makeMajor = async (event: FormEvent) => { event.preventDefault(); if (!majorComment.trim()) return; setBusy(true); setError(''); try { await post('/workspace/major', { revision: Number(project?.revision ?? currentProject?.revision ?? 0), comment: majorComment.trim() }); setMajorComment(''); setMajorOpen(false); await refresh('La version majeure a été créée.') } catch (e) { setError(e instanceof Error ? e.message : 'Impossible de créer une version majeure') } finally { setBusy(false) } }
  const downloadAction = async (path: string, fallback: string, message: string) => { setBusy(true); setError(''); try { const result = await downloadResponse(path, { method: 'POST', body: JSON.stringify({}) }, csrf, fallback); downloadBlob(result.blob, result.filename); await refresh(message) } catch (e) { setError(e instanceof Error ? e.message : 'Échec du téléchargement') } finally { setBusy(false) } }
  const previewPlc = async () => { setBusy(true); setError(''); try { const result = await post<Review>('/plc/preview', { mode: 'full' }); setReview(result) } catch (e) { setError(e instanceof Error ? e.message : 'Impossible de prévisualiser le programme PLC') } finally { setBusy(false) } }
  const downloadPlc = async () => { if (!review?.token || review.errors?.length) return; setBusy(true); setError(''); try { const result = await downloadResponse(`/plc/download/${encodeURIComponent(review.token)}`, {}, '', 'Gaia-program.xml'); downloadBlob(result.blob, result.filename); setReview(null); await refresh('Le programme PLCopen a été téléchargé.') } catch (e) { setError(e instanceof Error ? e.message : 'Échec du téléchargement PLCopen') } finally { setBusy(false) } }
  const previewRestore = async () => { if (!restoreFile) return; setBusy(true); setError(''); try { const form = new FormData(); form.append('file', restoreFile); const result = await api<Review>('/workspace/restore/preview', { method: 'POST', body: form }, csrf); setRestoreReview(result) } catch (e) { setError(e instanceof Error ? e.message : 'Impossible de prévisualiser la restauration') } finally { setBusy(false) } }
  const applyRestore = async () => { if (!restoreReview?.token || restoreReview.errors?.length) return; setBusy(true); setError(''); try { await post('/workspace/restore/apply', { token: restoreReview.token }); setRestoreReview(null); setRestoreFile(null); await refresh('La sauvegarde a été restaurée.') } catch (e) { setError(e instanceof Error ? e.message : 'Impossible de restaurer la sauvegarde') } finally { setBusy(false) } }
  const loadUsers = async () => { setAdminOpen(v => !v); if (!users.length && workspace?.is_admin) { try { const result = await api<{ users: User[] }>('/workspace/users'); setUsers(result.users || []) } catch (e) { setError(e instanceof Error ? e.message : 'Impossible de charger les comptes') } } }
  const addUser = async (event: FormEvent) => { event.preventDefault(); if (!username.trim() || !password) return; setBusy(true); setError(''); try { const result = await post<{ users: User[] }>('/workspace/users', { username: username.trim(), password, is_admin: newAdmin }); setUsers(result.users || [...users, { id: Date.now(), username: username.trim(), is_admin: newAdmin }]); setUsername(''); setPassword(''); setNewAdmin(false); setNotice('Le compte a été créé.') } catch (e) { setError(e instanceof Error ? e.message : 'Impossible de créer le compte') } finally { setPassword(''); setBusy(false) } }

  return <main className="ph-home">
    <header className="ph-hero"><div><span className="ph-eyebrow">ESPACE DE TRAVAIL</span><h1>{currentProject?.name || 'Projet Gaia'}</h1><p>Vue d’ensemble du projet, de son activité et de ses versions.</p></div><div className="ph-version"><span>Version actuelle</span><strong>{workspace?.version || currentProject?.version || '—'}</strong><button type="button" onClick={() => setMajorOpen(true)} disabled={busy}>Créer une version majeure</button></div></header>
    {error && <p className="ph-alert" role="alert">{error}</p>}{notice && <p className="ph-notice" role="status">{notice}</p>}
    <WorkspacePanel id="project:selection" title="Project selection"><section className="ph-card ph-project-card"><div className="ph-card-title"><div><h2>Projets</h2><p>Sélectionnez un projet ou créez-en un nouveau.</p></div><label className="ph-picker">Projet actif<select disabled={busy} value={currentId || ''} onChange={e => void selectProject(Number(e.target.value))}>{(workspace?.projects || []).map(item => <option key={item.id} value={item.id}>{item.name} · {item.version || 'v1.0.0'}</option>)}</select></label></div>
      <div className="ph-project-actions"><form onSubmit={createProject}><label>Nouveau projet<input value={newName} onChange={e => setNewName(e.target.value)} placeholder="Nom du projet" required /></label><label className="ph-check"><input type="checkbox" checked={copyCurrent} onChange={e => setCopyCurrent(e.target.checked)} />Copier le projet actif</label><button type="submit" disabled={busy || !newName.trim()}>Créer un projet</button></form><form onSubmit={renameProject}><label>Renommer le projet actif<input value={rename} onChange={e => setRename(e.target.value)} required /></label><button type="submit" disabled={busy || !rename.trim()}>Renommer</button></form></div></section></WorkspacePanel>
    <WorkspacePanel id="project:summary" title="Project summary"><section className="ph-metrics" aria-label="Résumé du projet">{counts.map(([label, value]) => <article key={label}><strong>{value}</strong><span>{label}</span></article>)}</section></WorkspacePanel>
    <WorkspacePanel id="project:recent-by-domain" title="Recent activity by domain"><section className="ph-recent-grid" aria-label="Activité récente par domaine">{highlightedActivity.map(row => <article key={row.key}><b>{row.label}</b>{row.latest ? <><span>{row.latest.name || row.latest.filename || 'Activité du projet'}</span><time>{formatDate(row.latest.at)}</time></> : <span className="ph-not-yet">Pas encore…</span>}</article>)}</section></WorkspacePanel>
    <WorkspacePanel id="project:exports" title="Exports and backups"><section className="ph-card ph-actions-card"><div className="ph-card-title"><div><h2>Exports et sauvegardes</h2><p>Préparez un export ou créez une sauvegarde du projet actif.</p></div></div><div className="ph-action-grid"><button type="button" onClick={() => void previewPlc()} disabled={busy}><b>Programme PLCopen complet</b><span>Prévisualiser, vérifier, puis télécharger le XML</span></button><button type="button" onClick={() => void downloadAction('/workspace/excel', 'Gaia-project.zip', 'Le paquet Excel a été téléchargé.')} disabled={busy}><b>Exporter les fichiers Excel</b><span>Télécharger les classeurs du projet</span></button><button type="button" onClick={() => void downloadAction('/workspace/backup', 'Gaia-project-backup.json', 'La sauvegarde JSON a été téléchargée.')} disabled={busy}><b>Télécharger une sauvegarde</b><span>Créer une copie JSON complète</span></button><label className="ph-restore"><b>Restaurer une sauvegarde</b><span>Choisissez un fichier JSON et vérifiez les changements avant de l’appliquer.</span><input type="file" accept=".json,application/json" onChange={(e: ChangeEvent<HTMLInputElement>) => { setRestoreFile(e.target.files?.[0] || null); setRestoreReview(null) }} /><button type="button" onClick={() => void previewRestore()} disabled={busy || !restoreFile}>Vérifier le fichier</button></label></div></section></WorkspacePanel>
    <WorkspacePanel id="project:history-activity" title="Version history and recent activity"><section className="ph-lower-grid"><article className="ph-card"><div className="ph-card-title"><div><h2>Historique des versions</h2><p>z : modification · y : import/export modifié · x : version majeure commentée.</p></div></div>{workspace?.history?.length ? <div className="ph-table-wrap"><table><thead><tr><th>Version</th><th>Date</th><th>Commentaire</th><th>Utilisateur</th></tr></thead><tbody>{workspace.history.map((entry, i) => <tr key={`${entry.version}-${i}`}><td><b>{entry.version || '—'}</b></td><td>{formatDate(entry.date)}</td><td>{entry.comment || '—'}</td><td>{entry.username || '—'}</td></tr>)}</tbody></table></div> : <p className="ph-empty">Aucune version historique.</p>}</article>
      <article className="ph-card"><div className="ph-card-title"><div><h2>Activité récente</h2><p>Changements et fichiers liés au projet actif.</p></div></div>{activityRows.length ? <ul className="ph-activity">{activityRows.slice(0, 12).map(row => <li key={row.key}><span className="ph-activity-dot"/><div><b>{row.label}</b><span>{row.item.name || row.item.filename || row.item.username || 'Activité du projet'}</span><time>{formatDate(row.item.at)}</time></div></li>)}</ul> : <p className="ph-empty">Aucune activité récente.</p>}</article></section></WorkspacePanel>
    {workspace?.is_admin && <WorkspacePanel id="project:administration" title="User administration"><section className="ph-card ph-admin"><button type="button" className="ph-admin-toggle" aria-expanded={adminOpen} onClick={() => void loadUsers()}><span><b>Administration des utilisateurs</b><small>Gérer les comptes qui ont accès aux projets</small></span><strong>{adminOpen ? '−' : '+'}</strong></button>{adminOpen && <div className="ph-admin-body"><form onSubmit={addUser}><label>Nom d’utilisateur<input value={username} autoComplete="off" onChange={e => setUsername(e.target.value)} required /></label><label>Mot de passe temporaire<input type="password" value={password} autoComplete="new-password" onChange={e => setPassword(e.target.value)} required /></label><label className="ph-check"><input type="checkbox" checked={newAdmin} onChange={e => setNewAdmin(e.target.checked)} />Administrateur</label><button type="submit" disabled={busy || !username.trim() || !password}>Créer le compte</button></form>{users.length ? <div className="ph-user-list">{users.map(user => <div key={user.id}><b>{user.username}</b><span>{user.is_admin ? 'Administrateur' : 'Utilisateur'}</span><time>{formatDate(user.created_at)}</time></div>)}</div> : <p className="ph-empty">Aucun compte à afficher.</p>}</div>}</section></WorkspacePanel>}
    {majorOpen && <div className="ph-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) setMajorOpen(false) }}><section className="ph-modal" role="dialog" aria-modal="true" aria-labelledby="ph-major-title"><header><h2 id="ph-major-title">Créer une version majeure</h2><button type="button" aria-label="Fermer" onClick={() => setMajorOpen(false)}>×</button></header><form onSubmit={makeMajor}><p>La version actuelle <b>{workspace?.version || currentProject?.version || '—'}</b> deviendra une nouvelle version majeure. Ajoutez un commentaire à l’historique.</p><label>Commentaire obligatoire<textarea value={majorComment} onChange={e => setMajorComment(e.target.value)} required rows={3}/></label><footer><button type="button" onClick={() => setMajorOpen(false)}>Annuler</button><button type="submit" disabled={busy || !majorComment.trim()}>Créer la version</button></footer></form></section></div>}
    {review && <div className="ph-backdrop"><section className="ph-modal ph-review-modal" role="dialog" aria-modal="true" aria-labelledby="ph-review-title"><header><h2 id="ph-review-title">Vérifier l’export PLCopen complet</h2><button type="button" aria-label="Fermer" onClick={() => setReview(null)}>×</button></header><div className="ph-modal-body"><p>{review.included?.length || 0} objets inclus</p>{review.changes?.length ? <ul>{review.changes.map((item, i) => <li key={i}>{errorMessage(item)}</li>)}</ul> : null}{review.warnings?.length ? <section className="ph-warnings"><h3>Avertissements</h3>{review.warnings.map((item, i) => <p key={i}>{errorMessage(item)}</p>)}</section> : null}{review.errors?.length ? <section className="ph-errors"><h3>Erreurs bloquantes</h3>{review.errors.map((item, i) => <p key={i}>{errorMessage(item)}</p>)}</section> : <p className="ph-success">Aucune erreur bloquante signalée.</p>}</div><footer><button type="button" onClick={() => setReview(null)}>Fermer</button><button type="button" onClick={() => void downloadPlc()} disabled={busy || !review.token || Boolean(review.errors?.length)}>Télécharger le programme</button></footer></section></div>}
    {restoreReview && <div className="ph-backdrop"><section className="ph-modal ph-review-modal" role="dialog" aria-modal="true" aria-labelledby="ph-restore-title"><header><h2 id="ph-restore-title">Vérifier la restauration</h2><button type="button" aria-label="Fermer" onClick={() => setRestoreReview(null)}>×</button></header><div className="ph-modal-body"><p>{restoreFile?.name}</p>{restoreReview.changes?.length ? <ul>{restoreReview.changes.map((item, i) => <li key={i}>{errorMessage(item)}</li>)}</ul> : <p>Aucun détail de changement n’a été fourni.</p>}{restoreReview.warnings?.map((item, i) => <p className="ph-warning-text" key={`w${i}`}>{errorMessage(item)}</p>)}{restoreReview.errors?.map((item, i) => <p className="ph-error-text" key={`e${i}`}>{errorMessage(item)}</p>)}</div><footer><button type="button" onClick={() => setRestoreReview(null)}>Annuler</button><button type="button" onClick={() => void applyRestore()} disabled={busy || !restoreReview.token || Boolean(restoreReview.errors?.length)}>Appliquer la restauration</button></footer></section></div>}
  </main>
}
