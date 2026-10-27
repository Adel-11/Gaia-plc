import { useEffect, useState } from 'react'

type Review = { token: string | null; type_name: string; errors: string[]; blockers: string[]; changes: string[] }
type Props = { typeId: string; typeName: string; revision: number; csrf: string; onClose: () => void; onDeleted: () => void }

export default function V2TypeDelete({ typeId, typeName, revision, csrf, onClose, onDeleted }: Props) {
  const [review, setReview] = useState<Review | null>(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  async function post(path: string, body: unknown) {
    const response = await fetch(`/api/v2/types/delete/${path}`, { method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify(body) })
    const result = await response.json()
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Action impossible. Actualisez puis réessayez.')
    return result
  }
  useEffect(() => {
    let active = true
    void post('preview', {revision, type_id: typeId}).then(value => { if (active) setReview(value) }).catch(e => { if (active) setError(e.message) }).finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [typeId, revision, csrf])
  async function remove() {
    if (!review?.token) return
    setBusy(true); setError('')
    try { await post('apply', {token: review.token}); onDeleted() }
    catch (e: any) { setError(e.message) }
    finally { setBusy(false) }
  }
  return <div className="instance-backdrop"><section className="instance-dialog" role="dialog" aria-modal="true" aria-labelledby="type-delete-title">
    <header><h2 id="type-delete-title">Supprimer le type {typeName} ?</h2><button aria-label="Fermer la suppression du type" disabled={busy} onClick={onClose}>×</button></header>
    <div className="instance-review">
      <p>Le catalogue de types est partagé entre tous les projets. La suppression retirera ses DUT, son FB et son classeur Excel du catalogue.</p>
      {busy && !review && <p>Vérification des dépendances…</p>}
      {error && <p className="instances-error" role="alert">{error}</p>}
      {review?.errors.map((message, i) => <p className="warning" role="alert" key={i}>{message}</p>)}
      {!!review?.blockers.length && <ul>{review.blockers.map((item, i) => <li key={i}>{item}</li>)}</ul>}
      {review?.token && <><h3>Éléments supprimés</h3><ul>{review.changes.map((item, i) => <li key={i}>{item}</li>)}</ul></>}
    </div>
    <footer><button disabled={busy} onClick={onClose}>Annuler</button><button className="type-delete-button" disabled={busy || !review?.token} onClick={() => void remove()}>{busy && review ? 'Suppression…' : 'Supprimer le type'}</button></footer>
  </section></div>
}
