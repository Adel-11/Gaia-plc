import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { StEditor } from './V2ObjectView'
import './exchanges.css'
import ExchangeWiring from './ExchangeWiring'

type Member = { path: string; iec_type: string; member?: string }
type Operand = { kind: 'variable' | 'literal'; value: string }
type Term = { left: string; operator: string; right: Operand }
type Condition = { mode: 'all' | 'any'; terms: Term[] }
type Mapping = { id?: string; source: string; destination: string; condition: Condition | null; else_value: Operand | null }
type ExchangeFunction = { id: string; name: string; type_a_id: string; type_b_id: string; parameter_a: string; parameter_b: string; sheet?: string; custom_code?: string; members_a: Member[]; members_b: Member[]; mappings: Mapping[]; generated_st?: string }
type Instance = { id: string; name: string; type_id?: string; type?: string; type_name?: string }
type Data = { revision: number; functions: ExchangeFunction[]; calls: any[]; instances: Instance[]; types: any[]; layout?: Layout }
type Layout = { nodes: Record<string, { x: number; y: number }>; viewport: { x: number; y: number; zoom: number } }
type Preview = { token: string; errors?: string[]; warnings?: string[]; changes?: string[]; generated_st?: string; exchanges?: Data }
type Props = { csrf: string; projectRevision?: number; onSaved: () => void }
const operators = ['is_true', 'is_false', '=', '<>', '>', '>=', '<', '<=']
const emptyLayout: Layout = { nodes: {}, viewport: { x: 0, y: 0, zoom: 1 } }

