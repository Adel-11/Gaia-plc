import { useRef, useState } from 'react'
import './fb-mapping-editor.css'

export type FbMapping = { id?: string; section: 'inputs' | 'outputs'; source: string; destination: string }
export type VariableOption = { path: string; iec_type: string }
type Props = { section: FbMapping['section']; mappings: FbMapping[]; variables: VariableOption[]; preserved: string; readOnly?: boolean; onChange: (mappings: FbMapping[]) => void }
const norm = (value: string) => value.replace(/\s+/g, '').toUpperCase()
const isExpression = (source: string) => /[()]|\b(?:AND|OR)\b/i.test(source)

function BooleanSource({ value, variables, onChange }: { value: string; variables: VariableOption[]; onChange: (value: string) => void }) {
  const input = useRef<HTMLTextAreaElement>(null)
  const insert = (token: string) => {
    const field = input.current
    if (!field) return
    const { selectionStart, selectionEnd } = field
    const before = value.slice(0, selectionStart)
    const after = value.slice(selectionEnd)
    const prefix = before && !/[\s(]$/.test(before) && token !== ')' ? ' ' : ''
    const suffix = after && !/^[\s)]/.test(after) && token !== '(' ? ' ' : ''
    const inserted = `${prefix}${token}${suffix}`
    onChange(before + inserted + after)
    requestAnimationFrame(() => {
      field.focus()
      field.setSelectionRange(selectionStart + inserted.length, selectionStart + inserted.length)
    })
  }
  return <div className="fb-map-expression">
    <label>Expression BOOL<textarea ref={input} rows={2} value={value} spellCheck={false} onChange={event => onChange(event.target.value)} /></label>
    <div className="fb-map-expression-tools">
      <select aria-label="Insérer une variable BOOL" value="" onChange={event => insert(event.target.value)}>
        <option value="">Insérer une variable BOOL</option>
        {variables.map(variable => <option key={variable.path} value={variable.path}>{variable.path}</option>)}
      </select>
      {['AND', 'OR', '(', ')'].map(token => <button key={token} type="button" onClick={() => insert(token)}>{token}</button>)}
    </div>
  </div>
}

export default function FbMappingEditor({ section, mappings, variables, preserved, readOnly, onChange }: Props) {
  const [query, setQuery] = useState('')
  const [editingExpression, setEditingExpression] = useState<string | number | null>(null)
  const rows = mappings.filter(mapping => mapping.section === section)
  const visible = rows.filter(mapping => `${mapping.source} ${mapping.destination}`.toLowerCase().includes(query.toLowerCase()))
  const external = variables.filter(variable => /^(PA|COM|EXC|IO)\./.test(variable.path))
  const internal = variables.filter(variable => variable.path.startsWith('INTERN.'))
  const sourceOptions = section === 'inputs' ? external : internal
  const destinationOptions = section === 'inputs' ? internal : external.filter(variable => !variable.path.startsWith('PA.'))
  const boolSources = sourceOptions.filter(variable => norm(variable.iec_type) === 'BOOL')
  const variableType = (path: string) => norm(variables.find(variable => variable.path === path)?.iec_type || '')
  const add = () => {
    const available = destinationOptions.filter(destination => !rows.some(mapping => mapping.destination === destination.path))
    const source = sourceOptions.find(variable => available.some(destination => norm(destination.iec_type) === norm(variable.iec_type)))
    const destination = available.find(variable => source && norm(variable.iec_type) === norm(source.iec_type))
    onChange([...mappings, { id: `draft-${Date.now()}-${Math.random()}`, section, source: source?.path || '', destination: destination?.path || '' }])
  }
  const change = (row: FbMapping, field: 'source' | 'destination', value: string) => {
    onChange(mappings.map(mapping => mapping === row ? { ...mapping, [field]: value } : mapping))
  }
  const options = (list: VariableOption[], selected: string) => {
    const selectedMissing = selected && !list.some(variable => variable.path === selected)
    return <>{selectedMissing && <option value={selected}>{selected}</option>}
      {list.map(variable => <option key={variable.path} value={variable.path}>{variable.path} · {variable.iec_type}</option>)}
    </>
  }
  return <section className="fb-map-section" aria-label={`${section} mappings`}>
    <header><div><h4>{section === 'inputs' ? 'Sources des entrées' : 'Sources des sorties'}</h4>
      <p>{section === 'inputs' ? 'PA, COM, EXC et IO vers INTERN.' : 'INTERN vers COM, EXC et IO.'}</p></div>
      {!readOnly && <button type="button" onClick={add} disabled={!sourceOptions.length || !destinationOptions.length}>＋ Ajouter un mapping</button>}
    </header>
    <input aria-label={`Rechercher dans les mappings ${section}`} value={query} onChange={event => setQuery(event.target.value)} placeholder="Rechercher une variable" className="fb-map-search"/>
    {preserved && <details className="fb-preserved"><summary>ST manuel</summary><pre>{preserved}</pre></details>}
    {visible.length ? <div className="fb-map-table-wrap"><table className="fb-map-table"><thead><tr><th>Source</th><th></th><th>Destination</th>{!readOnly && <th/>}</tr></thead><tbody>
      {visible.map(mapping => {
        const index = rows.indexOf(mapping)
        const key = mapping.id || index
        const expression = isExpression(mapping.source) || editingExpression === key
        const sourceType = expression ? 'BOOL' : variableType(mapping.source)
        const destinationType = variableType(mapping.destination)
        const sources = sourceOptions.filter(variable => !destinationType || norm(variable.iec_type) === destinationType)
        const destinations = destinationOptions.filter(variable => !sourceType || norm(variable.iec_type) === sourceType)
        return <tr key={key}><td className="fb-map-source">
          {expression ? readOnly ? <code>{mapping.source}</code> :
            <BooleanSource value={mapping.source} variables={boolSources} onChange={value => {
              setEditingExpression(key)
              change(mapping, 'source', value)
            }}/> :
            <select aria-label={`Source du mapping ${index + 1}`} disabled={readOnly} value={mapping.source} onChange={event => change(mapping, 'source', event.target.value)}>
              <option value="">Choisir la source</option>{options(sources, mapping.source)}
            </select>}
          {!readOnly && (expression || destinationType === 'BOOL') && <button type="button" className="fb-map-mode" onClick={() => {
            if (expression) {
              setEditingExpression(null)
              change(mapping, 'source', boolSources.find(variable => variable.path === mapping.source.trim())?.path || '')
            } else setEditingExpression(key)
          }}>{expression ? 'Utiliser une seule variable' : 'Combiner des variables'}</button>}
        </td><td className="fb-map-arrow" aria-hidden="true">→</td><td>
          <select aria-label={`Destination du mapping ${index + 1}`} disabled={readOnly} value={mapping.destination} onChange={event => change(mapping, 'destination', event.target.value)}>
            <option value="">Choisir la destination</option>{options(destinations, mapping.destination)}
          </select>
        </td>{!readOnly && <td><button className="fb-map-remove" type="button" aria-label={`Supprimer le mapping ${index + 1}`} onClick={() => {
          if (editingExpression === key) setEditingExpression(null)
          onChange(mappings.filter(row => row !== mapping))
        }}>×</button></td>}</tr>
      })}
    </tbody></table></div> : <p className="fb-map-empty">Aucun mapping affiché.</p>}
  </section>
}
