import { useId, useState } from 'react'
import type { FormEvent } from 'react'
import WorkspacePanel from './WorkspacePanel'
import './generic-objects.css'

type Direction = 'input' | 'output'
type SignalKind = 'analog' | 'digital'
export type GenericSignal = {
  id: string
  name: string
  direction: Direction
  kind: SignalKind
  iec_type: string
  unit?: string
  comment?: string
  io_name?: string
  internal_name?: string
  exchange_name?: string
}
export type GenericMember = {
  id: string
  role: 'PA' | 'COM' | 'EXC' | 'IO' | 'INT'
  name: string
  iec_type: string
  default_value?: string
  comment?: string
  unit?: string
  section?: string
}
export type GenericTemplate = {
  id: string
  name: string
  description: string
  builtin: boolean
  signals: GenericSignal[]
  members: GenericMember[]
  processing: string
}

const groups: { direction: Direction; kind: SignalKind; label: string; prefix: string }[] = [
  { direction: 'input', kind: 'analog', label: 'Entrées analogiques', prefix: 'MESURE' },
  { direction: 'output', kind: 'analog', label: 'Sorties analogiques', prefix: 'CONSIGNE' },
  { direction: 'input', kind: 'digital', label: 'Entrées TOR', prefix: 'ETAT' },
  { direction: 'output', kind: 'digital', label: 'Sorties TOR', prefix: 'COMMANDE' },
]
const analogTypes = ['DWORD', 'REAL', 'WORD', 'INT', 'UINT', 'DINT', 'UDINT', 'LREAL', 'BYTE']
const codeRole = (role: GenericMember['role']) => role === 'INT' ? 'INTERN' : role
const memberRoles: { role: GenericMember['role']; label: string }[] = [
  { role: 'PA', label: 'Paramètres' },
  { role: 'COM', label: 'Communication' },
  { role: 'EXC', label: 'Échanges' },
  { role: 'IO', label: 'Entrées / sorties' },
  { role: 'INT', label: 'Variables internes' },
]
const ioName = (signal: GenericSignal) =>
  signal.io_name?.trim() || `${signal.direction === 'input' ? 'INPUT' : 'OUTPUT'}_${signal.name}`
const internName = (signal: GenericSignal) => signal.internal_name?.trim() || signal.name
const excName = (signal: GenericSignal) => signal.exchange_name?.trim() || `OUTPUT_${signal.name}`

function generatedMembers(signals: GenericSignal[], role: GenericMember['role']) {
  if (role === 'IO') return signals.map(signal => ({ name: ioName(signal), type: signal.iec_type, id: signal.id }))
  if (role === 'INT') return signals.map(signal => ({ name: internName(signal), type: signal.iec_type, id: signal.id }))
  if (role === 'EXC') return signals.filter(signal => signal.direction === 'input')
    .map(signal => ({ name: excName(signal), type: signal.iec_type, id: signal.id }))
  return []
}

function freshName(prefix: string, names: string[]) {
  const used = new Set(names.map(name => name.toUpperCase()))
  let index = 1
  while (used.has(`${prefix}_${index}`)) index += 1
  return `${prefix}_${index}`
}

export function SignalFields({ signals, onChange }: { signals: GenericSignal[]; onChange: (rows: GenericSignal[]) => void }) {
  const edit = (id: string, update: Partial<GenericSignal>) =>
    onChange(signals.map(signal => signal.id === id ? { ...signal, ...update } : signal))

  return <div className="generic-signal-grid">
    {groups.map(group => {
      const rows = signals.filter(signal => signal.direction === group.direction && signal.kind === group.kind)
      return <fieldset className="generic-signal-group" key={group.label}>
        <legend>{group.label} <span>{rows.length}</span></legend>
        {rows.map(signal => <div className="generic-signal-row" key={signal.id}>
          <div className="generic-row-fields">
            <label>Nom du signal
              <input required value={signal.name} maxLength={80} spellCheck={false}
                onChange={event => edit(signal.id, { name: event.target.value })} />
            </label>
            {signal.kind === 'analog' && <>
              <label>Type IEC
                <select value={signal.iec_type} onChange={event => edit(signal.id, { iec_type: event.target.value })}>
                  {analogTypes.map(type => <option key={type}>{type}</option>)}
                </select>
              </label>
              <label>Unité
                <input value={signal.unit ?? ''} placeholder="Facultative" maxLength={80}
                  onChange={event => edit(signal.id, { unit: event.target.value })} />
              </label>
            </>}
            <button type="button" className="button secondary generic-remove"
              aria-label={`Retirer le signal ${signal.name}`}
              onClick={() => onChange(signals.filter(row => row.id !== signal.id))}>Retirer</button>
          </div>
          <code>IO.{ioName(signal)} · {signal.iec_type}</code>
          <details className="generic-variable-details">
            <summary>Noms dans les DUT</summary>
            <div className="generic-row-fields">
              <label>Variable IO
                <input value={signal.io_name ?? ''} placeholder={ioName(signal)} maxLength={80}
                  onChange={event => edit(signal.id, { io_name: event.target.value })} />
              </label>
              <label>Variable INT
                <input value={signal.internal_name ?? ''} placeholder={internName(signal)} maxLength={80}
                  onChange={event => edit(signal.id, { internal_name: event.target.value })} />
              </label>
              {signal.direction === 'input' && <label>Variable EXC
                <input value={signal.exchange_name ?? ''} placeholder={excName(signal)} maxLength={80}
                  onChange={event => edit(signal.id, { exchange_name: event.target.value })} />
              </label>}
            </div>
          </details>
        </div>)}
        {!rows.length && <p className="generic-empty">Aucun signal.</p>}
        <button type="button" className="button secondary" onClick={() => onChange([...signals, {
          id: crypto.randomUUID(),
          name: freshName(group.prefix, signals.map(signal => signal.name)),
          direction: group.direction,
          kind: group.kind,
          iec_type: group.kind === 'analog' ? 'DWORD' : 'BOOL',
        }])}>＋ Ajouter {group.direction === 'input' ? 'une entrée' : 'une sortie'}</button>
      </fieldset>
    })}
  </div>
}

