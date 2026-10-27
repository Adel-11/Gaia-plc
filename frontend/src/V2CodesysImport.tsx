import { ChangeEvent, useMemo, useState } from 'react'
import WorkspacePanel from './WorkspacePanel'

type Change = {
  name: string; kind: string; status: string; identity_changed?: boolean; folder_changed?: boolean
  codesys_changed?: boolean; generator_changed?: boolean; conflict?: boolean
  before_st?: string | null; imported_st?: string | null; generated_st?: string | null; can_adopt?: boolean
}
type Preview = { token: string; revision: number; changes: Change[]; errors: string[]; warnings: string[] }
type Resolution = 'keep_generator' | 'adopt_codesys'

async function request<T>(path: string, options: RequestInit = {}, csrf = ''): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (csrf && options.method && options.method !== 'GET') headers.set('X-CSRF-Token', csrf)
  const response = await fetch(`/api/v2${path}`, { ...options, headers, credentials: 'same-origin' })
  if (!response.ok) {
    let message = `Erreur ${response.status}`
    try { const body = await response.json(); message = errorText(body.detail ?? body.message ?? body.error) || message } catch { /* response may not be JSON */ }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

function errorText(value: unknown): string {
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.map(errorText).filter(Boolean).join(' · ')
  if (value && typeof value === 'object') {
    const item = value as Record<string, unknown>
    return errorText(item.message ?? item.msg ?? item.detail ?? item.error ?? item.loc)
  }
  return ''
}

const isConflict = (item: Change) => item.conflict === true

export default function V2CodesysImport({ csrf, onApplied }: { csrf: string; onApplied: () => void }) {
  const [scope, setScope] = useState<'partial' | 'full'>('partial')
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [resolutions, setResolutions] = useState<Record<string, Resolution>>({})
  const [reviewed, setReviewed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const changes = preview?.changes || []
  const conflicts = useMemo(() => changes.filter(isConflict), [changes])
  const unresolved = conflicts.filter(item => !resolutions[item.name])
  const counts = useMemo(() => changes.reduce<Record<string, number>>((out, item) => { out[item.status] = (out[item.status] || 0) + 1; return out }, {}), [changes])
  const resetPreview = () => { setPreview(null); setResolutions({}); setReviewed(false) }
  const chooseFile = (event: ChangeEvent<HTMLInputElement>) => { setFile(event.target.files?.[0] || null); resetPreview(); setError(''); setNotice('') }
  const createPreview = async () => {
    if (!file) return
    setBusy(true); setError(''); setNotice(''); resetPreview()
    try {
      const form = new FormData(); form.append('file', file); form.append('scope', scope)
      const result = await request<Preview>('/codesys/preview', { method: 'POST', body: form }, csrf)
      setPreview(result)
    } catch (e) { setError(e instanceof Error ? e.message : 'Impossible de prévisualiser l’instantané CODESYS') } finally { setBusy(false) }
  }
  const apply = async () => {
    if (!preview?.token || preview.errors.length || unresolved.length || !reviewed) return
    setBusy(true); setError(''); setNotice('')
    try {
      await request('/codesys/apply', { method: 'POST', body: JSON.stringify({ token: preview.token, resolutions }) }, csrf)
      resetPreview(); setFile(null); setNotice('Instantané CODESYS appliqué ; les données du projet et les aperçus d’export PLC ont été actualisés.'); onApplied()
    } catch (e) { setError(e instanceof Error ? e.message : 'Impossible d’appliquer l’instantané CODESYS') } finally { setBusy(false) }
  }
  return <>
    {error && <div className="v2-alert error" role="alert"><b>Instantané CODESYS</b><span>{error}</span></div>}
    {notice && <div className="v2-alert success" role="status">{notice}</div>}
    <WorkspacePanel id="import:codesys" title="CODESYS snapshot review"><section className="v2-panel codesys-import-panel">
      <div className="v2-panel-head"><div><p className="eyebrow">RÉFÉRENCE · CODESYS</p><h2>Actualiser la référence CODESYS</h2><p>Importez le dernier instantané XML, vérifiez les changements d’objets, puis appliquez-le.</p></div><span className="phase-badge">Import</span></div>
      <div className="codesys-scope-note"><b>Périmètre de l’instantané</b><span>Le XML sert de référence pour comparer les objets d’ingénierie. La configuration des appareils et les tâches sont exclues. Un instantané complet ne remplace jamais vos classeurs d’ingénierie.</span></div>
      <div className="import-controls codesys-controls">
        <label className="v2-field"><span>Périmètre de l’instantané</span><select value={scope} onChange={e => { setScope(e.target.value as 'partial' | 'full'); resetPreview() }}><option value="partial">Instantané partiel (par défaut)</option><option value="full">Instantané complet</option></select></label>
        <div className="mode-explanation"><b>{scope === 'partial' ? 'Conserver les objets inconnus' : 'Signaler les absences explicites'}</b><span>{scope === 'partial' ? 'Les objets absents de cet export sont conservés ; leur absence n’est pas considérée comme une suppression.' : 'Les objets absents de l’instantané complet sont signalés comme supprimés et doivent être vérifiés.'}</span></div>
        <label className="file-pick button secondary">{file ? file.name : 'Choisir un fichier XML CODESYS'}<input aria-label="Fichier XML CODESYS" type="file" accept=".xml,text/xml,application/xml" onChange={chooseFile}/></label>
        <button className="button primary" onClick={() => void createPreview()} disabled={!file || busy}>{busy ? 'Traitement…' : 'Prévisualiser l’instantané'}</button>
      </div>
      {preview && <div className="import-preview codesys-preview">
        <div className="preview-heading"><div><h3>Vérification de l’instantané</h3><p>Révision du projet {preview.revision} · {changes.length} changements d’objets</p></div><span className={`validation-pill ${preview.errors.length ? 'bad' : ''}`}>{preview.errors.length ? `${preview.errors.length} erreurs` : 'Aucune erreur'}</span></div>
        {scope === 'full' && <p className="codesys-full-warning">Les absences dans l’instantané complet modifient uniquement la référence de comparaison. Les appareils et les tâches sont exclus ; les classeurs d’ingénierie restent inchangés.</p>}
        <div className="diff-summary"><div className="diff-tile"><b>{changes.length}</b><span>objets</span></div>{Object.entries(counts).map(([status, count]) => <div className="diff-tile" key={status}><b>{count}</b><span>{status.replaceAll('_', ' ')}</span></div>)}<div className="diff-tile modified"><b>{changes.filter(item => item.identity_changed).length}</b><span>changements d’identité</span></div></div>
        {conflicts.length > 0 && <section className="codesys-conflict-list"><h4>Conflits <span>{conflicts.length} · {unresolved.length} à résoudre</span></h4><p>Choisissez une résolution pour chaque conflit. Le XML importé actualise uniquement la référence de comparaison ; le modèle d’ingénierie reste inchangé. Conserver le modèle généré signifie qu’un futur export pourra remplacer la version CODESYS. Les déclarations structurées restent en lecture seule. L’adoption du code CODESYS est possible uniquement si l’interface du POU FB_ ou FNC_ n’a pas changé.</p>{conflicts.map(item => <article className="codesys-conflict" key={item.name}><div className="codesys-conflict-title"><b>{item.name}</b><span>{item.kind}</span></div><div className="codesys-resolution"><label><input type="radio" name={`resolution-${item.name}`} checked={resolutions[item.name] === 'keep_generator'} onChange={() => setResolutions(current => ({ ...current, [item.name]: 'keep_generator' }))}/><span>Conserver le modèle généré</span></label>{/^(FB|FNC)_/.test(item.name) && <label className={item.can_adopt ? '' : 'disabled'}><input type="radio" name={`resolution-${item.name}`} checked={resolutions[item.name] === 'adopt_codesys'} disabled={!item.can_adopt} onChange={() => setResolutions(current => ({ ...current, [item.name]: 'adopt_codesys' }))}/><span>Adopter le code CODESYS{!item.can_adopt ? ' · interface différente' : ''}</span></label>}</div></article>)}</section>}
        <div className="codesys-changes"><h4>Changements des objets</h4><div className="v2-table-scroll"><table className="v2-table"><thead><tr><th>Objet</th><th>Type</th><th>État</th><th>Champs modifiés</th></tr></thead><tbody>{changes.map((item, index) => <tr key={`${item.name}-${index}`}><td><b>{item.name}</b></td><td>{item.kind}</td><td>{item.status}</td><td>{[item.identity_changed && 'identité', item.folder_changed && 'dossier', item.codesys_changed && 'CODESYS', item.generator_changed && 'générateur', item.conflict && 'conflit'].filter(Boolean).join(', ') || '—'}</td></tr>)}{!changes.length && <tr><td colSpan={4} className="v2-empty">Aucun changement d’objet.</td></tr>}</tbody></table></div></div>
        {changes.filter(item => item.before_st != null || item.imported_st != null || item.generated_st != null).map((item, index) => <details className="codesys-compare" key={`${item.name}-compare-${index}`}><summary>{item.name} · Comparaison du texte structuré (lecture seule)</summary><div className="codesys-st-grid">{([['Avant', item.before_st], ['Importé depuis CODESYS', item.imported_st], ['Généré', item.generated_st]] as const).map(([label, value]) => <section key={label}><h5>{label}</h5><pre>{value ?? 'Aucun texte structuré disponible.'}</pre></section>)}</div></details>)}
        <div className="import-validation"><div><h4>Erreurs <span>{preview.errors.length}</span></h4>{preview.errors.map((item, i) => <p className="issue error-text" key={i}>{item}</p>)}{!preview.errors.length && <p className="v2-empty">Aucune erreur de validation.</p>}</div><div><h4>Avertissements <span>{preview.warnings.length}</span></h4>{preview.warnings.map((item, i) => <p className="issue warning-text" key={i}>{item}</p>)}{!preview.warnings.length && <p className="v2-empty">Aucun avertissement.</p>}</div></div>
        <label className="review-check"><input type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)} disabled={preview.errors.length > 0}/><span>J’ai vérifié les changements d’objets, les comparaisons de code et les avertissements.</span></label>
        <div className="preview-actions"><button className="button secondary" onClick={() => resetPreview()}>Supprimer l’aperçu</button><button className="button primary" onClick={() => void apply()} disabled={busy || preview.errors.length > 0 || unresolved.length > 0 || !reviewed}>{busy ? 'Application…' : 'Appliquer l’instantané'}</button></div>
      </div>}
    </section></WorkspacePanel>
  </>
}
