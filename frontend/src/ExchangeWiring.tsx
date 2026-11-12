import { useEffect, useId, useRef, useState } from 'react'

type Member = { path: string; iec_type: string; member?: string; row?: number }
type Mapping = { id?: string; source: string; destination: string; condition?: { mode: string; terms: unknown[] } | null; else_value?: { value: string } | null }
type Props = {
  sources: Member[]; destinations: Member[]; sourceParameter: string; destinationParameter: string
  mappings: Mapping[]; armed: string | null; selected: string | null
  onArm: (path: string | null) => void; onConnect: (source: string, destination: string) => void
  onSelect: (id: string) => void
  bidirectional?: boolean
}

/** Ports and wires share the same SVG coordinates, including after scrolling. */
export default function ExchangeWiring({ sources, destinations, sourceParameter, destinationParameter, mappings, armed, selected, onArm, onConnect, onSelect, bidirectional = false }: Props) {
  const dragSource = useRef<string | null>(null)
  const viewport = useRef<HTMLDivElement>(null)
  const pan = useRef<{x:number; y:number; left:number; top:number} | null>(null)
  const [zoom,setZoom] = useState(1)
  const drawing = useId().replaceAll(':','')
  const scale = (factor: number) => setZoom(value => Math.max(.45, Math.min(2.8, value * factor)))
  const move = (left: number, top: number) => viewport.current?.scrollBy({left,top})
  useEffect(() => {
    const element = viewport.current
    if (!element) return
    const wheel = (event: WheelEvent) => {
      if (event.ctrlKey || event.metaKey) { event.preventDefault(); scale(event.deltaY < 0 ? 1.12 : 1 / 1.12) }
      else if (event.shiftKey && event.deltaY) { event.preventDefault(); move(event.deltaY, 0) }
    }
    element.addEventListener('wheel', wheel, {passive:false})
    return () => element.removeEventListener('wheel', wheel)
  }, [])
  const lastRow = Math.max(...[sources, destinations].flatMap(ports => ports.map((port, index) => port.row ?? index)), 0)
  const height = Math.max(220, 144 + lastRow * 64)
  const centerY = (index: number) => 90 + index * 64
  const connect = (path: string) => { if (armed) { onConnect(armed, path); onArm(null) } }
  const left = (path: string) => sources.some(port => port.path === path)
  const pick = (path: string) => {
    if (armed && left(armed) !== left(path)) connect(path)
    else onArm(armed === path ? null : path)
  }
  const startDrag = (event: React.PointerEvent<SVGGElement>, path: string) => {
    if (event.button !== 0) return
    event.preventDefault()
    dragSource.current = path
    event.currentTarget.ownerSVGElement?.setPointerCapture(event.pointerId)
  }
  const key = (mapping: Mapping, i: number) => mapping.id || `${mapping.source}:${mapping.destination}:${i}`
  return <div className="ex-wiring-frame">
    <div className="ex-wiring-zoom" aria-label="Commandes du dessin">
      <button type="button" onClick={() => scale(1 / 1.2)} aria-label="Dézoomer">−</button>
      <output>{Math.round(zoom * 100)} %</output>
      <button type="button" onClick={() => scale(1.2)} aria-label="Zoomer">+</button>
      <button type="button" onClick={() => { setZoom(1); viewport.current?.scrollTo(0,0) }} aria-label="Réinitialiser la vue">⌂</button>
      <button type="button" onClick={() => move(-230,0)} aria-label="Déplacer vers la gauche">←</button>
      <button type="button" onClick={() => move(230,0)} aria-label="Déplacer vers la droite">→</button>
    </div>
    <div ref={viewport} className="ex-wiring-diagram ex-wiring-viewport" role="region" tabIndex={0} aria-label="Dessin du mapping"
      onKeyDown={event => {
        if (event.target !== event.currentTarget) return
        const offsets: Record<string, [number, number]> = {ArrowLeft:[-100,0],ArrowRight:[100,0],ArrowUp:[0,-100],ArrowDown:[0,100]}
        if (offsets[event.key]) { event.preventDefault(); move(...offsets[event.key]) }
        if (event.key === '+' || event.key === '=') { event.preventDefault(); scale(1.2) }
        if (event.key === '-') { event.preventDefault(); scale(1 / 1.2) }
      }}>
    <svg viewBox={`0 0 1000 ${height}`} width={1000 * zoom} height={height * zoom}
      style={{width:1000 * zoom, minWidth:0, height:height * zoom}} className="ex-port-diagram" aria-label="Exchange member wiring"
      onPointerDown={e => {
        const element = viewport.current
        if (!element || e.button !== 0 || (e.target as Element).closest('[role="button"]')) return
        pan.current = {x:e.clientX,y:e.clientY,left:element.scrollLeft,top:element.scrollTop}
        e.currentTarget.setPointerCapture(e.pointerId)
      }}
      onPointerMove={e => {
        if (pan.current) viewport.current?.scrollTo(pan.current.left + pan.current.x - e.clientX, pan.current.top + pan.current.y - e.clientY)
      }} onPointerUp={e => {
      pan.current = null
      const source=dragSource.current; dragSource.current=null
      if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
      if (!source) return
      const attribute = bidirectional ? 'data-wiring-port' : 'data-destination-port'
      const destination=document.elementFromPoint(e.clientX,e.clientY)?.closest(`[${attribute}]`)?.getAttribute(attribute)
      if (destination && (!bidirectional || left(source) !== left(destination))) { onConnect(source,destination); onArm(null) }
      else if (bidirectional && destination === source) pick(source)
    }} onPointerCancel={()=>{dragSource.current=null;pan.current=null}}>
      <defs>
        <pattern id={`grid-${drawing}`} width="24" height="24" patternUnits="userSpaceOnUse"><path d="M24 0H0V24" fill="none" stroke="#e9e3d9" strokeWidth=".7"/></pattern>
        <marker id={`arrow-${drawing}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#a56319"/></marker>
      </defs>
      <rect width="1000" height={height} fill={`url(#grid-${drawing})`} className="ex-wiring-grid"/>
      <text x="20" y="28" className="ex-port-heading">{sourceParameter}</text>
      <text x="600" y="28" className="ex-port-heading">{destinationParameter}</text>
      {mappings.map((mapping, i) => {
        const fromLeft = sources.some(m => m.path === mapping.source)
        const si = (fromLeft ? sources : destinations).findIndex(m => m.path === mapping.source)
        const di = (fromLeft ? destinations : sources).findIndex(m => m.path === mapping.destination)
        if (si < 0 || di < 0) return null
        const x1 = fromLeft ? 400 : 600, x2 = fromLeft ? 600 : 400
        const y1 = centerY((fromLeft ? sources : destinations)[si].row ?? si)
        const y2 = centerY((fromLeft ? destinations : sources)[di].row ?? di)
        const id = key(mapping, i), y = (y1 + y2) / 2
        return <g key={id} className={`ex-actual-wire ${selected === id ? 'selected' : ''}`}>
          <path d={`M${x1} ${y1} C500 ${y1},500 ${y2},${x2} ${y2}`} markerEnd={`url(#arrow-${drawing})`}/>
          {mapping.condition && <g role="button" tabIndex={0} aria-label={`Edit condition for ${mapping.destination}`} onClick={() => onSelect(id)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(id) } }} className="ex-actual-condition">
            <rect x="414" y={y - 24} width="172" height="48" rx="6"/>
            <text x="500" y={y - 5}>IF {mapping.condition.mode.toUpperCase()} · {mapping.condition.terms.length}</text>
            <text x="500" y={y + 14}>ELSE {mapping.else_value ? mapping.else_value.value.slice(0, 18) : 'keep value'}</text>
            <title>{mapping.else_value ? `Else: ${mapping.else_value.value}` : 'No Else: keep the destination value'}</title>
          </g>}
        </g>
      })}
      {sources.map((m, index) => { const i = m.row ?? index; return <g key={m.path} role="button" tabIndex={0} data-wiring-port={m.path} aria-label={`${bidirectional ? '' : 'Source '}${m.row == null ? m.path : m.member || m.path}, ${m.iec_type}`} aria-pressed={armed === m.path} className={`ex-actual-port ${armed === m.path ? 'armed' : ''}`} onPointerDown={e => { startDrag(e, m.path); if (!bidirectional && e.button === 0) onArm(m.path) }} onClick={() => { if (!bidirectional) onArm(m.path) }} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (bidirectional) pick(m.path); else onArm(m.path) } }}>
        <rect x="20" y={centerY(i) - 26} width="380" height="52" rx="6"/>
        <text x="32" y={centerY(i) - 3}>{m.member || m.path.split('.').slice(1).join('.')}</text>
        <text x="32" y={centerY(i) + 17} className="ex-port-iec">{m.iec_type}</text>
        <circle cx="400" cy={centerY(i)} r="6"/><title>{m.path}</title>
      </g> })}
      {destinations.map((m, index) => { const i = m.row ?? index; return <g key={m.path} role="button" tabIndex={0} data-wiring-port={m.path} data-destination-port={m.path} aria-label={`${bidirectional ? '' : 'Destination '}${m.path}, ${m.iec_type}`} aria-pressed={bidirectional ? armed === m.path : undefined} className={`ex-actual-port destination ${armed === m.path ? 'armed' : ''}`} onPointerDown={e => { if (bidirectional) startDrag(e, m.path) }} onPointerUp={() => { if (!bidirectional) connect(m.path) }} onClick={() => { if (!bidirectional) connect(m.path) }} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (bidirectional) pick(m.path); else connect(m.path) } }}>
        <rect x="600" y={centerY(i) - 26} width="380" height="52" rx="6"/>
        <text x="612" y={centerY(i) - 3}>{m.member || m.path.split('.').slice(1).join('.')}</text>
        <text x="612" y={centerY(i) + 17} className="ex-port-iec">{m.iec_type}</text>
        <circle cx="600" cy={centerY(i)} r="6"/><title>{m.path}</title>
      </g> })}
    </svg>
    </div>
  </div>
}
