import { ChangeEvent, useEffect, useMemo, useState } from 'react'
import V2PlcExport from './V2PlcExport'
import V2CodesysImport from './V2CodesysImport'
import V2ObjectView from './V2ObjectView'
import V2Communications from './V2Communications'
import V2Instances from './V2Instances'
import V2TypeWizard from './V2TypeWizard'
import V2Exchanges from './V2Exchanges'

type Member = { position?: number | string; section?: string; name?: string; iec_type?: string; default_value?: string | number | boolean | null; unit?: string; comment?: string; exchange?: string }
type TypeRow = { id: string; name: string; kind?: string; description?: string; communication?: string; duts?: { name: string; role?: string; members?: Member[] }[]; fb?: { name?: string; body?: string; custom_code?: string } }
type Instance = { id: string; name: string; type_id: string }
type Workbook = { filename: string; sheets: { name: string; row_count?: number; headers?: string[] }[] }
type Project = { [key: string]: any; revision?: number; types?: TypeRow[]; instances?: Instance[]; counts?: Record<string, number>; workbooks?: Workbook[]; validation?: { errors?: unknown[]; warnings?: unknown[] }; snapshot?: Record<string, any>; migration?: Record<string, any>; phase?: number }
type SheetData = { filename: string; sheets: { name: string; state?: string; headers?: string[]; rows?: unknown[][] }[] }
type Preview = { token: string; filename?: string; diff?: { added?: unknown[]; modified?: unknown[]; removed?: unknown[] }; validation?: { errors?: unknown[]; warnings?: unknown[] } }

async function request<T>(path: string, options: RequestInit = {}, csrf = ''): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (csrf && options.method && options.method !== 'GET') headers.set('X-CSRF-Token', csrf)
  const response = await fetch(`/api/v2${path}`, { ...options, headers, credentials: 'same-origin' })
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try { const data = await response.json(); const detail = data.detail || data.message || data.error; message = typeof detail === 'string' ? detail : detail?.message || JSON.stringify(detail) || message } catch { /* response may not be JSON */ }
    throw new Error(message)
  }
  return (response.headers.get('content-type') || '').includes('application/json') ? response.json() as Promise<T> : response.blob() as T
}

const asText = (value: unknown) => value == null ? '—' : typeof value === 'string' ? value : JSON.stringify(value)
const list = <T,>(value: T[] | undefined): T[] => value || []
const pretty = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value, null, 2)

const pages = [
  { id: 'overview', label: 'Vue d’ensemble', detail: 'État du projet', icon: '◫', title: 'Votre projet, en un regard', description: 'Retrouvez les contrôles, les chiffres clés et la référence CODESYS actuelle.' },
  { id: 'types', label: 'Types', detail: 'Modèles et code ST', icon: '▤', title: 'Types d’équipements', description: 'Ouvrez un type pour explorer ses DUT, modifier les membres et éditer le code de son FB.' },
  { id: 'instances', label: 'Instances', detail: 'Objets du projet', icon: '⌂', title: 'Instances du projet', description: 'Déclarez vos instances et personnalisez leurs paramètres PA et leurs adresses IO ou Modbus.' },
  { id: 'communications', label: 'Communications', detail: 'Modbus et E/S', icon: '⌁', title: 'Communications', description: 'Consultez les clients TCP, les lignes RTU et les affectations des entrées et sorties.' },
  { id: 'exchanges', label: 'Exchanges', detail: 'Connections and conditions', icon: '⇆', title: 'Equipment exchanges', description: 'Connect instances, wire their variables and define conditional copies.' },
  { id: 'workbooks', label: 'Classeurs Excel', detail: 'Lecture et import', icon: '▦', title: 'Les données du projet', description: 'Consultez vos feuilles et prévisualisez chaque import avant de le valider.' },
  { id: 'plc', label: 'Génération PLC', detail: 'Programme et XML', icon: '⌘', title: 'Générer le programme', description: 'Préparez un export PLCopen complet ou partiel et vérifiez ses dépendances.' },
  { id: 'codesys', label: 'Référence CODESYS', detail: 'Comparer et actualiser', icon: '⇄', title: 'Rester en phase avec CODESYS', description: 'Comparez votre dernier XML au générateur et choisissez comment traiter les différences.' },
  { id: 'exports', label: 'Exports', detail: 'Fichiers et sauvegardes', icon: '↓', title: 'Emporter votre travail', description: 'Téléchargez les classeurs, les signaux FUXA ou une sauvegarde complète du projet.' },
] as const
type PageId = typeof pages[number]['id']
const pageFromHash = (): PageId => window.location.hash === '#equipment' ? 'types' : pages.find(page => `#${page.id}` === window.location.hash)?.id || 'overview'

