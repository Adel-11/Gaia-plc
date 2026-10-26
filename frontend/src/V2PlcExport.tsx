import { useEffect, useMemo, useState } from 'react'

type PlcObject = { name: string; kind: string; reason?: string; changed?: boolean }
type Options = { focus: { value: string; label: string; kind: string }[]; objects: PlcObject[] }
type Preview = { token: string; revision: number; included: PlcObject[]; available: PlcObject[]; manual_deletions: string[]; manual_actions: string[]; warnings: string[]; errors: string[] }

async function request<T>(path: string, options: RequestInit = {}, csrf = ''): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.body) headers.set('Content-Type', 'application/json')
  if (csrf && options.method && options.method !== 'GET') headers.set('X-CSRF-Token', csrf)
  const response = await fetch(`/api/v2${path}`, { ...options, headers, credentials: 'same-origin' })
  if (!response.ok) {
    let message = `Erreur ${response.status}`
    try { const body = await response.json(); message = errorText(body.detail ?? body.message ?? body.error) || message } catch { /* response may not be JSON */ }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url; anchor.download = filename; anchor.click()
  URL.revokeObjectURL(url)
}

const text = (value: unknown) => typeof value === 'string' ? value : String(value ?? '')
function errorText(value: unknown): string {
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.map(errorText).filter(Boolean).join(' · ')
  if (value && typeof value === 'object') {
    const item = value as Record<string, unknown>
    return errorText(item.message ?? item.msg ?? item.detail ?? item.error ?? item.loc)
  }
  return ''
}

