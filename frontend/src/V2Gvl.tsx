import { useEffect, useState } from 'react'
import { StEditor } from './V2ObjectView'
import RawStEditor from './RawStEditor'
import './gvl.css'

type Variable = { name: string; iec_type: string; folder: string; assigned: boolean; editable: boolean }
type Data = { revision: number; types: string[]; lists: { name: string; manual: boolean; rows: Variable[]; st: string }[] }
const blank: Variable = { name: '', iec_type: 'BOOL', folder: '', assigned: false, editable: true }

export default function V2Gvl({ csrf, revision, onSaved }: { csrf: string; revision?: number; onSaved: () => void }) {
  const [data, setData] = useState<Data>()
  const [selected, setSelected] = useState('GVL_IOMAP')
  const [filter, setFilter] = useState('')
  const [folderFilter, setFolderFilter] = useState('')
  const [draft, setDraft] = useState<{ original: string; row: Variable }>()
  const [raw, setRaw] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function request(path: string, body?: object) {
    const response = await fetch('/api/v2' + path, body ? {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body),
    } : undefined)
    const result = await response.json()
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Impossible de charger les GVL.')
    return result
  }
  useEffect(() => {
    let active = true
    void request('/gvl').then(result => { if (active) setData(result) }).catch(cause => { if (active) setError(cause.message) })
    return () => { active = false }
  }, [revision])
  const gvl = data?.lists.find(item => item.name === selected)
  const folders = [...new Set(gvl?.rows.map(row => row.folder).filter(Boolean) || [])].sort()
  const managed = selected === 'GVL_IOMAP' && !gvl?.manual
  const rows = gvl?.rows.filter(row => (!folderFilter || row.folder === folderFilter) &&
    (row.name + ' ' + row.iec_type).toLowerCase().includes(filter.toLowerCase())) || []
  async function save(deleteName?: string) {
    if (!data || !gvl) return
    setBusy(true); setError('')
    try {
      if (deleteName) {
        await request('/io-hardware/copies', { revision: data.revision, action: 'delete', iomap: deleteName })
        setData(await request('/gvl'))
      } else if (draft) {
        const { row, original } = draft
        const previous = gvl.rows.find(item => item.name === original)
        const fields = row.editable && (row.name !== previous?.name || row.iec_type !== previous?.iec_type)
          ? {new_name: row.name, iec_type: row.iec_type} : {}
        setData(await request('/gvl', { revision: data.revision, gvl: selected, name: original, folder: row.folder, ...fields }))
      }
      setDraft(undefined); onSaved()
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Enregistrement impossible.') }
    finally { setBusy(false) }
  }
  const add = () => setDraft({ original: '', row: { ...blank, folder: folderFilter } })
  return <section className="gvl-page">
    <div className="gvl-toolbar">
      <label>Liste GVL<select value={selected} onChange={event => { setSelected(event.target.value); setFolderFilter(''); setDraft(undefined); setError('') }}>
        {data?.lists.map(item => <option key={item.name}>{item.name}</option>)}
      </select></label>
      <label>Filtrer<input value={filter} onChange={event => setFilter(event.target.value)}/></label>
      {folders.length > 0 && <label>Dossier<select value={folderFilter} onChange={event => setFolderFilter(event.target.value)}>
        <option value="">Tous les dossiers</option>{folders.map(folder => <option key={folder}>{folder}</option>)}
      </select></label>}
      {managed && gvl && <button type="button" className="button secondary" disabled={busy} onClick={add}>＋ Ajouter une variable</button>}
      {gvl && <button type="button" className="button secondary" onClick={() => setRaw(selected)}>Ouvrir le ST complet</button>}
    </div>
    {error && <p role="alert" className="comm-alert">{error}</p>}
    {gvl && <>
      <div className="gvl-table"><table className="v2-table"><thead><tr><th>Variable</th><th>Type IEC</th><th>Dossier</th><th>Affectation</th><th>Actions</th></tr></thead>
        <tbody>{rows.map(row => <tr key={row.name}><td><code>{row.name}</code></td><td>{row.iec_type}</td><td>{row.folder || '—'}</td>
          <td><span className={row.assigned ? 'gvl-assigned' : 'gvl-free'}>{row.assigned ? '● Affectée' : '○ Libre'}</span></td><td>
            {managed && <><button type="button" className="button secondary" disabled={busy} onClick={() => setDraft({original:row.name,row})}>{row.editable ? 'Modifier' : 'Dossier'}</button>{' '}
              <button type="button" className="button secondary" disabled={busy} onClick={() => {
                if (window.confirm(`Supprimer ${row.name} et ses affectations dans le générateur ?`)) void save(row.name)
              }}>Supprimer</button></>}
          </td></tr>)}</tbody></table></div>
      {managed && <button type="button" className="button secondary" onClick={add} disabled={busy}>＋ Ajouter une variable</button>}
      {draft && <form className="gvl-toolbar" onSubmit={event => { event.preventDefault(); void save() }}>
        <label>Nom<input required value={draft.row.name} disabled={!draft.row.editable || busy} onChange={event => setDraft({...draft,row:{...draft.row,name:event.target.value}})}/></label>
        <label>Type IEC<select value={draft.row.iec_type} disabled={!draft.row.editable || busy} onChange={event => setDraft({...draft,row:{...draft.row,iec_type:event.target.value}})}>
          {data?.types.map(kind => <option key={kind}>{kind}</option>)}
        </select></label>
        <label>Dossier<input list="gvl-folder-names" value={draft.row.folder} maxLength={100} onChange={event => setDraft({...draft,row:{...draft.row,folder:event.target.value}})}/></label>
        <datalist id="gvl-folder-names">{folders.map(folder => <option key={folder} value={folder}/>)}</datalist>
        <button type="submit" className="button primary" disabled={busy}>Enregistrer</button>
        <button type="button" className="button secondary" disabled={busy} onClick={() => setDraft(undefined)}>Annuler</button>
      </form>}
      <div className="gvl-st"><StEditor key={gvl.name} value={gvl.st} readOnly label={`ST · ${gvl.name}`}/></div>
    </>}
    {raw && <RawStEditor name={raw} csrf={csrf} onClose={() => setRaw('')} onSaved={onSaved}/>}
  </section>
}
