import ModalFullscreen from './ModalFullscreen'
import { useEffect, useState } from 'react'
import V2ObjectView from './V2ObjectView'
import InstanceMapping from './InstanceMapping'
import './instances.css'

type Row = Record<string, any>
type Props = { project: any; csrf: string; onSaved: () => void; onCommunications: () => void; onProgram: () => void; openInstanceName?: string; onOpenInstanceHandled?: () => void }
const list = (v: unknown): Row[] => Array.isArray(v) ? v as Row[] : []
type IoProposal = { member: string; name: string; iecType: string; direction: 'input' | 'output'; fixed: boolean; selected: boolean }

export default function V2Instances({ project, csrf, onSaved, onCommunications, onProgram, openInstanceName, onOpenInstanceHandled }: Props) {
  const instances=list(project?.instances), types=list(project?.types)
  const [name,setName]=useState(''),[typeId,setTypeId]=useState(''),[busy,setBusy]=useState(false)
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[review,setReview]=useState<Row|null>(null)
  const [editor,setEditor]=useState<Row|null>(null),[editorDirty,setEditorDirty]=useState(false)
  const [mapping,setMapping]=useState<{instance: Row; role: 'IO' | 'COM'} | null>(null)
  const [ioProposals,setIoProposals]=useState<IoProposal[] | null>(null)
  useEffect(() => { if (!openInstanceName) return; const instance = instances.find(item => (item.name || item.id) === openInstanceName); if (instance) { setEditor(instance); setEditorDirty(false); onOpenInstanceHandled?.() } }, [openInstanceName, project?.revision])
  const [pa,setPa]=useState<Row|null>(null),[values,setValues]=useState<Row>({})
  const typeFor=(id:any)=>types.find(t=>String(t.id)===String(id)||String(t.name)===String(id))
  const proposedName=()=> { if(name.trim())return name.trim(); const prefix=typeFor(typeId)?.name||'INSTANCE';let n=1;while(instances.some(i=>i.name===`${prefix}_${n}`))n++;return `${prefix}_${n}` }
  async function request(path:string,body?:any){const r=await fetch(`/api/v2/instances${path}`,{method:body?'POST':'GET',credentials:'same-origin',headers:body?{'Content-Type':'application/json','X-CSRF-Token':csrf}:{},body:body?JSON.stringify(body):undefined});const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'Action impossible. Actualisez puis réessayez.');return d}
  async function apply(token:string){await request('/apply',{token});setReview(null);setPa(null);onSaved()}
  async function declare(iomap: IoProposal[] = []) {
    const instance = proposedName()
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await request('/preview', { revision: project.revision, action: 'create', name: instance, type_id: typeId,
        iomap: iomap.filter(row => row.selected).map(({ member, name, direction }) => ({ member, name, direction })) })
      if (result.errors?.length) throw new Error(result.errors.slice(0, 3).join('\n'))
      await apply(result.token)
      setNotice(`Instance ${instance} déclarée${iomap.some(row => row.selected) ? ' avec ses IOMAP' : ''}.`)
      setIoProposals(null); setName(''); setTypeId('')
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Création impossible.') }
    finally { setBusy(false) }
  }
  const hasRole = (type: Row | undefined, role: string) => list(type?.duts).some(dut => dut.role === role && list(dut.members).some(member => member.name !== 'SPARE'))
  function proposeIo() {
    setError('')
    const dut = list(typeFor(typeId)?.duts).find(row => row.role === 'IO')
    const members = list(dut?.members).filter(row => row.name !== 'SPARE' && /^(BOOL|BYTE|WORD|DWORD|LWORD|SINT|USINT|INT|UINT|DINT|UDINT|LINT|ULINT|REAL|LREAL|TIME|DATE|TIME_OF_DAY|DATE_AND_TIME)$/i.test(row.iec_type))
    if (!members.length) { void declare(); return }
    setIoProposals(members.map(row => ({
      member: row.name, name: `IOMAP_${proposedName()}_${row.name}`, iecType: row.iec_type,
      direction: /^OUTPUT_/i.test(row.name) ? 'output' : 'input', fixed: /^(INPUT|OUTPUT)_/i.test(row.name),
      selected: /^(INPUT|OUTPUT)_/i.test(row.name),
    })))
  }
  async function remove(instance:Row){setBusy(true);setError('');try{const d=await request('/preview',{revision:project.revision,action:'delete',instance_id:instance.id||instance.name});if(d.errors?.length)throw new Error(d.errors.slice(0,3).join('\n'));setReview({...d,deleting:true,name:instance.name||instance.id})}catch(e:any){setError(e.message)}finally{setBusy(false)}}
  async function openPa(instance:Row){setBusy(true);setError('');try{const d=await request(`/${encodeURIComponent(instance.id||instance.name)}/configuration`);setPa({instance,rows:d.pa_values||[]});setValues(Object.fromEntries((d.pa_values||[]).map((r:Row)=>[r.Member||r.member_name,r['Instance value']??r.value??''])))}catch(e:any){setError(e.message)}finally{setBusy(false)}}
  async function savePa(){if(!pa)return;setBusy(true);setError('');try{const d=await request('/preview',{revision:project.revision,action:'pa',instance_id:pa.instance.id||pa.instance.name,values});if(d.errors?.length)throw new Error(d.errors.slice(0,3).join('\n'));await apply(d.token);setNotice('Paramètres enregistrés.')}catch(e:any){setError(e.message)}finally{setBusy(false)}}
  const closeEditor=()=>{if(!editorDirty||window.confirm('Fermer sans enregistrer les modifications du type ?')){setEditor(null);setEditorDirty(false)}}
  return <main className="instances-page" aria-label="Instances">
    <section className="instance-simple-intro"><h2>Déclarer les instances</h2><p>Choisis un type et un nom. Si le type a des E/S, tu peux créer les IOMAP à la déclaration.</p><div><button onClick={onCommunications}>Configurer les communications →</button><button onClick={onProgram}>Ajouter aux GFC / GFB →</button></div></section>
    {error&&<p className="instances-error" role="alert">{error}</p>}{notice&&<p className="instance-simple-notice" role="status">{notice}</p>}
    <div className="instances-table-wrap"><table className="instances-table"><thead><tr><th>Instance</th><th>Type partagé</th><th>Actions</th></tr></thead><tbody>
      <tr className="instances-new"><td><input aria-label="New instance name" value={name} onChange={e=>setName(e.target.value)} placeholder={typeId?proposedName():'Nom de l’instance'}/></td><td><select aria-label="Object type" value={typeId} onChange={e=>setTypeId(e.target.value)}><option value="">Choisir un type</option>{types.map(t=><option key={t.id} value={t.id}>{t.name}</option>)}</select></td><td><button className="instance-button primary" disabled={busy||!typeId} onClick={proposeIo}>{busy?'Enregistrement…':'Déclarer'}</button></td></tr>
      {instances.map(i=><tr key={i.id||i.name}><td><strong>{i.name||i.id}</strong></td><td>{typeFor(i.type_id)?.name||i.type_id}</td><td className="instance-actions"><button onClick={()=>{setEditor(i);setEditorDirty(false)}}>Blocs et code</button>{hasRole(typeFor(i.type_id), 'IO') && <button onClick={() => setMapping({instance:i,role:'IO'})}>IOMAP</button>}{hasRole(typeFor(i.type_id), 'COM') && <button onClick={() => setMapping({instance:i,role:'COM'})}>Mapping COM</button>}{typeFor(i.type_id)?.duts?.some((d:Row)=>d.role==='PA')&&<button onClick={()=>void openPa(i)}>Paramètres PA</button>}<button className="danger" disabled={busy} aria-label={`Supprimer l’instance ${i.name||i.id}`} onClick={()=>void remove(i)}>Supprimer</button></td></tr>)}
      {!instances.length&&<tr><td colSpan={3}>Aucune instance. Déclarez-en une avec le formulaire ci-dessus.</td></tr>}
    </tbody></table></div>
    {ioProposals && <div className="instance-backdrop"><section className="instance-dialog" role="dialog" aria-modal="true" aria-label="IOMAP de la nouvelle instance"><ModalFullscreen/>
      <header><h2>IOMAP · {proposedName()}</h2></header>
      <div className="instance-form"><p>Choisis les variables à déclarer dans GVL_IOMAP et à relier aux E/S de cette instance.</p>
        {error && <p role="alert" className="instances-error">{error}</p>}
        {ioProposals.map((row,index) => <div className="instance-iomap-row" key={row.member}>
          <label><input type="checkbox" checked={row.selected} onChange={event => setIoProposals(rows => rows?.map((item,i) => i === index ? {...item, selected:event.target.checked} : item) || null)}/>{row.member} · {row.iecType}</label>
          <input aria-label={`Nom IOMAP de ${row.member}`} value={row.name} disabled={!row.selected || busy} onChange={event => setIoProposals(rows => rows?.map((item,i) => i === index ? {...item, name:event.target.value} : item) || null)}/>
          <select aria-label={`Sens de ${row.member}`} value={row.direction} disabled={!row.selected || row.fixed || busy} onChange={event => {
            const direction = event.target.value as IoProposal['direction']
            setIoProposals(rows => rows?.map((item,i) => i === index ? {...item, direction} : item) || null)
          }}><option value="input">Entrée</option><option value="output">Sortie</option></select>
        </div>)}
      </div>
      <footer><button disabled={busy} onClick={() => setIoProposals(null)}>Retour</button><button disabled={busy} onClick={() => void declare()}>Déclarer seule</button>
        <button className="instance-button primary" disabled={busy || !ioProposals.some(row => row.selected)} onClick={() => void declare(ioProposals)}>Déclarer avec les IOMAP</button></footer>
    </section></div>}
    {mapping && <InstanceMapping role={mapping.role} instance={mapping.instance.name || mapping.instance.id} csrf={csrf} onSaved={onSaved} onClose={() => setMapping(null)}/>}
    {editor&&<div className="instance-backdrop"><section className="instance-dialog instance-code-dialog" role="dialog" aria-modal="true" aria-labelledby="instance-code-title"><ModalFullscreen/><header><div><h2 id="instance-code-title">{editor.name||editor.id} · Blocs et code</h2><p>Type partagé : <b>{typeFor(editor.type_id)?.name}</b>. Les modifications s’appliquent à toutes les instances de ce type, dans tous les projets.</p></div><button aria-label="Fermer l’éditeur" onClick={closeEditor}>×</button></header><div className="instance-code-body"><V2ObjectView codeOnly kind="type" objectId={String(editor.type_id)} csrf={csrf} projectRevision={project.revision} onSaved={onSaved} onDirtyChange={setEditorDirty}/></div><footer><button onClick={closeEditor}>Fermer</button></footer></section></div>}
    {pa&&<div className="instance-backdrop"><section className="instance-dialog" role="dialog" aria-modal="true" aria-labelledby="instance-pa-title"><ModalFullscreen/><header><h2 id="instance-pa-title">Valeurs PA · {pa.instance.name}</h2><button aria-label="Fermer les paramètres" onClick={()=>setPa(null)}>×</button></header><div className="instance-form">{list(pa.rows).map((r:Row)=><label key={r.Member||r.member_name}><span>{r.Member||r.member_name}<small>{r.Type||r.data_type} · valeur du type : {String(r['Default value']??'—')}</small></span><input value={values[r.Member||r.member_name]??''} onChange={e=>setValues({...values,[r.Member||r.member_name]:e.target.value})}/></label>)}{!pa.rows.length&&<p>Aucun paramètre PA dans ce type.</p>}</div><footer><button onClick={()=>setPa(null)}>Annuler</button><button className="instance-button primary" disabled={busy} onClick={()=>void savePa()}>Enregistrer</button></footer></section></div>}
    {review&&<div className="instance-backdrop"><section className="instance-dialog" role="dialog" aria-modal="true"><ModalFullscreen/><header><h2>Supprimer l’instance {review.name} ?</h2><button aria-label="Annuler la suppression" onClick={()=>setReview(null)}>×</button></header><div className="instance-review">{error&&<p className="instances-error" role="alert">{error}</p>}{list(review.changes).map((c:any,i:number)=><p key={i}>{String(c)}</p>)}<p>Ses adresses, connexions et appels seront retirés. Son type partagé sera conservé.</p></div><footer><button onClick={()=>setReview(null)}>Annuler</button><button className="instance-button danger" disabled={busy} onClick={async()=>{setBusy(true);try{await apply(review.token);setNotice('Instance supprimée.')}catch(e:any){setError(e.message)}finally{setBusy(false)}}}>Supprimer l’instance</button></footer></section></div>}
  </main>
}
