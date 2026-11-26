import { useEffect, useState } from 'react'
import ModalFullscreen from './ModalFullscreen'
import './special-exc.css'

type Row = {id:string; member:string; source:string; comment:string}
type Instance = {id:string; name:string}
type Data = {revision:number; instances:Instance[]; special_exc_assignments:(Row & {instance_id:string})[]; special_exc_options:Record<string,{member:string;iec_type:string}[]>}
type Review = {token?:string; errors?:string[]; warnings?:string[]; changes?:string[]; generated_st?:string}
type Props = {csrf:string; instanceId?:string; onSaved:()=>void; onClose:()=>void}

export default function SpecialExcAssignments({csrf,instanceId,onSaved,onClose}:Props) {
  const [data,setData] = useState<Data>()
  const [selected,setSelected] = useState('')
  const [rows,setRows] = useState<Row[]>([])
  const [snapshot,setSnapshot] = useState('[]')
  const [review,setReview] = useState<Review|null>(null)
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  const dirty = JSON.stringify(rows) !== snapshot
  const instance = data?.instances.find(item => (item.id || item.name) === selected)
  const options = data?.special_exc_options[instance?.name || ''] || []
  const destination = (member:string) => member && instance ? `GVL_EXC.EXC_${instance.name}.${member}` : ''
  async function request<T>(path:string,body?:object):Promise<T> {
    const response = await fetch('/api/v2/exchanges' + path, body ? {
      method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:JSON.stringify(body),
    } : undefined)
    const result = await response.json()
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Impossible de traiter les affectations. Actualise le projet puis réessaie.')
    return result
  }
  function selectInstance(next:Instance|undefined, loaded:Data) {
    const saved = loaded.special_exc_assignments.filter(row => [next?.id,next?.name].includes(row.instance_id))
      .map(({id,member,source,comment}) => ({id,member,source,comment:comment || ''}))
    setSelected(next?.id || next?.name || ''); setRows(saved); setSnapshot(JSON.stringify(saved)); setReview(null); setError('')
  }
  useEffect(() => {
    let live = true
    void request<Data>('').then(loaded => {
      if (!live) return
      setData(loaded)
      const next = loaded.instances.find(item => [item.id,item.name].includes(instanceId || '')) || loaded.instances[0]
      selectInstance(next,loaded)
    }).catch(cause => { if (live) setError(cause.message) })
    return () => { live = false }
  },[csrf,instanceId])
  const close = () => { if (!busy && (!dirty || window.confirm('Fermer sans enregistrer les affectations EXC ?'))) onClose() }
  useEffect(() => {
    const key = (event:KeyboardEvent) => { if (event.key === 'Escape' && !busy) { if (review) setReview(null); else close() } }
    window.addEventListener('keydown',key)
    return () => window.removeEventListener('keydown',key)
  },[busy,dirty,review])
  const add = () => { setRows(previous => [...previous,{id:crypto.randomUUID(),member:'',source:'',comment:''}]); setReview(null) }
  const update = (id:string,values:Partial<Row>) => { setRows(previous => previous.map(row => row.id === id ? {...row,...values} : row)); setReview(null) }
  const prepare = async () => {
    if (!data || !instance) return
    setBusy(true); setError('')
    try {
      const result = await request<Review>('/preview',{revision:data.revision,action:'save_special',instance_a_id:instance.id || instance.name,assignments:rows})
      if (!result.token || result.errors?.length) throw new Error(result.errors?.join('\n') || 'Aperçu indisponible.')
      setReview(result)
    } catch(cause) { setError(cause instanceof Error ? cause.message : 'Enregistrement impossible.') }
    finally { setBusy(false) }
  }
  const apply = async () => {
    if (!review?.token) return
    setBusy(true); setError('')
    try { await request('/apply',{token:review.token}); onSaved(); onClose() }
    catch(cause) { setError(cause instanceof Error ? cause.message : 'Enregistrement impossible.') }
    finally { setBusy(false) }
  }
  const addButton = <button type="button" disabled={busy || !instance || !options.length} onClick={add}>＋ Ajouter une affectation</button>
  return <div className="special-exc-backdrop"><section className="special-exc-dialog" role="dialog" aria-modal="true" aria-labelledby="special-exc-title">
    <ModalFullscreen onClose={close} disabled={busy}/>
    <header><h2 id="special-exc-title">Affectations particulières EXC</h2><p>Une variable ou expression ST libre vers une variable EXC de l’instance.</p></header>
    <div className="special-exc-body">
      {error && <p className="special-exc-error" role="alert">{error}</p>}
      {!data && !error && <p>Chargement…</p>}
      {review ? <>
        <h3>Vérifier les affectations</h3><ul>{review.changes?.map((change,index) => <li key={index}>{change}</li>)}</ul>
        <p>Cette section sera insérée dans GFC_EXC avant les appels Exchange existants.</p>
        {review.warnings?.length ? <details><summary>Informations ({review.warnings.length})</summary>{review.warnings.map((warning,index) => <p key={index}>{warning}</p>)}</details> : null}
        <pre aria-label="ST des affectations particulières">{review.generated_st || '// Aucune affectation particulière.'}</pre>
      </> : <>
        <div className="special-exc-tools"><label>Instance<select aria-label="Instance des affectations EXC" value={selected} disabled={busy || !!instanceId} onChange={event => {
          if (!data || dirty && !window.confirm('Abandonner les modifications de cette instance ?')) return
          selectInstance(data.instances.find(item => (item.id || item.name) === event.target.value),data)
        }}><option value="">Choisir une instance</option>{data?.instances.map(item => <option key={item.id || item.name} value={item.id || item.name}>{item.name}</option>)}</select></label>{addButton}</div>
        <p>La source n’a pas besoin d’être cataloguée ou mappée. Elle doit exister dans CODESYS et être compatible avec le type de la cible. Saisis seulement la source, sans <code>:=</code> ni point-virgule.</p>
        <p>Les affectations sont exécutées avant les appels Exchange ; un appel qui écrit la même cible peut ensuite remplacer sa valeur.</p>
        {!options.length && data && <p>Aucune variable EXC unitaire disponible pour cette instance.</p>}
        {rows.map((row,index) => <div className="special-exc-row" key={row.id}>
          <div className="special-exc-row-title"><b>Affectation {index + 1}</b><button type="button" disabled={busy} aria-label={`Supprimer l’affectation EXC ${index + 1}`} onClick={() => setRows(previous => previous.filter(item => item.id !== row.id))}>Supprimer</button></div>
          <label>Variable EXC cible<select aria-label={`Cible EXC ${index + 1}`} value={row.member} disabled={busy} onChange={event => update(row.id,{member:event.target.value})}><option value="">Choisir une variable EXC</option>{row.member && !options.some(option => option.member === row.member) && <option value={row.member}>{row.member} · variable manquante</option>}{options.map(option => <option key={option.member} value={option.member}>{option.member} · {option.iec_type}</option>)}</select></label>
          <code className="special-exc-target">{destination(row.member)}</code>
          <label>Source ST libre<textarea aria-label={`Source ST de l’affectation EXC ${index + 1}`} disabled={busy} rows={2} maxLength={2000} value={row.source} spellCheck={false} onChange={event => update(row.id,{source:event.target.value})} placeholder="GVL_Machine.Pression ou PLC_PRG.MaVariable"/></label>
          <label>Commentaire<input aria-label={`Commentaire de l’affectation EXC ${index + 1}`} disabled={busy} maxLength={500} value={row.comment} onChange={event => update(row.id,{comment:event.target.value})}/></label>
        </div>)}
        {rows.length > 0 && addButton}
        {!rows.length && data && <p>Aucune affectation particulière enregistrée pour cette instance.</p>}
      </>}
    </div>
    <footer><button type="button" disabled={busy} onClick={review ? () => setReview(null) : close}>{review ? 'Retour au formulaire' : 'Fermer'}</button><button type="button" disabled={busy || !data || !instance || !dirty} onClick={() => void (review ? apply() : prepare())}>{busy ? 'Patiente…' : review ? 'Confirmer l’enregistrement' : 'Enregistrer'}</button></footer>
  </section></div>
}
