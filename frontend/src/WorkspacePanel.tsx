import { createContext, ReactNode, useContext, useEffect, useRef, useState } from 'react'
import './workspace-panel.css'

type Props = {
  id: string
  title: string
  children: ReactNode
  className?: string
  contentClassName?: string
  initialWidth?: number
  initialHeight?: number
  minWidth?: number
  minHeight?: number
}

type Saved = { width?: number; height?: number; collapsed?: boolean }
const PanelScope = createContext('guest/default')

export function usePanelScope() { return useContext(PanelScope) }

export function PanelPreferencesProvider({ scope, children }: { scope: string; children: ReactNode }) {
  const parent = useContext(PanelScope)
  return <PanelScope.Provider value={`${parent}/${scope || 'default'}`}>{children}</PanelScope.Provider>
}

/** Resizable, collapsible workspace section with a per-user, per-panel saved size. */
export default function WorkspacePanel({ id, title, children, className = '', contentClassName = '', initialWidth, initialHeight, minWidth = 280, minHeight = 160 }: Props) {
  const host = useRef<HTMLElement>(null)
  const userResizing = useRef(false)
  const collapsedRef = useRef(false)
  const savedRef = useRef<Saved>({})
  const scope = useContext(PanelScope)
  const [collapsed, setCollapsed] = useState(false)
  const [dimensions, setDimensions] = useState<Saved>({})
  const storeKey = `gaia.workspace-panel.v1.${scope}.${id}`

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(storeKey) || '{}') as Saved
      savedRef.current = saved
      setDimensions(saved)
      setCollapsed(!!saved.collapsed)
      collapsedRef.current = !!saved.collapsed
      if (host.current) {
        host.current.style.width = saved.width ? `${saved.width}px` : ''
        host.current.style.height = saved.height ? `${saved.height}px` : ''
      }
    } catch { /* Ignore unavailable or malformed local preferences. */ }
    const node = host.current
    if (!node || typeof ResizeObserver === 'undefined') return
    let timer = 0
    const observer = new ResizeObserver(entries => {
      const box = entries[0]?.contentRect
      if (!box || !userResizing.current || box.width <= 0 || box.height <= 0) return
      window.clearTimeout(timer)
      timer = window.setTimeout(() => {
        const actual = node.getBoundingClientRect()
        const width = Math.round(actual.width), height = Math.round(actual.height)
        if (width < minWidth || height < minHeight) return
        const next = {...savedRef.current, width, height, collapsed: collapsedRef.current}
        savedRef.current = next
        setDimensions(next)
        try { localStorage.setItem(storeKey, JSON.stringify(next)) } catch { /* Storage is optional. */ }
      }, 160)
    })
    const finishResize = () => {
      if (!userResizing.current || collapsedRef.current) return
      window.clearTimeout(timer)
      const box = node.getBoundingClientRect()
      const next = {...savedRef.current, width: Math.round(box.width), height: Math.round(box.height)}
      savedRef.current = next
      setDimensions(next)
      try { localStorage.setItem(storeKey, JSON.stringify(next)) } catch { /* Storage is optional. */ }
      userResizing.current = false
    }
    observer.observe(node)
    window.addEventListener('pointerup', finishResize)
    window.addEventListener('mouseup', finishResize)
    return () => { observer.disconnect(); window.clearTimeout(timer); window.removeEventListener('pointerup', finishResize); window.removeEventListener('mouseup', finishResize) }
  // Panel identity stays stable for the lifetime of the mounted workspace.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storeKey, minWidth, minHeight])

  const save = (next: Saved) => {
    savedRef.current = {...savedRef.current, ...next}
    setDimensions(savedRef.current)
    try { localStorage.setItem(storeKey, JSON.stringify(savedRef.current)) } catch { /* Storage is optional. */ }
  }
  const adjust = (axis: 'width' | 'height', amount: number) => {
    if (!host.current) return
    const current = axis === 'width' ? host.current.getBoundingClientRect().width : host.current.getBoundingClientRect().height
    const value = Math.max(axis === 'width' ? minWidth : minHeight, Math.round(current + amount))
    host.current.style[axis] = `${value}px`
    save({ [axis]: value })
  }
  const reset = () => {
    if (!host.current) return
    host.current.style.width = ''
    host.current.style.height = ''
    save({ width: undefined, height: undefined })
  }

  return <section ref={host as React.Ref<HTMLElement>} className={`workspace-panel ${collapsed ? 'is-collapsed' : ''} ${className}`} data-workspace-panel={id} onPointerDown={event => { const bounds = event.currentTarget.getBoundingClientRect(); userResizing.current = !collapsed && bounds.right - event.clientX < 24 && bounds.bottom - event.clientY < 24 }} style={{ '--panel-min-width': `${minWidth}px`, '--panel-min-height': `${minHeight}px`, ...(initialWidth && !dimensions.width ? { width: `${initialWidth}px` } : {}), ...(initialHeight && !dimensions.height ? { height: `${initialHeight}px` } : {}) } as React.CSSProperties}>
    <header className="workspace-panel-head"><h2>{title}</h2><div className="workspace-panel-tools"><button type="button" title="Make narrower" aria-label={`Make ${title} narrower`} onClick={() => adjust('width', -80)}>−W</button><button type="button" title="Make wider" aria-label={`Make ${title} wider`} onClick={() => adjust('width', 80)}>＋W</button><button type="button" title="Make shorter" aria-label={`Make ${title} shorter`} onClick={() => adjust('height', -80)}>−H</button><button type="button" title="Make taller" aria-label={`Make ${title} taller`} onClick={() => adjust('height', 80)}>＋H</button><button type="button" title="Reset size" aria-label={`Reset ${title} size`} onClick={reset}>Reset</button><button type="button" aria-expanded={!collapsed} onClick={() => { const next = !collapsed; collapsedRef.current = next; setCollapsed(next); save({ collapsed: next }) }}>{collapsed ? 'Expand' : 'Collapse'}</button></div></header>
    <div className={`workspace-panel-content ${contentClassName}`} hidden={collapsed}>{children}</div>
  </section>
}
