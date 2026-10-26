import { useEffect, useId, useRef, useState } from 'react'
import './object-canvas.css'

type Dut = { name: string; role?: string; members?: unknown[] }
type Props = { duts: Dut[]; fbName: string; active: string; onSelect: (name: string) => void; objectKey: string }
type Viewport = { x: number; y: number; zoom: number }
const W = 1000, H = 520, NODE_H = 62

export default function ObjectCanvas({ duts, fbName, active, onSelect, objectKey }: Props) {
  const uid = useId().replace(/:/g, '')
  const svg = useRef<SVGSVGElement>(null)
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null)
  const [view, setView] = useState<Viewport>({ x: 0, y: 0, zoom: 1 })
  const left = duts.filter(d => (d.role || '').toUpperCase() !== 'INT' && d.name.toUpperCase() !== 'INT')
    .sort((a, b) => {
      const order = ['PA', 'COM', 'EXC', 'IO']
      const ai = order.indexOf((a.role || '').toUpperCase()), bi = order.indexOf((b.role || '').toUpperCase())
      return (ai < 0 ? 4 : ai) - (bi < 0 ? 4 : bi)
    })
  const right = duts.filter(d => (d.role || '').toUpperCase() === 'INT' || d.name.toUpperCase() === 'INT')
  const positions = (items: Dut[], x: number) => items.map((dut, i) => ({ dut, x, y: (H - items.length * NODE_H) / 2 + i * NODE_H }))
  const lnodes = positions(left, 48), rnodes = positions(right, 704)
  const fbY = (H - NODE_H) / 2
  const zoomAt = (factor: number, cx = W / 2, cy = H / 2) => setView(v => {
    const zoom = Math.max(.55, Math.min(2.5, v.zoom * factor)), ratio = zoom / v.zoom
    return { zoom, x: cx - W / 2 - (cx - W / 2 - v.x) * ratio, y: cy - H / 2 - (cy - H / 2 - v.y) * ratio }
  })
  const toCanvas = (event: { clientX: number; clientY: number }) => {
    const element = svg.current
    if (!element) return { x: W / 2, y: H / 2 }
    const point = element.createSVGPoint(); point.x = event.clientX; point.y = event.clientY
    const local = point.matrixTransform(element.getScreenCTM()?.inverse())
    return { x: local.x, y: local.y }
  }
  const fit = () => setView({ x: 0, y: 0, zoom: 1 })
  useEffect(() => {
    const element = svg.current
    if (!element) return
    const wheel = (event: WheelEvent) => { event.preventDefault(); event.stopPropagation(); const point = toCanvas(event); zoomAt(event.deltaY < 0 ? 1.1 : 1 / 1.1, point.x, point.y) }
    element.addEventListener('wheel', wheel, { passive: false })
    return () => element.removeEventListener('wheel', wheel)
  }, [view])
  return <section className="object-canvas" aria-label="Object connection canvas" data-object-key={objectKey}>
    <div className="oc-toolbar"><span>Connections</span><div className="oc-controls">
      <button type="button" aria-label="Zoom out" onClick={() => zoomAt(1 / 1.2)}>−</button><output aria-live="polite">{Math.round(view.zoom * 100)}%</output>
      <button type="button" aria-label="Zoom in" onClick={() => zoomAt(1.2)}>+</button><button type="button" onClick={fit}>Fit</button>
    </div></div>
    <svg ref={svg} className="oc-svg" viewBox={`0 0 ${W} ${H}`} role="group" aria-label="Pan and zoom object connections" onPointerDown={e => { if ((e.target as Element).closest?.('.oc-node')) return; const p = toCanvas(e); drag.current = { ...p, vx: view.x, vy: view.y }; e.currentTarget.setPointerCapture(e.pointerId) }} onPointerMove={e => { if (!drag.current) return; const p = toCanvas(e); setView(v => ({ ...v, x: drag.current!.vx + p.x - drag.current!.x, y: drag.current!.vy + p.y - drag.current!.y })) }} onPointerUp={e => { drag.current = null; if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId) }} onPointerCancel={() => { drag.current = null }} onKeyDown={e => { if (e.key === '+' || e.key === '=') { e.preventDefault(); zoomAt(1.2) } else if (e.key === '-') { e.preventDefault(); zoomAt(1 / 1.2) } else if (e.key === '0') fit(); else if (e.key.startsWith('Arrow')) { e.preventDefault(); setView(v => ({ ...v, x: v.x + (e.key === 'ArrowLeft' ? 30 : e.key === 'ArrowRight' ? -30 : 0), y: v.y + (e.key === 'ArrowUp' ? 30 : e.key === 'ArrowDown' ? -30 : 0) })) } }} tabIndex={0}>
      <defs><pattern id={`${uid}-grid`} width="24" height="24" patternUnits="userSpaceOnUse"><path d="M 24 0 L 0 0 0 24" fill="none" stroke="#e8edf3" strokeWidth="1"/></pattern><marker id={`${uid}-arrow`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#647e9b"/></marker></defs>
      <g transform={`translate(${view.x} ${view.y}) translate(${W / 2} ${H / 2}) scale(${view.zoom}) translate(${-W / 2} ${-H / 2})`}>
        <rect x={-5000} y={-5000} width={11000} height={10520} fill={`url(#${uid}-grid)`} pointerEvents="none"/>
        {lnodes.map(({ dut, x, y }) => <path key={`line-${dut.name}`} className="oc-line" d={`M ${x + 246} ${y + NODE_H / 2} C 350 ${y + NODE_H / 2}, 352 ${H / 2}, 390 ${H / 2}`} markerEnd={`url(#${uid}-arrow)`}/>)}
        {rnodes.map(({ dut, x, y }) => <path key={`line-${dut.name}`} className="oc-line" d={`M 610 ${H / 2} C 650 ${H / 2}, 650 ${y + NODE_H / 2}, ${x} ${y + NODE_H / 2}`} markerEnd={`url(#${uid}-arrow)`}/>)}
        {lnodes.map(({ dut, x, y }) => <g key={dut.name} className={`oc-node ${active === dut.name ? 'is-active' : ''}`} role="button" tabIndex={0} aria-label={`${dut.name}, ${dut.role || 'DUT'}`} aria-pressed={active === dut.name} onClick={() => onSelect(dut.name)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(dut.name) } }}>
          <rect x={x} y={y} width="246" height={NODE_H} rx="10"/><circle className="oc-dot" cx={x + 22} cy={y + 22} r="5"/><text className="oc-kicker" x={x + 36} y={y + 24}>{dut.role || 'DUT'}</text><text className="oc-name" x={x + 18} y={y + 47}>{dut.name}</text></g>)}
        <g className={`oc-node oc-fb ${active === '__fb' ? 'is-active' : ''}`} role="button" tabIndex={0} aria-label={`${fbName}, Function block`} aria-pressed={active === '__fb'} onClick={() => onSelect('__fb')} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect('__fb') } }}>
          <rect x="390" y={fbY} width="220" height={NODE_H} rx="12"/><text className="oc-kicker" x="410" y={fbY + 24}>FUNCTION BLOCK</text><text className="oc-name" x="410" y={fbY + 47}>{fbName}</text></g>
        {rnodes.map(({ dut, x, y }) => <g key={dut.name} className={`oc-node oc-int ${active === dut.name ? 'is-active' : ''}`} role="button" tabIndex={0} aria-label={`${dut.name}, ${dut.role || 'INT'}`} aria-pressed={active === dut.name} onClick={() => onSelect(dut.name)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(dut.name) } }}>
          <rect x={x} y={y} width="246" height={NODE_H} rx="10"/><circle className="oc-dot" cx={x + 22} cy={y + 22} r="5"/><text className="oc-kicker" x={x + 36} y={y + 24}>{dut.role || 'INT'}</text><text className="oc-name" x={x + 18} y={y + 47}>{dut.name}</text></g>)}
      </g>
    </svg>
    <div className="oc-hint">Drag the canvas to pan · Use + / − or the mouse wheel to zoom · Press 0 to fit</div>
  </section>
}
