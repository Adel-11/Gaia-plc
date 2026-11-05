import { useId } from 'react'
import './local-variables.css'

export type LocalVariable = { name: string; iec_type: string; default_value: string }

export default function LocalVariables({ rows, onChange, onSave, disabled, dirty }: {
  rows: LocalVariable[]
  onChange: (rows: LocalVariable[]) => void
  onSave?: () => void
  disabled?: boolean
  dirty?: boolean
}) {
  const types = useId()
  const update = (i: number, change: Partial<LocalVariable>) => onChange(rows.map((row, n) => n === i ? { ...row, ...change } : row))
  return <section className="local-variables" aria-label="Variables internes au bloc">
    <div className="local-variables-head">
      <div><h4>Variables internes au bloc</h4><p>Les lignes ajoutées ici sont déclarées dans VAR. Les déclarations existantes restent visibles dans le ST.</p></div>
      <button type="button" className="button secondary" disabled={disabled} onClick={() => onChange([...rows, { name: '', iec_type: 'BOOL', default_value: '' }])}>＋ Ajouter une variable interne</button>
    </div>
    <datalist id={types}>{['BOOL', 'INT', 'UINT', 'DINT', 'WORD', 'DWORD', 'REAL', 'LREAL', 'TIME', 'STRING', 'TON', 'TOF', 'TP', 'R_TRIG', 'F_TRIG'].map(type => <option key={type} value={type}/>)}</datalist>
    {rows.map((row, i) => <div className="local-variables-row" key={i}>
      <label>Nom<input value={row.name} disabled={disabled} spellCheck={false} placeholder="Ex. TimerStart" onChange={e => update(i, { name: e.target.value })}/></label>
      <label>Type IEC<input value={row.iec_type} disabled={disabled} spellCheck={false} list={types} onChange={e => update(i, { iec_type: e.target.value })}/></label>
      <label>Valeur initiale (facultative)<input value={row.default_value} disabled={disabled} onChange={e => update(i, { default_value: e.target.value })}/></label>
      <button type="button" className="button secondary" disabled={disabled} aria-label={`Retirer la variable interne ${row.name || i + 1}`} onClick={() => onChange(rows.filter((_, n) => n !== i))}>Retirer</button>
    </div>)}
    {onSave && <button type="button" className="button primary" disabled={disabled || !dirty} onClick={onSave}>Enregistrer</button>}
  </section>
}
