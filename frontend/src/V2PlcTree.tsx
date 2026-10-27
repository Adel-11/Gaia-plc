import { useEffect, useMemo, useRef, useState } from 'react'
import './plc-tree.css'
import { usePanelScope } from './WorkspacePanel'

export type PlcTreeObject = { name: string; kind: string; folder: string; type_id?: string; role?: string }
type TreeData = { revision: number; folders: string[]; objects: PlcTreeObject[] }
type Props = { projectKey: string; projectRevision?: number; csrf: string; onOpen: (object: PlcTreeObject) => void; onChanged?: () => void }

async function api<T>(path: string, method = 'GET', body?: unknown, csrf = ''): Promise<T> {
  const response = await fetch(`/api/v2${path}`, { method, credentials: 'same-origin', headers: { ...(body ? { 'Content-Type': 'application/json' } : {}), ...(csrf && method !== 'GET' ? { 'X-CSRF-Token': csrf } : {}) }, body: body ? JSON.stringify(body) : undefined })
  if (!response.ok) { let message = `${response.status} ${response.statusText}`; try { const data = await response.json(); message = data.detail || data.message || message } catch { /* response can be non-JSON */ }; throw new Error(message) }
  return response.json() as Promise<T>
}

export default function V2PlcTree({ projectKey, projectRevision, csrf, onOpen, onChanged }: Props) {
  const [data, setData] = useState<TreeData | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({}), [newFolderParent, setNewFolderParent] = useState<string | null>(null), [folderName, setFolderName] = useState(''), [moveTarget, setMoveTarget] = useState<Record<string, string>>({})
  const panelScope = usePanelScope()
  const dragged = useRef<{name:string;folder:boolean}|null>(null)
  const pointerDrag = useRef<{pointerId:number;name:string;folder:boolean;startX:number;startY:number;active:boolean}|null>(null)
  const [pointerGhost, setPointerGhost] = useState<{name:string;x:number;y:number}|null>(null)
  const [pointerDropTarget, setPointerDropTarget] = useState<string|null>(null)
  const startDrag = (event: React.DragEvent, name: string, folder: boolean) => {
    event.stopPropagation()
    if (pointerDrag.current) { event.preventDefault(); return }
    if (busy) { event.preventDefault(); return }
    const item = {name, folder}
    dragged.current = item
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('application/x-gaia-plc', JSON.stringify(item))
    event.dataTransfer.setData('text/plain', name)
  }
  const drop = (event: React.DragEvent, folder: string) => {
    event.preventDefault(); event.stopPropagation()
    let item = dragged.current
    try { const raw = event.dataTransfer.getData('application/x-gaia-plc'); if (raw) item = JSON.parse(raw) } catch { /* Ignore external drag payloads. */ }
    if (!busy && item && typeof item.name === 'string' && typeof item.folder === 'boolean') void place(item.name, folder, item.folder)
    dragged.current = null
  }
  const pointerDown = (event: React.PointerEvent<HTMLSpanElement>, name: string, folder: boolean) => {
    if (event.button !== 0 || busy) return
    event.preventDefault(); event.stopPropagation()
    event.currentTarget.setPointerCapture(event.pointerId)
    pointerDrag.current = { pointerId:event.pointerId, name, folder, startX:event.clientX, startY:event.clientY, active:false }
  }
  const pointerMove = (event: React.PointerEvent<HTMLSpanElement>) => {
    const current = pointerDrag.current
    if (!current || current.pointerId !== event.pointerId) return
    if (!current.active && Math.hypot(event.clientX-current.startX,event.clientY-current.startY) < 6) return
    current.active = true
    event.preventDefault(); event.stopPropagation()
    const target = document.elementFromPoint(event.clientX,event.clientY)?.closest<HTMLElement>('[data-plc-folder]')
    setPointerGhost({name:current.name,x:event.clientX,y:event.clientY})
    setPointerDropTarget(target?.getAttribute('data-plc-folder') ?? null)
  }
  const clearPointerDrag = (event?: React.PointerEvent<HTMLSpanElement>) => {
    if (event) { event.preventDefault(); event.stopPropagation() }
    pointerDrag.current = null; setPointerGhost(null); setPointerDropTarget(null)
  }
  const pointerUp = (event: React.PointerEvent<HTMLSpanElement>) => {
    const current = pointerDrag.current
    if (!current || current.pointerId !== event.pointerId) return
    event.preventDefault(); event.stopPropagation()
    const target = document.elementFromPoint(event.clientX,event.clientY)?.closest<HTMLElement>('[data-plc-folder]')
    const destination = target?.getAttribute('data-plc-folder')
    const previous = current.folder ? current.name.split('/').slice(0,-1).join('/') : data?.objects.find(object=>object.name===current.name)?.folder || ''
    const invalidFolderMove = current.folder && (destination === current.name || !!destination?.startsWith(`${current.name}/`) || destination === previous)
    const sameObjectFolder = !current.folder && destination === previous
    clearPointerDrag()
    if (current.active && destination != null && !invalidFolderMove && !sameObjectFolder) void place(current.name,destination,current.folder)
    try { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId) } catch { /* Pointer capture may already be released by the browser. */ }
  }
  const [lastMove, setLastMove] = useState<{name:string;previous:string;label:string;folder:boolean}|null>(null)
  const storageKey = `gaia-v2-folders:${panelScope}:${projectKey}`
  useEffect(() => { try { setCollapsed(JSON.parse(localStorage.getItem(storageKey) || '{}')) } catch { setCollapsed({}) } }, [storageKey])
  const refresh = async () => { setError(''); try { const next = await api<TreeData>('/plc/tree'); setData(next); return next } catch (e) { setError(e instanceof Error ? e.message : 'Impossible de charger l’explorateur'); return null } }
  useEffect(() => { void refresh() }, [projectRevision])
  const folderSet = useMemo(() => new Set(data?.folders || []), [data])
  const children = (parent: string) => [...folderSet].filter(path => (path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '') === parent).sort((a,b)=>a.localeCompare(b))
  const objects = (folder: string) => (data?.objects || []).filter(item => (item.folder || '') === folder).sort((a,b)=>a.name.localeCompare(b.name))
  const toggle = (path: string) => setCollapsed(old => { const next = { ...old, [path]: !old[path] }; try { localStorage.setItem(storageKey, JSON.stringify(next)) } catch { /* Keep the tree usable when storage is unavailable. */ }; return next })
  const submitFolder = async (event: React.FormEvent) => { event.preventDefault(); const path = [newFolderParent, folderName.trim()].filter(Boolean).join('/'); if (!folderName.trim() || !data || busy) return; setBusy(true); setError(''); try { await api('/plc/folders','POST',{ revision: data?.revision, path },csrf); setFolderName(''); setNewFolderParent(null); await refresh(); onChanged?.() } catch(e) { setError(e instanceof Error ? e.message : 'Création impossible') } finally { setBusy(false) } }
  const place = async (name: string, folder: string, isFolder = false, undo = false) => { if (!data || busy) return; const previous = isFolder ? name.split('/').slice(0,-1).join('/') : data.objects.find(o=>o.name===name)?.folder || ''; setBusy(true); setError(''); try { const result = isFolder ? await api<{ revision:number; folders:string[] }>('/plc/folders/move','POST',{revision:data?.revision,path:name,destination:folder},csrf) : await api<{revision:number}>('/plc/placement','POST',{revision:data?.revision,name,folder},csrf); await refresh(); onChanged?.(); setLastMove(undo ? null : {name:isFolder?[folder,name.split('/').pop()].filter(Boolean).join('/'):name,previous,label:name.split('/').pop()||name,folder:isFolder}); if (isFolder) setCollapsed(old => { const next: Record<string,boolean> = {}; const base = name.slice(name.lastIndexOf('/') + 1); for(const [key,value] of Object.entries(old)) { if(key === name || key.startsWith(`${name}/`)) { const suffix = key === name ? base : `${base}/${key.slice(name.length + 1)}`; next[[folder, suffix].filter(Boolean).join('/')]=value } else next[key]=value }; try { localStorage.setItem(storageKey, JSON.stringify(next)) } catch { /* Keep the tree usable when storage is unavailable. */ }; return next }); else setMoveTarget(old=>{const next={...old};delete next[name];return next}); void result } catch(e) { setMoveTarget(old=>{const next={...old};delete next[name];return next});setError(e instanceof Error ? e.message : 'Déplacement impossible') } finally { setBusy(false) } }
  const folderOptions = ['', ...(data?.folders || [])]
  const renderFolder = (path: string, depth: number): React.ReactNode => <div className="plc-tree-folder" data-plc-folder={path} key={path}>
    <div className="plc-tree-row folder-row" data-plc-folder={path} data-plc-drop-active={pointerDropTarget===path} style={{'--tree-depth':depth,...(pointerDropTarget===path?{outline:'2px solid #51836e',backgroundColor:'#eff8f2'}:{})} as React.CSSProperties} draggable onDragStart={e=>startDrag(e,path,true)} onDragEnd={()=>{dragged.current=null}} onDragOver={e=>{e.preventDefault();e.dataTransfer.dropEffect='move'}} onDrop={e=>drop(e,path)}>
      <button className="plc-tree-toggle" aria-label={`${collapsed[path]?'Déplier':'Replier'} ${path}`} aria-expanded={!collapsed[path]} onClick={()=>toggle(path)}>{collapsed[path]?'▸':'▾'}</button><span className="plc-tree-drag-handle" aria-hidden="true" title={`Déplacer le dossier ${path}`} style={{touchAction:'none'}} onPointerDown={e=>pointerDown(e,path,true)} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={clearPointerDrag} onLostPointerCapture={clearPointerDrag}>⠿</span><span className="plc-tree-folder-icon" aria-hidden="true">▰</span><strong>{path.slice(path.lastIndexOf('/')+1)}</strong><label className="plc-tree-move"><span className="sr-only">Déplacer le dossier {path} vers</span><select aria-label={`Déplacer le dossier ${path} vers`} disabled={busy} value="__choose" onChange={e=>{if(e.target.value!=='__choose')void place(path,e.target.value==='__root'?'':e.target.value,true)}}><option value="__choose">Déplacer…</option>{folderOptions.filter(target=>target!==path&&!target.startsWith(`${path}/`)&&target!==(path.includes('/')?path.slice(0,path.lastIndexOf('/')):'')).map(target=><option key={target||'root'} value={target||'__root'}>{target||'Racine'}</option>)}</select></label><button className="plc-tree-add" onClick={()=>{setNewFolderParent(path);setFolderName('')}}>＋ dossier</button>
    </div>
    {!collapsed[path] && <div>{newFolderParent===path && <form className="plc-tree-new-folder" onSubmit={e=>void submitFolder(e)}><input autoFocus aria-label={`Nom du dossier dans ${path}`} value={folderName} onChange={e=>setFolderName(e.target.value)} placeholder="Nom du dossier"/><button disabled={busy||!data||!folderName.trim()}>Créer</button><button type="button" onClick={()=>setNewFolderParent(null)}>Annuler</button></form>}{children(path).map(child=>renderFolder(child,depth+1))}{objects(path).map(object=>renderObject(object,depth+1))}</div>}
  </div>
  const renderObject = (object: PlcTreeObject, depth: number) => <div className="plc-tree-row object-row" key={`${object.kind}:${object.name}`} style={{'--tree-depth':depth} as React.CSSProperties} draggable onDragStart={e=>startDrag(e,object.name,false)} onDragEnd={()=>{dragged.current=null}}>
    <span className="plc-tree-drag-handle" title={`Faire glisser ${object.name}`} aria-hidden="true" style={{touchAction:'none'}} onPointerDown={e=>pointerDown(e,object.name,false)} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={clearPointerDrag} onLostPointerCapture={clearPointerDrag}>⠿</span><span className="plc-tree-kind">{object.kind}</span><button className="plc-tree-open" onClick={()=>onOpen(object)}>{object.name}</button><label className="plc-tree-move"><span className="sr-only">Déplacer {object.name}</span><select aria-label={`Déplacer ${object.name} vers un dossier`} value={moveTarget[object.name] ?? object.folder ?? ''} disabled={busy} onChange={e=>{setMoveTarget(old=>({...old,[object.name]:e.target.value}));void place(object.name,e.target.value)}}>{folderOptions.map(folder=><option key={folder} value={folder}>{folder || 'Racine'}</option>)}</select></label>
  </div>
  return <section className="plc-tree-panel" aria-label="Explorateur des objets CODESYS"><header className="plc-tree-header"><div><h2>Objets et dossiers</h2><p>{data?.objects.length ?? 0} objets · {data?.folders.length ?? 0} dossiers · Glissez un élément ou utilisez son menu de déplacement.</p></div><button type="button" disabled={busy||!data} onClick={()=>{setNewFolderParent('');setFolderName('')}}>＋ Nouveau dossier</button></header>
    {lastMove&&<div className="plc-tree-move-notice" role="status"><span>{lastMove.label} déplacé.</span><button disabled={busy} onClick={()=>void place(lastMove.name,lastMove.previous,lastMove.folder,true)}>Annuler ce déplacement</button></div>}
    {error&&<p className="plc-tree-error" role="alert">{error}</p>}{newFolderParent===''&&<form className="plc-tree-new-folder" onSubmit={e=>void submitFolder(e)}><input autoFocus aria-label="Nom du nouveau dossier" value={folderName} onChange={e=>setFolderName(e.target.value)} placeholder="Nom du dossier"/><button disabled={busy||!data||!folderName.trim()}>Créer</button><button type="button" onClick={()=>setNewFolderParent(null)}>Annuler</button></form>}
    <div className="plc-tree-root" data-plc-folder="" data-plc-drop-active={pointerDropTarget===''} style={pointerDropTarget===''?{outline:'2px solid #51836e',backgroundColor:'#eff8f2'}:{}} onDragOver={e=>{e.preventDefault();e.dataTransfer.dropEffect='move'}} onDrop={e=>drop(e,'')}><div className="plc-tree-application" data-plc-folder="" data-plc-drop-active={pointerDropTarget===''}><strong>Application</strong><span>Racine du projet</span></div>{children('').map(path=>renderFolder(path,0))}{objects('').map(object=>renderObject(object,0))}{!data?.objects.length&&!data?.folders.length&&<p className="plc-tree-empty">Aucun objet CODESYS à afficher.</p>}</div>
    {pointerGhost&&<div aria-hidden="true" style={{position:'fixed',left:pointerGhost.x+14,top:pointerGhost.y+14,zIndex:2000,pointerEvents:'none',padding:'7px 11px',borderRadius:7,background:'#203b4b',color:'#fff',boxShadow:'0 4px 14px #10212b55',fontSize:13,whiteSpace:'nowrap'}}>{pointerGhost.name}</div>}
  </section>
}
