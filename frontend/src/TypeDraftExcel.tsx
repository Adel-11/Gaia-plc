import { useRef } from 'react'
import type { GenericSignal } from './GenericObjects'
import type { TemplateRow } from './ModbusTemplate'

export type ExcelScope = 'signals' | 'duts' | 'modbus'
export const excelLabels: Record<ExcelScope, string> = { signals: 'Signaux', duts: 'Blocs / DUT', modbus: 'Modbus' }
export type ExcelDraft = {
  scope?: ExcelScope | 'all'
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

export default function TypeDraftExcel({ draft, scope, csrf, busy, onBusy, onImport, onError }: {
  scope: ExcelScope
  draft: ExcelDraft; csrf: string; busy: boolean; onBusy: (busy: boolean) => void
  onImport: (draft: ExcelDraft, summary: { signals: number; members: number; mappings: number }, warnings: string[]) => void
  onError: (message: string) => void
}) {
  const menu = useRef<HTMLDetailsElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const download = async (empty: boolean) => {
    if (menu.current) menu.current.open = false
    onBusy(true); onError('')
    try {
      const body = { ...draft, scope }
      const response = await check(await fetch(`/api/v2/types/draft/excel${empty ? '?empty=true' : ''}`, {
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
    form.append('file', file); form.append('current', JSON.stringify({ ...draft, scope }))
    onBusy(true); onError('')
    try {
      const response = await check(await fetch('/api/v2/types/draft/excel/import', {
        method: 'POST', headers: { 'X-CSRF-Token': csrf }, body: form,
      }))
      const result = await response.json()
      onImport(result.draft, result.summary, result.warnings || [])
    } catch (cause) { onError(cause instanceof Error ? cause.message : 'Import impossible.') }
    finally { onBusy(false) }
  }
  return <div className="tw-excel">
    <input ref={input} hidden type="file" accept=".xlsx" aria-label={`Importer l’étape ${excelLabels[scope]} depuis Excel`}
      onChange={event => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void upload(file) }} />
    <details ref={menu} onKeyDown={event => { if (event.key === 'Escape' && menu.current) menu.current.open = false }}
      onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null) && menu.current) menu.current.open = false }}>
      <summary aria-disabled={busy} onClick={event => { if (busy) event.preventDefault() }}>
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M4 9h16M10 9v12M4 15h16"/></svg>
        {busy ? 'Patiente…' : 'Excel'} <span aria-hidden="true">⌄</span>
      </summary>
      <div className="tw-excel-menu">
        <button type="button" disabled={busy} onClick={() => void download(true)}>Modèle de cette étape <small>{excelLabels[scope]} · guide des colonnes et exemples</small></button>
        <button type="button" disabled={busy} onClick={() => void download(false)}>Exporter cette étape <small>Seulement la saisie {excelLabels[scope]}</small></button>
        <button type="button" disabled={busy} onClick={() => { if (menu.current) menu.current.open = false; input.current?.click() }}>Importer cette étape <small>Les champs restent modifiables</small></button>
        <p>Seules les feuilles de cette étape sont importées. Le code ST et les autres étapes sont conservés.</p>
      </div>
    </details>
  </div>
}
