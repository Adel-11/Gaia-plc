import { useEffect, useState } from 'react'
import V2PlcExport from './V2PlcExport'
import V2CodesysImport from './V2CodesysImport'

type Project = { workbooks?: { filename: string }[]; phase?: number }
type ExcelRow = { sheet: string; key: unknown; row?: Record<string, unknown>; before?: Record<string, unknown>; after?: Record<string, unknown> }
type ExcelDiff = { added: ExcelRow[]; modified: ExcelRow[]; removed: ExcelRow[] }
type ExcelPreview = { kind: 'excel'; token: string; filename?: string; mode?: 'merge' | 'replace'; diff: ExcelDiff; validation: { errors?: unknown[]; warnings?: unknown[] } }
type RestorePreview = { kind: 'restore'; token: string | null; errors: unknown[]; warnings: unknown[]; changes: string[] }
type Preview = ExcelPreview | RestorePreview
type Template = { filename: string; description: string }
type TemplateResponse = { templates: Template[] }

async function previewRequest(path: string, csrf: string, file: File, mode?: 'merge' | 'replace'): Promise<Record<string, unknown>> {
  const form = new FormData(); form.append('file', file); if (mode) form.append('mode', mode)
  const response = await fetch(`/api/v2${path}`, { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf }, body: form })
  if (!response.ok) { let message = `${response.status} ${response.statusText}`; try { const data = await response.json(); message = typeof data.detail === 'string' ? data.detail : data.detail?.message || message } catch {} throw new Error(message) }
  return response.json()
}
async function applyRequest(path: string, csrf: string, token: string): Promise<void> {
  const response = await fetch(`/api/v2${path}`, { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf, 'Content-Type': 'application/json' }, body: JSON.stringify({ token }) })
  if (!response.ok) { let message = `${response.status} ${response.statusText}`; try { const data = await response.json(); message = typeof data.detail === 'string' ? data.detail : data.detail?.message || message } catch {} throw new Error(message) }
}
function save(blob: Blob, name: string) { const url=URL.createObjectURL(blob), a=document.createElement('a'); a.href=url; a.download=name; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000) }
const messages = (values: unknown[] | undefined) => (values || []).map(value => typeof value === 'string' ? value : JSON.stringify(value))
const rowText = (value: unknown) => JSON.stringify(value, null, 2)

