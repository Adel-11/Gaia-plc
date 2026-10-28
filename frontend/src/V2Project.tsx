import { useEffect, useMemo, useRef, useState } from 'react'
import TransferHub from './TransferHub'
import V2ObjectView, { StEditor } from './V2ObjectView'
import V2Communications from './V2Communications'
import V2Instances from './V2Instances'
import V2TypeWizard from './V2TypeWizard'
import V2TypeDelete from './V2TypeDelete'
import V2Exchanges from './V2Exchanges'
import V2Code from './V2Code'
import ProjectHome from './ProjectHome'
import V2PlcTree, { type PlcTreeObject } from './V2PlcTree'
import WorkspacePanel, { PanelPreferencesProvider } from './WorkspacePanel'

type Member = { position?: number | string; section?: string; name?: string; iec_type?: string; default_value?: string | number | boolean | null; unit?: string; comment?: string; exchange?: string }
type TypeRow = { id: string; name: string; kind?: string; description?: string; communication?: string; duts?: { name: string; role?: string; members?: Member[] }[]; fb?: { name?: string; body?: string; custom_code?: string } }
type Instance = { id: string; name: string; type_id: string }
type Project = { [key: string]: any; revision?: number; types?: TypeRow[]; instances?: Instance[]; counts?: Record<string, number>; workbooks?: { filename: string }[]; validation?: { errors?: unknown[]; warnings?: unknown[] }; snapshot?: Record<string, any>; migration?: Record<string, any>; phase?: number }

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

const list = <T,>(value: T[] | undefined): T[] => value || []

const pages = [
  { id: 'overview', label: 'Vue d’ensemble', detail: 'État du projet', icon: '◫', title: 'Votre projet, en un regard', description: 'Retrouvez les contrôles, les chiffres clés et la référence CODESYS actuelle.' },
  { id: 'types', label: 'Types', detail: 'Modèles et code ST', icon: '▤', title: 'Types d’équipements', description: 'Ouvrez un type pour explorer ses DUT, modifier les membres et éditer le code de son FB.' },
  { id: 'instances', label: 'Instances', detail: 'Objets du projet', icon: '⌂', title: 'Instances du projet', description: 'Déclarez les blocs de vos instances. Configurez ensuite les communications et ajoutez les appels dans les GFC / GFB.' },
  { id: 'communications', label: 'Communications', detail: 'Modbus et E/S', icon: '⌁', title: 'Communications', description: 'Consultez les clients TCP, les lignes RTU et les affectations des entrées et sorties.' },
  { id: 'exchanges', label: 'Exchanges', detail: 'Connections and conditions', icon: '⇆', title: 'Equipment exchanges', description: 'Connect instances, wire their variables and define conditional copies.' },
  { id: 'generated', label: 'Code généré', detail: 'Appels GFC et GFB', icon: '⌘', title: 'Code généré', description: 'Consultez les objets calculés depuis le modèle et leurs différences avec la référence CODESYS.' },
  { id: 'functions', label: 'Fonctions', detail: 'Utilitaires et échanges', icon: 'ƒ', title: 'Fonctions du programme', description: 'Consultez et modifiez les fonctions utilitaires ; retrouvez les fonctions d’échange et leur mapping.' },
  { id: 'tree', label: 'Arborescence', detail: 'Objets CODESYS', icon: '⌘', title: 'Arborescence CODESYS', description: 'Parcourez les types, instances et objets du programme.' },
  { id: 'transfers', label: 'Imports / Exports', detail: 'Fichiers et sauvegardes', icon: '⇄', title: 'Imports / Exports', description: 'Importez vos données et préparez les fichiers du projet.' },
] as const
type PageId = typeof pages[number]['id']
const pageFromHash = (): PageId => { const hash=window.location.hash; if(hash==='#equipment')return 'types'; if(hash==='#parameters')return 'instances'; if(['#plc','#codesys','#workbooks','#exports'].includes(hash))return 'transfers'; return pages.find(page=>`#${page.id}`===hash)?.id||'overview' }

