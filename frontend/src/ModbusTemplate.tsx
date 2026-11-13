import { useEffect, useState } from 'react'
import ModalFullscreen from './ModalFullscreen'
import './modbus-wiring.css'

export type TemplateRow = {
  protocol: 'tcp' | 'rtu'; table: string; member: string; inbound: boolean; register?: number | null
  order: string; bank?: string | null; bank_type?: string | null; function?: string | null; channel?: string | null
}
type Variable = {path: string; iec_type: string}
const tables = {coils: 'Coils', discrete_inputs: 'Entrées discrètes', input_registers: 'Input Registers', holding_registers: 'Holding Registers'}
const wide = new Set(['REAL','DWORD','DINT','UDINT','TIME','T','DATE','DT','DATE_AND_TIME','TOD','TIME_OF_DAY'])
const words = new Set(['WORD','INT','UINT','BYTE','SINT','USINT', ...wide])
const bit = (table: string) => table === 'coils' || table === 'discrete_inputs'
const readonly = (table: string) => table === 'discrete_inputs' || table === 'input_registers'

export function TemplateRows({rows, variables, onChange, disabled}: {
  rows: TemplateRow[]; variables: Variable[]; onChange: (rows: TemplateRow[]) => void; disabled?: boolean
}) {
  const [protocol, setProtocol] = useState<TemplateRow['protocol']>('tcp')
  const members = Object.fromEntries(variables.filter(v => v.path.startsWith('COM.') && v.path !== 'COM.SPARE')
    .map(v => [v.path.slice(4), v.iec_type.toUpperCase()]))
  const update = (index: number, patch: Partial<TemplateRow>) => onChange(rows.map((row, i) => i === index ? {...row, ...patch} : row))
  const add = () => onChange([...rows, {protocol, table: 'coils', member: '', inbound: protocol === 'tcp', order: 'LSW_FIRST'}])
  const addButton = <button type="button" className="button secondary" disabled={disabled} onClick={add}>＋ Ajouter une ligne {protocol.toUpperCase()}</button>
  return <fieldset disabled={disabled}>
    <p>En TCP, les adresses sont attribuées à l’instance. En RTU, saisis les registres indiqués par le fabricant.</p>
    <div className="modbus-wiring-controls"><label>Protocole<select value={protocol} onChange={e => setProtocol(e.target.value as TemplateRow['protocol'])}><option value="tcp">TCP</option><option value="rtu">RTU</option></select></label>{addButton}</div>
    <div className="v2-table-scroll"><table className="v2-table"><thead><tr><th>Table</th><th>Variable COM</th><th>Sens</th>{protocol === 'rtu' && <th>Registre RTU</th>}<th>Ordre des mots</th><th>Actions</th></tr></thead>
      <tbody>{rows.map((row, index) => row.protocol === protocol && <tr key={index}>
        <td><select aria-label="Table Modbus" value={row.table} onChange={e => {
          const table = e.target.value
          update(index, {table, member: '', bank: null, bank_type: null, function: null, channel: null, inbound: readonly(table) ? protocol === 'rtu' : row.inbound})
        }}>{Object.entries(tables).map(([id, title]) => <option key={id} value={id}>{title}</option>)}</select></td>
        <td><select aria-label="Variable COM" value={row.member} onChange={e => update(index, {member: e.target.value, bank: null, bank_type: null, function: null, channel: null})}>
          <option value="">Choisir…</option>
          {Object.entries(members).filter(([name, type]) => name === row.member || (bit(row.table) ? type === 'BOOL' : words.has(type)))
            .map(([name,type]) => <option key={name} value={name}>{name} · {type}</option>)}
          {row.member && !(row.member in members) && <option value={row.member}>{row.member} · membre introuvable</option>}
        </select></td>
        <td><select aria-label="Sens" value={String(row.inbound)} disabled={readonly(row.table)} onChange={e => update(index, {inbound: e.target.value === 'true', function: null, channel: null})}>
          <option value="true">{protocol === 'rtu' ? 'Équipement' : 'Client'} → PLC</option><option value="false">PLC → {protocol === 'rtu' ? 'équipement' : 'client'}</option>
        </select></td>
        {protocol === 'rtu' && <td><input aria-label="Registre RTU" type="number" min="0" max="65535" value={row.register ?? ''} onChange={e => update(index, {register: e.target.value ? Number(e.target.value) : null})}/>{row.register != null && <small>0x{row.register.toString(16).toUpperCase()}</small>}{row.bank && <small>PLC · {row.bank}</small>}</td>}
        <td>{wide.has(members[row.member]) ? <select aria-label="Ordre des mots" value={row.order} onChange={e => update(index, {order: e.target.value})}><option value="LSW_FIRST">Mot faible en premier</option><option value="MSW_FIRST">Mot fort en premier</option></select> : '—'}</td>
        <td><button type="button" className="button secondary" onClick={() => onChange(rows.filter((_, i) => i !== index))}>Retirer</button></td>
      </tr>)}</tbody></table></div>
    {addButton}
  </fieldset>
}

export default function ModbusTemplate({id, name, csrf, onClose, onSaved}: {
  id: string; name: string; csrf: string; onClose: () => void; onSaved: () => void
}) {
  const path = `/api/v2/types/${encodeURIComponent(id)}/modbus-template`
  const [data, setData] = useState<{revision: number; members: Record<string, string>; rows: TemplateRow[]}>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function request(body?: object) {
    const response = await fetch(path, body ? {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify(body)} : undefined)
    const result = await response.json()
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Impossible de charger ou d’enregistrer le modèle.')
    return result
  }
  useEffect(() => {
    let active = true
    void request().then(result => { if (active) setData(result) }).catch(cause => { if (active) setError(cause.message) })
    return () => { active = false }
  }, [path])
  const save = async () => {
    if (!data) return
    setBusy(true); setError('')
    try { await request({revision: data.revision, rows: data.rows}); onSaved(); onClose() }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Enregistrement impossible.') }
    finally { setBusy(false) }
  }
  return <div className="modbus-backdrop"><section className="modbus-wiring" role="dialog" aria-modal="true" aria-labelledby="modbus-template-title">
    <ModalFullscreen onClose={onClose} disabled={busy}/>
    <header><h3 id="modbus-template-title">Modèle Modbus · {name}</h3></header>
    <div className="modbus-wiring-scroll">
      {data ? <TemplateRows rows={data.rows} variables={Object.entries(data.members).map(([key, type]) => ({path: 'COM.' + key, iec_type: type}))}
        disabled={busy} onChange={rows => setData({...data, rows})}/> : !error && <p>Chargement…</p>}
      {error && <p role="alert" className="comm-alert">{error}</p>}
    </div>
    <footer><button className="button secondary" disabled={busy} onClick={onClose}>Retour</button>
      <button className="button primary" disabled={!data || busy} onClick={() => void save()}>Enregistrer le modèle</button></footer>
  </section></div>
}
