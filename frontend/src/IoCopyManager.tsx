import { useRef, useState } from 'react'
import WorkspacePanel from './WorkspacePanel'
import ExchangeWiring from './ExchangeWiring'

type Direction = 'input' | 'output'
type Variable = { name: string; iec_type: string; direction?: Direction | null; missing?: boolean; folder?: string }
type Member = Variable & { path: string }
type Copy = { iomap: string; instance: string; target: string; block: string; direction: Direction; st: string; generated: boolean }
type Channel = { key: string; module: string; label: string; iec_type: string; direction: Direction; locked: boolean; variable: string }
export type CopyState = {
  revision: number
  variables: Variable[]; instances: { name: string; members: Member[] }[]; copies: Copy[]; manual_blocks: string[]
  hardware: { application: string; channels: Channel[] } | null
}
export type CopyAction = { action: 'assign'; iomap: string; instance: string; member: string; direction: Direction; channel_key?: string; create?: boolean }
  | { action: 'disconnect' | 'delete'; iomap: string }

export default function IoCopyManager({ state, busy, blocked, error, notice, update, scope }: {
  state?: CopyState; busy: boolean; blocked: boolean; error: string; notice: string; update: (action: CopyAction) => Promise<boolean>
  scope?: { instances: string[] }
}) {
  const [pickedInstance, setInstance] = useState(''), [memberName, setMember] = useState(''), [iomap, setIomap] = useState('')
  const [create, setCreate] = useState(false), [newName, setNewName] = useState('')
  const [direction, setDirection] = useState<Direction>('input'), [channelKey, setChannel] = useState('')
  const [search, setSearch] = useState('')
  const [folder, setFolder] = useState('')
  const [graph, setGraph] = useState(false)
  const [armed, setArmed] = useState<string | null>(null)
  const instanceInput = useRef<HTMLSelectElement>(null)
  const instances = state?.instances.filter(item => !scope || scope.instances.includes(item.name)) || []
  const instance = pickedInstance || (scope ? instances[0]?.name || '' : '')
  const memberList = instances.find(item => item.name === instance)?.members || []
  const member = memberList.find(item => item.name === memberName)
  const sense = member?.direction || direction
  const variables = state?.variables || []
  const matching = variables.filter(variable => member && !variable.missing && (!folder || variable.folder === folder || variable.name === iomap) &&
    variable.iec_type === member.iec_type && (!variable.direction || variable.direction === sense))
  const selected = create && member && newName.trim() ? { name: newName.trim(), iec_type: member.iec_type } : matching.find(variable => variable.name === iomap)
  const copies = state?.copies || []
  const hardware = state?.hardware
  const path = (name: string) => `${hardware?.application}.GVL_IOMAP.${name}`.toLowerCase()
  const matchingChannels = hardware?.channels.filter(channel => selected && !channel.locked && channel.direction === sense && channel.iec_type === selected.iec_type && (!channel.variable || channel.variable.toLowerCase() === path(selected.name))) || []
  const channel = matchingChannels.find(item => item.key === channelKey)
  const targetBusy = copies.find(copy => copy.target === member?.path && copy.iomap !== selected?.name)
  const replacing = selected && !create && copies.find(copy => copy.iomap === selected.name && copy.target !== member?.path)
  const block = sense === 'input' ? 'GFC_IO_IN' : 'GFC_IO_OUT'
  const manual = state?.manual_blocks.includes(block)
  const rhs = `GVL_IOMAP.${selected?.name || ''}`
  const statement = member && selected ? (sense === 'input' ? `${member.path} := ${rhs};` : `${rhs} := ${member.path};`) : ''
  const disabled = busy || blocked
  const instanceCopies = copies.filter(copy => copy.instance === instance)
  const linked = new Set(instanceCopies.map(copy => copy.iomap))
  const graphVariables = variables.filter(variable => linked.has(variable.name) || (!folder || variable.folder === folder) &&
    !variable.missing && memberList.some(item => item.iec_type === variable.iec_type && (!item.direction || !variable.direction || item.direction === variable.direction)))
  function chooseWire(first: string, second: string) {
    if (disabled) return
    const [global, local] = first.startsWith('GVL_IOMAP.') ? [first,second] : [second,first]
    const variable = graphVariables.find(item => 'GVL_IOMAP.' + item.name === global)
    const target = memberList.find(item => item.path === local)
    if (!variable || !target || variable.iec_type !== target.iec_type ||
        (variable.direction && target.direction && variable.direction !== target.direction)) return
    setCreate(false); setIomap(variable.name); setMember(target.name); setChannel('')
    setDirection(target.direction || variable.direction || direction)
  }

  function edit(copy: Copy) {
    setInstance(copy.instance); setMember(copy.target.split('.').pop() || ''); setDirection(copy.direction)
    setCreate(false); setIomap(copy.iomap); setChannel(''); instanceInput.current?.focus()
  }

  return <WorkspacePanel id="communications:io-copies" title="Recopies entre GVL_IO et GVL_IOMAP" minHeight={220}>
    <div className="rack-io-mapping">
      <p>Choisis une instance, son membre E/S et une variable IOMAP compatible. Tu peux aussi relier un canal du rack à cette variable dans la même opération.</p>
      {error && <p role="alert" className="comm-alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {blocked && <p className="comm-warning">Enregistre ou annule d’abord le brouillon du rack ci-dessous.</p>}
      {!state && !error && <p role="status">Chargement des instances et des variables E/S…</p>}
      {state && <>
        {instance && <div className="rack-io-toolbar"><strong>{instance} · {instanceCopies.length} connexion(s)</strong>
          <button type="button" className="comm-btn" onClick={() => { setGraph(!graph); setArmed(null) }}>{graph ? 'Masquer le dessin' : 'Mapper graphiquement'}</button>
        </div>}
        {graph && instance && <><p>Relie deux variables dans le dessin, puis enregistre la recopie proposée.</p>
          <ExchangeWiring sources={graphVariables.map(variable => ({path:'GVL_IOMAP.' + variable.name, iec_type:variable.iec_type}))}
            destinations={memberList.map(item => ({...item,member:item.name}))}
            sourceParameter="GVL_IOMAP" destinationParameter={instance} bidirectional
            mappings={instanceCopies.map(copy => {
              const global = 'GVL_IOMAP.' + copy.iomap
              return {source:copy.direction === 'input' ? global : copy.target, destination:copy.direction === 'input' ? copy.target : global}
            })}
            armed={armed} selected={null} onArm={setArmed} onConnect={chooseWire} onSelect={() => {}}/>
        </>}
        <form className="rack-io-copy-form" onSubmit={event => {
          event.preventDefault()
          if (!member || !selected || disabled || targetBusy || manual) return
          if (replacing && !window.confirm(`Remplacer la recopie existante vers ${replacing.target} ?`)) return
          void update({ action: 'assign', iomap: selected.name, instance, member: member.name, direction: sense, channel_key: channel?.key, create }).then(saved => {
            if (saved && create) { setCreate(false); setIomap(selected.name); setNewName('') }
          })
        }}>
          <div className="rack-io-copy-grid">
            <label>Instance<select ref={instanceInput} value={instance} disabled={disabled || !instances.length} onChange={event => { setInstance(event.target.value); setMember(''); setIomap(''); setChannel('') }}>
              <option value="" disabled={!!scope && instances.length > 0}>{instances.length ? 'Choisir une instance…' : 'Aucune instance avec un DUT_IO'}</option>
              {instances.map(item => <option key={item.name}>{item.name}</option>)}
            </select></label>
            <label>Membre GVL_IO<select value={member?.name || ''} disabled={disabled || !instance} onChange={event => { setMember(event.target.value); setIomap(''); setChannel('') }}>
              <option value="">Choisir un membre…</option>{memberList.map(item => <option key={item.name} value={item.name}>{item.name} · {item.iec_type}</option>)}
            </select></label>
            <label>Sens de la recopie<select value={sense} disabled={disabled || !member || !!member.direction} onChange={event => { setDirection(event.target.value as Direction); setIomap(''); setChannel('') }}>
              <option value="input">Entrée · IOMAP → instance</option><option value="output">Sortie · instance → IOMAP</option>
            </select></label>
            <label>Dossier IOMAP<select value={folder} onChange={event => {setFolder(event.target.value);setArmed(null)}}><option value="">Tous les dossiers</option>
              {[...new Set(variables.map(variable => variable.folder).filter(Boolean))].sort().map(item => <option key={item}>{item}</option>)}
            </select></label>
            <label>Variable GVL_IOMAP<select value={create ? ':new' : selected?.name || ''} disabled={disabled || !member} onChange={event => { setCreate(event.target.value === ':new'); setIomap(event.target.value === ':new' ? '' : event.target.value); setChannel('') }}>
              <option value="">Choisir une variable…</option>
              {matching.map(variable => <option key={variable.name} value={variable.name}>{variable.name} · {variable.iec_type}</option>)}
              <option value=":new">Créer une variable IOMAP…</option>
            </select></label>
            {create && <label>Nom IOMAP<input value={newName} disabled={disabled || !member} onChange={event => { setNewName(event.target.value); setChannel('') }} placeholder="Ex. IOMAP_AI_PH"/></label>}
            {hardware && <label>Canal du rack (facultatif)<select value={channel?.key || ''} disabled={disabled || !matchingChannels.length} onChange={event => setChannel(event.target.value)}>
              <option value="">Ne pas modifier le rack</option>
              {matchingChannels.map(item => <option key={item.key} value={item.key}>{item.module} · {item.label}{item.variable ? ' · déjà relié' : ''}</option>)}
            </select></label>}
          </div>
          {statement && <div className="rack-io-st"><b>Aperçu ST · {block}</b><code>{statement}</code></div>}
          {targetBusy && <p className="comm-warning">Ce membre est déjà relié à {targetBusy.iomap}. Retire cette recopie avant de le réassigner.</p>}
          {replacing && <p className="comm-warning">La recopie actuelle vers <code>{replacing.target}</code> sera remplacée.</p>}
          {member && manual && <p className="comm-warning">{block} utilise du ST manuel. Rétablis son corps généré dans l’éditeur ST pour utiliser ce formulaire.</p>}
          {state.manual_blocks.includes('PLC_PRG') && <p className="comm-warning">PLC_PRG utilise du ST manuel. Vérifie qu’il appelle GFC_IO_IN et GFC_IO_OUT.</p>}
          <button className="comm-btn primary" disabled={disabled || !selected || !!targetBusy || manual}>{create ? 'Créer et mapper' : 'Enregistrer la recopie'}</button>
        </form>
        <div className="rack-io-toolbar"><b>Variables déclarées dans GVL_IOMAP</b><label className="rack-io-search">Filtrer<input value={search} onChange={event => setSearch(event.target.value)} placeholder="Variable, instance ou membre"/></label></div>
        <div className="rack-io-table-wrap"><table className="v2-table rack-io-table">
          <thead><tr><th>Variable · type</th><th>Recopie ST</th><th>Canaux du rack</th><th>Actions</th></tr></thead>
          <tbody>{variables.filter(variable => (!scope || linked.has(variable.name)) &&
            [variable.name, ...copies.filter(copy => copy.iomap === variable.name).map(copy => copy.target)].some(value => value.toLowerCase().includes(search.trim().toLowerCase()))).map(variable => {
            const links = copies.filter(copy => copy.iomap === variable.name)
            const channels = hardware?.channels.filter(item => item.variable.toLowerCase() === path(variable.name)) || []
            return <tr key={variable.name}>
              <td><code>{variable.name}</code><small>{variable.iec_type}{variable.missing && ' · retirée de la déclaration manuelle'}</small></td>
              <td>{links.length ? links.map((copy, index) => <div key={index} className="rack-io-copy-line"><b>{copy.block}{!copy.generated && ' · non générée'}</b><code>{copy.st}</code></div>) : 'Aucune recopie'}</td>
              <td>{channels.length ? channels.map(item => `${item.module} · ${item.label}`).join(', ') : '—'}</td>
              <td><div className="rack-io-row-actions">
                {links.length === 1 && links[0].instance && <button type="button" className="comm-btn" disabled={disabled} onClick={() => edit(links[0])}>Modifier</button>}
                {!!links.length && <button type="button" className="comm-btn" disabled={disabled} onClick={() => void update({ action: 'disconnect', iomap: variable.name })}>Retirer la recopie</button>}
                <button type="button" className="comm-btn rack-io-danger" disabled={disabled} aria-label={`Supprimer ${variable.name}`} onClick={() => {
                  if (window.confirm(`Supprimer GVL_IOMAP.${variable.name} du projet ? Ses recopies générées et ses liaisons dans le XML du rack seront retirées.`)) void update({ action: 'delete', iomap: variable.name })
                }}>Supprimer</button>
              </div></td>
            </tr>
          })}</tbody>
        </table></div>
        {!variables.length && <p>Aucune variable IOMAP. Ajoutes-en une dans le panneau de création.</p>}
      </>}
    </div>
  </WorkspacePanel>
}
