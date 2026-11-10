import { useEffect, useRef, useState } from 'react'
import './exchanges.css'

type Preview = { token?: string | null; changes?: string[]; errors?: string[] }
type Props = { name: string; functionId?: string; revision: number; csrf: string; onClose: () => void; onDeleted: () => void }

async function post<T>(action: 'preview' | 'apply', csrf: string, body: object, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/v2/exchanges/${action}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
    body: JSON.stringify(body), signal,
  })
  const result = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(response.status === 409
    ? 'Le projet a changé. Fermez cette fenêtre, rechargez la page, puis réessayez.'
    : result.detail || `Erreur ${response.status}`)
  return result as T
}

export default function ExchangeDeleteDialog({ name, functionId = name, revision, csrf, onClose, onDeleted }: Props) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    const element = dialog.current
    element?.showModal()
    return () => element?.close()
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    setPreview(null)
    setError('')
    void post<Preview>('preview', csrf, { revision, action: 'delete_function', function_id: functionId }, controller.signal)
      .then(result => { if (!controller.signal.aborted) setPreview(result) })
      .catch(e => { if (!controller.signal.aborted) setError(e.message) })
    return () => controller.abort()
  }, [csrf, functionId, revision])

  async function remove() {
    if (!preview?.token || saving) return
    setSaving(true)
    setError('')
    try {
      await post('apply', csrf, { token: preview.token })
      onDeleted()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Impossible de supprimer la fonction.')
      setSaving(false)
    }
  }

  return <dialog ref={dialog} className="ex-preview ex-delete-dialog" aria-labelledby="ex-delete-title"
    onCancel={event => { event.preventDefault(); if (!saving) onClose() }}>
    <header className="ex-preview-top"><h2 id="ex-delete-title">Supprimer {name} ?</h2></header>
    {!preview && !error && <p role="status">Vérification des connexions…</p>}
    {!!preview?.changes?.length && <ul className="ex-changes">{preview.changes.map((change, i) => <li key={i}>{change}</li>)}</ul>}
    {preview?.errors?.map((message, i) => <p className="ex-alert" role="alert" key={i}>{message}</p>)}
    {error && <p className="ex-alert" role="alert">{error}</p>}
    <footer className="ex-preview-footer">
      <button className="ex-button" type="button" autoFocus disabled={saving} onClick={onClose}>Annuler</button>
      <button className="ex-button ex-delete-button" type="button" disabled={!preview?.token || saving} onClick={() => void remove()}>
        {saving ? 'Suppression…' : 'Confirmer la suppression'}
      </button>
    </footer>
  </dialog>
}
