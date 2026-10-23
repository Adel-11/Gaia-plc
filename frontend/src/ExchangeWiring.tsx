import { useRef } from 'react'

type Member = { path: string; iec_type: string; member?: string }
type Mapping = { id?: string; source: string; destination: string; condition?: { mode: string; terms: unknown[] } | null; else_value?: { value: string } | null }
type Props = {
  sources: Member[]; destinations: Member[]; sourceParameter: string; destinationParameter: string
  mappings: Mapping[]; armed: string | null; selected: string | null
  onArm: (path: string | null) => void; onConnect: (source: string, destination: string) => void
  onSelect: (id: string) => void
}

/** Ports and wires share the same SVG coordinates, including after scrolling. */
export default function ExchangeWiring({ sources, destinations, sourceParameter, destinationParameter, mappings, armed, selected, onArm, onConnect, onSelect }: Props) {
  const dragSource = useRef<string | null>(null)
  const height = Math.max(220, 80 + Math.max(sources.length, destinations.length) * 64)
  const centerY = (index: number) => 90 + index * 64
  const connect = (path: string) => { if (armed) { onConnect(armed, path); onArm(null) } }
  const key = (mapping: Mapping, i: number) => mapping.id || `${mapping.source}:${mapping.destination}:${i}`
  return <div className="ex-wiring-diagram">
    <svg viewBox={`0 0 1000 ${height}`} width="1000" height={height} className="ex-port-diagram" aria-label="Exchange member wiring" onPointerUp={e => {
      const source=dragSource.current; dragSource.current=null
      if (!source) return
      const destination=document.elementFromPoint(e.clientX,e.clientY)?.closest('[data-destination-port]')?.getAttribute('data-destination-port')
      if(destination){onConnect(source,destination);onArm(null)}
      if(e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.releasePointerCapture(e.pointerId)
    }} onPointerCancel={()=>{dragSource.current=null}}>
      <defs>
        <pattern id="ex-port-grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M24 0H0V24" fill="none" stroke="#e9e3d9" strokeWidth=".7"/></pattern>
        <marker id="ex-port-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#a56319"/></marker>
      </defs>
      <rect width="1000" height={height} fill="url(#ex-port-grid)"/>
      <text x="20" y="28" className="ex-port-heading">{sourceParameter}</text>
      <text x="600" y="28" className="ex-port-heading">{destinationParameter}</text>
      {mappings.map((mapping, i) => {
        const fromLeft = sources.some(m => m.path === mapping.source)
        const si = (fromLeft ? sources : destinations).findIndex(m => m.path === mapping.source)
        const di = (fromLeft ? destinations : sources).findIndex(m => m.path === mapping.destination)
        if (si < 0 || di < 0) return null
        const x1 = fromLeft ? 400 : 600, x2 = fromLeft ? 600 : 400, y1 = centerY(si), y2 = centerY(di)
        const id = key(mapping, i), y = (y1 + y2) / 2
        return <g key={id} className={`ex-actual-wire ${selected === id ? 'selected' : ''}`}>
          <path d={`M${x1} ${y1} C500 ${y1},500 ${y2},${x2} ${y2}`} markerEnd="url(#ex-port-arrow)"/>
          {mapping.condition && <g role="button" tabIndex={0} aria-label={`Edit condition for ${mapping.destination}`} onClick={() => onSelect(id)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(id) } }} className="ex-actual-condition">
            <rect x="414" y={y - 24} width="172" height="48" rx="6"/>
            <text x="500" y={y - 5}>IF {mapping.condition.mode.toUpperCase()} · {mapping.condition.terms.length}</text>
            <text x="500" y={y + 14}>ELSE {mapping.else_value ? mapping.else_value.value.slice(0, 18) : 'keep value'}</text>
            <title>{mapping.else_value ? `Else: ${mapping.else_value.value}` : 'No Else: keep the destination value'}</title>
          </g>}
        </g>
      })}
      {sources.map((m, i) => <g key={m.path} role="button" tabIndex={0} aria-label={`Source ${m.path}, ${m.iec_type}`} aria-pressed={armed === m.path} className={`ex-actual-port ${armed === m.path ? 'armed' : ''}`} onPointerDown={e => {e.preventDefault();dragSource.current=m.path;onArm(m.path);e.currentTarget.ownerSVGElement?.setPointerCapture(e.pointerId)}} onClick={() => onArm(m.path)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onArm(m.path) } }}>
        <rect x="20" y={centerY(i) - 26} width="380" height="52" rx="6"/>
        <text x="32" y={centerY(i) - 3}>{m.member || m.path.split('.').slice(1).join('.')}</text>
        <text x="32" y={centerY(i) + 17} className="ex-port-iec">{m.iec_type}</text>
        <circle cx="400" cy={centerY(i)} r="6"/><title>{m.path}</title>
      </g>)}
      {destinations.map((m, i) => <g key={m.path} role="button" tabIndex={0} data-destination-port={m.path} aria-label={`Destination ${m.path}, ${m.iec_type}`} className="ex-actual-port destination" onPointerUp={() => connect(m.path)} onClick={() => connect(m.path)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); connect(m.path) } }}>
        <rect x="600" y={centerY(i) - 26} width="380" height="52" rx="6"/>
        <text x="612" y={centerY(i) - 3}>{m.member || m.path.split('.').slice(1).join('.')}</text>
        <text x="612" y={centerY(i) + 17} className="ex-port-iec">{m.iec_type}</text>
        <circle cx="600" cy={centerY(i)} r="6"/><title>{m.path}</title>
      </g>)}
    </svg>
  </div>
}