export function LogicFields({ members, signals, onChange, onRemoveSignal }: {
  members: GenericMember[]
  signals: GenericSignal[]
  onChange: (rows: GenericMember[]) => void
  onRemoveSignal?: (id: string) => void
}) {
  const typeList = useId()
  const [role, setRole] = useState<GenericMember['role']>(members[0]?.role ?? 'PA')
  const edit = (id: string, update: Partial<GenericMember>) =>
    onChange(members.map(member => member.id === id ? { ...member, ...update } : member))
  const selected = members.filter(member => member.role === role)
  const generated = generatedMembers(signals, role)
  return <div className="generic-logic-fields">
    <div className="generic-role-buttons" role="group" aria-label="Familles de variables">
      {memberRoles.map(item => <button type="button" className="button secondary" key={item.role}
        aria-pressed={role === item.role} title={item.label} onClick={() => setRole(item.role)}>
        {item.role} <span>{members.filter(member => member.role === item.role).length + generatedMembers(signals, item.role).length}</span>
      </button>)}
    </div>
    <p><strong>{memberRoles.find(item => item.role === role)?.label}</strong></p>
    {generated.length > 0 && <div className="generic-generated">
      <p>Retirer un signal retire aussi les variables IO, INT et EXC associées.</p>
      {generated.map(member => <div className="generic-generated-row" key={member.id}><code>{codeRole(role)}.{member.name} · {member.type}</code>{role === 'IO' && onRemoveSignal && <button type="button" className="button secondary" aria-label={`Retirer le signal ${member.name}`} onClick={() => onRemoveSignal(member.id)}>Retirer le signal</button>}</div>)}
    </div>}
    <datalist id={typeList}>{['BOOL', ...analogTypes, 'TIME', 'TON', 'TOF', 'TP'].map(type => <option key={type} value={type} />)}</datalist>
    {selected.map(member => <div className="generic-signal-row" key={member.id}>
      <div className="generic-row-fields">
        <label>Nom de variable
          <input required value={member.name} maxLength={80} spellCheck={false}
            onChange={event => edit(member.id, { name: event.target.value })} />
        </label>
        <label>Type IEC
          <input required value={member.iec_type} list={typeList} maxLength={128}
            onChange={event => edit(member.id, { iec_type: event.target.value })} />
        </label>
        <label>Valeur initiale
          <input value={member.default_value ?? ''} maxLength={500} placeholder="Facultative"
            onChange={event => edit(member.id, { default_value: event.target.value })} />
        </label>
        <button type="button" className="button secondary generic-remove"
          aria-label={`Retirer la variable ${member.name}`}
          onClick={() => onChange(members.filter(row => row.id !== member.id))}>Retirer</button>
      </div>
      <code>{codeRole(member.role)}.{member.name || 'NOM'}</code>
      <details className="generic-variable-details">
        <summary>Informations de la variable</summary>
        <div className="generic-row-fields">
          <label>Section<input value={member.section ?? ''} maxLength={100}
            onChange={event => edit(member.id, { section: event.target.value })} /></label>
          <label>Unité<input value={member.unit ?? ''} maxLength={80}
            onChange={event => edit(member.id, { unit: event.target.value })} /></label>
          <label>Commentaire<input value={member.comment ?? ''} maxLength={500}
            onChange={event => edit(member.id, { comment: event.target.value })} /></label>
        </div>
      </details>
    </div>)}
    {!selected.length && <p className="generic-empty">Aucune variable supplémentaire.</p>}
    <button type="button" className="button secondary" onClick={() => onChange([...members, {
      id: crypto.randomUUID(), role, name: freshName('VARIABLE', [...selected, ...generated].map(member => member.name)), iec_type: 'BOOL',
    }])}>＋ Ajouter une variable {role}</button>
  </div>
}

type Draft = Omit<GenericTemplate, 'builtin'>
const emptyDraft: Draft = { id: '', name: '', description: '', signals: [], members: [], processing: '' }