export default function TransferHub({ csrf, project, revision, onChanged }: { csrf:string; project:Project; revision?:number; onChanged:()=>void }) {
  const [tab,setTab]=useState<'imports'|'exports'|'templates'|'plc'|'codesys'>('imports')
  const [excelFile,setExcelFile]=useState<File|null>(null), [restoreFile,setRestoreFile]=useState<File|null>(null)
  const [mode,setMode]=useState<'merge'|'replace'>('merge'), [preview,setPreview]=useState<Preview|null>(null), [reviewed,setReviewed]=useState(false), [error,setError]=useState(''), [busy,setBusy]=useState(false)
  const [templates,setTemplates]=useState<Template[]>([]), [templatesLoading,setTemplatesLoading]=useState(false)
  useEffect(()=>{if(tab!=='templates'||templates.length)return;let active=true;setTemplatesLoading(true);fetch('/api/v2/templates',{credentials:'same-origin'}).then(async response=>{if(!response.ok)throw new Error(`Chargement des modèles impossible (${response.status})`);return response.json() as Promise<TemplateResponse>}).then(data=>{if(active)setTemplates(data.templates||[])}).catch(e=>{if(active)setError(e instanceof Error?e.message:'Chargement des modèles impossible')}).finally(()=>{if(active)setTemplatesLoading(false)});return()=>{active=false}},[tab,templates.length])
  const run=async(fn:()=>Promise<void>)=>{setBusy(true);setError('');try{await fn()}catch(e){setError(e instanceof Error?e.message:'Opération échouée')}finally{setBusy(false)}}
  const showPreview=async(kind:'excel'|'restore')=>run(async()=>{
    const file=kind==='excel'?excelFile:restoreFile
    if(!file)return
    const payload=await previewRequest(kind==='excel'?'/imports/preview':'/workspace/restore/preview',csrf,file,kind==='excel'?mode:undefined)
    setPreview(kind==='excel'?{...(payload as Omit<ExcelPreview,'kind'>),kind:'excel'}:{...(payload as Omit<RestorePreview,'kind'>),kind:'restore'})
    setReviewed(false)
  })
  const download=async(path:string,name:string,post=false)=>run(async()=>{const r=await fetch(`/api/v2${path}`,{method:post?'POST':'GET',credentials:'same-origin',headers:post?{'X-CSRF-Token':csrf}:undefined});if(!r.ok)throw new Error(`${r.status} ${r.statusText}`);const d=r.headers.get('content-disposition')||'',m=d.match(/filename\*?=(?:UTF-8''|\")?([^;\"\r\n]+)/i);save(await r.blob(),m?.[1]?decodeURIComponent(m[1].replaceAll('"','')):name);if(post)onChanged()})
  const apply=()=>{const current=preview;if(!current||!current.token)return;const token=current.token;void run(async()=>{await applyRequest(current.kind==='excel'?'/imports/apply':'/workspace/restore/apply',csrf,token);setPreview(null);setReviewed(false);onChanged()})}
  const excelErrors=preview?.kind==='excel'?messages(preview.validation?.errors):[]
  const restoreErrors=preview?.kind==='restore'?messages(preview.errors):[]
  const warnings=preview?.kind==='excel'?messages(preview.validation?.warnings):preview?.kind==='restore'?messages(preview.warnings):[]
  return <section className="transfer-hub">
    <nav className="transfer-tabs" aria-label="Imports et exports">{[['imports','Imports'],['exports','Exports'],['templates','Modèles Excel'],['plc','Programme PLC'],...(project.phase&&project.phase>=3?[['codesys','Référence CODESYS']]:[])] .map(([id,label])=><button key={id} className={tab===id?'active':''} onClick={()=>setTab(id as typeof tab)}>{label}</button>)}</nav>
    {error&&<div className="v2-alert error" role="alert">{error}</div>}
    {tab==='imports'&&<div className="transfer-grid"><article className="v2-panel"><h2>Importer un classeur Excel</h2><p>Choisissez un mode, vérifiez les changements, puis confirmez l’import.</p><label>Mode <select disabled={busy} value={mode} onChange={e=>{setMode(e.target.value as 'merge'|'replace');if(preview?.kind==='excel')setPreview(null)}}><option value="merge">Fusionner</option><option value="replace">Remplacer</option></select></label><input type="file" disabled={busy} accept=".xlsx" onChange={e=>{setExcelFile(e.target.files?.[0]||null);if(preview?.kind==='excel')setPreview(null)}}/><button className="button primary" disabled={!excelFile||busy} onClick={()=>void showPreview('excel')}>Prévisualiser</button></article>
      <article className="v2-panel"><h2>Restaurer une sauvegarde JSON</h2><p>La sauvegarde sera vérifiée avant son application.</p><input type="file" disabled={busy} accept=".json,application/json" onChange={e=>{setRestoreFile(e.target.files?.[0]||null);if(preview?.kind==='restore')setPreview(null)}}/><button className="button secondary" disabled={!restoreFile||busy} onClick={()=>void showPreview('restore')}>Vérifier la sauvegarde</button></article>
      {preview&&<article className="v2-panel transfer-review"><h2>Vérification : {preview.kind==='excel'?preview.filename||excelFile?.name:restoreFile?.name}</h2>
        {preview.kind==='excel'?<><p>Mode : {preview.mode==='merge'?'Fusionner':'Remplacer'}.</p><div className="diff-summary">{(['added','modified','removed'] as const).map(kind=><div className={`diff-tile ${kind}`} key={kind}><b>{preview.diff?.[kind]?.length||0}</b><span>{kind==='added'?'Ajouts':kind==='modified'?'Modifications':'Suppressions'}</span></div>)}</div><div className="diff-details">{(['added','modified','removed'] as const).map(kind=><section key={kind}><h4>{kind==='added'?'Ajouts':kind==='modified'?'Modifications':'Suppressions'} ({preview.diff?.[kind]?.length||0})</h4>{preview.diff?.[kind]?.length?<details><summary>Afficher les lignes concernées</summary>{preview.diff[kind].map((row,index)=><pre key={index}>{rowText(row)}</pre>)}</details>:<p>Aucun changement.</p>}</section>)}</div></>:<div>{preview.changes.map((change,index)=><p key={index}>{change}</p>)}</div>}
        {excelErrors.map((item,index)=><p className="error-text" key={`e${index}`}>Erreur : {item}</p>)}{restoreErrors.map((item,index)=><p className="error-text" key={`r${index}`}>Erreur : {item}</p>)}{warnings.map((item,index)=><p className="warning-text" key={`w${index}`}>Avertissement : {item}</p>)}
        <label><input type="checkbox" checked={reviewed} onChange={e=>setReviewed(e.target.checked)} disabled={excelErrors.length>0||restoreErrors.length>0||!preview.token}/> J’ai vérifié l’aperçu et ses avertissements.</label><div><button className="button secondary" onClick={()=>{setPreview(null);setReviewed(false)}}>Annuler</button><button className="button primary" disabled={!reviewed||busy||!preview.token||excelErrors.length>0||restoreErrors.length>0} onClick={apply}>Appliquer</button></div></article>}</div>}
    {tab==='exports'&&<div className="transfer-grid"><article className="v2-panel"><h2>Classeurs Excel</h2><p>Téléchargez un classeur individuel ou l’ensemble en ZIP.</p>{(project.workbooks||[]).map(b=><button className="button secondary" key={b.filename} onClick={()=>void download(`/exports/${encodeURIComponent(b.filename)}`,b.filename)}>{b.filename} ↓</button>)}<button className="button primary" disabled={busy} onClick={()=>void download('/workspace/excel','classeurs-excel.zip',true)}>Télécharger tous les classeurs (ZIP)</button></article><article className="v2-panel"><h2>FUXA</h2><p>Export des signaux au format CSV.</p><button className="button secondary" onClick={()=>void download('/exports/fuxa.csv','fuxa.csv')}>Télécharger CSV</button></article><article className="v2-panel"><h2>Sauvegarde projet</h2><p>Créer une sauvegarde JSON complète.</p><button className="button secondary" onClick={()=>void download('/workspace/backup','gaia-backup.json',true)}>Télécharger JSON</button></article></div>}
    {tab==='templates'&&<div className="transfer-grid"><article className="v2-panel"><h2>Modèles Excel</h2><p>Téléchargez les modèles officiels du pack, avec leurs listes de choix et info-bulles. Complétez-les puis renommez-les avant import : TYPE_MonType.xlsx, MODBUS_TCP.xlsx, MODBUS_RTU.xlsx, IO_PHYSICAL.xlsx, PARAMETERS_PA.xlsx ou EXCHANGES_EXC.xlsx.</p>{templatesLoading?<p>Chargement…</p>:templates.map(item=><div className="template-download" key={item.filename}><span><strong>{item.filename}</strong><small>{item.description}</small></span><button className="button secondary" disabled={busy} onClick={()=>void download(`/templates/${encodeURIComponent(item.filename)}`,item.filename)}>Télécharger</button></div>)}{!templatesLoading&&!templates.length&&<p>Aucun modèle disponible.</p>}<button className="button primary" disabled={busy||templatesLoading||!templates.length} onClick={()=>void download('/templates.zip','modeles-excel.zip')}>Télécharger tous les modèles (ZIP)</button></article></div>}
    {tab==='plc'&&<V2PlcExport csrf={csrf} projectRevision={revision} onExported={onChanged}/>}
    {tab==='codesys'&&<V2CodesysImport key={revision} csrf={csrf} onApplied={onChanged}/>}
  </section>
}
