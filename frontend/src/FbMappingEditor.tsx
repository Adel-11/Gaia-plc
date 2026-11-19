import { useEffect, useRef, useState } from 'react'
import VariableOptions, { variableFamily } from './VariableOptions'
import './fb-mapping-editor.css'

export type FbMapping = { id?: string; section: 'inputs' | 'outputs'; source: string; destination: string }
export type VariableOption = { path: string; iec_type: string }
type Props = { section: FbMapping['section']; mappings: FbMapping[]; variables: VariableOption[]; preserved: string; readOnly?: boolean; csrf: string; name?: string; onChange: (mappings: FbMapping[]) => void }
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
        <VariableOptions variables={variables}/>
      </select>
      {['AND', 'OR', '(', ')'].map(token => <button key={token} type="button" onClick={() => insert(token)}>{token}</button>)}
    </div>
  </div>
}

export default function FbMappingEditor({ section, mappings, variables, preserved, readOnly, csrf, name = 'FB', onChange }: Props) {
  const [query, setQuery] = useState('')
  const [editingExpression, setEditingExpression] = useState<string | number | null>(null)
  const excelInput = useRef<HTMLInputElement>(null)
  const [excelBusy, setExcelBusy] = useState(false)
  const [excelError, setExcelError] = useState('')
  const [excelNotice, setExcelNotice] = useState('')
  const [excelUndo, setExcelUndo] = useState<FbMapping[] | null>(null)
  const excelContextId = useRef(0)
  useEffect(() => {
    excelContextId.current += 1
    setExcelUndo(null); setExcelNotice(''); setExcelError(''); setExcelBusy(false)
    return () => { excelContextId.current += 1 }
  }, [name, section])
  const excelContext = { name, section, mappings, variables }
  const checked = async (response: Response) => {
    if (!response.ok) {
      const data = await response.json().catch(() => ({}))
      throw new Error(typeof data.detail === 'string' ? data.detail : 'Impossible de traiter ce classeur.')
    }
    return response
  }
  const exportExcel = async (empty: boolean) => {
    setExcelBusy(true); setExcelError('')
    try {
      const response = await checked(await fetch(`/api/v2/fb-mappings/excel${empty ? '?empty=true' : ''}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(excelContext),
      }))
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a')
      link.href = url; link.download = response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1] || `FB_${section}.xlsx`
      link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (cause) { setExcelError(cause instanceof Error ? cause.message : 'Export Excel impossible.') }
    finally { setExcelBusy(false) }
  }
  const importExcel = async (file: File) => {
    if (readOnly) return
    if (!file.name.toLowerCase().endsWith('.xlsx') || file.size > 8 * 1024 * 1024) { setExcelError('Choisis un fichier .xlsx de moins de 8 Mo.'); return }
    const form = new FormData()
    const contextId = excelContextId.current
    form.append('file', file); form.append('current', JSON.stringify(excelContext))
    setExcelBusy(true); setExcelError('')
    try {
      const response = await checked(await fetch('/api/v2/fb-mappings/excel/import', { method: 'POST', headers: { 'X-CSRF-Token': csrf }, body: form }))
      const result = await response.json()
      if (contextId !== excelContextId.current) return
      setExcelUndo(mappings.filter(mapping => mapping.section === section))
      onChange([...mappings.filter(mapping => mapping.section !== section), ...result.mappings])
      setQuery(''); setEditingExpression(null)
      setExcelNotice(`${result.count} mapping(s) importé(s). Enregistre le FB pour appliquer les modifications.`)
    } catch (cause) { if (contextId === excelContextId.current) setExcelError(cause instanceof Error ? cause.message : 'Import Excel impossible.') }
    finally { if (contextId === excelContextId.current) setExcelBusy(false) }
  }
  const rows = mappings.filter(mapping => mapping.section === section)
  const visible = rows.filter(mapping => `${mapping.source} ${mapping.destination}`.toLowerCase().includes(query.toLowerCase()))
  const external = variables.filter(variable => /^(PA|COM|HMI|EXC|IO)\./.test(variable.path))
  const internal = variables.filter(variable => variable.path.startsWith('INTERN.'))
  const sourceOptions = section === 'inputs' ? external : internal
  const destinationOptions = section === 'inputs' ? internal : external.filter(variable => !variable.path.startsWith('PA.'))
  const boolSources = sourceOptions.filter(variable => norm(variable.iec_type) === 'BOOL')
  const variableType = (path: string) => norm(variables.find(variable => variable.path === path)?.iec_type || '')
  const add = () => {
    setQuery('')
    onChange([...mappings, { id: `draft-${Date.now()}-${Math.random()}`, section, source: '', destination: '' }])
  }
  const change = (row: FbMapping, field: 'source' | 'destination', value: string) => {
    onChange(mappings.map(mapping => mapping === row ? { ...mapping, [field]: value } : mapping))
  }
  const options = (list: VariableOption[], selected: string) => {
    const selectedMissing = selected && !list.some(variable => variable.path === selected)
    return <>{selectedMissing && <option value={selected}>{selected}</option>}
      <VariableOptions variables={list}/>
    </>
  }
  return <section className="fb-map-section" aria-label={`${section} mappings`}>
    <header><div><h4>{section === 'inputs' ? 'Sources des entrées' : 'Sources des sorties'}</h4>
      <p>{section === 'inputs' ? 'PA, COM terrain, HMI, EXC et IO vers INTERN.' : 'INTERN vers COM, HMI, EXC et IO.'}</p></div>
      <div className="fb-map-actions">
        <input hidden type="file" accept=".xlsx" ref={excelInput} aria-label={`Importer le mapping ${section} depuis Excel`}
          onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void importExcel(file) }}/>
        <select className="fb-map-excel" aria-label={`Excel des ${section === 'inputs' ? 'entrées' : 'sorties'}`} value="" disabled={excelBusy}
          onChange={event => { if (event.target.value === 'import') excelInput.current?.click(); else void exportExcel(event.target.value === 'template') }}>
          <option value="">{excelBusy ? 'Patiente…' : 'Excel'}</option><option value="template">Télécharger le modèle</option><option value="export">Exporter ce mapping</option><option value="import" disabled={readOnly}>Importer ce mapping</option>
        </select>
        {!readOnly && <button type="button" onClick={add} disabled={!sourceOptions.length || !destinationOptions.length}>＋ Ajouter un mapping</button>}
      </div>
    </header>
    {excelError && <p className="fb-map-excel-error" role="alert">{excelError}</p>}
    {excelNotice && <div className="fb-map-excel-notice" role="status"><span>{excelNotice}</span>{excelUndo && !readOnly && <button type="button" disabled={excelBusy} onClick={() => {
      onChange([...mappings.filter(mapping => mapping.section !== section), ...excelUndo]); setExcelUndo(null); setExcelNotice('Import annulé. Le mapping précédent est restauré.'); setExcelError('')
    }}>Annuler l’import</button>}</div>}
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
            <select aria-label={`Source du mapping ${index + 1}`} data-variable-family={mapping.source ? variableFamily(mapping.source) : undefined} disabled={readOnly} value={mapping.source} onChange={event => change(mapping, 'source', event.target.value)}>
              <option value="">Choisir la source</option>{options(sources, mapping.source)}
            </select>}
          {!readOnly && (expression || destinationType === 'BOOL') && <button type="button" className="fb-map-mode" onClick={() => {
            if (expression) {
              setEditingExpression(null)
              change(mapping, 'source', boolSources.find(variable => variable.path === mapping.source.trim())?.path || '')
            } else setEditingExpression(key)
          }}>{expression ? 'Utiliser une seule variable' : 'Combiner des variables'}</button>}
        </td><td className="fb-map-arrow" aria-hidden="true">→</td><td>
          <select aria-label={`Destination du mapping ${index + 1}`} data-variable-family={mapping.destination ? variableFamily(mapping.destination) : undefined} disabled={readOnly} value={mapping.destination} onChange={event => change(mapping, 'destination', event.target.value)}>
            <option value="">Choisir la destination</option>{options(destinations, mapping.destination)}
          </select>
        </td>{!readOnly && <td><button className="fb-map-remove" type="button" aria-label={`Supprimer le mapping ${index + 1}`} onClick={() => {
          if (editingExpression === key) setEditingExpression(null)
          onChange(mappings.filter(row => row !== mapping))
        }}>×</button></td>}</tr>
      })}
    </tbody></table></div> : <p className="fb-map-empty">Aucun mapping affiché.</p>}
    {!readOnly && <button type="button" className="fb-map-add-bottom" onClick={add} disabled={!sourceOptions.length || !destinationOptions.length}>＋ Ajouter un mapping</button>}
  </section>
}