export default function V2Project({ csrf, onWorkspaceChanged }: { csrf: string; onWorkspaceChanged?: () => void }) {
  const [activePage, setActivePage] = useState<PageId>(pageFromHash)
  const [pageHistory, setPageHistory] = useState<PageId[]>([])
  useEffect(() => { const changed = () => setActivePage(pageFromHash()); window.addEventListener('hashchange', changed); return () => window.removeEventListener('hashchange', changed) }, [])
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({project: true, design: true, data: true, delivery: true, code: true})
  const [codeDirty, setCodeDirty] = useState<Record<string, boolean>>({})
  const pageScroll = useRef<Record<string, number>>({})
  const [openedCodePages, setOpenedCodePages] = useState<Record<string, boolean>>({})
  useEffect(() => { if (activePage === 'generated' || activePage === 'functions') setOpenedCodePages(old => old[activePage] ? old : {...old, [activePage]: true}) }, [activePage])
  const openPage = (id: PageId) => { if (id !== activePage && codeDirty[activePage] && !window.confirm('Le code contient des modifications non enregistrées. Quitter cette page ? Votre brouillon reste conservé.')) return; if (id !== activePage && activePage === 'types' && typeDraftDirty && !window.confirm('Le type contient des modifications non enregistrées. Quitter cette page ? Le brouillon restera ouvert.')) return; if (id !== activePage) { pageScroll.current[activePage] = window.scrollY; setPageHistory(old => [...old, activePage]); } setActivePage(id); window.location.hash = id; requestAnimationFrame(() => window.scrollTo({top:pageScroll.current[id] || 0})) }
  useEffect(() => { const group = activePage === 'overview' ? 'project' : ['types','instances','communications','exchanges','tree'].includes(activePage) ? 'design' : ['generated','functions'].includes(activePage) ? 'code' : 'delivery'; setExpandedGroups(old => ({...old, [group]: true})) }, [activePage])
  const [project, setProject] = useState<Project | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [query, setQuery] = useState('')
  const [typeKind, setTypeKind] = useState('all')
  const [typeDraftDirty, setTypeDraftDirty] = useState(false)
  const [typeOpenedFromTree, setTypeOpenedFromTree] = useState(false)
  const selectType = (id: string, fromTree = false) => { if (!typeDraftDirty || window.confirm('Discard unsaved type changes?')) { if (selectedTypeId && selectedTypeId !== id) setTypeSelectionHistory(old => [...old, selectedTypeId]); setTypeDraftDirty(false); setSelectedTypeId(id); setTypeOpenedFromTree(fromTree); return true } return false }
  const [selectedTypeId, setSelectedTypeId] = useState('')
  const [typeSelectionHistory, setTypeSelectionHistory] = useState<string[]>([])
  const [treeInstanceName, setTreeInstanceName] = useState('')
  const [treeCodeSelection, setTreeCodeSelection] = useState<{ category: 'generated' | 'functions'; name: string } | null>(null)
  const [treeCodeRequestVersion, setTreeCodeRequestVersion] = useState(0)
  const [treeStructureSelection, setTreeStructureSelection] = useState<{ typeId: string; name: string } | null>(null)
  const [treeStructureRequestVersion, setTreeStructureRequestVersion] = useState(0)
  const [treeInspector, setTreeInspector] = useState<{ name: string; kind: string; folder?: string; declaration?: string; body?: string } | null>(null)
  const [treeInspectorError, setTreeInspectorError] = useState('')
  const [treeInspectorOpen, setTreeInspectorOpen] = useState(false)
  const [treeInspectorLoading, setTreeInspectorLoading] = useState(false)
  const [wizardOpen, setWizardOpen] = useState(false)
  useEffect(() => { if (!selectedTypeId || activePage !== 'types' || wizardOpen) return; const frame = requestAnimationFrame(() => document.querySelector('.type-object-workspace-panel')?.scrollIntoView({block:'start'})); return () => cancelAnimationFrame(frame) }, [selectedTypeId, treeStructureRequestVersion])

  const [deletingType, setDeletingType] = useState<TypeRow | null>(null)
  const [sortOrder, setSortOrder] = useState('asc')
  const [busy, setBusy] = useState(false)
  const goBack = () => {
    if (wizardOpen) { setWizardOpen(false); return }
    if (treeInspectorOpen) { setTreeInspectorOpen(false); setTreeInspector(null); setTreeInspectorError(''); return }
    if (activePage === 'types' && selectedTypeId) { if (typeDraftDirty && !window.confirm('Discard unsaved type changes?')) return; setTypeDraftDirty(false); setSelectedTypeId(typeSelectionHistory[typeSelectionHistory.length - 1] || ''); setTypeSelectionHistory(old => old.slice(0, -1)); if (!typeSelectionHistory.length && typeOpenedFromTree) { setPageHistory(old=>old[old.length-1]==='tree'?old.slice(0,-1):old); setActivePage('tree'); window.location.hash='tree'; requestAnimationFrame(()=>window.scrollTo({top:pageScroll.current.tree||0})) } return }
    if (codeDirty[activePage] && !window.confirm('Le code contient des modifications non enregistrées. Quitter cette page ? Votre brouillon reste conservé.')) return
    const previous = pageHistory[pageHistory.length - 1]; if (!previous) return
    pageScroll.current[activePage] = window.scrollY; setPageHistory(old => old.slice(0, -1)); setActivePage(previous); window.location.hash = previous; requestAnimationFrame(() => window.scrollTo({top:pageScroll.current[previous] || 0}))
  }

  const load = async () => {
    setLoading(true); setError('')
    try {
      const data = await request<Project>('/project')
      setProject(data)
      setSelectedTypeId(old => data.types?.some(type => type.id === old) ? old : '')
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not load project') } finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [])


  const types = list(project?.types)
  const instances = list(project?.instances)
  const typeKinds = useMemo(() => [...new Set(types.map(type => type.kind).filter((kind): kind is string => Boolean(kind)))].sort(), [types])
  const filteredTypes = useMemo(() => types.filter(type => (typeKind === 'all' || type.kind === typeKind) && `${type.name} ${type.kind || ''} ${type.description || ''}`.toLowerCase().includes(query.toLowerCase())), [types, query, typeKind]).sort((a, b) => a.name.localeCompare(b.name) * (sortOrder === 'asc' ? 1 : -1))

  const errors = list(project?.validation?.errors)
  const downloadPost = async (path: string, fallbackName: string) => {
    if (busy) return
    setBusy(true); setError(''); setNotice('')
    try {
      const response = await fetch(`/api/v2${path}`, { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf } })
      if (!response.ok) { let message = `${response.status} ${response.statusText}`; try { const body = await response.json(); message = typeof body.detail === 'string' ? body.detail : body.detail?.errors?.slice(0,4).join('\n') || body.detail?.message || message } catch { /* non-JSON error */ }; throw new Error(message) }
      const blob = await response.blob(), disposition = response.headers.get('content-disposition') || '', match = disposition.match(/filename\*?=(?:UTF-8''|")?([^;"\r\n]+)/i)
      const name = match?.[1] ? decodeURIComponent(match[1].replaceAll('"', '')) : fallbackName
      const url = URL.createObjectURL(blob), anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click(); URL.revokeObjectURL(url); setNotice(`${name} downloaded`); await load()
    } catch (e) { setError(e instanceof Error ? e.message : 'Download failed') } finally { setBusy(false) }
  }
  const openTreeObject = (object: PlcTreeObject) => {
    if (object.type_id && !object.role) { setTreeInstanceName(object.name); openPage('instances'); return }
    if (object.type_id && object.role) { if (selectType(String(object.type_id), true)) { setTreeStructureSelection({ typeId: String(object.type_id), name: object.role.toUpperCase() === 'FB' ? '__fb' : object.name }); setTreeStructureRequestVersion(version => version + 1); openPage('types') } return }
    const matching = types.find(type => type.id === object.name || type.name === object.name)
    if (matching) { if (selectType(matching.id, true)) openPage('types'); return }
    if (['FUNCTION','FC','FNC','EXCHANGE'].includes(object.kind.toUpperCase())) {
      setTreeCodeSelection({ category: 'functions', name: object.name }); setTreeCodeRequestVersion(version => version + 1); openPage('functions'); return
    }
    if (['GFC','GFB','PROGRAM','ACTION','METHOD'].includes(object.kind.toUpperCase())) {
      setTreeCodeSelection({ category: 'generated', name: object.name }); setTreeCodeRequestVersion(version => version + 1); openPage('generated'); return
    }
    setTreeInspector(null); setTreeInspectorError(''); setTreeInspectorOpen(true); setTreeInspectorLoading(true)
    void request<{ name: string; kind: string; folder?: string; declaration?: string; body?: string }>(`/plc/object/${encodeURIComponent(object.name)}`).then(setTreeInspector).catch(e => setTreeInspectorError(e instanceof Error ? e.message : 'Impossible d’ouvrir cet objet')).finally(() => setTreeInspectorLoading(false))
  }



  if (loading && !project) return <div className="v2-content"><div className="loading-inline">Loading Project v2…</div></div>

  const phase = Math.max(1, project?.phase || 1)
  const active = pages.find(page => page.id === activePage) || pages[0]
  return <PanelPreferencesProvider scope={String(project?.project_id || project?.project_name || 'active')}><div className="page-content v2-content v2-workspace" data-page={activePage}>
    <div className="v2-workspace-layout">
      <nav className="v2-page-nav" aria-label="Pages du projet">
        <p className="v2-nav-heading">Espace projet</p>
        {[
          {id: 'project', label: 'Projet', ids: ['overview']},
          {id: 'design', label: 'Conception', ids: ['types', 'instances', 'communications', 'exchanges', 'tree']},
          {id: 'code', label: 'Programme', ids: ['generated', 'functions']},
          {id: 'delivery', label: 'Livrables', ids: ['transfers']},
        ].map(group => <section className="v2-nav-group" key={group.id}>
          <button className="v2-nav-group-toggle" aria-expanded={expandedGroups[group.id]} aria-controls={`nav-group-${group.id}`} onClick={() => setExpandedGroups(old => ({...old, [group.id]: !old[group.id]}))}><span>{group.label}</span><span aria-hidden="true">{expandedGroups[group.id] ? '⌄' : '›'}</span></button>
          <div id={`nav-group-${group.id}`} role="tablist" aria-label={group.label} aria-orientation="vertical" hidden={!expandedGroups[group.id]}>
          {pages.filter(page => group.ids.includes(page.id) && (page.id !== 'transfers' || phase >= 1)).map(page => <button key={page.id} type="button" role="tab" id={`tab-${page.id}`} aria-controls={`page-${page.id}`} aria-selected={activePage === page.id} tabIndex={activePage === page.id || (!group.ids.includes(activePage) && group.ids[0] === page.id) ? 0 : -1} className={`v2-page-tab ${activePage === page.id ? 'active' : ''}`} data-page={page.id} onClick={() => openPage(page.id)} onKeyDown={event => {
            const available = pages.filter(item => group.ids.includes(item.id) && (item.id !== 'transfers' || phase >= 1))
            const index = available.findIndex(item => item.id === page.id)
            const next = event.key === 'Home' ? 0 : event.key === 'End' ? available.length - 1 : ['ArrowDown', 'ArrowRight'].includes(event.key) ? (index + 1) % available.length : ['ArrowUp', 'ArrowLeft'].includes(event.key) ? (index - 1 + available.length) % available.length : -1
            if (next >= 0) { event.preventDefault(); openPage(available[next].id); document.getElementById(`tab-${available[next].id}`)?.focus() }
          }}><span className="v2-tab-icon" aria-hidden="true">{page.icon}</span><span className="v2-tab-label"><strong>{page.label}</strong><small>{page.detail}</small></span>{page.id === 'overview' && errors.length > 0 && <span className="v2-tab-count">{errors.length}</span>}</button>)}
          </div>
        </section>)}
        <div className="v2-nav-foot">Gaia Refinery<span>Révision {project?.revision ?? '—'}</span></div>
      </nav>
      <div className="v2-page-body">
        <header className="v2-page-banner"><div><p className="v2-page-kicker">Gaia PLC Generator</p><h1>{activePage === 'overview' ? project?.project_name || active.title : active.title}</h1><p>{active.description}</p></div><div className="v2-page-tools">{(treeInspectorOpen || (activePage === 'types' && selectedTypeId) || pageHistory.length > 0) && <button className="button secondary" onClick={goBack}>← Retour</button>}<button className="button secondary" onClick={() => void load()} disabled={loading}>{loading ? 'Actualisation…' : 'Actualiser'}</button></div></header>
        {error && <div className="v2-alert error" role="alert"><b>Could not complete request</b><span>{error}</span></div>}{notice && <div className="v2-alert success" role="status">{notice}</div>}
        <div className="v2-tab-panel" role="tabpanel" tabIndex={0} id="page-overview" aria-labelledby="tab-overview" hidden={activePage !== 'overview'}>
          {project && <ProjectHome csrf={csrf} project={project} onChanged={() => void load()} beforeProjectChange={() => (!typeDraftDirty && !wizardOpen && !Object.values(codeDirty).some(Boolean)) || window.confirm('Changer de projet ferme les formulaires et brouillons non enregistrés. Continuer ?')} onSelectProject={() => onWorkspaceChanged?.()} onOpenTransfers={() => openPage('transfers')} />}
        </div>
        <div className="v2-tab-panel" role="tabpanel" tabIndex={0} id="page-types" aria-labelledby="tab-types" hidden={activePage !== 'types'}>
          <div className="type-page-actions"><button className="button primary" onClick={()=>{if(!typeDraftDirty || window.confirm('Discard unsaved type changes and create a new type?')) setWizardOpen(true)}}>＋ Create type</button></div>
          {wizardOpen && project && <V2TypeWizard project={project} csrf={csrf} onChanged={()=>void load()} onCancel={()=>setWizardOpen(false)} onCreated={id=>{setWizardOpen(false);setTypeDraftDirty(false);setQuery('');setTypeKind('all');if(id)setSelectedTypeId(id);void load()}}/>}
          <div hidden={wizardOpen}>
          <WorkspacePanel id="type-catalogue" title="Types d’équipements" className="type-catalogue-workspace-panel"><section className="v2-panel object-catalogue">
            <div className="v2-panel-head"><div><h2>Equipment types</h2><p>Select a model to open its object view.</p></div><div className="catalogue-filters"><label className="v2-filter"><span>Kind</span><select aria-label="Filter types by kind" value={typeKind} onChange={e=>setTypeKind(e.target.value)}><option value="all">All kinds</option>{typeKinds.map(kind=><option key={kind}>{kind}</option>)}</select></label><input aria-label="Search types" value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search types"/><select aria-label="Sort names" value={sortOrder} onChange={e=>setSortOrder(e.target.value)}><option value="asc">A–Z</option><option value="desc">Z–A</option></select></div></div>
            <div className="v2-table-scroll object-list-scroll"><table className="v2-table"><thead><tr><th>Name</th><th>Kind</th><th>Instances</th><th>Members</th><th>Actions</th></tr></thead><tbody>{filteredTypes.map(type=><tr key={type.id} className={selectedTypeId===type.id?'active':''}><td><button className="object-open-link" onClick={()=>selectType(type.id, false)}>{type.name}</button><small>{type.description}</small></td><td>{type.kind}</td><td>{instances.filter(instance=>instance.type_id===type.id).length}</td><td>{(type.duts||[]).reduce((n,dut)=>n+(dut.members?.length||0),0)}</td><td><button className="button secondary" aria-label={`Exporter ${type.name} en XML`} disabled={busy} onClick={()=>void downloadPost(`/plc/export/type/${encodeURIComponent(type.id)}`,`${type.name}.xml`)}>Exporter XML</button> <button className="type-delete-button" aria-label={`Supprimer le type ${type.name}`} onClick={()=>{if(!typeDraftDirty || window.confirm('Fermer les modifications non enregistrées pour supprimer un type ?')) setDeletingType(type)}}>Supprimer</button></td></tr>)}{!filteredTypes.length&&<tr><td colSpan={5}>No matching types.</td></tr>}</tbody></table></div>
          </section></WorkspacePanel>
          {deletingType && project && <V2TypeDelete typeId={deletingType.id} typeName={deletingType.name} revision={project.revision!} csrf={csrf} onClose={()=>setDeletingType(null)} onDeleted={()=>{setDeletingType(null);setTypeDraftDirty(false);setNotice(`Type ${deletingType.name} supprimé du catalogue partagé.`);void load()}}/>}
          {selectedTypeId && <WorkspacePanel id={`type-object-${selectedTypeId}`} title={types.find(type=>type.id===selectedTypeId)?.name || 'Éditeur du type'} className="type-object-workspace-panel"><div className="type-editor-navigation"><button className="button secondary" onClick={goBack}>← {typeOpenedFromTree ? 'Retour à l’arborescence' : 'Retour au catalogue'}</button></div><V2ObjectView kind="type" objectId={selectedTypeId} initialStructure={treeStructureSelection?.typeId === selectedTypeId ? treeStructureSelection.name : undefined} initialStructureVersion={treeStructureRequestVersion} csrf={csrf} projectRevision={project?.revision} onDirtyChange={setTypeDraftDirty} onSaved={()=>void load()}/></WorkspacePanel>}
          </div>
        </div>
        <div className="v2-tab-panel" role="tabpanel" tabIndex={0} id="page-instances" aria-labelledby="tab-instances" hidden={activePage !== 'instances'}>
          {project && <><div className="type-page-actions"><button className="button secondary" disabled={busy} onClick={()=>void downloadPost('/plc/export/instances','instances-and-types.xml')}>Exporter les instances et leurs types en XML</button></div><WorkspacePanel id="instances:table" title="Instances du projet"><V2Instances project={project} csrf={csrf} openInstanceName={treeInstanceName} onSaved={()=>void load()} onCommunications={()=>openPage('communications')} onProgram={()=>openPage('generated')} onOpenInstanceHandled={()=>setTreeInstanceName('')}/></WorkspacePanel></>}
        </div>
        <div className="v2-tab-panel" role="tabpanel" tabIndex={0} id="page-communications" aria-labelledby="tab-communications" hidden={activePage !== 'communications'}>
          {project && <V2Communications csrf={csrf} onSaved={() => void load()} project={project} onOpenInstance={()=>openPage('instances')}/>}
        </div>
        <div className="v2-tab-panel" role="tabpanel" tabIndex={0} id="page-exchanges" aria-labelledby="tab-exchanges" hidden={activePage !== 'exchanges'}>
          {project && <V2Exchanges csrf={csrf} projectRevision={project.revision} onSaved={()=>void load()}/>}
        </div>
        <div className="v2-tab-panel" role="tabpanel" tabIndex={0} id="page-generated" aria-labelledby="tab-generated" hidden={activePage !== 'generated'}>
          {project && (activePage === 'generated' || openedCodePages.generated) && <V2Code category="generated" initialObject={treeCodeSelection?.category === 'generated' ? treeCodeSelection.name : undefined} initialObjectVersion={treeCodeRequestVersion} csrf={csrf} projectRevision={project.revision} onSaved={() => void load()} onOpenExchanges={() => openPage('exchanges')} onDirtyChange={dirty => setCodeDirty(old => old.generated === dirty ? old : {...old, generated: dirty})} />}
        </div>
        <div className="v2-tab-panel" role="tabpanel" tabIndex={0} id="page-functions" aria-labelledby="tab-functions" hidden={activePage !== 'functions'}>
          {project && (activePage === 'functions' || openedCodePages.functions) && <V2Code category="functions" initialObject={treeCodeSelection?.category === 'functions' ? treeCodeSelection.name : undefined} initialObjectVersion={treeCodeRequestVersion} csrf={csrf} projectRevision={project.revision} onSaved={() => void load()} onOpenExchanges={() => openPage('exchanges')} onDirtyChange={dirty => setCodeDirty(old => old.functions === dirty ? old : {...old, functions: dirty})} />}
        </div>
        <div className="v2-tab-panel" role="tabpanel" tabIndex={0} id="page-tree" aria-labelledby="tab-tree" hidden={activePage !== 'tree'}>
          {project && <WorkspacePanel id="plc-explorer" title="Explorateur CODESYS" className="plc-tree-workspace-panel"><V2PlcTree projectKey={String(project.project_id || project.project_name || 'active')} projectRevision={project.revision} csrf={csrf} onOpen={openTreeObject} onChanged={()=>void load()}/></WorkspacePanel>}
        </div>
        <div className="v2-tab-panel" role="tabpanel" tabIndex={0} id="page-transfers" aria-labelledby="tab-transfers" hidden={activePage !== 'transfers'}>
          {project && <TransferHub csrf={csrf} project={project} revision={project.revision} onChanged={()=>void load()}/>}
        </div>
    {treeInspectorOpen && <div className="plc-object-inspector-backdrop" role="presentation"><section className="plc-object-inspector" role="dialog" aria-modal="true" aria-labelledby="plc-object-inspector-title"><header><div><p className="eyebrow">CODESYS OBJECT</p><h2 id="plc-object-inspector-title">{treeInspector?.name || 'Objet PLC'}</h2><p>{treeInspector?.kind}{treeInspector?.folder ? ` · ${treeInspector.folder}` : ''} · lecture seule</p></div><button type="button" onClick={()=>{setTreeInspectorOpen(false);setTreeInspector(null);setTreeInspectorError('')}}>Retour</button></header><div className="plc-object-inspector-body">{treeInspectorLoading ? <p>Chargement de l’objet…</p> : treeInspectorError ? <div className="v2-alert error" role="alert">{treeInspectorError}</div> : treeInspector && <StEditor value={[treeInspector.declaration, treeInspector.body].filter(Boolean).join('\n\n')} readOnly label={`${treeInspector.name} · Structured Text`}/>}</div><footer><button type="button" className="button secondary" onClick={()=>{setTreeInspectorOpen(false);setTreeInspector(null);setTreeInspectorError('')}}>Fermer</button></footer></section></div>}
    <footer className="page-footer"><span>GAIA IO GENERATOR <b>·</b> PROJECT V2</span><span>Phase {project?.phase ?? 1} <i/> Connected</span></footer>
      </div>
    </div>
  </div></PanelPreferencesProvider>
}
