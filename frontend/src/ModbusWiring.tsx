import ModalFullscreen from './ModalFullscreen'
import { useEffect, useState } from 'react'
import ExchangeWiring from './ExchangeWiring'
import './modbus-wiring.css'

type Row = Record<string, any>
type WordOrder = 'MSW_FIRST' | 'LSW_FIRST'
type Link = { id: string; member: string; address: number; direction: string; manual?: boolean; plcVariable?: string; wordOrder?: WordOrder }
type Change = { collection: string; op: 'create' | 'update' | 'delete'; id?: string; values?: Row }
type Review = { token?: string; changes?: string[]; errors?: string[]; warnings?: string[]; manual_actions?: string[] }
type Props = {
  mode: 'tcp' | 'rtu'; revision: number
  instances: { key: string; name: string; typeId?: string }[]; peers: { id: string; label: string; ranges?: Row; wordOrder?: string }[]
  points: Row[]; capacity: Row
  members: (instance: string) => Row[]
  switchMode?: (mode: 'tcp' | 'rtu') => void
  templateOnly?: boolean
  templateLines?: {id: string; label: string; occupied: number[]}[]
  request: (path: string, body?: Row) => Promise<any>
  onSaved: (configuration: Row) => void; onClose: () => void
}

const tcpTables = { coils: 'Coils', discrete_inputs: 'Discrete inputs', input_registers: 'Input registers', holding_registers: 'Holding registers' }
const rtuTables = { Coil: 'Coils', 'Discrete input': 'Discrete inputs', IR: 'Input registers', HR: 'Holding registers' }
const PAGE = 20
const bits = new Set(['coils', 'discrete_inputs', 'Coil', 'Discrete input'])
const readOnly = new Set(['discrete_inputs', 'input_registers', 'Discrete input', 'IR'])
const wideTypes = new Set(['REAL', 'DWORD', 'DINT', 'UDINT', 'TIME', 'T', 'DATE', 'DT', 'DATE_AND_TIME', 'TOD', 'TIME_OF_DAY'])
const wordTypes = new Set(['WORD', 'INT', 'UINT', 'BYTE', 'SINT', 'USINT'])
const registers = (link: Link) => link.wordOrder ? [link.address, link.address + 1] : [link.address]
const orderOptions = <><option value="MSW_FIRST">Mot fort puis mot faible (MSW → LSW)</option><option value="LSW_FIRST">Mot faible puis mot fort (LSW → MSW)</option></>
const value = (row: Row, ...keys: string[]) => keys.map(key => row[key]).find(item => item != null && item !== '')
const address = (row: Row) => Number(value(row, 'address', 'Modbus address', 'Equipment address'))
const memberPath = (row: Row) => {
  const qualified = String(value(row, 'GVL_COM variable') || '')
  const fromPlc = qualified.match(/^(?:GVL_COM\.)?COM_[^.]+\.(.+)$/i)?.[1]
  return 'COM.' + (fromPlc || String(value(row, 'member_path', 'Member', 'member_name') || '').replace(/^COM\./i, ''))
}

