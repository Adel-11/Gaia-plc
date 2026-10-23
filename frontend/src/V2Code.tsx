import { useEffect, useMemo, useState } from 'react'
import { StEditor } from './V2ObjectView'
import './code.css'

type Category = 'generated' | 'functions'
type CodeObject = { name: string; kind: string; category: 'generated' | 'function' | 'exchange'; folder?: string; body?: string; full_body?: string; custom_code?: string; declaration?: string; changed?: boolean; status?: 'new' | 'changed' | 'unchanged'; reference_body?: string; diff?: string; dependencies?: string[] }
type Data = { revision: number; calculated_at?: string | null; snapshot_imported_at?: string | null; objects: CodeObject[]; validation?: { errors?: unknown[]; warnings?: unknown[] } }
type Review = { token?: string | null; errors?: unknown[]; warnings?: unknown[]; changes?: unknown[]; object?: CodeObject }
type Props = { csrf: string; projectRevision?: number; onSaved: () => void; onOpenExchanges: () => void; onDirtyChange?: (dirty: boolean) => void; category: Category }
const messages = (v: unknown) => Array.isArray(v) ? v.map(x => typeof x === 'string' ? x : String((x as any)?.message ?? (x as any)?.detail ?? JSON.stringify(x))) : []
const lines = (s?: string) => (s || '').split(/\r?\n/).length
const date = (s?: string | null) => s ? new Date(s).toLocaleString('fr-FR') : '—'

async function api<T>(path: string, body?: unknown, csrf = ''): Promise<T> {
  const response = await fetch(path, { method: body ? 'POST' : 'GET', credentials: 'same-origin', headers: body ? { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf } : {}, body: body ? JSON.stringify(body) : undefined })
  const json = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(json.detail || json.message || `Erreur ${response.status}`)
  return json as T
}

function makeDiff(reference = '', current = '') {
  const a = reference.split(/\r?\n/), b = current.split(/\r?\n/)
  if (a.length * b.length > 180000) return current === reference ? [] : [{ kind: 'context', text: 'Diff trop volumineux pour un alignement détaillé.' }]
  const dp = Array.from({ length: a.length + 1 }, () => new Uint32Array(b.length + 1))
  for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--) dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
  const out: { kind: string; text: string }[] = []; let i = 0, j = 0
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) { out.push({ kind: 'same', text: a[i++] }); j++ }
    else if (j < b.length && (i === a.length || dp[i][j + 1] >= dp[i + 1][j])) out.push({ kind: 'add', text: b[j++] })
    else out.push({ kind: 'remove', text: a[i++] })
  }
  return out
}

