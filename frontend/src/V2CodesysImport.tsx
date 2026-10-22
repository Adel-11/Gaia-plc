import { ChangeEvent, useMemo, useState } from 'react'

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
    let message = `${response.status} ${response.statusText}`
    try { const body = await response.json(); const detail = body.detail || body.message || body.error; message = typeof detail === 'string' ? detail : detail?.message || JSON.stringify(detail) || message } catch { /* response may not be JSON */ }
    throw new Error(message)
  }
  return response.json() as Promise<T>
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
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not preview CODESYS snapshot') } finally { setBusy(false) }
  }
  const apply = async () => {
    if (!preview?.token || preview.errors.length || unresolved.length || !reviewed) return
    setBusy(true); setError(''); setNotice('')
    try {
      await request('/codesys/apply', { method: 'POST', body: JSON.stringify({ token: preview.token, resolutions }) }, csrf)
      resetPreview(); setFile(null); setNotice('CODESYS snapshot applied; project data and PLC export previews refreshed.'); onApplied()
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not apply CODESYS snapshot') } finally { setBusy(false) }
  }
  return <>
    {error && <div className="v2-alert error" role="alert"><b>CODESYS snapshot</b><span>{error}</span></div>}
    {notice && <div className="v2-alert success" role="status">{notice}</div>}
    <section className="v2-panel codesys-import-panel">
      <div className="v2-panel-head"><div><p className="eyebrow">STAGE 3 · CODESYS SNAPSHOT</p><h2>Refresh CODESYS reference</h2><p>Import the latest XML snapshot, review its object changes, then apply it.</p></div><span className="phase-badge">Phase 3</span></div>
      <div className="codesys-scope-note"><b>Snapshot scope</b><span>The XML provides a comparison baseline for engineering objects. Device configuration and tasks are excluded. A full snapshot never replaces your engineering workbooks.</span></div>
      <div className="import-controls codesys-controls">
        <label className="v2-field"><span>Snapshot scope</span><select value={scope} onChange={e => { setScope(e.target.value as 'partial' | 'full'); resetPreview() }}><option value="partial">Partial snapshot (default)</option><option value="full">Full snapshot</option></select></label>
        <div className="mode-explanation"><b>{scope === 'partial' ? 'Keep unknown objects' : 'Record explicit absence'}</b><span>{scope === 'partial' ? 'Objects missing from this export are retained; absence is not treated as a deletion.' : 'Objects absent from the full snapshot are reported as removals and require review.'}</span></div>
        <label className="file-pick button secondary">{file ? file.name : 'Choose CODESYS XML'}<input type="file" accept=".xml,text/xml,application/xml" onChange={chooseFile}/></label>
        <button className="button primary" onClick={() => void createPreview()} disabled={!file || busy}>{busy ? 'Working…' : 'Preview snapshot'}</button>
      </div>
      {preview && <div className="import-preview codesys-preview">
        <div className="preview-heading"><div><h3>Snapshot review</h3><p>Project revision {preview.revision} · {changes.length} object changes</p></div><span className={`validation-pill ${preview.errors.length ? 'bad' : ''}`}>{preview.errors.length ? `${preview.errors.length} errors` : 'No errors'}</span></div>
        {scope === 'full' && <p className="codesys-full-warning">Full snapshot absence affects the comparison baseline only. Devices and tasks are excluded; engineering workbooks remain unchanged.</p>}
        <div className="diff-summary"><div className="diff-tile"><b>{changes.length}</b><span>objects</span></div>{Object.entries(counts).map(([status, count]) => <div className="diff-tile" key={status}><b>{count}</b><span>{status.replaceAll('_', ' ')}</span></div>)}<div className="diff-tile modified"><b>{changes.filter(item => item.identity_changed).length}</b><span>identity changes</span></div></div>
        {conflicts.length > 0 && <section className="codesys-conflict-list"><h4>Conflicts <span>{conflicts.length} · {unresolved.length} unresolved</span></h4><p>Choose a resolution for every conflict. The imported XML updates the comparison baseline only; the engineering model stays unchanged. Keeping the generated model acknowledges that a future export may replace the CODESYS version. Structured declarations stay read only. CODESYS code adoption is available only where the FB_ or FNC_ POU interface is unchanged.</p>{conflicts.map(item => <article className="codesys-conflict" key={item.name}><div className="codesys-conflict-title"><b>{item.name}</b><span>{item.kind}</span></div><div className="codesys-resolution"><label><input type="radio" name={`resolution-${item.name}`} checked={resolutions[item.name] === 'keep_generator'} onChange={() => setResolutions(current => ({ ...current, [item.name]: 'keep_generator' }))}/><span>Keep generated model</span></label>{/^(FB|FNC)_/.test(item.name) && <label className={item.can_adopt ? '' : 'disabled'}><input type="radio" name={`resolution-${item.name}`} checked={resolutions[item.name] === 'adopt_codesys'} disabled={!item.can_adopt} onChange={() => setResolutions(current => ({ ...current, [item.name]: 'adopt_codesys' }))}/><span>Adopt CODESYS body{!item.can_adopt ? ' · interface differs' : ''}</span></label>}</div></article>)}</section>}
        <div className="codesys-changes"><h4>Object changes</h4><div className="v2-table-scroll"><table className="v2-table"><thead><tr><th>Object</th><th>Kind</th><th>Status</th><th>Changed fields</th></tr></thead><tbody>{changes.map((item, index) => <tr key={`${item.name}-${index}`}><td><b>{item.name}</b></td><td>{item.kind}</td><td>{item.status}</td><td>{[item.identity_changed && 'identity', item.folder_changed && 'folder', item.codesys_changed && 'CODESYS', item.generator_changed && 'generator', item.conflict && 'conflict'].filter(Boolean).join(', ') || '—'}</td></tr>)}{!changes.length && <tr><td colSpan={4} className="v2-empty">No object changes.</td></tr>}</tbody></table></div></div>
        {changes.filter(item => item.before_st != null || item.imported_st != null || item.generated_st != null).map((item, index) => <details className="codesys-compare" key={`${item.name}-compare-${index}`}><summary>{item.name} · Structured Text comparison (read only)</summary><div className="codesys-st-grid">{([['Before', item.before_st], ['Imported CODESYS', item.imported_st], ['Generated', item.generated_st]] as const).map(([label, value]) => <section key={label}><h5>{label}</h5><pre>{value ?? 'No ST available.'}</pre></section>)}</div></details>)}
        <div className="import-validation"><div><h4>Errors <span>{preview.errors.length}</span></h4>{preview.errors.map((item, i) => <p className="issue error-text" key={i}>{item}</p>)}{!preview.errors.length && <p className="v2-empty">No validation errors.</p>}</div><div><h4>Warnings <span>{preview.warnings.length}</span></h4>{preview.warnings.map((item, i) => <p className="issue warning-text" key={i}>{item}</p>)}{!preview.warnings.length && <p className="v2-empty">No warnings.</p>}</div></div>
        <label className="review-check"><input type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)} disabled={preview.errors.length > 0}/><span>I reviewed the object changes, code comparisons, and warnings.</span></label>
        <div className="preview-actions"><button className="button secondary" onClick={() => resetPreview()}>Discard preview</button><button className="button primary" onClick={() => void apply()} disabled={busy || preview.errors.length > 0 || unresolved.length > 0 || !reviewed}>{busy ? 'Applying…' : 'Apply snapshot'}</button></div>
      </div>}
    </section>
  </>
}