export default function ModbusWiring({ mode, revision, instances, peers, points, capacity, members, switchMode, templateOnly, templateLines = [], request, onSaved, onClose }: Props) {
  const tcp = mode === 'tcp'
  const tables: Record<string, string> = tcp ? tcpTables : rtuTables
  const [instance, setInstance] = useState(instances[0]?.key || '')
  const [client, setClient] = useState(peers[0]?.id || '')
  const [table, setTable] = useState(tcp ? 'holding_registers' : 'HR')
  const [start, setStart] = useState('')
  const [towardPlc, setTowardPlc] = useState(false)
  const [armed, setArmed] = useState<string | null>(null)
  const [showMapped, setShowMapped] = useState(true)
  const [alignByAddress, setAlignByAddress] = useState(false)
  const [pending, setPending] = useState<Link[]>([])
  const [deletions, setDeletions] = useState<string[]>([])
  const [orders, setOrders] = useState<Record<string, WordOrder>>({})
  const [newOrder, setWordOrder] = useState<WordOrder>()
  const [review, setReview] = useState<Review | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [base, setBase] = useState(revision)
  const [rtuLine, setRtuLine] = useState('')
  const [slaveAddress, setSlaveAddress] = useState('')
  const [includeGfc, setIncludeGfc] = useState(false)

  const orderCount = Object.keys(orders).length
  const dirty = pending.length > 0 || deletions.length > 0 || orderCount > 0
  const instanceName = instances.find(row => row.key === instance)?.name || ''
  const peer = peers.find(row => row.id === (tcp ? client : instanceName))
  const wordOrder = newOrder ?? (/\bCDAB\b|\bLSW\b.*\bMSW\b/i.test(peer?.wordOrder || '') ? 'LSW_FIRST' : 'MSW_FIRST')
  const direction = tcp ? (towardPlc ? 'Client to PLC' : 'PLC to client') : (towardPlc ? 'Equipment to PLC' : 'PLC to equipment')
  const actualDirection = readOnly.has(table) ? (tcp ? 'PLC to client' : 'Equipment to PLC') : direction
  const collection = tcp ? 'tcp_points' : 'rtu_channels'
  const inTable = (row: Row) => String(value(row, 'table', 'Table', 'sheet')).toLowerCase() === table.toLowerCase()
  const byInstance = (row: Row) => [instance, instanceName].includes(String(value(row, 'instance_id', 'Instance', 'instance') || ''))
  const byPeer = (row: Row) => tcp ? String(value(row, 'Client', 'client_id') || '') === client : byInstance(row)
  const tableRows = points.filter(row => inTable(row) && (tcp || byPeer(row)))
  const scopedRows = tableRows.filter(row => byPeer(row) && byInstance(row) && Number.isFinite(address(row)))
  const rawDirection = (row: Row) => String(value(row, 'Direction', 'direction') || '')
  const pairedMember = (row: Row) => memberPath(row).replace(/\.Word_value\[\d+\]$/i, '.Real_value')
  const pairWord = (row: Row) => /^(LSW|MSW)$/i.test(row.Word || '') &&
    (pairedMember(row) !== memberPath(row) || (tcp || row.manual_scalar === true) &&
      wideTypes.has(String(value(row, 'Type', 'data_type') || '').toUpperCase()))
  const removed = new Set(deletions)
  const existing: Link[] = []
  const grouped = new Set<Row>()
  for (const row of [...scopedRows].sort((a, b) => address(a) - address(b))) {
    if (grouped.has(row)) continue
    const id = String(row.id)
    const other = pairWord(row) && scopedRows.find(item => address(item) === address(row) + 1
      && pairWord(item) && String(item.Word).toUpperCase() !== String(row.Word).toUpperCase()
      && pairedMember(item) === pairedMember(row) && rawDirection(item) === rawDirection(row) && item.Name === row.Name)
    if (other) {
      grouped.add(other)
      if (removed.has(id)) removed.add(String(other.id))
    }
    existing.push({ id, member: other ? pairedMember(row) : memberPath(row), address: address(row), direction: rawDirection(row),
      manual: row.manual_scalar === true || row.dip1500_register === true, plcVariable: String(value(row, 'GVL_MODBUS variable') || ''),
      wordOrder: other ? orders[id] || (String(row.Word).toUpperCase() === 'MSW' ? 'MSW_FIRST' : 'LSW_FIRST') : undefined })
  }
  const effective = [...existing.filter(link => !deletions.includes(link.id)), ...pending]
  const occupied = new Set([...tableRows.filter(row => !removed.has(String(row.id))).map(address), ...pending.flatMap(registers)])
  const range = peer?.ranges?.[table]
  const cap = capacity?.[table]
  const min = tcp ? Math.max(Number(range?.[0] ?? 1), Number(cap?.[0] ?? 0)) : 0
  const max = tcp ? Math.min(Number(range?.[1] ?? -1), Number(cap?.[1] ?? 65535)) : 65535
  const firstFree = () => { for (let n = min; n <= max; n += 1) if (!occupied.has(n)) return n; return min }
  const offset = start === '' ? firstFree() : Number(start)
  const available = peer && Number.isInteger(offset) && offset >= min && offset <= max
    ? Array.from({ length: Math.min(PAGE, max - offset + 1) }, (_, i) => offset + i).filter(n => !occupied.has(n))
    : []
  const choices = members(instance).filter(row => {
    const dtype = String(row.iec_type).toUpperCase()
    return bits.has(table) ? dtype === 'BOOL' : wordTypes.has(dtype) || wideTypes.has(dtype)
  })
  const ports = [...choices, ...effective.filter(link => !choices.some(row => row.path === link.member)).map(link => ({ path: link.member, iec_type: 'Liaison existante' }))]
  const uniqueSources = [...new Map(ports.map(row => [String(row.path), { path: String(row.path), iec_type: String(row.iec_type) }])).values()]
  const mapped = new Set(effective.map(link => link.member))
  const links = showMapped ? [...effective].sort((a, b) => a.address - b.address) : []
  const assigned = new Map(links.flatMap(link => registers(link).map((n, i) =>
    [n, link.wordOrder ? (i === 0) === (link.wordOrder === 'MSW_FIRST') ? 'MSW · mot fort' : 'LSW · mot faible' : 'Affectée'] as const)))
  const destinations = [...new Set([...assigned.keys(), ...available])].sort((a,b) => a-b)
    .map(n => ({ path: 'Adresse.' + n, member: String(n), iec_type: assigned.get(n) || 'Libre' }))
  const sourceByPort = new Map<string, string>()
  const sourceForLink = new Map<Link, string>()
  let sources: { path: string; iec_type: string; member?: string; row?: number }[] = uniqueSources.filter(port => showMapped || !mapped.has(port.path))
  if (alignByAddress && showMapped) {
    const takenRows = new Set<number>()
    let extraRow = destinations.length
    const aligned = links.map((link, index) => {
      const path = `${link.member}@${index}`
      const destinationRow = destinations.findIndex(port => port.path === `Adresse.${link.address}`)
      const row = takenRows.has(destinationRow) ? extraRow++ : destinationRow
      takenRows.add(row)
      sourceByPort.set(path, link.member)
      sourceForLink.set(link, path)
      return { path, member: link.member, row, iec_type: uniqueSources.find(port => port.path === link.member)?.iec_type ?? '' }
    })
    const freeRows = destinations.flatMap((_, index) => takenRows.has(index) ? [] : [index])
    sources = [...aligned, ...uniqueSources.filter(port => !mapped.has(port.path))
      .map((port, index) => ({ ...port, row: freeRows[index] ?? extraRow++ }))].sort((a, b) => a.row - b.row)
  }

  useEffect(() => { if (!dirty && !busy) setBase(revision) }, [revision, dirty, busy])
  useEffect(() => {
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape' && !busy) { if (review) setReview(null); else if (!dirty || window.confirm('Abandonner les modifications Modbus ?')) onClose() } }
    document.addEventListener('keydown', key)
    return () => document.removeEventListener('keydown', key)
  }, [busy, review, dirty, onClose])
  const resetScope = () => { setArmed(null); setStart(''); setWordOrder(undefined); setError(''); setReview(null) }
  const connect = (first: string, second: string) => {
    if (busy || revision !== base) return
    const [port, target] = first.startsWith('Adresse.') ? [second, first] : [first, second]
    const source = sourceByPort.get(port) ?? port
    const n = Number(target.split('.')[1])
    const choice = choices.find(row => row.path === source)
    if (!choice) { setError('Cette variable ne peut pas être ajoutée dans cette table.'); return }
    if (!available.includes(n)) { setError('Choisis une adresse libre.'); return }
    const wide = wideTypes.has(String(choice.iec_type).toUpperCase())
    if (wide && (n >= max || occupied.has(n + 1))) { setError('Choisis deux registres libres et consécutifs dans la plage.'); return }
    if (effective.some(link => link.member === source && (link.direction === actualDirection || / to PLC$/i.test(link.direction)))) {
      setError('Ce membre est déjà relié dans cette table. Retire la liaison avant de la déplacer.'); return
    }
    const plcVariable = tcp ? undefined : 'GVL_MODBUS.RTU_' + `${instanceName}_${table}_${n}`.replace(/[^A-Za-z0-9_]/g, '_')
    setPending(rows => [...rows, { id: 'draft:' + n, member: source, address: n, direction: actualDirection, plcVariable, wordOrder: wide ? wordOrder : undefined }])
    setError(''); setReview(null)
  }
  const save = async () => {
    setError(''); setBusy(true)
    try {
      const changes: Change[] = [
        ...deletions.map(id => ({ collection, id, op: 'delete' as const })),
        ...Object.entries(orders).filter(([id]) => !deletions.includes(id)).map(([id, order]) =>
          ({ collection, id, op: 'update' as const, values: { 'Register word order': order } })),
        ...pending.map(link => ({ collection, op: 'create' as const, values: {
          Instance: instance, Member: link.member.slice(4), Table: table, Direction: link.direction,
          ...(link.wordOrder ? { 'Register word order': link.wordOrder } : {}),
          ...(tcp ? { Client: client, 'Modbus address': link.address } : { 'Equipment address': link.address, 'GVL_MODBUS variable': link.plcVariable }),
        } })),
      ]
      setReview(await request('/preview', { revision: base, changes }))
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Impossible de préparer les liaisons.') }
    finally { setBusy(false) }
  }
  const apply = async () => {
    if (!review?.token || review.errors?.length) return
    setError(''); setBusy(true)
    try {
      await request('/apply', { token: review.token })
      onSaved(await request(''))
      setPending([]); setDeletions([]); setOrders({}); setReview(null); setArmed(null)
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Impossible d’enregistrer les liaisons.') }
    finally { setBusy(false) }
  }
  const selectedLine = templateLines.find(line => line.id === rtuLine) || templateLines[0]
  const maximum = instances.find(row => row.key === instance)?.typeId === 'DIP1500' ? 31 : 247
  const freeSlaves = selectedLine ? Array.from({length: maximum}, (_, i) => i + 1).filter(n => !selectedLine.occupied.includes(n)) : []
  const selectedSlave = freeSlaves.includes(Number(slaveAddress)) ? slaveAddress : String(freeSlaves[0] ?? '')
  const previewTemplate = async () => {
    setBusy(true); setError(''); setReview(null)
    try {
      setReview(await request('/preview', {revision, changes: [], modbus_template: {
        instance_id: instance, protocol: mode, client: tcp ? client : undefined, line: selectedLine?.id,
        address: selectedSlave ? Number(selectedSlave) : undefined, include: includeGfc,
      }}))
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Impossible de préparer le modèle Modbus.') }
    finally { setBusy(false) }
  }
  const close = () => { if (!dirty || window.confirm('Abandonner les modifications Modbus ?')) onClose() }
  const objectOnLeft = !actualDirection.endsWith(' to PLC')
  const objectLabel = `COM · ${instanceName}`
  const modbusLabel = `Modbus · ${tables[table]}`
  const updateOrder = (link: Link, order: WordOrder) => {
    setReview(null)
    if (link.id.startsWith('draft:')) setPending(rows => rows.map(row => row.id === link.id ? { ...row, wordOrder: order } : row))
    else setOrders(previous => ({ ...previous, [link.id]: order }))
  }

  return <div className="modbus-backdrop"><section className="modbus-wiring" role="dialog" aria-modal="true" aria-labelledby="modbus-wiring-title"><ModalFullscreen onClose={close} disabled={busy}/>
    <header><div><h3 id="modbus-wiring-title">{templateOnly ? 'Appliquer le modèle Modbus' : `Mapping graphique Modbus ${mode.toUpperCase()}`}</h3><p>{templateOnly ? 'Choisis le protocole et vérifie les adresses proposées.' : 'Choisis une variable COM et une adresse libre, ou tire un lien entre les deux.'}</p></div></header>
    <div className="modbus-wiring-scroll">
      <div className="modbus-wiring-controls" aria-label="Modèle Modbus du type">
        {switchMode && <label>Protocole<select value={mode} disabled={dirty || busy} onChange={event => switchMode(event.target.value as 'tcp' | 'rtu')}><option value="tcp">TCP</option><option value="rtu">RTU</option></select></label>}
        {!tcp && !peer && <>
          <label>Ligne RS-485<select value={selectedLine?.id || ''} disabled={busy || dirty} onChange={event => { setRtuLine(event.target.value); setSlaveAddress(''); setReview(null) }}>
            {templateLines.map(line => <option key={line.id} value={line.id}>{line.label}</option>)}
          </select></label>
          <label>Adresse esclave libre<select value={selectedSlave} disabled={busy || dirty} onChange={event => { setSlaveAddress(event.target.value); setReview(null) }}>
            {freeSlaves.map(number => <option key={number}>{number}</option>)}
          </select></label>
        </>}
        <label><input type="checkbox" checked={includeGfc} disabled={busy || dirty} onChange={event => { setIncludeGfc(event.target.checked); setReview(null) }}/> Ajouter aux GFC COM</label>
        <button type="button" className="comm-btn" disabled={busy || dirty || !instance || (tcp ? !client : !peer && !selectedSlave)}
          onClick={() => void previewTemplate()}>Appliquer le modèle {mode.toUpperCase()}…</button>
      </div>
      <div className="modbus-wiring-controls">
        <label>Instance<select value={instance} disabled={dirty || busy || !instances.length} onChange={e => { setInstance(e.target.value); resetScope() }}>{instances.map(row => <option key={row.key} value={row.key}>{row.name}</option>)}</select></label>
        {tcp && <label>Client<select value={client} disabled={dirty || busy} onChange={e => { setClient(e.target.value); resetScope() }}>{peers.map(row => <option key={row.id} value={row.id}>{row.label}</option>)}</select></label>}
        {!templateOnly && <><label>Table<select value={table} disabled={dirty || busy} onChange={e => { setTable(e.target.value); resetScope() }}>{Object.entries(tables).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label>Sens<select value={objectOnLeft ? 'device' : 'plc'} disabled={readOnly.has(table) || busy} onChange={e => { setTowardPlc(e.target.value === 'plc'); setArmed(null) }}><option value="plc">{tcp ? 'Client' : 'Équipement'} → PLC</option><option value="device">PLC → {tcp ? 'client' : 'équipement'}</option></select></label>
        <label>Afficher à partir de<input type="number" min={min} max={max} step="1" value={start} placeholder={String(offset)} onChange={e => setStart(e.target.value)} disabled={!peer || busy}/></label>
        {choices.some(choice => wideTypes.has(String(choice.iec_type).toUpperCase())) &&
          <label>Ordre des mots à l’ajout<select value={wordOrder} disabled={busy} onChange={event => setWordOrder(event.target.value as WordOrder)}>{orderOptions}</select></label>}</>}
      </div>
      {!templateOnly && (!instance ? <p className="comm-help">Crée une instance de ce type pour lui affecter des adresses.</p> : !peer ? <p className="comm-help">{tcp ? 'Configure un client TCP avant de mapper une adresse.' : 'Choisis la ligne et l’adresse esclave pour appliquer le modèle RTU.'}</p> :
        <><div className="modbus-view-tools">
          <button type="button" className="comm-btn" aria-pressed={!showMapped} onClick={() => { setShowMapped(!showMapped); setArmed(null) }}>{showMapped ? 'Masquer les variables mappées' : 'Afficher les variables mappées'}</button>
          <button type="button" className="comm-btn" aria-pressed={alignByAddress} onClick={() => { setAlignByAddress(!alignByAddress); setArmed(null) }}>{alignByAddress ? 'Rétablir l’ordre des variables' : 'Aligner par adresse'}</button>
        </div><p className="comm-help">Plage {min}–{max} · {available.length} adresses libres affichées{!choices.length && ' · Aucun membre COM compatible avec cette table'}</p>
          <ExchangeWiring sources={objectOnLeft ? sources : destinations} destinations={objectOnLeft ? destinations : sources}
            sourceParameter={objectOnLeft ? objectLabel : modbusLabel} destinationParameter={objectOnLeft ? modbusLabel : objectLabel}
            mappings={links.flatMap(link => registers(link).map(n => {
              const source = sourceForLink.get(link) ?? link.member
              const target = `Adresse.${n}`
              return { id: `${link.id}:${n}`, source: link.direction.endsWith(' to PLC') ? target : source, destination: link.direction.endsWith(' to PLC') ? source : target }
            }))}
            armed={armed} selected={null} onArm={setArmed} onConnect={connect} onSelect={() => {}} bidirectional />
          <ul className="modbus-links">{links.map(link => <li key={link.id}><b>{link.member} ↔ {registers(link).join(' + ')}</b><span>{link.direction}</span>
            {link.wordOrder && <label>Ordre des mots<select aria-label={`Ordre des mots de ${link.member} à l’adresse ${link.address}`} value={link.wordOrder} disabled={busy} onChange={event => updateOrder(link, event.target.value as WordOrder)}>{orderOptions}</select></label>}
            {!link.wordOrder && link.plcVariable && <span>{link.plcVariable}</span>}
            {(link.id.startsWith('draft:') || tcp || link.manual) && <button type="button" className="comm-btn small" disabled={busy} onClick={() => {
              setReview(null)
              if (link.id.startsWith('draft:')) setPending(rows => rows.filter(row => row.id !== link.id))
              else setDeletions(ids => [...ids, link.id])
            }}>Retirer le lien</button>}</li>)}</ul>
        </>)}
      {error && <p role="alert" className="comm-alert">{error}</p>}
      {revision !== base && dirty && <p role="alert" className="comm-alert">Le projet a changé. Annule ce brouillon puis rouvre le mapping.</p>}
      {review && <section className="modbus-review" aria-label="Revue du mapping">
        <h4>Vérifier les liaisons avant d’enregistrer</h4>
        {[...(review.errors || []), ...(review.warnings || []), ...(review.manual_actions || [])].map((item, i) => <p key={i}>{String(item)}</p>)}
        {!dirty && review.changes?.map(item => <p key={item}>{item}</p>)}
        {!!review.token && dirty && <p>{pending.length} liaison(s) à ajouter · {deletions.length} à retirer · {orderCount} ordre(s) des mots à modifier.</p>}
      </section>}
    </div>
    <footer><span>{pending.length} ajout(s) · {deletions.length} retrait(s){orderCount > 0 && ` · ${orderCount} modification(s)`}</span>{dirty && <button type="button" className="comm-btn" disabled={busy} onClick={() => { setPending([]); setDeletions([]); setOrders({}); setReview(null); setError('') }}>Annuler le brouillon</button>}
      {review?.token ? <button type="button" className="comm-btn primary" disabled={busy || revision !== base} onClick={() => void apply()}>Confirmer l’enregistrement</button> :
        <button type="button" className="comm-btn primary" disabled={!dirty || busy || revision !== base} onClick={() => void save()}>Enregistrer</button>}</footer>
  </section></div>
}