export default function V2Code({ csrf, projectRevision, onSaved, onOpenExchanges, onDirtyChange, category }: Props) {
  const [data, setData] = useState<Data | null>(null), [selected, setSelected] = useState(''), [filter, setFilter] = useState<'all' | 'new' | 'changed' | 'unchanged'>('all'), [kindFilter, setKindFilter] = useState<'all' | 'function' | 'exchange'>('all'), [query, setQuery] = useState('')
  const [drafts, setDrafts] = useState<Record<string, { body?: string; custom_code?: string }>>({}), [pane, setPane] = useState<'code' | 'compare'>('code')
  const [review, setReview] = useState<Review | null>(null), [confirmed, setConfirmed] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('')
  const isDirty = (o: CodeObject) => { const d = drafts[o.name]; return !!d && ((d.body !== undefined && d.body !== (o.body || '')) || (d.custom_code !== undefined && d.custom_code !== (o.custom_code || ''))) }
  const dirty = data?.objects.filter(isDirty) || []
  useEffect(() => { onDirtyChange?.(dirty.length > 0) }, [dirty.length, onDirtyChange])
  async function load(force = false) {
    setError('')
    try {
      const fresh = await api<Data>('/api/v2/code')
      if (!force && data && Number(fresh.revision) !== Number(data.revision) && dirty.length) { setNotice('Le projet a changé ailleurs. Votre brouillon est conservé. Rechargez pour comparer avec la dernière version enregistrée.'); return }
      setData(fresh); setNotice('')
      setSelected(current => fresh.objects.some(o => o.name === current) ? current : fresh.objects.find(o => category === 'generated' ? o.category === 'generated' : o.category !== 'generated')?.name || '')
    } catch (e) { setError(e instanceof Error ? e.message : 'Impossible de charger le code') }
  }
  useEffect(() => { void load() }, [])
  useEffect(() => { if (projectRevision !== undefined && data && Number(projectRevision) !== Number(data.revision)) void load() }, [projectRevision])
  const visible = useMemo(() => (data?.objects || []).filter(o => category === 'generated' ? o.category === 'generated' : o.category === 'function' || o.category === 'exchange').filter(o => category !== 'functions' || kindFilter === 'all' || o.category === kindFilter).filter(o => filter === 'all' || (o.status || (o.changed ? 'changed' : 'unchanged')) === filter).filter(o => !query || `${o.name} ${o.kind} ${o.folder || ''}`.toLowerCase().includes(query.toLowerCase())), [data, category, filter, kindFilter, query])
  const object = data?.objects.find(o => o.name === selected)
  const draft = object ? drafts[object.name] || {} : {}
  const currentBody = object ? draft.body ?? object.body ?? '' : ''
  const currentCustom = object ? draft.custom_code ?? object.custom_code ?? '' : ''
  const currentComparable = object ? `${currentBody}${currentCustom ? `\n${currentCustom}` : ''}` : ''
  const editable = !!object && object.category === 'function'
  const objectDirty = !!object && isDirty(object)
  function change(field: 'body' | 'custom_code', value: string) { if (!object) return; setDrafts(old => ({ ...old, [object.name]: { ...old[object.name], [field]: value } })); setReview(null); setConfirmed(false); setError('') }
  async function preview() {
    if (!object || !objectDirty || !data) return
    setBusy(true); setError('')
    try { setReview(await api<Review>('/api/v2/code/preview', { revision: data.revision, name: object.name, ...(draft.body !== undefined ? { body: draft.body } : {}), ...(draft.custom_code !== undefined ? { custom_code: draft.custom_code } : {}) }, csrf)); setConfirmed(false) }
    catch (e) { setError(e instanceof Error ? e.message : 'Impossible de préparer la revue') } finally { setBusy(false) }
  }
  async function apply() {
    if (!review?.token) return
    setBusy(true); setError('')
    try { await api('/api/v2/code/apply', { token: review.token }, csrf); const savedName = object?.name; setReview(null); setConfirmed(false); if (savedName) setDrafts(old => { const next = { ...old }; delete next[savedName]; return next }); await load(true); onSaved() }
    catch (e) { setError(e instanceof Error ? e.message : 'Impossible d’enregistrer') } finally { setBusy(false) }
  }
  const rows = visible
  const statusLabel = (o: CodeObject) => ({ new: 'Nouveau', changed: 'Modifié', unchanged: 'Identique' }[o.status || (o.changed ? 'changed' : 'unchanged')])
  return <main className={`code-page ${category === 'generated' ? 'generated' : 'functions'}`}>
    <header className="code-head"><div><span className="code-kicker">PROJET · CODE SOURCE</span><h1>{category === 'generated' ? 'Code généré' : 'Fonctions et échanges'}</h1><p>{category === 'generated' ? 'Code calculé depuis le modèle du projet, en lecture seule.' : 'Consultez les fonctions et comparez-les à la référence CODESYS.'}</p></div><div className="code-head-meta"><span>Révision {data?.revision ?? '—'}</span><span>Calcul {date(data?.calculated_at)}</span><button onClick={() => void load()} disabled={busy}>Actualiser</button></div></header>
    {category === 'generated' && <div className="code-calc"><b>Dernier calcul réel</b><span>{date(data?.calculated_at)}</span><b>Snapshot importé</b><span>{date(data?.snapshot_imported_at)}</span></div>}
    {notice && <div className="code-notice" role="status">{notice} <button onClick={() => { setDrafts({}); void load(true) }}>Abandonner le brouillon et recharger</button></div>}
    {error && <div className="code-error" role="alert">{error}</div>}
    {messages(data?.validation?.errors).length > 0 && <div className="code-error" role="alert"><b>Export à corriger</b>{messages(data?.validation?.errors).map((message,index) => <p key={index}>{message}</p>)}</div>}
    {messages(data?.validation?.warnings).length > 0 && <details className="code-notice"><summary>{messages(data?.validation?.warnings).length} avertissement(s) de validation</summary>{messages(data?.validation?.warnings).map((message,index) => <p key={index}>{message}</p>)}</details>}

    <section className="code-workspace">
      <div className="code-list"><div className="code-list-top"><label>Rechercher<input value={query} onChange={e => setQuery(e.target.value)} placeholder="Nom ou dossier" /></label>{category === 'functions' && <label>État<select value={filter} onChange={e => setFilter(e.target.value as any)}><option value="all">Tous</option><option value="new">Nouveaux</option><option value="changed">Modifiés</option><option value="unchanged">Identiques</option></select></label>}</div>
        {category === 'functions' && <div className="code-kind-tabs"><span>Affichage</span><button className={kindFilter === 'all' ? 'active' : ''} onClick={() => setKindFilter('all')}>FNC et échanges</button><button className={kindFilter === 'function' ? 'active' : ''} onClick={() => setKindFilter('function')}>FNC</button><button className={kindFilter === 'exchange' ? 'active' : ''} onClick={() => setKindFilter('exchange')}>Échanges</button><button className="code-exchanges-link" onClick={onOpenExchanges}>Ouvrir la page échanges ↗</button></div>}
        <div className="code-table-wrap"><table className="code-table"><thead><tr><th>Nom</th><th>{category === 'generated' ? 'Dossier' : 'Type'}</th><th>État</th><th>Lignes</th></tr></thead><tbody>{rows.map(o => <tr key={o.name} className={selected === o.name ? 'selected' : ''} onClick={() => { setSelected(o.name); setReview(null); setConfirmed(false) }}><td><button className="code-row-name" onClick={e => { e.stopPropagation(); setSelected(o.name); setReview(null); setConfirmed(false) }}>{o.name}</button></td><td>{category === 'generated' ? o.folder || '—' : o.kind}</td><td><span className={`code-status ${o.status || (o.changed ? 'changed' : 'unchanged')}`}>{statusLabel(o)}</span></td><td>{lines(o.full_body || o.body)}</td></tr>)}{!rows.length && <tr><td colSpan={4} className="code-empty">Aucun objet dans ce filtre.</td></tr>}</tbody></table></div>
      </div>
      <section className="code-detail" aria-label="Détail du code">
        {!object ? <div className="code-empty-detail">Sélectionnez un objet pour afficher son code.</div> : <>
          <div className="code-detail-head"><div><span className="code-kicker">{object.kind} · {object.folder || object.category}</span><h2>{object.name}</h2><p>{object.dependencies?.length ? `Dépendances : ${object.dependencies.join(', ')}` : 'Aucune dépendance déclarée'}{objectDirty && <b className="code-dirty">Brouillon non enregistré</b>}</p></div><div className="code-detail-actions">{object.category === 'exchange' ? <button className="code-primary" onClick={onOpenExchanges}>Modifier dans les échanges ↗</button> : <button className="code-primary" disabled={!objectDirty || busy} onClick={() => void preview()}>{busy ? 'Préparation…' : 'Revoir et enregistrer'}</button>}</div></div>
          <div className="code-pane-tabs"><button className={pane === 'code' ? 'active' : ''} onClick={() => setPane('code')}>Code source</button><button className={pane === 'compare' ? 'active' : ''} onClick={() => setPane('compare')}>Comparer à CODESYS</button></div>
          {pane === 'code' ? <div className="code-editor-stack">{object.declaration && <StEditor value={object.declaration} readOnly label="Déclaration · lecture seule"/>}<StEditor value={currentBody} readOnly={!editable} onChange={object.category === 'function' ? v => change('body', v) : undefined} label={`${object.category === 'generated' ? 'Code généré · lecture seule' : 'Corps ST'} · ${lines(currentBody)} lignes`}/>{object.category !== 'exchange' && <StEditor value={currentCustom} onChange={v => change('custom_code', v)} label="Code personnalisé · modifiable"/>}</div> : <div className="code-diff"><div className="code-diff-head"><div><b>{objectDirty ? 'Brouillon courant' : 'Version du projet'}</b><span>{object.name}</span></div><div><b>Référence CODESYS</b><span>Snapshot {date(data?.snapshot_imported_at)}</span></div></div><pre>{makeDiff(object.reference_body || '', !objectDirty && object.full_body ? object.full_body : currentComparable).map((line, i) => <span key={i} className={`diff-${line.kind}`}>{line.kind === 'add' ? '+' : line.kind === 'remove' ? '−' : ' '} {line.text || ' '}\n</span>)}</pre></div>}
        </>}
      </section>
    </section>
    {review && <div className="code-modal-backdrop" role="presentation"><section className="code-modal" role="dialog" aria-modal="true" aria-labelledby="code-review-title"><header><div><span className="code-kicker">REVUE DES CHANGEMENTS</span><h2 id="code-review-title">Enregistrer {object?.name}</h2></div><button aria-label="Fermer la revue" onClick={() => { setReview(null); setConfirmed(false) }}>×</button></header><div className="code-modal-body"><p>Les modifications seront appliquées à la révision {data?.revision}. Voici le code ST final proposé :</p>{review.object?.full_body !== undefined && <div className="code-review-editor"><StEditor value={review.object.full_body} readOnly label="Code ST final proposé · lecture seule"/></div>}{messages(review.changes).length > 0 && <ul>{messages(review.changes).map((m,i)=><li key={i}>{m}</li>)}</ul>}{messages(review.errors).length > 0 && <div className="code-modal-errors"><b>Erreurs</b>{messages(review.errors).map((m,i)=><p key={i}>{m}</p>)}</div>}{messages(review.warnings).length > 0 && <div className="code-modal-warnings"><b>Avertissements</b>{messages(review.warnings).map((m,i)=><p key={i}>{m}</p>)}</div>}{!messages(review.errors).length && review.token && <label className="code-confirm"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)}/> J’ai vérifié les changements et souhaite les enregistrer.</label>}</div><footer><button onClick={() => { setReview(null); setConfirmed(false) }}>Continuer la modification</button><button className="code-primary" disabled={busy || !review.token || !!messages(review.errors).length || !confirmed} onClick={() => void apply()}>Enregistrer</button></footer></section></div>}
  </main>
}