export default function GenericObjects({ templates, revision, csrf, onChanged, onUse }: {
  templates: GenericTemplate[]
  revision: number
  csrf: string
  onChanged: () => void
  onUse: (id: string) => void
}) {
  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [original, setOriginal] = useState<GenericTemplate>()

  const open = (next: Draft) => {
    setOriginal(templates.find(template => template.id === next.id))
    setDraft(next)
    setNotice('')
    setError('')
  }
  const edit = (changes: Partial<Draft>) => setDraft(current => current ? { ...current, ...changes } : current)
  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!draft || busy) return
    if (original && JSON.stringify(original) !== JSON.stringify(templates.find(item => item.id === original.id))) {
      setError('Ce modèle a changé depuis son ouverture. Rouvre-le avant de le modifier.')
      return
    }
    setBusy(true)
    setError('')
    try {
      const response = await fetch('/api/v2/generic-objects', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify({ ...draft, id: draft.id || null, revision }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Vérifie les champs du modèle.')
      setNotice(`Modèle « ${draft.name.trim()} » enregistré.`)
      setDraft(null)
      onChanged()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Enregistrement impossible.')
    } finally {
      setBusy(false)
    }
  }

  const remove = async (template: GenericTemplate) => {
    if (busy || template.builtin || !window.confirm(`Supprimer le modèle « ${template.name} » ? Les types déjà créés à partir de ce modèle sont conservés.`)) return
    setBusy(true); setError(''); setNotice('')
    try {
      const response = await fetch(`/api/v2/generic-objects/${encodeURIComponent(template.id)}?revision=${revision}`, {
        method: 'DELETE', credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf },
      })
      if (!response.ok) { const result = await response.json(); throw new Error(result.detail || 'Suppression impossible.') }
      setNotice(`Modèle « ${template.name} » supprimé.`)
      onChanged()
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Suppression impossible.') }
    finally { setBusy(false) }
  }

  if (draft) return <form className="generic-editor" onSubmit={save}>
    <WorkspacePanel id="generic:identity" title={draft.id ? 'Modifier le modèle' : 'Créer un modèle générique'}>
      <div className="generic-identity">
        <label>Nom du modèle<input autoFocus required value={draft.name} maxLength={100}
          onChange={event => edit({ name: event.target.value })} placeholder="Capteur de niveau" /></label>
        <label>Description<textarea value={draft.description} maxLength={1000} rows={2}
          onChange={event => edit({ description: event.target.value })} /></label>
      </div>
    </WorkspacePanel>
    <WorkspacePanel id="generic:signals" title="Signaux du modèle">
      <SignalFields signals={draft.signals} onChange={signals => edit({ signals })} />
    </WorkspacePanel>
    <WorkspacePanel id="generic:logic" title="Variables de logique et ST de base">
      <p>Une entrée crée une variable interne et une sortie EXC disponible pour le programme.</p>
      <LogicFields members={draft.members} signals={draft.signals} onChange={members => edit({ members })} onRemoveSignal={id => edit({ signals: draft.signals.filter(signal => signal.id !== id) })} />
      <label className="generic-processing">Code PROCESSING
        <small>Utilise les noms indiqués, par exemple INTERN.MESURE. L’assistant adapte ces références lors des renommages.</small>
        <textarea value={draft.processing} maxLength={30000} rows={9} spellCheck={false}
          onChange={event => edit({ processing: event.target.value })} />
      </label>
    </WorkspacePanel>
    {error && <div className="v2-alert error" role="alert">{error}</div>}
    <div className="generic-actions">
      <button type="button" className="button secondary" disabled={busy} onClick={() => setDraft(null)}>Annuler</button>
      <button type="submit" className="button primary" disabled={busy}>{busy ? 'Enregistrement…' : 'Enregistrer le modèle'}</button>
    </div>
  </form>

  return <section className="generic-library">
    <div className="generic-actions">
      <button type="button" className="button primary" onClick={() => open(emptyDraft)}>＋ Nouveau modèle</button>
    </div>
    {error && <div className="v2-alert error" role="alert">{error}</div>}
    {notice && <div className="v2-alert success" role="status">{notice}</div>}
    <div className="generic-library-grid">
      {templates.map(template => <article className="generic-card" key={template.id}>
        <header><h2>{template.name}</h2><span>{template.builtin ? 'Modèle de départ' : 'Ce projet'}</span></header>
        <p>{template.description || 'Structure et logique de base personnalisées.'}</p>
        <div className="generic-counts">
          {groups.map(group => <span key={group.label}>
            <strong>{template.signals.filter(signal => signal.direction === group.direction && signal.kind === group.kind).length}</strong> {group.label.toLowerCase()}
          </span>)}
        </div>
        <div className="generic-actions">
          <button type="button" className="button primary" onClick={() => onUse(template.id)}>Créer un type</button>
          <button type="button" className="button secondary" onClick={() => open(template.builtin
            ? { ...template, id: '', name: `${template.name} – copie` } : template)}>
            {template.builtin ? 'Personnaliser une copie' : 'Modifier le modèle'}
          </button>
          {!template.builtin && <button type="button" className="button secondary" disabled={busy} onClick={() => void remove(template)}>Supprimer le modèle</button>}
        </div>
      </article>)}
    </div>
  </section>
}