export default function V2PlcExport({ csrf, projectRevision, onExported }: { csrf: string; projectRevision?: number; onExported?: () => void }) {
  const [options, setOptions] = useState<Options | null>(null)
  const [mode, setMode] = useState<'full' | 'partial'>('full')
  const [focus, setFocus] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [selectionEdited, setSelectionEdited] = useState(false)
  const [reviewed, setReviewed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => { setPreview(null); setReviewed(false); setSelected([]); setSelectionEdited(false); request<Options>('/plc/options').then(data => { setOptions(data); setFocus(old => data.focus.some(item => item.value === old) ? old : data.focus?.[0]?.value || '') }).catch(e => setError(e instanceof Error ? e.message : 'Impossible de charger les options d’export PLC')) }, [projectRevision])
  const includedNames = useMemo(() => new Set((preview?.included || []).map(item => item.name)), [preview])
  const invalidate = () => { setPreview(null); setReviewed(false); setNotice('') }

  const createPreview = async (names?: string[]) => {
    setBusy(true); setError(''); setNotice(''); setPreview(null); setReviewed(false)
    try {
      const body: { mode: 'full' | 'partial'; focus?: string; selected?: string[] } = { mode }
      if (mode === 'partial') { if (focus) body.focus = focus; if (names) body.selected = names }
      const result = await request<Preview>('/plc/preview', { method: 'POST', body: JSON.stringify(body) }, csrf)
      setPreview(result); setSelected(result.included.map(item => item.name))
    } catch (e) { setError(e instanceof Error ? e.message : 'Impossible de prévisualiser l’export PLCopen') } finally { setBusy(false) }
  }

  const toggleObject = (name: string, checked: boolean) => {
    setSelectionEdited(true)
    setSelected(current => checked ? [...new Set([...current, name])] : current.filter(item => item !== name))
    invalidate()
  }

  const download = async (kind: 'download' | 'report') => {
    if (!preview?.token || (kind === 'download' && (preview.errors.length || !reviewed))) return
    setBusy(true); setError(''); setNotice('')
    try {
      const response = await fetch(`/api/v2/plc/${kind}/${encodeURIComponent(preview.token)}`, { credentials: 'same-origin' })
      if (!response.ok) { let message = `Erreur ${response.status}`; try { const body = await response.json(); message = errorText(body.detail ?? body.message ?? body.error) || message } catch { /* ignore */ }; throw new Error(message) }
      const blob = await response.blob()
      const disposition = response.headers.get('content-disposition') || ''
      const match = disposition.match(/filename\*?=(?:UTF-8''|\")?([^;\"\r\n]+)/i)
      const filename = match?.[1] ? decodeURIComponent(match[1].replaceAll('\"', '')) : kind === 'report' ? 'plcopen-export-summary.txt' : 'gaia-plcopen.xml'
      saveBlob(blob, filename); if (kind === 'download') onExported?.(); setNotice(kind === 'report' ? 'Résumé de l’export téléchargé' : 'Fichier XML PLCopen téléchargé')
    } catch (e) { setError(e instanceof Error ? e.message : 'Échec du téléchargement') } finally { setBusy(false) }
  }

  const allErrors = preview?.errors || []
  const canDownload = Boolean(preview?.token && !allErrors.length && reviewed && !busy)
  const choices = preview?.available?.length ? preview.available : options?.objects || []
  return <>
    {error && <div className="v2-alert error" role="alert"><b>Export PLCopen</b><span>{error}</span></div>}
    {notice && <div className="v2-alert success" role="status">{notice}</div>}
    <section className="v2-panel plc-export-panel"><div className="v2-panel-head"><div><p className="eyebrow">EXPORT · PLCOPEN</p><h2>Exporter en XML PLCopen</h2><p>Prévisualisez les objets générés et les étapes manuelles CODESYS avant le téléchargement.</p></div><span className="phase-badge">Export</span></div>
      <div className="plc-export-controls">
        <label className="v2-field"><span>Mode d’export</span><select value={mode} onChange={e => { setMode(e.target.value as 'full' | 'partial'); setSelected([]); setSelectionEdited(false); invalidate() }}><option value="full">Projet complet</option><option value="partial">Export partiel</option></select></label>
        {mode === 'partial' && <label className="v2-field"><span>Objet de référence</span><select value={focus} onChange={e => { setFocus(e.target.value); setSelected([]); setSelectionEdited(false); invalidate() }}><option value="">Choisir un objet</option>{(options?.focus || []).map(item => <option key={item.value} value={item.value}>{item.label} · {item.kind}</option>)}</select></label>}
        <button className="button primary" onClick={() => void createPreview(mode === 'partial' && selectionEdited ? selected : undefined)} disabled={busy || !options || (mode === 'partial' && !focus)}>{busy ? 'Traitement…' : preview ? 'Actualiser l’aperçu' : 'Prévisualiser l’export'}</button>
      </div>
      {mode === 'partial' && options && <div className="plc-object-picker"><div><h3>Objets à inclure</h3><p>{preview ? 'Modifiez la liste proposée, puis créez un nouvel aperçu. Toute modification invalide le jeton d’export actuel.' : selected.length ? 'Les objets sélectionnés seront inclus dans le prochain aperçu.' : 'Choisissez un objet de référence et prévisualisez pour afficher les objets proposés. Vous pourrez ensuite ajuster la liste.'}</p></div><div className="plc-object-list">{choices.map(item => <label key={item.name}><input type="checkbox" checked={preview ? includedNames.has(item.name) : selected.includes(item.name)} onChange={e => toggleObject(item.name, e.target.checked)} /><span><b>{item.name}</b><small>{item.kind}{item.changed ? ' · modifié' : ''}</small></span></label>)}{!choices.length && <p className="v2-empty">Les objets proposés apparaîtront après la prévisualisation de l’export partiel.</p>}</div></div>}
      {preview && <div className="plc-export-preview"><div className="preview-heading"><div><h3>Récapitulatif de l’export</h3><p>Révision du projet {preview.revision} · {preview.included.length} objets inclus</p></div><span className={`validation-pill ${allErrors.length ? 'bad' : ''}`}>{allErrors.length ? `${allErrors.length} erreurs` : 'Aucune erreur'}</span></div>
        <section className="plc-summary-section"><h4>Objets inclus <span>{preview.included.length}</span></h4>{preview.included.length ? <div className="v2-table-scroll"><table className="v2-table plc-summary-table"><thead><tr><th>Objet</th><th>Type</th><th>Motif d’inclusion</th></tr></thead><tbody>{preview.included.map((item, i) => <tr key={`${item.name}-${i}`}><td><b>{item.name}</b></td><td>{item.kind}</td><td>{item.reason || '—'}</td></tr>)}</tbody></table></div> : <p className="v2-empty">Aucun objet inclus.</p>}</section>
        <div className="plc-summary-lists"><section><h4>Suppressions manuelles <span>{preview.manual_deletions.length}</span></h4>{preview.manual_deletions.length ? <ul>{preview.manual_deletions.map((item, i) => <li key={i}>{text(item)}</li>)}</ul> : <p className="v2-empty">Aucune</p>}</section><section><h4>Actions manuelles <span>{preview.manual_actions.length}</span></h4>{preview.manual_actions.length ? <ul>{preview.manual_actions.map((item, i) => <li key={i}>{text(item)}</li>)}</ul> : <p className="v2-empty">Aucune</p>}</section></div>
        <div className="plc-validation"><section><h4>Avertissements <span>{preview.warnings.length}</span></h4>{preview.warnings.length ? preview.warnings.map((item, i) => <p className="issue warning-text" key={i}>{text(item)}</p>) : <p className="v2-empty">Aucun</p>}</section><section><h4>Erreurs <span>{allErrors.length}</span></h4>{allErrors.length ? allErrors.map((item, i) => <p className="issue error-text" key={i}>{text(item)}</p>) : <p className="v2-empty">Aucune</p>}</section></div>
        <div className="plc-copy-warning"><b>Importez dans une COPIE de votre projet CODESYS et compilez-le ; le générateur ne remplace pas un compilateur.</b></div>
        <label className="review-check"><input type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)} disabled={allErrors.length > 0} /><span>J’ai vérifié les objets inclus, les étapes manuelles, les avertissements et les erreurs.</span></label>
        <div className="preview-actions"><button className="button secondary" onClick={() => void download('report')} disabled={busy || !preview.token}>Télécharger le récapitulatif</button><button className="button secondary" onClick={() => { setPreview(null); setReviewed(false); setNotice('') }}>Supprimer l’aperçu</button><button className="button primary" onClick={() => void download('download')} disabled={!canDownload}>{busy ? 'Préparation…' : 'Télécharger le XML PLCopen'}</button></div>
      </div>}
    </section>
  </>
}
