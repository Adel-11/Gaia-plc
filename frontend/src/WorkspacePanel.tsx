import { createContext, PointerEvent, ReactNode, useContext, useEffect, useRef, useState } from 'react'
import './workspace-panel.css'

type Props = { id: string; title: string; children: ReactNode; className?: string; contentClassName?: string; initialWidth?: number; initialHeight?: number; minWidth?: number; minHeight?: number }
type Saved = { width?: number; height?: number; collapsed?: boolean }
const PanelScope = createContext('guest/default')
export function usePanelScope() { return useContext(PanelScope) }
export function PanelPreferencesProvider({ scope, children }: { scope: string; children: ReactNode }) { const parent = useContext(PanelScope); return <PanelScope.Provider value={`${parent}/${scope || 'default'}`}>{children}</PanelScope.Provider> }

/** Resizable, collapsible workspace section with a per-user, per-panel saved size. */
export default function WorkspacePanel({ id, title, children, className = '', contentClassName = '', initialWidth, initialHeight, minWidth = 280, minHeight = 160 }: Props) {
  const host = useRef<HTMLElement>(null), scope = useContext(PanelScope)
  const [collapsed, setCollapsed] = useState(false), [dimensions, setDimensions] = useState<Saved>({})
  const storeKey = `gaia.workspace-panel.v1.${scope}.${id}`
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(storeKey) || '{}') as Saved
      setDimensions(saved); setCollapsed(!!saved.collapsed)
      if (host.current) { host.current.style.width = saved.width ? `${saved.width}px` : ''; host.current.style.height = saved.height ? `${saved.height}px` : ''; if (id.includes(':list')) host.current.parentElement?.style.setProperty('--code-list-width', saved.width ? `${saved.width}px` : '') }
    } catch { /* Ignore unavailable or malformed preferences. */ }
  }, [storeKey, id])
  const save = (next: Saved) => { setDimensions(old => { const merged = { ...old, ...next }; try { localStorage.setItem(storeKey, JSON.stringify(merged)) } catch { /* Storage is optional. */ }; return merged }) }
  const reset = () => { if (!host.current) return; host.current.style.width = ''; host.current.style.height = ''; if (id.includes(':list')) host.current.parentElement?.style.removeProperty('--code-list-width'); save({ width: undefined, height: undefined }) }
  const resize = (axis: 'width' | 'height' | 'both', event: PointerEvent<HTMLDivElement>) => {
    if (collapsed || !host.current || event.button !== 0 || !event.isPrimary) return
    event.preventDefault(); event.stopPropagation()
    const handle = event.currentTarget, node = host.current, startX = event.clientX, startY = event.clientY, rect = node.getBoundingClientRect(), parentRect = node.parentElement?.getBoundingClientRect(), isCodeList = id.includes(':list')
    const maxW = Math.max(minWidth, Math.min(window.innerWidth - rect.left - 8, parentRect ? parentRect.right - rect.left - (isCodeList ? 300 : 0) : window.innerWidth - rect.left - 8)), maxH = Math.max(minHeight, Math.min(window.innerHeight - rect.top - 8, 1200))
    handle.setPointerCapture(event.pointerId)
    const move = (e: globalThis.PointerEvent) => { const w = Math.max(minWidth, Math.min(maxW, rect.width + e.clientX - startX)), h = Math.max(minHeight, Math.min(maxH, rect.height + e.clientY - startY)); if (axis !== 'height') { node.style.width = `${Math.round(w)}px`; if (isCodeList) node.parentElement?.style.setProperty('--code-list-width', `${Math.round(w)}px`) } if (axis !== 'width') node.style.height = `${Math.round(h)}px` }
    let finished = false
    const finish = () => { if (finished) return; finished = true; handle.removeEventListener('pointermove', move); handle.removeEventListener('pointerup', finish); handle.removeEventListener('pointercancel', finish); handle.removeEventListener('lostpointercapture', finish); if (axis !== 'height') { const width = Math.round(node.getBoundingClientRect().width); if (isCodeList) node.parentElement?.style.setProperty('--code-list-width', `${width}px`); save({ width }) } if (axis !== 'width') save({ height: Math.round(node.getBoundingClientRect().height) }) }
    handle.addEventListener('pointermove', move); handle.addEventListener('pointerup', finish); handle.addEventListener('pointercancel', finish); handle.addEventListener('lostpointercapture', finish)
  }
  const keyboardResize = (axis: 'width' | 'height' | 'both', event: React.KeyboardEvent<HTMLDivElement>) => { const delta = event.shiftKey ? 40 : 12; if (!['ArrowRight', 'ArrowLeft', 'ArrowUp', 'ArrowDown'].includes(event.key)) return; event.preventDefault(); if (!host.current) return; const bounds = host.current.getBoundingClientRect(), width = Math.max(minWidth, Math.min(window.innerWidth - bounds.left - 8, bounds.width + (event.key === 'ArrowRight' ? delta : event.key === 'ArrowLeft' ? -delta : 0))), height = Math.max(minHeight, Math.min(Math.min(window.innerHeight - bounds.top - 8, 1200), bounds.height + (event.key === 'ArrowDown' ? delta : event.key === 'ArrowUp' ? -delta : 0))); if (axis !== 'height') { host.current.style.width = `${width}px`; if (id.includes(':list')) host.current.parentElement?.style.setProperty('--code-list-width', `${width}px`) } if (axis !== 'width') host.current.style.height = `${height}px`; save({ ...(axis !== 'height' ? { width } : {}), ...(axis !== 'width' ? { height } : {}) }) }
  const handleProps = (axis: 'width' | 'height' | 'both', label: string) => ({ role: 'separator' as const, tabIndex: collapsed ? -1 : 0, 'aria-label': `${label} — utiliser les flèches pour redimensionner`, 'aria-orientation': axis === 'width' ? 'vertical' as const : 'horizontal' as const, onPointerDown: (e: PointerEvent<HTMLDivElement>) => resize(axis, e), onKeyDown: (e: React.KeyboardEvent<HTMLDivElement>) => keyboardResize(axis, e) })
  return <section ref={host as React.Ref<HTMLElement>} className={`workspace-panel ${collapsed ? 'is-collapsed' : ''} ${className}`} data-workspace-panel={id} style={{ '--panel-min-width': `${minWidth}px`, '--panel-min-height': `${minHeight}px`, ...(initialWidth && !dimensions.width ? { width: `${initialWidth}px` } : {}), ...(initialHeight && !dimensions.height ? { height: `${initialHeight}px` } : {}) } as React.CSSProperties}>
    <header className="workspace-panel-head"><h2>{title}</h2><div className="workspace-panel-tools"><button type="button" title="Réinitialiser la taille" aria-label={`Réinitialiser la taille de ${title}`} onClick={reset}>Reset</button><button type="button" aria-expanded={!collapsed} onClick={() => { const next = !collapsed; setCollapsed(next); save({ collapsed: next }) }}>{collapsed ? 'Déplier' : 'Replier'}</button></div></header>
    <div className={`workspace-panel-content ${contentClassName}`} hidden={collapsed}>{children}</div>
    {!collapsed && <><div className="workspace-panel-resize-edge workspace-panel-resize-right" {...handleProps('width', `Redimensionner ${title} horizontalement`)} /><div className="workspace-panel-resize-edge workspace-panel-resize-bottom" {...handleProps('height', `Redimensionner ${title} verticalement`)} /><div className="workspace-panel-resize-corner" {...handleProps('both', `Redimensionner ${title}`)} /></>}
  </section>
}
