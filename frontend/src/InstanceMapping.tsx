import { useEffect, useState } from 'react'
import IoCopyManager, { type CopyAction, type CopyState } from './IoCopyManager'
import ModalFullscreen from './ModalFullscreen'
import ModbusWiring from './ModbusWiring'
import './rack-io-mapping.css'
import './communications.css'

type Instance = { id?: string; name?: string; type_id: string }
type Configuration = {
  revision: number
  instances: Instance[]
  tcp_clients: { id: string; Client?: string; word_order?: string; 'Word order (32-bit)'?: string; ranges: Record<string, number[]> }[]
  rtu_slaves: { id: string; Instance?: string; instance_id?: string; word_order?: string; '32-bit word order'?: string }[]
  tcp_points: Record<string, unknown>[]
  rtu_channels: Record<string, unknown>[]
  tcp_capacity: Record<string, number[]>
  options: { communication_members: Record<string, { path: string; iec_type: string }[]> }
}

async function api(path: string, csrf: string, body?: object) {
  const response = await fetch(`/api/v2${path}`, body ? {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
    body: JSON.stringify(body),
  } : undefined)
  const result = await response.json()
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Impossible de charger ou d’enregistrer le mapping.')
  return result
}

export default function InstanceMapping({ role, instance, csrf, onClose, onSaved }: {
  role: 'IO' | 'COM'; instance: string; csrf: string; onClose: () => void; onSaved: () => void
}) {
  const [config, setConfig] = useState<Configuration>()
  const [io, setIo] = useState<CopyState>()
  const [mode, setMode] = useState<'tcp' | 'rtu'>('tcp')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let active = true
    const requests = [api('/configuration', csrf), role === 'IO' ? api('/io-hardware', csrf) : Promise.resolve(undefined)]
    void Promise.all(requests).then(([configuration, copies]) => {
      if (active) { setConfig(configuration); setIo(copies) }
    }).catch(cause => { if (active) setError(cause.message) })
    return () => { active = false }
  }, [role, csrf])

  const instances = (config?.instances || []).filter(row => instance === row.name || instance === row.id)
    .map(row => ({ key: String(row.id ?? row.name), name: String(row.name ?? row.id) }))
  const updateCopy = async (action: CopyAction) => {
    if (!io) return false
    setBusy(true); setError(''); setNotice('')
    try {
      setIo(await api('/io-hardware/copies', csrf, { revision: io.revision, ...action }))
      setNotice('Mapping IOMAP enregistré.')
      onSaved()
      return true
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Impossible d’enregistrer la recopie.')
      return false
    }
    finally { setBusy(false) }
  }
  if (config && role === 'COM') {
    const peers = mode === 'tcp'
      ? config.tcp_clients.map(row => ({ id: row.Client || row.id, label: row.Client || row.id, ranges: row.ranges,
        wordOrder: row['Word order (32-bit)'] || row.word_order }))
      : config.rtu_slaves.map(row => ({ id: row.Instance || row.instance_id || row.id, label: row.Instance || row.instance_id || row.id,
        wordOrder: row['32-bit word order'] || row.word_order }))
    return <ModbusWiring key={mode} mode={mode} switchMode={setMode} revision={config.revision} instances={instances}
      peers={peers} points={mode === 'tcp' ? config.tcp_points : config.rtu_channels} capacity={config.tcp_capacity}
      members={key => config.options.communication_members[instances.find(row => row.key === key)?.name || key] || []}
      request={(path, body) => api('/configuration' + path, csrf, body)}
      onSaved={fresh => { setConfig(fresh as Configuration); onSaved() }} onClose={onClose}/>
  }
  return <div className="modbus-backdrop"><section className="modbus-wiring" role="dialog" aria-modal="true" aria-labelledby="instance-mapping-title">
    <ModalFullscreen onClose={onClose} disabled={busy}/>
    <header><h3 id="instance-mapping-title">{role === 'IO' ? 'Mapping IOMAP' : 'Mapping Modbus'}</h3></header>
    <div className="modbus-wiring-scroll">
      {io && config ? (instances.length ? <IoCopyManager state={io} busy={busy} blocked={false} error={error}
        notice={notice} update={updateCopy} scope={{ instances: instances.map(row => row.name) }}/>
        : <p>Instance introuvable.</p>) : error ? <p role="alert" className="comm-alert">{error}</p> : <p role="status">Chargement du mapping…</p>}
    </div>
  </section></div>
}
