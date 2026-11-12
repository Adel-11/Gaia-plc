import { useEffect, useRef, useState } from 'react'
import IoCopyManager, { type CopyAction, type CopyState } from './IoCopyManager'
import WorkspacePanel from './WorkspacePanel'
import './rack-io-mapping.css'

type Channel = NonNullable<CopyState['hardware']>['channels'][number] & { error?: string | null }
type Hardware = { filename: string; application: string; channels: Channel[]; variables: CopyState['variables']; cards: { name: string; slot: string | number | null; model: string | null }[] }
type RackState = Omit<CopyState, 'hardware'> & { revision: number; hardware: Hardware | null; can_load_gaia: boolean }
type Area = 'rack' | 'exports' | 'copies'
const files = { gvl: 'Gaia-GVL-ES.xml', gfc: 'Gaia-GFC-ES.xml', rack: 'Gaia-mapping-ES.xml', mapping: 'Gaia-mappage-canaux.csv', bundle: 'Gaia-exports-ES.zip' }

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
  const [file, setFile] = useState<File | null>(null), [module, setModule] = useState('')
  const [choices, setChoices] = useState<Record<string, string>>({})
  const [creating, setCreating] = useState(''), [signal, setSignal] = useState('')
  const [feedback, setFeedback] = useState<{ area: Area; error?: string; notice?: string }>()
  const [busy, setBusy] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const controller = new AbortController()
    void rackRequest('', { signal: controller.signal }).then(r => r.json())
      .then(result => { if (!controller.signal.aborted) setState(result) })
      .catch(cause => { if (!controller.signal.aborted) setFeedback({ area: 'rack', error: cause.message }) })
    return () => controller.abort()
  }, [revision])

  const hardware = state?.hardware
  const cards = hardware?.cards || []
  const selectedModule = cards.find(card => card.name === module) || cards[0]
  const invalid = hardware?.channels.filter(channel => channel.error).length || 0
  const json = (body: object): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body) })
  const label = (channel: Channel) => `${cards.find(card => card.name === channel.module)?.model || channel.module} · ${channel.label}`
  const path = (name: string) => `${hardware?.application}.GVL_IOMAP.${name}`.toLowerCase()

  async function run(area: Area, action: () => Promise<string>) {
    setBusy(true); setFeedback(undefined)
    try { setFeedback({ area, notice: await action() }); return true }
    catch (cause) { setFeedback({ area, error: cause instanceof Error ? cause.message : 'La modification a échoué.' }); return false }
    finally { setBusy(false) }
  }

  async function saveState(response: Response) {
    setState(await response.json())
    onSaved()
  }

  function upload(reference: boolean) {
    if (!state || (!reference && !file)) return
    if (hardware && !window.confirm('Remplacer le rack et les liaisons matérielles enregistrées dans Gaia par celles du fichier ?')) return
    void run('rack', async () => {
      if (reference) await saveState(await rackRequest('/template/gaia', json({ revision: state.revision })))
      else {
        const form = new FormData()
        form.set('file', file!)
        form.set('revision', String(state.revision))
        await saveState(await rackRequest('/template', { method: 'POST', headers: { 'X-CSRF-Token': csrf }, body: form }))
      }
      setFile(null); setModule(''); setChoices({}); setCreating('')
      if (fileInput.current) fileInput.current.value = ''
      return 'Rack chargé. Sélectionne une carte puis un canal.'
    })
  }

  function assign(channel: Channel, target: string | null) {
    if (!state || !hardware) return
    const changes: Record<string, string | null> = { [channel.key]: target }
    if (target) {
      const linked = hardware.channels.filter(item => item.key !== channel.key && item.variable.toLowerCase() === path(target) && (channel.direction === 'input' || item.direction === 'input'))
      if (linked.length) {
        if (!window.confirm(`Déplacer ${target} de ${linked.map(label).join(', ')} vers ${label(channel)} ? Les anciens canaux seront déliés.`)) return
        linked.forEach(item => { changes[item.key] = null })
      } else if (channel.variable && !window.confirm(`Remplacer la liaison de ${label(channel)} par ${target} ?`)) return
    }
    void run('rack', async () => {
      await saveState(await rackRequest('/mappings', json({ revision: state.revision, changes })))
      setChoices(old => { const next = { ...old }; Object.keys(changes).forEach(key => delete next[key]); return next })
      return target ? `${label(channel)} est relié à ${target}.` : `${label(channel)} est délié. La variable et sa recopie sont conservées.`
    })
  }

  function create(channel: Channel) {
    if (!state) return
    void run('rack', async () => {
      await saveState(await rackRequest('/channels/create-variable', json({ revision: state.revision, channel_key: channel.key, signal: signal.trim() })))
      setCreating(''); setSignal('')
      return `Variable ${channel.iec_type} créée et reliée à ${label(channel)}. Tu peux maintenant configurer sa recopie avec une instance.`
    })
  }

  function download(kind: keyof typeof files) {
    if (!state) return
    void run('exports', async () => {
      const response = await (kind === 'rack' ? rackRequest('/export') : kind === 'mapping' ? rackRequest('/mapping.csv') : rackRequest(`/exports/${kind}`, json({ revision: state.revision })))
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a')
      link.href = url; link.download = files[kind]; link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
      if (kind !== 'rack' && kind !== 'mapping') {
        const savedRevision = Number(response.headers.get('X-Gaia-Revision'))
        if (savedRevision) setState(current => current ? { ...current, revision: savedRevision } : current)
        onSaved()
      }
      return `${files[kind]} téléchargé.`
    })
  }

  function updateCopy(action: CopyAction) {
    if (!state) return Promise.resolve(false)
    return run('copies', async () => {
      await saveState(await rackRequest('/copies', json({ revision: state.revision, ...action })))
      return action.action === 'delete' ? `${action.iomap} a été supprimée.` : action.action === 'disconnect' ? 'Recopie retirée. La variable et ses canaux restent en place.' : 'Recopie enregistrée.'
    })
  }

  const message = (area: Area) => feedback?.area === area && (feedback.error ? <p role="alert" className="comm-alert">{feedback.error}</p> : <p role="status">{feedback.notice}</p>)

  return <>
    <WorkspacePanel id="communications:io-hardware" title="Cartes automate · Mappage E/S" minHeight={200}>
      <div className="rack-io-mapping">
        {message('rack')}
        <p>Choisis une carte puis une variable <code>GVL_IOMAP</code> sur le canal voulu. Retirer une liaison du canal conserve la variable et sa recopie ST.</p>
        {!state && !feedback?.error && <p role="status">Chargement des cartes…</p>}
        {!hardware && state && <>
          <p>Charge le XML des cartes exporté depuis CODESYS pour afficher leurs canaux réels.</p>
          {state.can_load_gaia && <div><button type="button" className="comm-btn primary" disabled={busy} onClick={() => upload(true)}>Charger les cartes Gaia V8</button></div>}
        </>}
        {hardware && <>
          <div className="rack-io-cards" role="group" aria-label="Cartes automate">
            {cards.map(card => {
              const channels = hardware.channels.filter(channel => channel.module === card.name)
              const count = channels.filter(channel => channel.variable).length
              return <button type="button" className="comm-btn rack-io-card" key={card.name} aria-pressed={selectedModule?.name === card.name} onClick={() => { setModule(card.name); setCreating('') }}>
                <small>{card.slot != null ? `Emplacement ${card.slot} · ${card.name}` : 'Module CODESYS'}</small>
                <strong>{card.model || card.name}</strong><span>{count} / {channels.length} canaux reliés</span>
              </button>
            })}
          </div>
          {!!invalid && <p className="comm-warning" role="status">{invalid} liaison(s) du rack à corriger avant l’export matériel.</p>}
          <div className="rack-io-table-wrap"><table className="v2-table rack-io-table">
            <thead><tr><th>Canal</th><th>Sens</th><th>Type IEC</th><th>IOMAP sur la carte</th><th>Actions</th></tr></thead>
            <tbody>{hardware.channels.filter(channel => channel.module === selectedModule?.name).map(channel => {
              const options = hardware.variables.filter(variable => variable.iec_type === channel.iec_type && (!variable.direction || variable.direction === channel.direction))
              const current = options.find(variable => path(variable.name) === channel.variable.toLowerCase())?.name || ''
              const choice = choices[channel.key] ?? current
              const isCreating = creating === channel.key
              return <tr key={channel.key}>
                <td>{channel.label}</td><td>{channel.direction === 'input' ? 'Entrée' : 'Sortie'}</td><td><code>{channel.iec_type}</code></td>
                <td>{channel.locked ? <span>{channel.variable || 'Mappage au niveau du groupe'}</span> : <>
                  <select aria-label={`IOMAP ${label(channel)}`} disabled={busy || !options.length} value={options.some(option => option.name === choice) ? choice : ''} onChange={event => setChoices({ ...choices, [channel.key]: event.target.value })}>
                    <option value="">{options.length ? 'Choisir une variable…' : `Aucun IOMAP ${channel.iec_type} compatible`}</option>
                    {options.map(variable => <option value={variable.name} key={variable.name}>{variable.name}</option>)}
                  </select>
                  {channel.variable && choice !== current && current && <small>Actuel : {current}</small>}
                  {!current && channel.variable && <small>Actuel : {channel.variable}</small>}
                </>}{channel.error && <small className="rack-io-error">{channel.error}</small>}</td>
                <td>{!channel.locked && <div className="rack-io-row-actions">
                  <button type="button" className="comm-btn" disabled={busy || !choice || choice === current || !options.some(option => option.name === choice)} onClick={() => assign(channel, choice)}>{channel.variable ? 'Remplacer' : 'Assigner'}</button>
                  {channel.variable ? <button type="button" className="comm-btn rack-io-danger" disabled={busy} onClick={() => assign(channel, null)}>Retirer du canal</button> :
                    <button type="button" className="comm-btn" disabled={busy} aria-expanded={isCreating} onClick={() => { setCreating(isCreating ? '' : channel.key); setSignal('') }}>Créer un IOMAP</button>}
                </div>}
                {isCreating && <form className="rack-io-create" onSubmit={event => { event.preventDefault(); create(channel) }}>
                  <label>Nom du signal<input value={signal} maxLength={80} pattern="[A-Za-z_][A-Za-z0-9_]*" required onChange={event => setSignal(event.target.value)} placeholder="Ex. Consigne_Pompe" /></label>
                  <button className="comm-btn primary" disabled={busy || !signal.trim()}>Créer et assigner</button>
                </form>}</td>
              </tr>
            })}</tbody>
          </table></div>
        </>}
        {state && <details open={!hardware || undefined}><summary>{hardware ? `Remplacer le XML du rack · ${hardware.filename}` : 'Importer un XML du rack'}</summary>
          <form className="rack-io-upload" onSubmit={event => { event.preventDefault(); upload(false) }}>
            <label>XML du rack CODESYS<input ref={fileInput} type="file" accept=".xml,application/xml,text/xml" disabled={busy} onChange={event => setFile(event.target.files?.[0] || null)} /></label>
            <button type="submit" className="comm-btn" disabled={!file || busy}>{hardware ? 'Remplacer le rack' : 'Importer le rack'}</button>
          </form>
        </details>}
      </div>
    </WorkspacePanel>
    <WorkspacePanel id="communications:io-exports" title="Exports E/S" minHeight={150}>
      <div className="rack-io-mapping">
        <p>Importe les GVL et les fonctions sous <b>Application</b> dans CODESYS. Le CSV met à jour les liaisons des canaux gérés dans Gaia sur le rack existant.</p>
        <div className="rack-io-toolbar">
          <button type="button" className="comm-btn" disabled={!state || busy} onClick={() => download('gvl')}>XML · GVL_IO et GVL_IOMAP</button>
          <button type="button" className="comm-btn" disabled={!state || busy} onClick={() => download('gfc')}>XML · GFC_IO_IN et GFC_IO_OUT</button>
          <button type="button" className="comm-btn" disabled={!hardware || !!invalid || busy} onClick={() => download('mapping')}>CSV · Mappage des canaux</button>
          <button type="button" className="comm-btn primary" disabled={!hardware || !!invalid || busy} onClick={() => download('bundle')}>ZIP · GVL, GFC et mappage</button>
        </div>
        {!hardware ? <p>Charge le rack pour activer le CSV et le ZIP.</p> : !!invalid && <p>Corrige les liaisons signalées pour exporter le mappage.</p>}
        {hardware && <details><summary>Configuration matérielle du rack</summary><p>L’import de ce XML peut remplacer le rack dans CODESYS.</p><button type="button" className="comm-btn" disabled={!!invalid || busy} onClick={() => download('rack')}>XML · Configuration du rack</button></details>}
        {message('exports')}
      </div>
    </WorkspacePanel>
    <IoCopyManager state={state} busy={busy} blocked={false} error={feedback?.area === 'copies' ? feedback.error || '' : ''} notice={feedback?.area === 'copies' ? feedback.notice || '' : ''} update={updateCopy}/>
  </>
}
