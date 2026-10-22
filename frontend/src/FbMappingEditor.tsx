import { useState } from 'react'
import './fb-mapping-editor.css'

export type FbMapping = { id?: string; section: 'inputs' | 'outputs'; source: string; destination: string }
export type VariableOption = { path: string; iec_type: string }
type Props = { section: 'inputs' | 'outputs'; mappings: FbMapping[]; variables: VariableOption[]; preserved: string; readOnly?: boolean; onChange: (mappings: FbMapping[]) => void }
const norm = (value: string) => value.replace(/\s+/g, '').toUpperCase()

export default function FbMappingEditor({ section, mappings, variables, preserved, readOnly, onChange }: Props) {
  const [query,setQuery] = useState('')
  const rows = mappings.filter(mapping => mapping.section === section && `${mapping.source} ${mapping.destination}`.toLowerCase().includes(query.toLowerCase()))
  const external = variables.filter(v => ['PA.', 'COM.', 'EXC.', 'IO.'].some(prefix => v.path.startsWith(prefix)))
  const internal = variables.filter(v => v.path.startsWith('INTERN.'))
  const sourceOptions = section === 'inputs' ? external : internal
  const destinationOptions = section === 'inputs' ? internal : variables.filter(v => ['COM.', 'EXC.', 'IO.'].some(prefix => v.path.startsWith(prefix)))
  const add = () => {
    const source = sourceOptions.find(v=>destinationOptions.some(d=>norm(d.iec_type)===norm(v.iec_type) && !mappings.some(m=>m.section===section && m.destination===d.path)))
    const destination = destinationOptions.find(d=>source && norm(d.iec_type)===norm(source.iec_type) && !mappings.some(m=>m.section===section && m.destination===d.path))
    onChange([...mappings,{id:`draft-${Date.now()}-${Math.random()}`,section,source:source?.path || '',destination:destination?.path || ''}])
  }
  const remove = (index: number) => onChange(mappings.filter((_, i) => !(i === index && mappings[i]?.section === section)))
  const changeAt = (row: FbMapping, field: 'source' | 'destination', value: string) => {
    if (row) { const absolute = mappings.indexOf(row); onChange(mappings.map((mapping, i) => i === absolute ? { ...mapping, [field]: value } : mapping)) }
  }
  const compatible = (sourcePath: string) => {
    const source = variables.find(v => v.path === sourcePath)
    return destinationOptions.filter(option => !source || norm(option.iec_type) === norm(source.iec_type))
  }
  const sourceFor = (destinationPath: string) => {
    const destination = variables.find(v => v.path === destinationPath)
    return sourceOptions.filter(option => !destination || norm(option.iec_type) === norm(destination.iec_type))
  }
  return <section className="fb-map-section" aria-label={`${section} mappings`}>
    <header><div><h4>{section.toUpperCase()}</h4><p>{section === 'inputs' ? 'Copy PA, COM, EXC, or IO values into INTERN.' : 'Copy INTERN values to COM, EXC, or IO outputs.'}</p></div>{!readOnly && <button type="button" onClick={add} disabled={!sourceOptions.length || !destinationOptions.length}>＋ Add mapping</button>}</header>
    <input aria-label={`Search ${section} mappings`} value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search variable mappings" className="fb-map-search"/>
    {preserved && <details className="fb-preserved"><summary>Preserved handwritten {section} code</summary><pre>{preserved}</pre></details>}
    {rows.length ? <div className="fb-map-table-wrap"><table className="fb-map-table"><thead><tr><th>Source</th><th></th><th>Destination</th>{!readOnly && <th/>}</tr></thead><tbody>{rows.map((mapping, index) => {
      const sourceList = sourceFor(mapping.destination), destinationList = compatible(mapping.source)
      return <tr key={mapping.id || index}><td><select aria-label={`${section} mapping ${mappings.filter(m=>m.section===section).indexOf(mapping) + 1} source`} disabled={readOnly} value={mapping.source} onChange={e => changeAt(mapping, 'source', e.target.value)}><option value="">Select source</option>{(sourceList.some(v => v.path === mapping.source) ? sourceList : [...sourceList, ...variables.filter(v => v.path === mapping.source)]).map(v => <option key={v.path} value={v.path}>{v.path} · {v.iec_type}</option>)}</select></td><td className="fb-map-arrow" aria-hidden="true">→</td><td><select aria-label={`${section} mapping ${mappings.filter(m=>m.section===section).indexOf(mapping) + 1} destination`} disabled={readOnly} value={mapping.destination} onChange={e => changeAt(mapping, 'destination', e.target.value)}><option value="">Select destination</option>{(destinationList.some(v => v.path === mapping.destination) ? destinationList : [...destinationList, ...variables.filter(v => v.path === mapping.destination)]).map(v => <option key={v.path} value={v.path}>{v.path} · {v.iec_type}</option>)}</select></td>{!readOnly && <td><button className="fb-map-remove" type="button" aria-label={`Remove ${section} mapping ${mappings.filter(m=>m.section===section).indexOf(mapping) + 1}`} onClick={() => remove(mappings.indexOf(mapping))}>×</button></td>}</tr>
    })}</tbody></table></div> : <p className="fb-map-empty">No mappings in this section.</p>}
  </section>
}
