import { useContext, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { variableFamily } from './VariableOptions'
import { blockCall, callable, StTypeCatalog, type StVariable } from './StVariables'
import './st-insert.css'

export const stSnippets = {
  IF: ['IF condition THEN\n    // Instructions\nEND_IF;', 'condition'],
  'IF / ELSE': ['IF condition THEN\n    // Instructions\nELSE\n    // Instructions\nEND_IF;', 'condition'],
  FOR: ['FOR indice := 0 TO limite DO\n    // Instructions\nEND_FOR;', 'indice'],
  WHILE: ['WHILE condition DO\n    // Instructions\nEND_WHILE;', 'condition'],
  CASE: ['CASE valeur OF\n    0: // Instructions\nELSE\n    // Instructions\nEND_CASE;', 'valeur'],
  ABS: ['ABS(valeur)', 'valeur'],
  MIN: ['MIN(valeurA, valeurB)', 'valeurA'],
  MAX: ['MAX(valeurA, valeurB)', 'valeurA'],
  LIMIT: ['LIMIT(minimum, valeur, maximum)', 'minimum'],
  SEL: ['SEL(condition, siFaux, siVrai)', 'condition'],
  'INT_TO_REAL': ['INT_TO_REAL(valeur)', 'valeur'],
} satisfies Record<string, [string, string]>

export type InsertMode = 'choices' | 'variables' | 'locals' | 'snippets' | 'functions'

const groupOf = variableFamily

export default function StInsertMenu({ x, y, bounds, initialMode, variables, onInsert, onClose }: {
  x: number; y: number; initialMode: InsertMode; variables: StVariable[]
  bounds?: DOMRect
  onInsert: (text: string, placeholder?: string) => void; onClose: (restoreFocus: boolean) => void
}) {
  const [mode, setMode] = useState<InsertMode>(initialMode)
  const [group, setGroup] = useState('')
  const [query, setQuery] = useState('')
  const { blocks, functions } = useContext(StTypeCatalog)
  const isLocal = mode === 'locals'
  const isVariableMode = mode === 'variables' || isLocal
  const panel = useRef<HTMLDivElement>(null)
  const search = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (isVariableMode || mode === 'functions') search.current?.focus()
    else panel.current?.querySelector('button')?.focus()
  }, [isVariableMode, mode])
  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      if (!panel.current?.contains(event.target as Node)) onClose(false)
    }
    document.addEventListener('pointerdown', dismiss)
    return () => document.removeEventListener('pointerdown', dismiss)
  }, [onClose])

  const available = variables.filter(variable => isLocal === !!variable.internal)
  const groups = [...new Set(available.map(variable => groupOf(variable.path)))]
  const matches = available.filter(variable => (isLocal || !group || groupOf(variable.path) === group) &&
    `${variable.path} ${variable.iec_type}`.toLowerCase().includes(query.trim().toLowerCase()))
  const minX = Math.max(0, bounds?.left ?? 0) + 8
  const maxX = Math.min(window.innerWidth, bounds?.right ?? window.innerWidth) - 8
  const minY = Math.max(0, bounds?.top ?? 0) + 8
  const maxY = Math.min(window.innerHeight, bounds?.bottom ?? window.innerHeight) - 8
  const menuWidth = Math.min(200, maxX - minX)
  const left = Math.max(minX, Math.min(x, maxX - menuWidth))
  const roomLeft = left - minX
  const roomRight = maxX - left - menuWidth
  const side = roomLeft >= 366 || roomLeft >= roomRight ? 'left' : 'right'
  const room = side === 'left' ? roomLeft : roomRight
  const narrow = room < 326
  const submenuWidth = Math.min(345, room - 146)
  const top = Math.max(minY, Math.min(y, maxY - 330))
  const changeMode = (next: InsertMode) => { setMode(next); setGroup(''); setQuery('') }
  const variableResults = <>
        {matches.map(variable => {
          const call = isLocal ? blockCall(variable.path, variable.iec_type, blocks) : undefined
          return <div className="st-insert-result" key={variable.path} data-variable-family={groupOf(variable.path)}>
            <button type="button" title={call?.[0]} onClick={() => call ? onInsert(...call) : onInsert(variable.path)}>
              <code>{variable.path}</code><small>{variable.iec_type}{call && ' · Appeler'}</small>
            </button>
            {call && <button type="button" className="st-insert-name" aria-label={`Insérer uniquement le nom ${variable.path}`} onClick={() => onInsert(variable.path)}>Nom seul</button>}
          </div>
        })}
  </>
  return createPortal(<div ref={panel} className={`st-cascade ${narrow ? 'compact' : side} ${mode === 'variables' && group ? 'has-third' : ''}`} role="dialog" aria-label="Insérer dans le ST"
    style={{ left, top, width: menuWidth, '--st-submenu-width': `${submenuWidth}px`, '--st-menu-height': `${maxY - top}px` } as React.CSSProperties}
    onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose(true) }
    }}>
    <div className="st-insert-menu st-cascade-main">
      {([['variables','Variables des DUT'],['locals','Variables internes'],['snippets','Instructions ST'],['functions','Fonctions du projet']] as const)
        .map(([key,label]) => <button key={key} type="button" aria-expanded={mode === key} onClick={() => changeMode(mode === key ? 'choices' : key)}>
          {label}<span aria-hidden="true">{!narrow && side === 'left' ? '‹' : '›'}</span>
        </button>)}
    </div>
    {mode !== 'choices' && <div className="st-insert-menu st-cascade-sub">
    {isVariableMode && <>
      <div className="st-insert-filters">
        <input ref={search} aria-label="Chercher une variable ou un type IEC" placeholder="Nom ou type IEC" value={query} onChange={event => { setQuery(event.target.value); setGroup('') }}/>
      </div>
      <div className="st-insert-results">
        {!isLocal && !query.trim() && groups.map(role => <button key={role} type="button" data-variable-family={role} aria-expanded={group === role} onClick={() => setGroup(group === role ? '' : role)}>
          <b>{role === 'INTERN' ? 'INT (INTERN)' : role}</b><span aria-hidden="true">{!narrow && side === 'left' ? '‹' : '›'}</span>
        </button>)}
        {(isLocal || query.trim()) && variableResults}
        {!matches.length && <p>Aucune variable trouvée.</p>}
      </div>
    </>}
    {mode === 'snippets' && <div className="st-insert-results">
      {Object.entries(stSnippets).map(([name, [text, placeholder]]) => <button key={name} type="button" onClick={() => onInsert(text, placeholder)}><code>{name}</code></button>)}
    </div>}
    {mode === 'functions' && <>
      <div className="st-insert-filters"><input ref={search} value={query} onChange={event => setQuery(event.target.value)}
        placeholder="Nom de la fonction" aria-label="Chercher une fonction du projet"/></div>
      <div className="st-insert-results">
        {Object.entries(functions || {}).filter(([name]) => name.toLowerCase().includes(query.trim().toLowerCase())).map(([name, fn]) =>
          <div key={name} className="st-insert-result">
            <button type="button" onClick={() => onInsert(...callable(name, fn.pins))}><code>{name}</code><small>{fn.result}</small></button>
            {fn.result && <button type="button" className="st-insert-name" onClick={() => onInsert(...callable(name, fn.pins, false))}>Expression</button>}
          </div>)}
        {!Object.keys(functions || {}).length && <p>Aucune fonction disponible.</p>}
      </div>
    </>}
    </div>}
    {mode === 'variables' && group && <div className="st-insert-menu st-cascade-third"><div className="st-insert-results">{variableResults}</div></div>}
  </div>, document.body)
}