export default function V2Project({ csrf }: { csrf: string }) {
  const [activePage, setActivePage] = useState<PageId>(pageFromHash)
  useEffect(() => { const changed = () => setActivePage(pageFromHash()); window.addEventListener('hashchange', changed); return () => window.removeEventListener('hashchange', changed) }, [])
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({project: true, design: true, data: true, delivery: true})
  const openPage = (id: PageId) => { setActivePage(id); window.location.hash = id }
  useEffect(() => { const group = activePage === 'overview' ? 'project' : ['types','instances','communications','exchanges'].includes(activePage) ? 'design' : activePage === 'workbooks' ? 'data' : 'delivery'; setExpandedGroups(old => ({...old, [group]: true})) }, [activePage])
  const [project, setProject] = useState<Project | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [query, setQuery] = useState('')
  const [typeKind, setTypeKind] = useState('all')
  const [typeDraftDirty, setTypeDraftDirty] = useState(false)
  const selectType = (id: string) => { if (!typeDraftDirty || window.confirm('Discard unsaved type changes?')) { setTypeDraftDirty(false); setSelectedTypeId(id) } }
  const [selectedTypeId, setSelectedTypeId] = useState('')
  const [wizardOpen, setWizardOpen] = useState(false)
  const [sortOrder, setSortOrder] = useState('asc')
  const [workbookName, setWorkbookName] = useState('')
  const [sheetName, setSheetName] = useState('')
  const [sheetData, setSheetData] = useState<SheetData | null>(null)
  const [workbookLoading, setWorkbookLoading] = useState(false)
  const [mode, setMode] = useState<'merge' | 'replace'>('merge')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [reviewed, setReviewed] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = async () => {
    setLoading(true); setError('')
    try {
      const data = await request<Project>('/project')
      setProject(data)
      setSelectedTypeId(old => data.types?.some(type => type.id === old) ? old : data.types?.[0]?.id || '')
      setWorkbookName(old => data.workbooks?.some(book => book.filename === old) ? old : data.workbooks?.[0]?.filename || '')
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not load project') } finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [])
  useEffect(() => {
    if (!workbookName) { setSheetData(null); return }
    let active = true
    setWorkbookLoading(true); setError('')
    request<SheetData>(`/workbooks/${encodeURIComponent(workbookName)}`).then(data => {
      if (!active) return
      setSheetData(data); setSheetName(old => data.sheets?.some(sheet => sheet.name === old) ? old : data.sheets?.[0]?.name || '')
    }).catch(e => { if (active) setError(e instanceof Error ? e.message : 'Could not read workbook') }).finally(() => { if (active) setWorkbookLoading(false) })
    return () => { active = false }
  }, [workbookName, project?.revision])

  const types = list(project?.types)
  const instances = list(project?.instances)
  const typeKinds = useMemo(() => [...new Set(types.map(type => type.kind).filter((kind): kind is string => Boolean(kind)))].sort(), [types])
  const filteredTypes = useMemo(() => types.filter(type => (typeKind === 'all' || type.kind === typeKind) && `${type.name} ${type.kind || ''} ${type.description || ''}`.toLowerCase().includes(query.toLowerCase())), [types, query, typeKind]).sort((a, b) => a.name.localeCompare(b.name) * (sortOrder === 'asc' ? 1 : -1))

  const currentSheet = sheetData?.sheets?.find(sheet => sheet.name === sheetName)
  const errors = list(project?.validation?.errors)
  const warnings = list(project?.validation?.warnings)
  const allErrors = list(preview?.validation?.errors)
  const allWarnings = list(preview?.validation?.warnings)
  const counts = project?.counts || {}
  const summary = [
    ['Equipment types', counts.types ?? counts.equipment_types ?? types.length, '▤'],
    ['Instances', counts.instances ?? counts.equipment_instances ?? instances.length, '⌂'],
    ['DUT members', counts.dut_members ?? counts.members ?? types.reduce((n, type) => n + (type.duts || []).reduce((m, dut) => m + (dut.members?.length || 0), 0), 0), '≋'],
    ['FUXA CSV tags', counts.fuxa_signals ?? counts.signals ?? '—', '⌁'],
    ['Exchange functions', counts.exchange_functions ?? counts.exchange ?? '—', '⇄'],
  ] as [string, number | string, string][]

  const download = async (path: string, fallbackName: string) => {
    setError(''); setNotice('')
    try {
      const response = await fetch(`/api/v2${path}`, { credentials: 'same-origin' })
      if (!response.ok) { let message = `${response.status} ${response.statusText}`; try { const body = await response.json(); message = body.detail || message } catch { /* ignore */ }; throw new Error(message) }
      const blob = await response.blob(); const disposition = response.headers.get('content-disposition') || ''
      const match = disposition.match(/filename\*?=(?:UTF-8''|\")?([^;\"\r\n]+)/i)
      const name = match?.[1] ? decodeURIComponent(match[1].replaceAll('"', '')) : fallbackName
      const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click(); URL.revokeObjectURL(url); setNotice(`${name} downloaded`)
    } catch (e) { setError(e instanceof Error ? e.message : 'Download failed') }
  }

  const chooseFile = (event: ChangeEvent<HTMLInputElement>) => { setFile(event.target.files?.[0] || null); setPreview(null); setReviewed(false); setNotice('') }
  const previewImport = async () => {
    if (!file) return
    setBusy(true); setError(''); setNotice(''); setPreview(null); setReviewed(false)
    try {
      const form = new FormData(); form.append('file', file); form.append('mode', mode)
      const result = await request<Preview>('/imports/preview', { method: 'POST', body: form }, csrf)
      setPreview(result)
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not preview workbook') } finally { setBusy(false) }
  }
  const applyImport = async () => {
    if (!preview?.token || allErrors.length || !reviewed) return
    setBusy(true); setError(''); setNotice('')
    try { await request('/imports/apply', { method: 'POST', body: JSON.stringify({ token: preview.token }) }, csrf); setPreview(null); setFile(null); setReviewed(false); setNotice('Workbook import applied'); await load() }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not apply import') } finally { setBusy(false) }
  }

  if (loading && !project) return <div className="v2-content"><div className="loading-inline">Loading Project v2…</div></div>

  const phase = Math.max(1, project?.phase || 1)
  const active = pages.find(page => page.id === activePage) || pages[0]
  return <div className="page-content v2-content v2-workspace" data-page={activePage}>
    <div className="v2-workspace-layout">
      <nav className="v2-page-nav" aria-label="Pages du projet">
        <p className="v2-nav-heading">Espace projet</p>
        {[
          {id: 'project', label: 'Projet', ids: ['overview']},
          {id: 'design', label: 'Conception', ids: ['types', 'instances', 'communications', 'exchanges']},
          {id: 'data', label: 'Données', ids: ['workbooks']},
          {id: 'delivery', label: 'Livrables', ids: ['plc', 'codesys', 'exports']},
        ].map(group => <section className="v2-nav-group" key={group.id}>
          <button className="v2-nav-group-toggle" aria-expanded={expandedGroups[group.id]} aria-controls={`nav-group-${group.id}`} onClick={() => setExpandedGroups(old => ({...old, [group.id]: !old[group.id]}))}><span>{group.label}</span><span aria-hidden="true">{expandedGroups[group.id] ? '⌄' : '›'}</span></button>
          <div id={`nav-group-${group.id}`} role="tablist" aria-label={group.label} aria-orientation="vertical" hidden={!expandedGroups[group.id]}>
          {pages.filter(page => group.ids.includes(page.id) && (page.id !== 'codesys' || phase >= 3)).map(page => <button key={page.id} type="button" role="tab" id={`tab-${page.id}`} aria-controls={`page-${page.id}`} aria-selected={activePage === page.id} tabIndex={activePage === page.id || (!group.ids.includes(activePage) && group.ids[0] === page.id) ? 0 : -1} className={`v2-page-tab ${activePage === page.id ? 'active' : ''}`} data-page={page.id} onClick={() => openPage(page.id)} onKeyDown={event => {
            const available = pages.filter(item => group.ids.includes(item.id) && (item.id !== 'codesys' || phase >= 3))
            const index = available.findIndex(item => item.id === page.id)
            const next = event.key === 'Home' ? 0 : event.key === 'End' ? available.length - 1 : ['ArrowDown', 'ArrowRight'].includes(event.key) ? (index + 1) % available.length : ['ArrowUp', 'ArrowLeft'].includes(event.key) ? (index - 1 + available.length) % available.length : -1
            if (next >= 0) { event.preventDefault(); openPage(available[next].id); document.getElementById(`tab-${available[next].id}`)?.focus() }
          }}><span className="v2-tab-icon" aria-hidden="true">{page.icon}</span><span className="v2-tab-label"><strong>{page.label}</strong><small>{page.detail}</small></span>{page.id === 'overview' && errors.length > 0 && <span className="v2-tab-count">{errors.length}</span>}</button>)}
          </div>
        </section>)}
        <div className="v2-nav-foot">Gaia Refinery<span>Révision {project?.revision ?? '—'}</span></div>
      </nav>
      <div className="v2-page-body">
        <header className="v2-page-banner"><div><p className="v2-page-kicker">Gaia PLC Generator</p><h1>{active.title}</h1><p>{active.description}</p></div><div className="v2-page-tools"><button className="button secondary" onClick={() => void load()} disabled={loading}>{loading ? 'Actualisation…' : 'Actualiser'}</button></div></header>
        {error && <div className="v2-alert error" role="alert"><b>Could not complete request</b><span>{error}</span></div>}{notice && <div className="v2-alert success" role="status">{notice}</div>}
        <div className="v2-tab-panel" role="tabpanel" tabIndex={0} id="page-overview" aria-labelledby="tab-overview" hidden={activePage !== 'overview'}>
    <section className="v2-metrics" aria-label="Project counts">{summary.map(([label,value,icon]) => <article className="v2-metric" key={label}><span className="v2-metric-icon">{icon}</span><div><b>{value}</b><span>{label}</span></div></article>)}</section>

    <section className="v2-meta-grid"><article className="v2-panel"><div className="v2-panel-head compact"><div><p className="eyebrow">VALIDATION</p><h2>Project checks</h2></div><span className={`validation-pill ${errors.length?'bad':''}`}>{errors.length?'Needs attention':'Current'}</span></div><div className="validation-summary"><div><b className={errors.length?'error-text':''}>{errors.length}</b><span>Errors</span></div><div><b>{warnings.length}</b><span>Warnings</span></div></div>{[...errors.map(item=>({item,kind:'error'})),...warnings.map(item=>({item,kind:'warning'}))].slice(0,8).map((entry,index)=><p className={`issue ${entry.kind==='error'?'error-text':'warning-text'}`} key={index}>{asText(entry.item)}</p>)}{!errors.length&&!warnings.length&&<p className="v2-empty">No project validation issues reported.</p>}</article><article className="v2-panel"><div className="v2-panel-head compact"><div><p className="eyebrow">SOURCE SNAPSHOT</p><h2>Import status</h2></div></div><dl className="snapshot-list"><dt>Snapshot</dt><dd>{project?.snapshot?.filename || '—'}</dd><dt>Imported</dt><dd>{project?.snapshot?.imported_at ? new Date(project.snapshot.imported_at).toLocaleString() : '—'}</dd><dt>Objects</dt><dd>{asText(project?.snapshot?.object_count)}</dd><dt>Legacy backup</dt><dd>{asText(project?.migration?.legacy_backup || project?.migration?.legacy_backup_filename)}</dd><dt>Revision</dt><dd>{asText(project?.revision)}</dd></dl></article></section>
        </div>
        <div className="v2-tab-panel" role="tabpanel" tabIndex={0} id="page-types" aria-labelledby="tab-types" hidden={activePage !== 'types'}>
          <div className="type-page-actions"><button className="button primary" onClick={()=>{if(!typeDraftDirty || window.confirm('Discard unsaved type changes and create a new type?')) setWizardOpen(true)}}>＋ Create type</button></div>
          {wizardOpen && project && <V2TypeWizard project={project} csrf={csrf} onCancel={()=>setWizardOpen(false)} onCreated={id=>{setWizardOpen(false);setTypeDraftDirty(false);setQuery('');setTypeKind('all');if(id)setSelectedTypeId(id);void load()}}/>}
          <div hidden={wizardOpen}>
          <section className="v2-panel object-catalogue">
            <div className="v2-panel-head"><div><h2>Equipment types</h2><p>Select a model to open its object view.</p></div><div className="catalogue-filters"><label className="v2-filter"><span>Kind</span><select aria-label="Filter types by kind" value={typeKind} onChange={e=>setTypeKind(e.target.value)}><option value="all">All kinds</option>{typeKinds.map(kind=><option key={kind}>{kind}</option>)}</select></label><input aria-label="Search types" value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search types"/><select aria-label="Sort names" value={sortOrder} onChange={e=>setSortOrder(e.target.value)}><option value="asc">A–Z</option><option value="desc">Z–A</option></select></div></div>
            <div className="v2-table-scroll object-list-scroll"><table className="v2-table"><thead><tr><th>Name</th><th>Kind</th><th>Communication</th><th>Instances</th><th>Members</th></tr></thead><tbody>{filteredTypes.map(type=><tr key={type.id} className={selectedTypeId===type.id?'active':''}><td><button className="object-open-link" onClick={()=>selectType(type.id)}>{type.name}</button><small>{type.description}</small></td><td>{type.kind}</td><td>{type.communication || '—'}</td><td>{instances.filter(instance=>instance.type_id===type.id).length}</td><td>{(type.duts||[]).reduce((n,dut)=>n+(dut.members?.length||0),0)}</td></tr>)}{!filteredTypes.length&&<tr><td colSpan={5}>No matching types.</td></tr>}</tbody></table></div>
          </section>
          {selectedTypeId && <V2ObjectView kind="type" objectId={selectedTypeId} csrf={csrf} projectRevision={project?.revision} onDirtyChange={setTypeDraftDirty} onSaved={()=>void load()}/>}
          </div>
        </div>
        <div className="v2-tab-panel" role="tabpanel" tabIndex={0} id="page-instances" aria-labelledby="tab-instances" hidden={activePage !== 'instances'}>
          {project && <V2Instances project={project} csrf={csrf} onSaved={()=>void load()}/>}
        </div>
        <div className="v2-tab-panel" role="tabpanel" tabIndex={0} id="page-communications" aria-labelledby="tab-communications" hidden={activePage !== 'communications'}>
          {project && <V2Communications project={project} onOpenInstance={()=>openPage('instances')}/>}
        </div>
        <div className="v2-tab-panel" role="tabpanel" tabIndex={0} id="page-exchanges" aria-labelledby="tab-exchanges" hidden={activePage !== 'exchanges'}>
          {project && <V2Exchanges csrf={csrf} projectRevision={project.revision} onSaved={()=>void load()}/>}
        </div>
        <div className="v2-tab-panel" role="tabpanel" tabIndex={0} id="page-workbooks" aria-labelledby="tab-workbooks" hidden={activePage !== 'workbooks'}>
    <section className="v2-panel workbook-panel"><div className="v2-panel-head"><div><p className="eyebrow">SOURCE WORKBOOKS</p><h2>Workbook reader</h2><p>Inspect source tables without downloading or changing the workbook.</p></div><div className="workbook-pickers"><label><span>Workbook</span><select value={workbookName} onChange={e=>setWorkbookName(e.target.value)}><option value="">Choose workbook</option>{(project?.workbooks||[]).map(book=><option key={book.filename} value={book.filename}>{book.filename}</option>)}</select></label><label><span>Sheet</span><select value={sheetName} onChange={e=>setSheetName(e.target.value)} disabled={!sheetData?.sheets?.length}><option value="">Choose sheet</option>{(sheetData?.sheets||[]).map(sheet=><option key={sheet.name} value={sheet.name}>{sheet.name}</option>)}</select></label></div></div>
      {workbookName && <div className="workbook-meta"><span>{workbookLoading ? 'Loading workbook…' : `${currentSheet?.rows?.length ?? (project?.workbooks||[]).find(book=>book.filename===workbookName)?.sheets.find(sheet=>sheet.name===sheetName)?.row_count ?? 0} rows`}</span><span>{currentSheet?.state || 'Read-only'}</span></div>}
      {currentSheet ? <div className="v2-table-scroll workbook-scroll"><table className="v2-table workbook-table"><thead><tr>{(currentSheet.headers||[]).map((header,index)=><th key={`${header}-${index}`}>{header}</th>)}</tr></thead><tbody>{(currentSheet.rows||[]).map((row,index)=><tr key={index}>{(Array.isArray(row)?row:[]).map((cell,i)=><td key={i}>{asText(cell)}</td>)}</tr>)}{!currentSheet.rows?.length&&<tr><td colSpan={Math.max(1,currentSheet.headers?.length||1)} className="v2-empty">This sheet has no data rows.</td></tr>}</tbody></table></div> : <div className="workbook-empty">{workbookLoading?'Loading sheet contents…':workbookName?'Choose a sheet to inspect its rows.':'No workbook selected.'}</div>}
    </section>

    <section className="v2-panel import-panel"><div className="v2-panel-head"><div><p className="eyebrow">CONTROLLED IMPORT</p><h2>Import an Excel workbook</h2><p>Upload a workbook, review its proposed changes, then apply the preview.</p></div></div><div className="import-controls"><label className="v2-field"><span>Import mode</span><select value={mode} onChange={e=>{setMode(e.target.value as 'merge'|'replace');setPreview(null);setReviewed(false)}}><option value="merge">Merge</option><option value="replace">Replace</option></select></label><div className={`mode-explanation ${mode}`}><b>{mode === 'merge' ? 'Merge existing data' : 'Replace selected workbook'}</b><span>{mode === 'merge' ? 'Add new rows and update matching rows. Unmatched existing rows are kept.' : 'Replace data in the selected workbook only. Other workbooks and project data remain unchanged. Review every proposed removal before applying.'}</span></div><label className="file-pick button secondary">{file ? file.name : 'Choose .xlsx workbook'}<input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={chooseFile}/></label><button className="button primary" onClick={()=>void previewImport()} disabled={!file||busy}>{busy?'Working…':'Preview import'}</button></div>
      {preview && <div className="import-preview"><div className="preview-heading"><div><h3>Preview: {preview.filename || file?.name}</h3><p>Review the row level changes and validation before applying this import.</p></div><span className={`validation-pill ${allErrors.length?'bad':''}`}>{allErrors.length ? `${allErrors.length} errors` : 'No errors'}</span></div><div className="diff-summary">{(['added','modified','removed'] as const).map(kind=><div className={`diff-tile ${kind}`} key={kind}><b>{list(preview.diff?.[kind]).length}</b><span>{kind}</span></div>)}</div><div className="diff-details">{(['added','modified','removed'] as const).map(kind=><section key={kind}><h4>{kind[0].toUpperCase()+kind.slice(1)} rows <span>{list(preview.diff?.[kind]).length}</span></h4>{list(preview.diff?.[kind]).length ? <details><summary>Show affected row details</summary><div className="diff-json-list">{list(preview.diff?.[kind]).map((item,index)=><pre key={index}>{pretty(item)}</pre>)}</div></details> : <p className="v2-empty">None</p>}</section>)}</div><div className="import-validation"><div><h4>Errors <span>{allErrors.length}</span></h4>{allErrors.map((item,index)=><p className="issue error-text" key={index}>{asText(item)}</p>)}{!allErrors.length&&<p className="v2-empty">No validation errors.</p>}</div><div><h4>Warnings <span>{allWarnings.length}</span></h4>{allWarnings.map((item,index)=><p className="issue warning-text" key={index}>{asText(item)}</p>)}{!allWarnings.length&&<p className="v2-empty">No warnings.</p>}</div></div><label className="review-check"><input type="checkbox" checked={reviewed} onChange={e=>setReviewed(e.target.checked)} disabled={allErrors.length>0}/><span>I reviewed the preview, including affected rows and warnings.</span></label><div className="preview-actions"><button className="button secondary" onClick={()=>{setPreview(null);setReviewed(false)}}>Discard preview</button><button className="button primary" onClick={()=>void applyImport()} disabled={busy||allErrors.length>0||!reviewed}>{busy?'Applying…':'Apply import'}</button></div></div>}
    </section>

        </div>
        <div className="v2-tab-panel" role="tabpanel" tabIndex={0} id="page-plc" aria-labelledby="tab-plc" hidden={activePage !== 'plc'}>
    <V2PlcExport csrf={csrf} projectRevision={project?.revision}/>
        </div>
        <div className="v2-tab-panel" role="tabpanel" tabIndex={0} id="page-codesys" aria-labelledby="tab-codesys" hidden={activePage !== 'codesys'}>

    {phase >= 3 && <V2CodesysImport key={project?.revision} csrf={csrf} onApplied={() => { void load() }} />}

        </div>
        <div className="v2-tab-panel" role="tabpanel" tabIndex={0} id="page-exports" aria-labelledby="tab-exports" hidden={activePage !== 'exports'}>
    <section className="v2-panel exports-panel"><div className="v2-panel-head"><div><p className="eyebrow">PROJECT OUTPUTS</p><h2>Exports &amp; backups</h2><p>Download the current project files for downstream tools and recovery.</p></div></div><div className="export-cards"><article><span className="export-icon blue">XLS</span><h3>Workbook exports</h3><p>Download a workbook from the project export set.</p><div className="export-file-list">{(project?.workbooks||[]).map(book=><button key={book.filename} onClick={()=>void download(`/exports/${encodeURIComponent(book.filename)}`,book.filename)}><span>{book.filename}</span><b>↓</b></button>)}{!project?.workbooks?.length&&<span className="v2-empty">No workbook exports listed.</span>}</div></article><article><span className="export-icon teal">CSV</span><h3>FUXA signals</h3><p>Export the current signal list for FUXA.</p><button className="button secondary" onClick={()=>void download('/exports/fuxa.csv','fuxa.csv')}>Download FUXA CSV <b>↓</b></button></article><article><span className="export-icon slate">JSON</span><h3>Full project backup</h3><p>Save a JSON backup of the full v2 project state.</p><button className="button secondary" onClick={()=>void download('/backup','gaia-project-backup.json')}>Download JSON backup <b>↓</b></button></article></div></section>

        </div>
    <footer className="page-footer"><span>GAIA IO GENERATOR <b>·</b> PROJECT V2</span><span>Phase {project?.phase ?? 1} <i/> Connected</span></footer>
      </div>
    </div>
  </div>
}
