import { useEffect, useRef, useState, type FormEvent } from 'react'
import IoCopyManager, { type CopyAction, type CopyState } from './IoCopyManager'
import WorkspacePanel from './WorkspacePanel'
import './rack-io-mapping.css'

type Channel = {
  key: string; module: string; label: string; iec_type: string; direction: 'input' | 'output'
  variable: string; locked: boolean; error?: string | null
}
type Variable = { name: string; iec_type: string; direction?: Channel['direction'] | null }
type Hardware = { filename: string; application: string; channels: Channel[]; variables: Variable[] }
type RackState = Omit<CopyState, 'hardware'> & { revision: number; hardware: Hardware | null }
type Draft = { revision: number; changes: Record<string, string | null> }

async function rackRequest(path: string, options?: RequestInit): Promise<Response> {
  const response = await fetch(`/api/v2/io-hardware${path}`, options)
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    throw new Error(typeof payload.detail === 'string' ? payload.detail : `Requête impossible (${response.status}).`)
  }
  return response
}

export default function RackIoMapping({ csrf, revision, onSaved }: { csrf: string; revision?: number; onSaved: () => void }) {
  const [state, setState] = useState<RackState>()
  const [file, setFile] = useState<File | null>(null)
  const [module, setModule] = useState('')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [feedbackArea, setFeedbackArea] = useState<'rack' | 'copies'>('copies')
  const [busy, setBusy] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const controller = new AbortController()
    void rackRequest('', { signal: controller.signal }).then(r => r.json())
      .then(result => { if (!controller.signal.aborted) setState(result) })
      .catch(cause => { if (!controller.signal.aborted) { setFeedbackArea('copies'); setError(cause.message) } })
    return () => controller.abort()
  }, [revision])

  const hardware = state?.hardware
  const modules = [...new Set(hardware?.channels.map(channel => channel.module) || [])]
  const selectedModule = modules.includes(module) ? module : modules[0]
  const changed = Object.keys(draft?.changes || {}).length
  const stale = !!changed && draft?.revision !== state?.revision
  const invalid = hardware?.channels.filter(channel => channel.error).length || 0

  const edit = (channel: Channel, target: string | null) => {
    if (!state || !hardware) return
    setNotice(''); setError('')
    const desired = target ? `${hardware.application}.GVL_IOMAP.${target}` : ''
    setDraft(previous => {
      const changes = { ...previous?.changes }
      if (desired === channel.variable) delete changes[channel.key]
      else changes[channel.key] = target
      return Object.keys(changes).length ? { revision: previous?.revision ?? state.revision, changes } : null
    })
  }

  async function run(action: () => Promise<void>, area: 'rack' | 'copies' = 'rack') {
    setFeedbackArea(area); setBusy(true); setError(''); setNotice('')
    try { await action() }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'La modification a échoué.') }
    finally { setBusy(false) }
  }

  const upload = (event: FormEvent) => {
    event.preventDefault()
    if (!file || !state || changed) return
    if (hardware && !window.confirm('Remplacer le modèle du rack et ses liaisons enregistrées dans Gaia ?')) return
    void run(async () => {
      const form = new FormData()
      form.set('file', file)
      form.set('revision', String(state.revision))
      const response = await rackRequest('/template', { method: 'POST', headers: { 'X-CSRF-Token': csrf }, body: form })
      setState(await response.json())
      setFile(null); setModule(''); setDraft(null)
      if (fileInput.current) fileInput.current.value = ''
      setNotice('Rack chargé. Les liaisons du fichier sont visibles dans le tableau.')
      onSaved()
    })
  }

  const save = () => {
    if (!draft || stale) return
    void run(async () => {
      const response = await rackRequest('/mappings', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(draft) })
      setState(await response.json())
      setDraft(null)
      setNotice('Mappage enregistré. Le XML matériel est prêt à télécharger si toutes les liaisons sont valides.')
      onSaved()
    })
  }

  const download = () => void run(async () => {
    const response = await rackRequest('/export')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'Gaia-mapping-ES.xml'
    link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  })

  const updateCopy = async (action: CopyAction) => {
    if (!state || changed) return
    await run(async () => {
      const response = await rackRequest('/copies', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify({ revision: state.revision, ...action }) })
      setState(await response.json())
      setNotice(action.action === 'delete' ? `${action.iomap} a été supprimée.` : action.action === 'disconnect' ? 'Recopie retirée. La variable et ses canaux restent en place.' : 'Recopie enregistrée.')
      onSaved()
    }, 'copies')
  }

  return <>
    <IoCopyManager state={state} busy={busy} blocked={!!changed} error={feedbackArea === 'copies' ? error : ''} notice={feedbackArea === 'copies' ? notice : ''} update={updateCopy}/>
    <WorkspacePanel id="communications:io-hardware" title="Mappage matériel CODESYS" minHeight={180}>
    <div className="rack-io-mapping">
      <form className="rack-io-upload" onSubmit={upload}>
        <label>XML du rack CODESYS<input ref={fileInput} type="file" accept=".xml,application/xml,text/xml" disabled={busy || !!changed} onChange={event => setFile(event.target.files?.[0] || null)} /></label>
        <button type="submit" className="comm-btn" disabled={!file || !state || busy || !!changed}>{hardware ? 'Remplacer le modèle' : 'Charger le modèle'}</button>
      </form>
      {feedbackArea === 'rack' && error && <p role="alert" className="comm-alert">{error}</p>}
      {feedbackArea === 'rack' && notice && <p role="status">{notice}</p>}
      {!hardware && <p>Charge le XML CODESYS du rack et de ses modules E/S. Les canaux pourront être reliés aux variables du projet dans <code>GVL_IOMAP</code>.</p>}
      {hardware && <>
        <p>Ce tableau relie le rack à <code>GVL_IOMAP</code>. Pour définir la recopie avec l’instance, utilise le panneau ci-dessus.</p>
        <div className="rack-io-toolbar">
          <label>Module<select value={selectedModule} onChange={event => setModule(event.target.value)}>{modules.map(name => <option key={name}>{name}</option>)}</select></label>
          <span className="rack-io-filename" title={hardware.filename}>{hardware.filename} · {hardware.channels.length} canaux</span>
          {!!changed && <button type="button" className="comm-btn" disabled={busy} onClick={() => { setDraft(null); setError('') }}>Annuler les changements</button>}
          <button type="button" className="comm-btn primary" disabled={!changed || stale || busy} onClick={save}>Enregistrer le mappage{changed ? ` (${changed})` : ''}</button>
          <button type="button" className="comm-btn" disabled={!!changed || !!invalid || busy} onClick={download}>Télécharger le XML matériel</button>
        </div>
        {stale && <p className="comm-warning" role="status">Le projet a changé depuis l’ouverture du brouillon. Annule les changements locaux pour repartir de la version actuelle.</p>}
        {!!invalid && <p className="comm-warning" role="status">{invalid} liaison(s) à corriger avant l’export. Les détails sont affichés sur les canaux concernés.</p>}
        <div className="rack-io-table-wrap"><table className="v2-table rack-io-table">
          <thead><tr><th>Canal</th><th>Sens</th><th>Type</th><th>Variable reliée</th><th>Action</th></tr></thead>
          <tbody>{hardware.channels.filter(channel => channel.module === selectedModule).map(channel => {
            const options = hardware.variables.filter(variable => variable.iec_type === channel.iec_type && (!variable.direction || variable.direction === channel.direction))
            const path = options.find(variable => `${hardware.application}.GVL_IOMAP.${variable.name}`.toLowerCase() === channel.variable.toLowerCase())?.name
            const original = path ?? (channel.variable ? '__original' : '')
            const edited = draft && channel.key in draft.changes
            const value = edited ? draft.changes[channel.key] ?? '' : original
            return <tr key={channel.key}>
              <td>{channel.label}</td><td>{channel.direction === 'input' ? 'Entrée' : 'Sortie'}</td><td><code>{channel.iec_type}</code></td>
              <td>{channel.locked ? <span>{channel.variable || 'Mappage au niveau du groupe'}</span> : <select aria-label={`Variable ${channel.module} ${channel.label}`} disabled={busy || stale} value={value} onChange={event => edit(channel, event.target.value || null)}>
                <option value="">Non relié</option>
                {original === '__original' && <option value="__original" disabled>{channel.variable}</option>}
                {options.map(variable => <option value={variable.name} key={variable.name}>{variable.name}</option>)}
              </select>}
              {channel.error && !edited && <small className="rack-io-error">{channel.error}</small>}</td>
              <td>{!channel.locked && value && <button type="button" className="comm-btn" disabled={busy || stale} onClick={() => edit(channel, null)}>Délier</button>}</td>
            </tr>
          })}</tbody>
        </table></div>
      </>}
    </div>
  </WorkspacePanel>
  </>
}