async function api<T>(path: string, csrf: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/v2/exchanges${path}`, { method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', headers: body === undefined ? {} : { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: body === undefined ? undefined : JSON.stringify(body) })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || data.message || `Request failed (${response.status})`)
  return data as T
}
const display = (v: any) => String(v?.name ?? v?.id ?? '')
const compatible = (a?: string, b?: string) => Boolean(a && b && a.toUpperCase() === b.toUpperCase())
const defaultLiteral = (type?: string) => !type ? '0' : type.toUpperCase() === 'BOOL' ? 'FALSE' : /^(REAL|LREAL)$/.test(type.toUpperCase()) ? '0.0' : '0'
function newId() { return `m_${Math.random().toString(36).slice(2, 10)}` }

export default function V2Exchanges({ csrf, projectRevision, onSaved }: Props) {
  const [data, setData] = useState<Data | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const [selectedFn, setSelectedFn] = useState<string | null>(null)
  const [mapDraft, setMapDraft] = useState<Mapping[]>([]), [customCode, setCustomCode] = useState(''), [dirty, setDirty] = useState(false)
  const [mappingSource, setMappingSource] = useState(''), [mappingDestination, setMappingDestination] = useState('')
  const [wireReverse, setWireReverse] = useState(false), [staleDraft, setStaleDraft] = useState(false)
  const [sourcePort, setSourcePort] = useState<string | null>(null), [connectA, setConnectA] = useState(''), [connectB, setConnectB] = useState(''), [connectionFunction, setConnectionFunction] = useState('')
  const [selectedMapping, setSelectedMapping] = useState<string | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null), [layout, setLayout] = useState<Layout>(emptyLayout), [dragNode, setDragNode] = useState<{id:string; x:number;y:number; startX:number;startY:number}|null>(null)
  const [pan, setPan] = useState<{x:number;y:number;startX:number;startY:number}|null>(null), [dragInstance, setDragInstance] = useState<string | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const autoFitted = useRef(false)
  const [networkSource, setNetworkSource] = useState<string | null>(null)
  const [focusedInstance, setFocusedInstance] = useState<string | null>(null)
  const refresh = useCallback(async () => { setError(''); try { const next = await api<Data>('', csrf); setData(next); setLayout(next.layout || emptyLayout) } catch (e) { setError(e instanceof Error ? e.message : 'Could not load exchanges') } }, [csrf])
  useEffect(() => { void refresh() }, [refresh, projectRevision])
  const fn = data?.functions.find(f => String(f.id) === selectedFn) || null
  const sourceSide: 'a'|'b' = wireReverse ? 'b' : 'a', destinationSide: 'a'|'b' = wireReverse ? 'a' : 'b'
  const sourceMembers = fn ? (sourceSide === 'a' ? fn.members_a : fn.members_b) : []
  const destinationMembers = fn ? (destinationSide === 'a' ? fn.members_a : fn.members_b) : []
  const sourceParameter = fn ? (sourceSide === 'a' ? fn.parameter_a : fn.parameter_b) : ''
  const destinationParameter = fn ? (destinationSide === 'a' ? fn.parameter_a : fn.parameter_b) : ''
  const proposedSt = preview?.generated_st || preview?.exchanges?.functions?.find(f => String(f.id) === String(selectedFn))?.generated_st
  const lastLoadedFunction = useRef<string | null>(null), lastDataRevision = useRef<number | undefined>(undefined)
  useEffect(() => {
    const changedFunction = lastLoadedFunction.current !== selectedFn
    const changedRevision = lastDataRevision.current !== undefined && lastDataRevision.current !== data?.revision
    if (changedFunction) { lastLoadedFunction.current = selectedFn; setWireReverse(false); setMappingSource(''); setMappingDestination(''); setMapDraft(fn?.mappings?.map(m => ({ ...m, condition: m.condition ? { ...m.condition, terms: [...m.condition.terms] } : null })) || []); setCustomCode(fn?.custom_code || ''); setDirty(false); setStaleDraft(false); setPreview(null) }
    else if (changedRevision && dirty) { setStaleDraft(true); setPreview(null) }
    else if (changedRevision && fn) { setMapDraft(fn.mappings?.map(m => ({ ...m, condition: m.condition ? { ...m.condition, terms: [...m.condition.terms] } : null })) || []); setCustomCode(fn.custom_code || ''); setPreview(null) }
    lastDataRevision.current = data?.revision
  }, [selectedFn, data?.revision])
  const instances = data?.instances || [], functions = data?.functions || [], calls = data?.calls || []
  const instanceMap = useMemo(() => new Map(instances.map(i => [String(i.id), i])), [instances])
  const typeFor = (i?: Instance) => String(i?.type_id ?? i?.type ?? i?.type_name ?? '')
  const instName = (id: any) => display(instanceMap.get(String(id)) || { name: String(id ?? '') })
  const isUpi = (instance: Instance) => String(data?.types.find(t=>String(t.id)===String(instance.type_id))?.kind || '').toLowerCase().includes('upi')
  const pos = (id: string) => {
    if (layout.nodes[id]) return layout.nodes[id]
    const instance=instances.find(i=>String(i.id)===id)
    const upi=instance ? isUpi(instance) : false
    const index=instances.filter(i=>isUpi(i)===upi).findIndex(i=>String(i.id)===id)
    return {x:upi?40:600,y:55+Math.max(0,index)*160}
  }
  const fitCanvas = () => {
    const svg=svgRef.current
    if (!svg || !svg.clientWidth || !svg.clientHeight || !instances.length) return
    const points=instances.map(i=>pos(String(i.id)))
    const minX=Math.min(...points.map(p=>p.x)),minY=Math.min(...points.map(p=>p.y))
    const maxX=Math.max(...points.map(p=>p.x+184)),maxY=Math.max(...points.map(p=>p.y+90))
    const zoom=Math.max(.1,Math.min(1.4,(svg.clientWidth-48)/(maxX-minX),(svg.clientHeight-48)/(maxY-minY)))
    setLayout(l=>({...l,viewport:{zoom,x:(svg.clientWidth-(maxX-minX)*zoom)/2-minX*zoom,y:(svg.clientHeight-(maxY-minY)*zoom)/2-minY*zoom}}))
  }
  useEffect(()=>{
    const svg=svgRef.current
    if(!svg || !instances.length) return
    const observer=new ResizeObserver(()=>{
      if(!autoFitted.current && svg.clientWidth && !Object.keys(layout.nodes).length){autoFitted.current=true;fitCanvas()}
    })
    observer.observe(svg)
    return ()=>observer.disconnect()
  },[data?.revision])

  const viewport = layout.viewport || emptyLayout.viewport
  const callA = (c:any) => String(c.instance_a_id ?? c.instance_a ?? c.source_instance_id ?? '')
  const callB = (c:any) => String(c.instance_b_id ?? c.instance_b ?? c.destination_instance_id ?? '')
  const callFunction = (c:any) => functions.find(f => String(f.id) === String(c.function_id ?? c.exchange_id))
  const callId = (c:any) => String(c.id ?? `${callA(c)}:${callB(c)}`)
  const visibleCalls = focusedInstance ? calls.filter(c => callA(c) === focusedInstance || callB(c) === focusedInstance) : []
  const connectionColor = (c:any) => {
    const palette = ['#1769aa','#a33b20','#568322','#7946a5','#008577','#bd6418','#b02e62','#52616b']
    const index = visibleCalls.map(callId).sort().indexOf(callId(c))
    return palette[index] || `hsl(${(index * 137.508) % 360} 65% 37%)`
  }
  const stage = async (action: string, extra: any = {}, isLayout = false) => {
    if(dirty && action !== 'save_function'){setError('Save or discard your function edits before changing connections or layout.');return}
    setBusy(true); setError(''); setPreview(null)
    try {
      const body = { revision: data?.revision ?? projectRevision ?? 0, action, ...extra }
      const result = await api<Preview>('/preview', csrf, body)
      if (result.errors?.length) { setError(result.errors.join('\n')); return }
      if (isLayout) { if (!result.token) throw new Error('The server did not return a layout preview token'); await api('/apply', csrf, { token: result.token }); await refresh(); onSaved(); return }
      setPreview(result)
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not prepare change') } finally { setBusy(false) }
  }
  const apply = async () => { if (!preview?.token) return; setBusy(true); setError(''); try { await api('/apply', csrf, { token: preview.token }); setPreview(null); setDirty(false); await refresh(); onSaved() } catch (e) { setError(e instanceof Error ? e.message : 'Could not save') } finally { setBusy(false) } }
  const saveMappings = () => fn && void stage('save_function', { function_id: fn.id, mappings: mapDraft, custom_code: customCode })
  const connect = (a:string,b:string,functionId?:string) => { const ia=instanceMap.get(a), ib=instanceMap.get(b); if (!ia||!ib) return; void stage('connect',{instance_a_id:a,instance_b_id:b,...(functionId?{function_id:functionId}:{})}) }
  const addCallFromPalette = (id:string) => { if (!connectA) { setConnectA(id); return } if (connectA !== id) connect(connectA,id); setConnectA('') }
  const member = (fnc:ExchangeFunction, side:'a'|'b', path:string) => (side === 'a' ? fnc.members_a : fnc.members_b)?.find(m => m.path === path)
  const addMapping = (source:string,destination:string) => {
    const from = fn && member(fn,sourceSide,source), to = fn && member(fn,destinationSide,destination)
    if (!fn || !from || !to || !compatible(from.iec_type,to.iec_type)) return
    const exists = mapDraft.some(m => m.source === source && m.destination === destination)
    if (!exists) { setMapDraft([...mapDraft,{ id:newId(),source,destination,condition:null,else_value:null }]); setDirty(true) }
  }
  const chooseFunction = (id:string) => { if (id === selectedFn) return; if (dirty && !window.confirm('Discard unsaved mapping edits and open another shared function?')) return; setSelectedFn(id) }
  const saveLayout = () => void stage('layout',{ layout:{...layout,nodes:Object.fromEntries(instances.map(i=>[String(i.id),pos(String(i.id))]))} },true)
  const mousePoint = (e: React.MouseEvent) => { const rect=svgRef.current?.getBoundingClientRect(); return rect ? {x:(e.clientX-rect.left-viewport.x)/viewport.zoom,y:(e.clientY-rect.top-viewport.y)/viewport.zoom} : {x:0,y:0} }
  const pointerDownNode = (e:React.MouseEvent,id:string) => { if(e.button!==0)return; e.stopPropagation(); const p=mousePoint(e), n=pos(id); setDragNode({id,x:n.x,y:n.y,startX:p.x,startY:p.y}) }
  const pointerDownCanvas = (e:React.MouseEvent) => { if(e.button!==0)return; const p=mousePoint(e); setPan({x:viewport.x,y:viewport.y,startX:e.clientX,startY:e.clientY}); if (sourcePort) setSourcePort(null); void p }
  const pointerMove = (e:React.MouseEvent) => { if(dragNode){const p=mousePoint(e); setLayout(l=>({...l,nodes:{...l.nodes,[dragNode.id]:{x:dragNode.x+p.x-dragNode.startX,y:dragNode.y+p.y-dragNode.startY}}}))} else if(pan) setLayout(l=>({...l,viewport:{...l.viewport,x:pan.x+e.clientX-pan.startX,y:pan.y+e.clientY-pan.startY}})) }
  const pointerUp = () => { setDragNode(null); setPan(null) }
  const changeMapping=(i:number,patch:Partial<Mapping>)=>{if('condition' in patch && patch.condition===null)patch={...patch,else_value:null};setMapDraft(old=>old.map((m,n)=>n===i?{...m,...patch}:m));setDirty(true)}
  const addConditionTerm=(i:number)=>{const m=mapDraft[i];changeMapping(i,{condition:{mode:m.condition?.mode||'all',terms:[...(m.condition?.terms||[]),{left:'',operator:'is_true',right:{kind:'literal',value:'TRUE'}}]}})}

  return <section className="ex-page" aria-label="Exchanges">
    <header className="ex-header"><div><span className="ex-eyebrow">DATA EXCHANGE</span><h1>Exchanges</h1><p>Connect PLC instances and map the values passed between them.</p></div><div className="ex-head-actions"><span className="ex-count">{instances.length} instances · {functions.length} shared functions</span><button className="ex-button" disabled={busy||!dirty} onClick={saveMappings}>{busy?'Preparing…':'Preview function changes'}</button></div></header>
    {error && <div className="ex-alert" role="alert">{error}</div>}{staleDraft&&<div className="ex-alert" role="status">Project data changed while this function had unsaved edits. Review the latest shared function members before applying your draft.</div>}
    <div className="ex-workspace">
      <aside className="ex-palette"><div className="ex-panel-title"><div><span className="ex-eyebrow">PROJECT OBJECTS</span><h2>Instance palette</h2></div><span>{instances.length}</span></div><p className="ex-help">Drag an instance onto the canvas, or select it and click another instance to connect.</p>
        <div className="ex-instance-list">{instances.map(i=><button key={i.id} className={`ex-instance-item ${connectA===String(i.id)?'selected':''}`} draggable onDragStart={()=>setDragInstance(String(i.id))} onDragEnd={()=>setDragInstance(null)} onClick={()=>{const id=String(i.id);setFocusedInstance(id);addCallFromPalette(id)}}><span className="ex-instance-glyph">◇</span><span><b>{display(i)}</b><small>{data?.types?.find(t=>String(t.id)===typeFor(i))?.name || i.type_name || i.type || 'PLC type'}</small></span><span className="ex-grip">⠿</span></button>)}</div>
        <div className="ex-connect-box"><b>Connect instances</b><label>Instance A<select value={connectA} onChange={e=>setConnectA(e.target.value)}><option value="">Choose instance</option>{instances.map(i=><option key={i.id} value={i.id}>{display(i)}</option>)}</select></label><label>Instance B<select value={connectB} onChange={e=>setConnectB(e.target.value)}><option value="">Choose instance</option>{instances.map(i=><option key={i.id} value={i.id}>{display(i)}</option>)}</select></label><label>Shared exchange function<select aria-label="Function for new connection" value={connectionFunction} onChange={e=>setConnectionFunction(e.target.value)}><option value="">Create or reuse matching function</option>{functions.filter(f=>{const a=typeFor(instanceMap.get(connectA)),b=typeFor(instanceMap.get(connectB));return (String(f.type_a_id)===a&&String(f.type_b_id)===b)||(String(f.type_a_id)===b&&String(f.type_b_id)===a)}).map(f=><option key={f.id} value={f.id}>{f.name}</option>)}</select></label><button className="ex-button primary" disabled={!connectA||!connectB||connectA===connectB||busy} onClick={()=>{connect(connectA,connectB,connectionFunction);setConnectA('');setConnectB('');setConnectionFunction('')}}>＋ Connect instances</button></div>
        <div className="ex-shared-note"><b>Shared function</b><p>Each function is shared by every connection that uses it. Editing mappings or custom code changes all those call pairs.</p></div>
      </aside>
      <section className="ex-canvas-panel"><div className="ex-canvas-toolbar"><div><span className="ex-eyebrow">CONNECTION MAP</span><b>{focusedInstance?`${visibleCalls.length} connections · ${instName(focusedInstance)}`:'Select an instance to focus connections'}</b></div><div className="ex-canvas-tools"><button aria-label="Zoom out" onClick={()=>setLayout(l=>({...l,viewport:{...l.viewport,zoom:Math.max(.1,l.viewport.zoom-.1)}}))}>−</button><span>{Math.round(viewport.zoom*100)}%</span><button aria-label="Zoom in" onClick={()=>setLayout(l=>({...l,viewport:{...l.viewport,zoom:Math.min(4,l.viewport.zoom+.1)}}))}>＋</button><button type="button" onClick={fitCanvas}>Fit</button><button title="Save canvas positions" disabled={busy||dirty} onClick={saveLayout}>Save layout</button></div></div>
        <div className="ex-canvas-wrap"><svg ref={svgRef} className="ex-canvas" role="group" tabIndex={0} aria-label="Exchange connection canvas. Select an instance to focus its connections. Use plus and minus to zoom; arrow keys to pan; F to fit." onKeyDown={e=>{if(e.key==="+"){setLayout(l=>({...l,viewport:{...l.viewport,zoom:Math.min(4,l.viewport.zoom+.1)}}))}else if(e.key==="-"){setLayout(l=>({...l,viewport:{...l.viewport,zoom:Math.max(.1,l.viewport.zoom-.1)}}))}else if(e.key.toLowerCase()==="f"){fitCanvas()}else if(e.key.startsWith("Arrow")){const delta=e.shiftKey?32:14;setLayout(l=>({...l,viewport:{...l.viewport,x:l.viewport.x+(e.key==="ArrowLeft"?delta:e.key==="ArrowRight"?-delta:0),y:l.viewport.y+(e.key==="ArrowUp"?delta:e.key==="ArrowDown"?-delta:0)}}));e.preventDefault()}}} onMouseDown={pointerDownCanvas} onMouseMove={pointerMove} onMouseUp={pointerUp} onMouseLeave={pointerUp} onWheel={e=>{e.preventDefault();e.stopPropagation();setLayout(l=>({...l,viewport:{...l.viewport,zoom:Math.max(.1,Math.min(4,l.viewport.zoom+(e.deltaY<0?.05:-.05)))}}))}} onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault(); if(!dragInstance)return;const p=mousePoint(e as any);setLayout(l=>({...l,nodes:{...l.nodes,[dragInstance]:p}}));setDragInstance(null)}}>
          <defs><pattern id="ex-grid" patternTransform={`translate(${viewport.x} ${viewport.y}) scale(${viewport.zoom})`} width="24" height="24" patternUnits="userSpaceOnUse"><path d="M 24 0 L 0 0 0 24" fill="none" stroke="#eadfcf" strokeWidth=".7"/></pattern>{visibleCalls.map((c,i)=><marker key={callId(c)} id={`ex-arrow-${i}`} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill={connectionColor(c)}/></marker>)}</defs><rect width="100%" height="100%" fill="url(#ex-grid)"/>
          <g transform={`translate(${viewport.x} ${viewport.y}) scale(${viewport.zoom})`}>
            {visibleCalls.map((c,index)=>{const a=callA(c),b=callB(c),p1=pos(a),p2=pos(b),x=(p1.x+184+p2.x)/2,offset=((index%3)-1)*35,y=(p1.y+p2.y)/2+45+offset*.75,label=callFunction(c)?.name||'Exchange',color=connectionColor(c);return <g key={callId(c)} className="ex-link" style={{'--link-color':color} as React.CSSProperties} role="button" tabIndex={0} aria-label={label} onMouseDown={e=>e.stopPropagation()} onClick={e=>{e.stopPropagation();chooseFunction(String(c.function_id??c.exchange_id??''))}} onKeyDown={e=>{if(e.key==='Enter'){e.stopPropagation();chooseFunction(String(c.function_id??''))}}}><title>{label}: {instName(a)} to {instName(b)}</title><path d={`M ${p1.x+184} ${p1.y+45} C ${x} ${p1.y+45+offset}, ${x} ${p2.y+45+offset}, ${p2.x} ${p2.y+45}`} markerEnd={`url(#ex-arrow-${index})`}/><rect x={x-110} y={y-14} width="220" height="28" rx="6"/><text x={x} y={y+5}>{label.replace('FC_EXC_','').slice(0,29)}</text></g>})}
            {instances.map(i=>{const id=String(i.id),p=pos(id),portColor=visibleCalls.find(c=>callA(c)===id||callB(c)===id);return <g key={id} className={`ex-node ${focusedInstance===id?'focused':''}`} transform={`translate(${p.x} ${p.y})`} onMouseDown={e=>pointerDownNode(e,id)} onClick={e=>{e.stopPropagation();setFocusedInstance(current=>current===id?null:id)}}><rect width="184" height="90" rx="10"/><rect className="ex-node-top" width="184" height="5" rx="3"/><text className="ex-node-kicker" x="14" y="25">PLC INSTANCE</text><text className="ex-node-name" x="14" y="50">{display(i).slice(0,22)}</text><text className="ex-node-type" x="14" y="70">{data?.types?.find(t=>String(t.id)===typeFor(i))?.name||i.type_name||i.type||'Instance'}</text><circle className="ex-port" style={portColor?{stroke:connectionColor(portColor),fill:connectionColor(portColor)}:{}} cx="184" cy="45" r="6" onMouseDown={e=>e.stopPropagation()} onClick={e=>{e.stopPropagation();setNetworkSource(id)}}/><circle className="ex-port" style={portColor?{stroke:connectionColor(portColor),fill:connectionColor(portColor)}:{}} cx="0" cy="45" r="6" onMouseDown={e=>e.stopPropagation()} onClick={e=>{e.stopPropagation();if(networkSource && networkSource!==id){connect(networkSource,id);setNetworkSource(null)}else setNetworkSource(id)}}/></g>})}
          </g></svg>{!instances.length&&<div className="ex-canvas-empty">Create PLC instances first to build exchange connections.</div>}{!focusedInstance&&instances.length>0&&<div className="ex-canvas-hint">Select an instance to show its connections.</div>}{focusedInstance&&<div className="ex-canvas-hint">Showing {visibleCalls.length} connections for {instName(focusedInstance)} · <button onClick={()=>setFocusedInstance(null)}>Clear focus</button></div>}{networkSource&&<div className="ex-canvas-hint">Choose a destination port to connect {instName(networkSource)} <button onClick={()=>setNetworkSource(null)}>Cancel</button></div>}</div><div className="ex-link-legend" aria-label="Connection color legend">{visibleCalls.map(c=><span key={callId(c)}><i style={{background:connectionColor(c)}}/>{instName(callA(c))} ↔ {instName(callB(c))} · {callFunction(c)?.name||'Exchange'}</span>)}</div>
        <div className="ex-canvas-foot"><span><i className="ex-dot"/> Grid · drag to arrange · scroll to zoom</span><span>Connection lines select the shared function</span></div>
      </section>
    </div>
    <section className="ex-editor-section"><div className="ex-editor-heading"><div><span className="ex-eyebrow">FUNCTION EDITOR</span><h2>{fn?.name || 'Select a connection to edit its function'}</h2><p>{fn ? `${fn.parameter_a} : ${fn.type_a_id}  →  ${fn.parameter_b} : ${fn.type_b_id}` : 'Select a connection line above, or a function below.'}</p></div><label className="ex-function-picker">Shared exchange function<select aria-label="Shared exchange function" value={selectedFn||""} onChange={e=>chooseFunction(e.target.value)}><option value="">Choose function</option>{functions.map(f=><option key={f.id} value={f.id}>{f.name}</option>)}</select></label>{fn&&<span className="ex-scope-tag">Shared by {calls.filter(c=>String(c.function_id??c.exchange_id)===String(fn.id)).length} call pair(s)</span>}</div>
      {fn ? <><div className="ex-connected-pairs"><h3>Connected instance pairs</h3>{calls.filter(c=>String(c.function_id)===String(fn.id)&&(!focusedInstance||callA(c)===focusedInstance||callB(c)===focusedInstance)).map(c=><div key={callId(c)}><span>{instName(callA(c))} ↔ {instName(callB(c))}</span><button className="ex-button" disabled={busy||dirty} onClick={()=>void stage('disconnect',{call_id:callId(c)})}>Disconnect</button></div>)}<button className="ex-button" disabled={busy||dirty} onClick={()=>void stage('delete_function',{function_id:fn.id})}>Delete shared function</button>{dirty&&<button className="ex-button" onClick={()=>{setMapDraft(fn.mappings.map(m=>({...m})));setCustomCode(fn.custom_code||'');setDirty(false);setPreview(null);setStaleDraft(false)}}>Discard function edits</button>}</div><div className="ex-editor-grid"><div className="ex-wiring"><div className="ex-wiring-head"><b>Member wiring</b><small>Choose direction, then click or drag compatible ports.</small></div><div className="ex-direction"><span>Wiring direction</span><button className={!wireReverse?"active":""} onClick={()=>{setWireReverse(false);setSourcePort(null);setMappingSource("");setMappingDestination("")}}>{fn.parameter_a} → {fn.parameter_b}</button><button className={wireReverse?"active":""} onClick={()=>{setWireReverse(true);setSourcePort(null);setMappingSource("");setMappingDestination("")}}>{fn.parameter_b} → {fn.parameter_a}</button></div><div className="ex-map-select"><label>Output member<select aria-label="Output member" value={mappingSource} onChange={e=>setMappingSource(e.target.value)}><option value="">Select output</option>{sourceMembers.map(m=><option key={m.path} value={m.path}>{m.path} · {m.iec_type}</option>)}</select></label><label>Input member<select aria-label="Input member" value={mappingDestination} onChange={e=>setMappingDestination(e.target.value)}><option value="">Select compatible input</option>{destinationMembers.filter(m=>compatible(member(fn,sourceSide,mappingSource)?.iec_type,m.iec_type)).map(m=><option key={m.path} value={m.path}>{m.path} · {m.iec_type}</option>)}</select></label><button className="ex-button" disabled={!mappingSource||!mappingDestination} onClick={()=>{addMapping(mappingSource,mappingDestination);setMappingSource('');setMappingDestination('')}}>＋ Add mapping</button></div><ExchangeWiring sources={sourceMembers} destinations={destinationMembers} sourceParameter={sourceParameter} destinationParameter={destinationParameter} mappings={mapDraft} armed={sourcePort} selected={selectedMapping} onArm={setSourcePort} onConnect={addMapping} onSelect={setSelectedMapping}/><div className="ex-map-table-wrap"><table className="ex-map-table"><thead><tr><th>Output</th><th>Type</th><th>Input</th><th>Condition</th><th>Else value</th><th/></tr></thead><tbody>{mapDraft.map((m,i)=>{const sm=fn.members_a.some(x=>x.path===m.source)?member(fn,'a',m.source):member(fn,'b',m.source),dm=fn.members_a.some(x=>x.path===m.destination)?member(fn,'a',m.destination):member(fn,'b',m.destination);return <tr key={m.id||`${m.source}-${m.destination}`}><td>{m.source}</td><td><span className={`ex-type ${compatible(sm?.iec_type,dm?.iec_type)?'':'bad'}`}>{sm?.iec_type||'?'}</span></td><td>{m.destination}</td><td><button className="ex-text-button" onClick={()=>addConditionTerm(i)}>{m.condition?'＋ Add condition term':'＋ Add condition'}</button></td><td>{m.else_value?<span className="ex-else-edit"><select aria-label="Else value kind" value={m.else_value.kind} onChange={e=>changeMapping(i,{else_value:{...m.else_value!,kind:e.target.value as 'variable'|'literal'}})}><option value="variable">Variable</option><option value="literal">Literal</option></select>{m.else_value.kind==='variable'?<select aria-label="Else variable" value={m.else_value.value} onChange={e=>changeMapping(i,{else_value:{kind:'variable',value:e.target.value}})}><option value="">Choose variable</option>{[...fn.members_a,...fn.members_b].filter(x=>compatible(x.iec_type,dm?.iec_type)).map(x=><option key={x.path} value={x.path}>{x.path}</option>)}</select>:<input aria-label="Else value" value={m.else_value.value} onChange={e=>changeMapping(i,{else_value:{...m.else_value!,value:e.target.value}})}/>}<button className="ex-remove" aria-label="Remove else value" onClick={()=>changeMapping(i,{else_value:null})}>×</button></span>:<button className="ex-text-button" disabled={!m.condition} title={m.condition ? "Set a fallback value" : "Add a condition first"} onClick={()=>changeMapping(i,{else_value:{kind:'literal',value:defaultLiteral(dm?.iec_type)}})}>＋ Add</button>}</td><td><button className="ex-remove" aria-label="Remove mapping" onClick={()=>{setMapDraft(mapDraft.filter((_,n)=>n!==i));setDirty(true)}}>×</button></td></tr>})}</tbody></table>{mapDraft.map((m,i)=>m.condition&&<div className={`ex-condition ${selectedMapping===(m.id||`${m.source}->${m.destination}`)?"selected":""}`} id={`ex-condition-${m.id||i}`} key={`condition-${i}`}><b>Condition for {m.destination}</b><select value={m.condition.mode} onChange={e=>changeMapping(i,{condition:{...m.condition!,mode:e.target.value as 'all'|'any'}})}><option value="all">Require all terms</option><option value="any">Require any term</option></select>{m.condition.terms.map((t,j)=><div className="ex-term" key={j}><select aria-label="Condition left member" value={t.left} onChange={e=>{const terms=[...m.condition!.terms];terms[j]={...t,left:e.target.value};changeMapping(i,{condition:{...m.condition!,terms}})}}><option value="">Choose output</option>{[...fn.members_a,...fn.members_b].map(x=><option key={x.path} value={x.path}>{x.path}</option>)}</select><select aria-label="Condition operator" value={t.operator} onChange={e=>{const terms=[...m.condition!.terms];terms[j]={...t,operator:e.target.value};changeMapping(i,{condition:{...m.condition!,terms}})}}>{operators.map(op=><option key={op}>{op}</option>)}</select>{t.operator==='is_true'||t.operator==='is_false'?<span className="ex-boolean-word">{t.operator==='is_true'?'is TRUE':'is FALSE'}</span>:<><select aria-label="Right value kind" value={t.right.kind} onChange={e=>{const terms=[...m.condition!.terms];terms[j]={...t,right:{...t.right,kind:e.target.value as 'variable'|'literal'}};changeMapping(i,{condition:{...m.condition!,terms}})}}><option value="variable">Variable</option><option value="literal">Literal</option></select>{t.right.kind==='variable'?<select aria-label="Condition comparison variable" value={t.right.value} onChange={e=>{const terms=[...m.condition!.terms];terms[j]={...t,right:{kind:'variable',value:e.target.value}};changeMapping(i,{condition:{...m.condition!,terms}})}}><option value="">Choose variable</option>{[...fn.members_a,...fn.members_b].filter(x=>compatible(x.iec_type,[...fn.members_a,...fn.members_b].find(v=>v.path===t.left)?.iec_type)).map(x=><option key={x.path} value={x.path}>{x.path}</option>)}</select>:<input aria-label="Condition value" value={t.right.value} onChange={e=>{const terms=[...m.condition!.terms];terms[j]={...t,right:{...t.right,value:e.target.value}};changeMapping(i,{condition:{...m.condition!,terms}})}} placeholder="Literal value"/>}</>}<button className="ex-remove" onClick={()=>changeMapping(i,{condition:m.condition!.terms.length<=1?null:{...m.condition!,terms:m.condition!.terms.filter((_,n)=>n!==j)}})}>×</button></div>)}<button className="ex-text-button" onClick={()=>changeMapping(i,{condition:null})}>Remove condition</button></div>)}</div>
            <div className="ex-editor-notes"><span>Rows marked with amber types need a compatible member pair.</span><span>Use “＋ Add condition” to define typed conditions.</span></div></div><div className="ex-code-stack"><StEditor value={proposedSt||fn.generated_st||''} readOnly label="Generated Structured Text"/><StEditor value={customCode} onChange={v=>{setCustomCode(v);setDirty(true)}} label="Custom code · editable"/></div></div></> : <div className="ex-select-empty"><span>↗</span><b>Choose a connection</b><p>Click any canvas connection line to open its shared function and member mappings.</p>{functions.length>0&&<div className="ex-function-list">{functions.map(f=><button key={f.id} className={selectedFn===String(f.id)?'active':''} onClick={()=>chooseFunction(String(f.id))}>{f.name}<small>{f.parameter_a} → {f.parameter_b}</small></button>)}</div>}</div>}
    </section>
    {preview&&<div className="ex-preview-backdrop"><section className="ex-preview" role="dialog" aria-modal="true" aria-labelledby="ex-preview-title"><div className="ex-preview-top"><div><span className="ex-eyebrow">REVIEW CHANGES</span><h2 id="ex-preview-title">Preview exchange update</h2></div><button aria-label="Close preview" onClick={()=>setPreview(null)}>×</button></div>{preview.warnings?.length? <div className="ex-warning"><b>Warnings</b>{preview.warnings.map((w,i)=><p key={i}>{w}</p>)}</div>:null}<ul className="ex-changes">{preview.changes?.map((c,i)=><li key={i}>{c}</li>)}</ul>{proposedSt&&<details className="ex-preview-st"><summary>Generated Structured Text</summary><pre>{proposedSt}</pre></details>}<div className="ex-preview-footer"><button className="ex-button" onClick={()=>setPreview(null)}>Cancel</button><button className="ex-button primary" disabled={busy} onClick={()=>void apply()}>{busy?'Saving…':'Apply reviewed changes'}</button></div></section></div>}
  </section>
}
