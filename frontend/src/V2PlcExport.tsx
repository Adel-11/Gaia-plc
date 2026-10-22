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
    let message = `${response.status} ${response.statusText}`
    try { const body = await response.json(); message = body.detail || body.message || body.error || message } catch { /* response may not be JSON */ }
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

export default function V2PlcExport({ csrf, projectRevision }: { csrf: string; projectRevision?: number }) {
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

  useEffect(() => { setPreview(null); setReviewed(false); setSelected([]); setSelectionEdited(false); request<Options>('/plc/options').then(data => { setOptions(data); setFocus(old => data.focus.some(item => item.value === old) ? old : data.focus?.[0]?.value || '') }).catch(e => setError(e instanceof Error ? e.message : 'Could not load PLC export options')) }, [projectRevision])
  const includedNames = useMemo(() => new Set((preview?.included || []).map(item => item.name)), [preview])
  const invalidate = () => { setPreview(null); setReviewed(false); setNotice('') }

  const createPreview = async (names?: string[]) => {
    setBusy(true); setError(''); setNotice(''); setPreview(null); setReviewed(false)
    try {
      const body: { mode: 'full' | 'partial'; focus?: string; selected?: string[] } = { mode }
      if (mode === 'partial') { if (focus) body.focus = focus; if (names) body.selected = names }
      const result = await request<Preview>('/plc/preview', { method: 'POST', body: JSON.stringify(body) }, csrf)
      setPreview(result); setSelected(result.included.map(item => item.name))
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not preview PLCopen export') } finally { setBusy(false) }
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
      if (!response.ok) { let message = `${response.status} ${response.statusText}`; try { const body = await response.json(); message = body.detail || body.message || message } catch { /* ignore */ }; throw new Error(message) }
      const blob = await response.blob()
      const disposition = response.headers.get('content-disposition') || ''
      const match = disposition.match(/filename\*?=(?:UTF-8''|\")?([^;\"\r\n]+)/i)
      const filename = match?.[1] ? decodeURIComponent(match[1].replaceAll('\"', '')) : kind === 'report' ? 'plcopen-export-summary.txt' : 'gaia-plcopen.xml'
      saveBlob(blob, filename); setNotice(kind === 'report' ? 'Export summary downloaded' : 'PLCopen XML downloaded')
    } catch (e) { setError(e instanceof Error ? e.message : 'Download failed') } finally { setBusy(false) }
  }

  const allErrors = preview?.errors || []
  const canDownload = Boolean(preview?.token && !allErrors.length && reviewed && !busy)
  const choices = preview?.available?.length ? preview.available : options?.objects || []
  return <>
    {error && <div className="v2-alert error" role="alert"><b>PLCopen export</b><span>{error}</span></div>}
    {notice && <div className="v2-alert success" role="status">{notice}</div>}
    <section className="v2-panel plc-export-panel"><div className="v2-panel-head"><div><p className="eyebrow">STAGE 2 · PLCOPEN</p><h2>PLCopen XML export</h2><p>Preview generated objects and manual CODESYS steps before downloading.</p></div><span className="phase-badge">Phase 2</span></div>
      <div className="plc-export-controls">
        <label className="v2-field"><span>Export mode</span><select value={mode} onChange={e => { setMode(e.target.value as 'full' | 'partial'); setSelected([]); setSelectionEdited(false); invalidate() }}><option value="full">Full project</option><option value="partial">Partial export</option></select></label>
        {mode === 'partial' && <label className="v2-field"><span>Focus object</span><select value={focus} onChange={e => { setFocus(e.target.value); setSelected([]); setSelectionEdited(false); invalidate() }}><option value="">Choose an object</option>{(options?.focus || []).map(item => <option key={item.value} value={item.value}>{item.label} · {item.kind}</option>)}</select></label>}
        <button className="button primary" onClick={() => void createPreview(mode === 'partial' && selectionEdited ? selected : undefined)} disabled={busy || !options || (mode === 'partial' && !focus)}>{busy ? 'Working…' : preview ? 'Refresh preview' : 'Preview export'}</button>
      </div>
      {mode === 'partial' && options && <div className="plc-object-picker"><div><h3>Objects to include</h3><p>{preview ? 'Adjust the proposed object list, then create a new preview. Any change clears the current export token.' : selected.length ? 'Selected objects will be included in the next preview.' : 'Choose a focus and preview to see the proposed objects. You can then adjust the list and preview again.'}</p></div><div className="plc-object-list">{choices.map(item => <label key={item.name}><input type="checkbox" checked={preview ? includedNames.has(item.name) : selected.includes(item.name)} onChange={e => toggleObject(item.name, e.target.checked)} /><span><b>{item.name}</b><small>{item.kind}{item.changed ? ' · changed' : ''}</small></span></label>)}{!choices.length && <p className="v2-empty">Object choices will appear after previewing the partial export.</p>}</div></div>}
      {preview && <div className="plc-export-preview"><div className="preview-heading"><div><h3>Export summary</h3><p>Project revision {preview.revision} · {preview.included.length} objects included</p></div><span className={`validation-pill ${allErrors.length ? 'bad' : ''}`}>{allErrors.length ? `${allErrors.length} errors` : 'No errors'}</span></div>
        <section className="plc-summary-section"><h4>Included objects <span>{preview.included.length}</span></h4>{preview.included.length ? <div className="v2-table-scroll"><table className="v2-table plc-summary-table"><thead><tr><th>Object</th><th>Kind</th><th>Reason included</th></tr></thead><tbody>{preview.included.map((item, i) => <tr key={`${item.name}-${i}`}><td><b>{item.name}</b></td><td>{item.kind}</td><td>{item.reason || '—'}</td></tr>)}</tbody></table></div> : <p className="v2-empty">No objects included.</p>}</section>
        <div className="plc-summary-lists"><section><h4>Manual deletions <span>{preview.manual_deletions.length}</span></h4>{preview.manual_deletions.length ? <ul>{preview.manual_deletions.map((item, i) => <li key={i}>{text(item)}</li>)}</ul> : <p className="v2-empty">None</p>}</section><section><h4>Manual actions <span>{preview.manual_actions.length}</span></h4>{preview.manual_actions.length ? <ul>{preview.manual_actions.map((item, i) => <li key={i}>{text(item)}</li>)}</ul> : <p className="v2-empty">None</p>}</section></div>
        <div className="plc-validation"><section><h4>Warnings <span>{preview.warnings.length}</span></h4>{preview.warnings.length ? preview.warnings.map((item, i) => <p className="issue warning-text" key={i}>{text(item)}</p>) : <p className="v2-empty">None</p>}</section><section><h4>Errors <span>{allErrors.length}</span></h4>{allErrors.length ? allErrors.map((item, i) => <p className="issue error-text" key={i}>{text(item)}</p>) : <p className="v2-empty">None</p>}</section></div>
        <div className="plc-copy-warning"><b>Import into a COPY of your CODESYS project and compile; generator is not a compiler</b></div>
        <label className="review-check"><input type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)} disabled={allErrors.length > 0} /><span>I reviewed the included objects, manual steps, warnings, and errors.</span></label>
        <div className="preview-actions"><button className="button secondary" onClick={() => void download('report')} disabled={busy || !preview.token}>Download summary</button><button className="button secondary" onClick={() => { setPreview(null); setReviewed(false); setNotice('') }}>Discard preview</button><button className="button primary" onClick={() => void download('download')} disabled={!canDownload}>{busy ? 'Preparing…' : 'Download PLCopen XML'}</button></div>
      </div>}
    </section>
  </>
}
