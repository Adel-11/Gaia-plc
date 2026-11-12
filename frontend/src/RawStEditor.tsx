import ModalFullscreen from './ModalFullscreen'
import { useEffect, useId, useState } from 'react'
import { StEditor } from './V2ObjectView'
import { declarationVariables, visibleVariables, type StVariable } from './StVariables'
import WorkspacePanel from './WorkspacePanel'
import './st-source.css'

type Source = {
  name: string; kind: string; revision: number
  declaration: string; body: string | null; manual_declaration: boolean; manual_body: boolean
}
type Part = 'declaration' | 'body'
type Draft = Partial<Record<Part, string | null>>

export default function RawStEditor({ name, csrf, onClose, onSaved, insertVariables }: {
  name: string; csrf: string; onClose: () => void; onSaved: () => void; insertVariables?: StVariable[]
}) {
  const title = useId()
  const [source, setSource] = useState<Source>()
  const [draft, setDraft] = useState<Draft>({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const url = `/api/v2/code/source/${encodeURIComponent(name)}`
  const dirty = Object.keys(draft).length > 0
  const close = () => {
    if (!busy && (!dirty || window.confirm('Fermer et abandonner le brouillon ST ?'))) onClose()
  }

  useEffect(() => {
    const controller = new AbortController()
    fetch(url, { signal: controller.signal }).then(async response => {
      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || 'Impossible de charger le ST.')
      setSource(result)
    }).catch(cause => { if (!controller.signal.aborted) setError(cause.message) })
    return () => controller.abort()
  }, [url])

  const save = async () => {
    if (!source || !dirty) return
    setBusy(true); setError('')
    try {
      const response = await fetch(url, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify({ revision: source.revision, ...draft }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || 'Impossible d’enregistrer le ST.')
      onSaved()
      onClose()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Impossible d’enregistrer le ST.')
    } finally { setBusy(false) }
  }

  const renderPart = (part: Part, label: string) => {
    if (!source || source[part] === null) return null
    const original = source[part] || ''
    const manual = source[`manual_${part}`]
    const modified = part in draft
    const reset = modified && draft[part] === null
    return <WorkspacePanel id={`source:${name}:${part}`} title={label} className="raw-st-part" initialHeight={360} verticalOnly>
      <div className="raw-st-part-head">
        <span>{reset ? 'Retour au modèle à l’enregistrement' : manual || modified ? 'ST manuel' : 'Issu du modèle'}</span>
        {reset ? <button type="button" onClick={() => setDraft(previous => {
          const next = { ...previous }; delete next[part]; return next
        })}>Annuler le retour</button> :
          (manual || modified) && <button type="button" disabled={busy} onClick={() => {
            if (manual) setDraft(previous => ({ ...previous, [part]: null }))
            else setDraft(previous => { const next = { ...previous }; delete next[part]; return next })
          }}>Revenir au modèle</button>}
      </div>
      <StEditor label={label} value={draft[part] ?? original} readOnly={busy || reset}
        insertVariables={part === 'body' ? visibleVariables((insertVariables ?? []).filter(row => !row.internal), declarationVariables(draft.declaration ?? source.declaration)) : undefined}
        onChange={value => setDraft(previous => {
          const next = { ...previous }
          if (value === original) delete next[part]
          else next[part] = value
          return next
        })}/>
    </WorkspacePanel>
  }

  return <div className="raw-st-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) close() }}>
    <section className="raw-st-dialog" role="dialog" aria-modal="true" aria-labelledby={title}
      onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close() } }}>
      <ModalFullscreen/>
      <header><div><h2 id={title}>{name} · ST complet</h2><p>Le texte modifié est utilisé à l’export. Les formulaires et mappings restent disponibles si tu reviens au modèle.</p></div>
        <button type="button" className="button secondary" disabled={busy} onClick={close}>Fermer</button>
      </header>
      <div className="raw-st-body">
        {error && <p className="v2-alert error" role="alert">{error}</p>}
        {!source && !error && <p>Chargement du code…</p>}
        {renderPart('declaration', 'Déclaration ST')}
        {renderPart('body', 'Corps ST')}
      </div>
      <footer><button type="button" className="button primary" disabled={!dirty || busy} onClick={() => void save()}>{busy ? 'Enregistrement…' : 'Enregistrer le ST'}</button></footer>
    </section>
  </div>
}
