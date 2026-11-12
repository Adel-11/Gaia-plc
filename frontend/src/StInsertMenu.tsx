import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { VariableOption } from './FbMappingEditor'
import { variableFamily } from './VariableOptions'
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
  'Appel TON': ['temporisateur(IN := condition, PT := T#1s);', 'temporisateur'],
} satisfies Record<string, [string, string]>

export type InsertMode = 'choices' | 'variables' | 'snippets'

const groupOf = variableFamily

export default function StInsertMenu({ x, y, initialMode, variables, onInsert, onClose }: {
  x: number; y: number; initialMode: InsertMode; variables: VariableOption[]
  onInsert: (text: string, placeholder?: string) => void; onClose: (restoreFocus: boolean) => void
}) {
  const [mode, setMode] = useState(initialMode)
  const [group, setGroup] = useState('')
  const [query, setQuery] = useState('')
  const panel = useRef<HTMLDivElement>(null)
  const search = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (mode === 'variables') search.current?.focus()
    else panel.current?.querySelector('button')?.focus()
  }, [mode])
  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      if (!panel.current?.contains(event.target as Node)) onClose(false)
    }
    document.addEventListener('pointerdown', dismiss)
    return () => document.removeEventListener('pointerdown', dismiss)
  }, [onClose])

  const groups = [...new Set(variables.map(variable => groupOf(variable.path)))]
  const matches = variables.filter(variable => (!group || groupOf(variable.path) === group) &&
    `${variable.path} ${variable.iec_type}`.toLowerCase().includes(query.trim().toLowerCase()))
  const top = Math.max(8, Math.min(y, window.innerHeight - 290))
  return createPortal(<div ref={panel} className="st-insert-menu" role="dialog" aria-label="Insérer dans le ST"
    style={{ left: Math.max(8, Math.min(x, window.innerWidth - 378)), top, maxHeight: Math.min(370, window.innerHeight - top - 8) }}
    onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose(true) }
    }}>
    <div className="st-insert-tabs">
      <button type="button" aria-pressed={mode === 'variables'} onClick={() => setMode('variables')}>Variables des DUT</button>
      <button type="button" aria-pressed={mode === 'snippets'} onClick={() => setMode('snippets')}>Instructions ST</button>
    </div>
    {mode === 'variables' && <>
      <div className="st-insert-filters">
        <select aria-label="DUT" value={group} onChange={event => setGroup(event.target.value)}>
          <option value="">Tous les DUT</option>
          {groups.map(role => <option key={role} value={role} data-variable-family={role}>{role === 'INTERN' ? 'INT (INTERN)' : role === 'VAR' ? 'VAR du bloc' : role}</option>)}
        </select>
        <input ref={search} aria-label="Chercher une variable ou un type IEC" placeholder="Nom ou type IEC" value={query} onChange={event => setQuery(event.target.value)}/>
      </div>
      <div className="st-insert-results">
        {matches.map(variable => <button type="button" key={variable.path} data-variable-family={groupOf(variable.path)} onClick={() => onInsert(variable.path)}>
          <code>{variable.path}</code><small>{variable.iec_type}</small>
        </button>)}
        {!matches.length && <p>Aucune variable trouvée.</p>}
      </div>
    </>}
    {mode === 'snippets' && <div className="st-insert-results">
      {Object.entries(stSnippets).map(([name, [text, placeholder]]) => <button key={name} type="button" onClick={() => onInsert(text, placeholder)}><code>{name}</code></button>)}
      <p>Pour TON, déclare une instance de type TON dans les variables du bloc.</p>
    </div>}
  </div>, document.body)
}
