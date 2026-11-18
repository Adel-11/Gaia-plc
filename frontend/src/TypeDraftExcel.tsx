import { useRef } from 'react'
import type { GenericSignal } from './GenericObjects'
import type { TemplateRow } from './ModbusTemplate'

export type ExcelDraft = {
  name: string; kind: string; generic_template_id?: string | null
  signals: GenericSignal[]; duts: { role: string; members: Record<string, any>[] }[]
  modbus_template: TemplateRow[]
}

async function check(response: Response) {
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(typeof data.detail === 'string' ? data.detail : 'Impossible de traiter le classeur Excel.')
  }
  return response
}

export default function TypeDraftExcel({ draft, csrf, busy, onBusy, onImport, onError }: {
  draft: ExcelDraft; csrf: string; busy: boolean; onBusy: (busy: boolean) => void
  onImport: (draft: ExcelDraft, summary: { signals: number; members: number; mappings: number }) => void
  onError: (message: string) => void
}) {
  const menu = useRef<HTMLDetailsElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const download = async (empty: boolean) => {
    if (menu.current) menu.current.open = false
    onBusy(true); onError('')
    try {
      const body = empty ? { name: draft.name, kind: draft.kind, generic_template_id: draft.generic_template_id } : draft
      const response = await check(await fetch('/api/v2/types/draft/excel', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body),
      }))
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a')
      link.href = url
      link.download = response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1] || 'TYPE_TEMPLATE.xlsx'
      link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (cause) { onError(cause instanceof Error ? cause.message : 'Téléchargement impossible.') }
    finally { onBusy(false) }
  }
  const upload = async (file: File) => {
    if (file.size > 8 * 1024 * 1024 || !file.name.toLowerCase().endsWith('.xlsx')) {
      onError('Choisis un fichier .xlsx de moins de 8 Mo.'); return
    }
    const form = new FormData()
    form.append('file', file); form.append('current', JSON.stringify(draft))
    onBusy(true); onError('')
    try {
      const response = await check(await fetch('/api/v2/types/draft/excel/import', {
        method: 'POST', headers: { 'X-CSRF-Token': csrf }, body: form,
      }))
      const result = await response.json()
      onImport(result.draft, result.summary)
    } catch (cause) { onError(cause instanceof Error ? cause.message : 'Import impossible.') }
    finally { onBusy(false) }
  }
  return <div className="tw-excel">
    <input ref={input} hidden type="file" accept=".xlsx" aria-label="Importer les variables du type depuis Excel"
      onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void upload(file) }} />
    <details ref={menu} onKeyDown={event => { if (event.key === 'Escape' && menu.current) menu.current.open = false }}
      onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null) && menu.current) menu.current.open = false }}>
      <summary aria-disabled={busy} onClick={event => { if (busy) event.preventDefault() }}>
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M4 9h16M10 9v12M4 15h16"/></svg>
        {busy ? 'Patiente…' : 'Excel'} <span aria-hidden="true">⌄</span>
      </summary>
      <div className="tw-excel-menu">
        <button type="button" disabled={busy} onClick={() => void download(false)}>Exporter la saisie <small>Un classeur pour ce type</small></button>
        <button type="button" disabled={busy} onClick={() => void download(true)}>Télécharger le modèle vide <small>Listes de choix et exemples inclus</small></button>
        <button type="button" disabled={busy} onClick={() => { if (menu.current) menu.current.open = false; input.current?.click() }}>Importer un classeur <small>Remplit les champs, toujours modifiables</small></button>
        <p>Les feuilles présentes remplacent la saisie correspondante. Le code ST reste inchangé.</p>
      </div>
    </details>
  </div>
}
